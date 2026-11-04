"""Mobile-friendly Instagram photo post -> PDF / Android print PWA.

Run: uvicorn mobile_app:app --host 0.0.0.0 --port 8000
The Instagram account credentials are never baked into the application.
"""
from __future__ import annotations

import io
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from converter import (MAX_FILES, PostConversionError, build_pdf,
                       canonical_post_url, download_post_images)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
APP_PIN = os.environ.get("APP_PIN", "").strip()
# Secret generated on every restart for local development; set APP_SECRET_KEY
# for a stable hosted login session.
SESSION_SECRET = os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32)
JOB_TTL = 45 * 60  # Results held in RAM, then forgotten.
MAX_MEMORY_BYTES = 95 * 1024 * 1024
TOTAL_CACHE_LIMIT = 160 * 1024 * 1024
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

app = FastAPI(title="PostPDF Mobil", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax",
                   https_only=os.getenv("COOKIE_SECURE", "0") == "1")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


@dataclass
class Job:
    owner: str
    created_at: float
    filename: str
    pdf: bytes
    preview_images: list[bytes]


_jobs: dict[str, Job] = {}
_jobs_lock = threading.RLock()
_busy = threading.BoundedSemaphore(value=2)
_failed_logins: dict[str, tuple[int, float]] = {}


def prune_jobs() -> None:
    now = time.monotonic()
    with _jobs_lock:
        for key in list(_jobs):
            if now - _jobs[key].created_at > JOB_TTL:
                del _jobs[key]


def owner_id(request: Request) -> str:
    if not request.session.get("owner"):
        request.session["owner"] = secrets.token_urlsafe(20)
    return str(request.session["owner"])


def csrf_token(request: Request) -> str:
    if not request.session.get("csrf"):
        request.session["csrf"] = secrets.token_urlsafe(24)
    return str(request.session["csrf"])


def has_access(request: Request) -> bool:
    return not APP_PIN or request.session.get("signed_in") is True


def csrf_valid(request: Request) -> bool:
    expected = request.session.get("csrf", "")
    provided = request.headers.get("x-csrf-token", "")
    return bool(expected and provided) and secrets.compare_digest(str(expected), provided)


def bad_request(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def store_job(owner: str, pdf: bytes, images: list[bytes], filename: str) -> tuple[str, int]:
    if not pdf or not images:
        raise PostConversionError("PDF için fotoğraf bulunamadı.")
    if len(pdf) + sum(map(len, images)) > MAX_MEMORY_BYTES:
        raise PostConversionError("PDF çok büyük. Daha az veya küçük fotoğraf deneyin.")
    prune_jobs()
    job_id = secrets.token_urlsafe(20)
    with _jobs_lock:
        # Bound both the number of results and total server RAM usage.
        def cache_bytes():
            return sum(len(j.pdf) + sum(map(len, j.preview_images)) for j in _jobs.values())
        while _jobs and (len(_jobs) >= 12 or cache_bytes() + len(pdf) + sum(map(len, images)) > TOTAL_CACHE_LIMIT):
            del _jobs[min(_jobs, key=lambda x: _jobs[x].created_at)]
        _jobs[job_id] = Job(owner, time.monotonic(), filename, pdf, images)
    return job_id, len(images)


def get_job(request: Request, job_id: str) -> Job | None:
    prune_jobs()
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job and request.session.get("owner") == job.owner and has_access(request):
            return job
    return None


def images_for_print(paths: list[Path]) -> list[bytes]:
    """Make small, EXIF-correct white-backed previews for Android printing."""
    out: list[bytes] = []
    for path in paths:
        try:
            with Image.open(path) as img:
                if img.width * img.height > MAX_IMAGE_PIXELS:
                    raise PostConversionError("Fotoğraflardan birinin çözünürlüğü çok yüksek.")
                img = ImageOps.exif_transpose(img).convert("RGBA")
                backdrop = Image.new("RGB", img.size, "white")
                backdrop.paste(img, mask=img.getchannel("A"))
                backdrop.thumbnail((1800, 2400), Image.Resampling.LANCZOS)
                file_obj = io.BytesIO()
                backdrop.save(file_obj, "JPEG", quality=90, optimize=True)
                out.append(file_obj.getvalue())
        except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
            raise PostConversionError("Fotoğraflardan biri okunamadı.") from exc
    return out


def convert_from_link(owner: str, url: str, paper: str) -> tuple[str, int, str]:
    canonical, shortcode = canonical_post_url(url)
    with TemporaryDirectory(prefix="postpdf_ig_") as tmp:
        paths = download_post_images(canonical, Path(tmp))
        pdf = build_pdf(paths, paper)
        preview = images_for_print(paths)
    filename = f"instagram_{shortcode}.pdf"
    jobid, count = store_job(owner, pdf, preview, filename)
    return jobid, count, filename


def convert_from_upload(owner: str, image_data: list[bytes], paper: str) -> tuple[str, int, str]:
    if not image_data or len(image_data) > MAX_FILES:
        raise PostConversionError("Lütfen 1–30 fotoğraf seçin.")
    if sum(map(len, image_data)) > MAX_UPLOAD_BYTES:
        raise PostConversionError("Fotoğrafların toplam boyutu 60 MB'ı geçemez.")
    with TemporaryDirectory(prefix="postpdf_upload_") as tmp:
        paths = []
        for idx, payload in enumerate(image_data, start=1):
            if len(payload) > 20 * 1024 * 1024:
                raise PostConversionError("Tek fotoğraf en fazla 20 MB olabilir.")
            try:
                with Image.open(io.BytesIO(payload)) as img:
                    img.verify()
                with Image.open(io.BytesIO(payload)) as img:
                    if img.width * img.height > MAX_IMAGE_PIXELS:
                        raise PostConversionError("Fotoğrafın çözünürlüğü çok yüksek.")
                    img = ImageOps.exif_transpose(img).convert("RGBA")
                    base = Image.new("RGB", img.size, "white")
                    base.paste(img, mask=img.getchannel("A"))
                    path = Path(tmp) / f"{idx:03d}.jpg"
                    base.save(path, "JPEG", quality=93)
                    paths.append(path)
            except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
                raise PostConversionError(f"{idx}. fotoğraf geçerli bir görsel değil.") from exc
        pdf = build_pdf(paths, paper)
        preview = images_for_print(paths)
    filename = "fotograflarim.pdf"
    jobid, count = store_job(owner, pdf, preview, filename)
    return jobid, count, filename


@app.middleware("http")
async def privacy_headers(request: Request, call_next: Callable):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not has_access(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {
        "csrf": csrf_token(request), "configured": bool(os.environ.get("IG_COOKIES_FILE") or
                 (os.environ.get("IG_USERNAME") and os.environ.get("IG_PASSWORD"))),
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not APP_PIN or has_access(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"csrf": csrf_token(request), "error": ""})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    if not APP_PIN:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    expected = request.session.get("csrf", "")
    supplied = str(form.get("csrf", ""))
    client_key = request.client.host if request.client else "unknown"
    attempts, then = _failed_logins.get(client_key, (0, 0))
    if time.monotonic() - then > 900:
        attempts = 0
    if attempts >= 8:
        return templates.TemplateResponse(request, "login.html", {
            "csrf": csrf_token(request), "error": "Çok fazla deneme. 15 dakika sonra tekrar deneyin."
        }, status_code=429)
    if not supplied or not secrets.compare_digest(str(expected), supplied) or not secrets.compare_digest(APP_PIN, str(form.get("pin", ""))):
        _failed_logins[client_key] = (attempts + 1, time.monotonic())
        return templates.TemplateResponse(request, "login.html", {
            "csrf": csrf_token(request), "error": "Erişim kodu yanlış."
        }, status_code=403)
    request.session.clear()
    request.session["signed_in"] = True
    request.session["owner"] = secrets.token_urlsafe(20)
    request.session["csrf"] = secrets.token_urlsafe(24)
    _failed_logins.pop(client_key, None)
    return RedirectResponse("/", status_code=303)


@app.post("/api/convert")
async def convert_link(request: Request):
    if not has_access(request):
        return bad_request("Önce giriş yapın.", 401)
    if not csrf_valid(request):
        return bad_request("Güvenlik doğrulaması başarısız. Sayfayı yenileyin.", 403)
    try:
        payload = await request.json()
        url = str(payload.get("url", ""))[:1000]
        paper = payload.get("paper", "Orijinal oran")
        if paper not in ("Orijinal oran", "A4"):
            return bad_request("Sayfa biçimi geçerli değil.")
        canonical_post_url(url)
    except (ValueError, TypeError, AttributeError, PostConversionError) as exc:
        return bad_request(str(exc) if isinstance(exc, PostConversionError) else "Geçersiz bağlantı.")
    if not _busy.acquire(blocking=False):
        return bad_request("Sunucu şu anda meşgul. Biraz sonra deneyin.", 429)
    try:
        jobid, count, filename = await run_in_threadpool(convert_from_link, owner_id(request), url, paper)
        return {"id": jobid, "count": count, "filename": filename,
                "pdf_url": f"/pdf/{jobid}", "print_url": f"/print/{jobid}"}
    except PostConversionError as exc:
        return bad_request(str(exc))
    except Exception:
        return bad_request("İşlem tamamlanamadı. Başka bir gönderi deneyin.", 502)
    finally:
        _busy.release()


@app.post("/api/upload")
async def convert_upload(request: Request):
    if not has_access(request):
        return bad_request("Önce giriş yapın.", 401)
    if not csrf_valid(request):
        return bad_request("Güvenlik doğrulaması başarısız. Sayfayı yenileyin.", 403)
    length_header = request.headers.get("content-length", "")
    if length_header.isdigit() and int(length_header) > MAX_UPLOAD_BYTES + 1024 * 1024:
        return bad_request("Yükleme 60 MB sınırını aşıyor.", 413)
    form = await request.form(max_files=MAX_FILES, max_fields=8, max_part_size=MAX_UPLOAD_BYTES)
    paper = str(form.get("paper", "Orijinal oran"))
    if paper not in ("Orijinal oran", "A4"):
        return bad_request("Sayfa biçimi geçerli değil.")
    uploaded = form.getlist("images")
    if not uploaded or len(uploaded) > MAX_FILES:
        return bad_request("Lütfen 1–30 fotoğraf seçin.")
    chunks: list[bytes] = []
    total = 0
    for item in uploaded:
        if not hasattr(item, "read"):
            return bad_request("Seçilen dosya geçerli değil.")
        data = await item.read(20 * 1024 * 1024 + 1)
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            return bad_request("Fotoğraflar toplam 60 MB'ı geçemez.", 413)
        chunks.append(data)
    if not _busy.acquire(blocking=False):
        return bad_request("Sunucu şu anda meşgul. Biraz sonra deneyin.", 429)
    try:
        jobid, count, filename = await run_in_threadpool(convert_from_upload, owner_id(request), chunks, paper)
        return {"id": jobid, "count": count, "filename": filename,
                "pdf_url": f"/pdf/{jobid}", "print_url": f"/print/{jobid}"}
    except PostConversionError as exc:
        return bad_request(str(exc))
    except Exception:
        return bad_request("Fotoğraflar dönüştürülemedi. Başka dosyalar deneyin.", 502)
    finally:
        _busy.release()


@app.get("/pdf/{job_id}")
async def download_pdf(request: Request, job_id: str):
    job = get_job(request, job_id)
    if not job:
        return Response("PDF bulunamadı veya süresi doldu.", status_code=404)
    return Response(job.pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{job.filename}"',
        "Content-Length": str(len(job.pdf)),
    })


@app.get("/print/{job_id}", response_class=HTMLResponse)
async def print_preview(request: Request, job_id: str):
    job = get_job(request, job_id)
    if not job:
        return HTMLResponse("Yazdırma işi bulunamadı veya süresi doldu.", status_code=404)
    return templates.TemplateResponse(request, "print.html", {
        "job_id": job_id, "count": len(job.preview_images), "filename": job.filename
    })


@app.get("/image/{job_id}/{index}")
async def image_for_print(request: Request, job_id: str, index: int):
    job = get_job(request, job_id)
    if not job or index < 0 or index >= len(job.preview_images):
        return Response("Fotoğraf bulunamadı.", status_code=404)
    return Response(job.preview_images[index], media_type="image/jpeg")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
