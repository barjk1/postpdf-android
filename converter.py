"""Instagram public photo post to PDF conversion helpers.

This project does not bypass Instagram access controls.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


class PostConversionError(RuntimeError):
    """Expected validation, download, or conversion error."""


SHORTCODE = re.compile(r"[a-zA-Z0-9_-]{3,64}\Z")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_FILES = 30  # Guardrail; Instagram carousel posts currently have fewer slides.
MAX_TOTAL_BYTES = 150 * 1024 * 1024


def canonical_post_url(raw_url: str) -> tuple[str, str]:
    """Accept only an Instagram /p/ shortcode URL, stripping tracking parameters."""
    link = raw_url.strip()
    if not link:
        raise PostConversionError("Lütfen bir Instagram gönderi bağlantısı yapıştırın.")
    if "://" not in link:
        link = "https://" + link
    try:
        parsed = urlsplit(link)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise PostConversionError("Bağlantının biçimi geçerli değil.") from exc
    if parsed.scheme.lower() != "https" or host not in {
        "instagram.com", "www.instagram.com", "m.instagram.com"
    } or port not in (None, 443) or parsed.username or parsed.password:
        raise PostConversionError("Yalnızca https://www.instagram.com/p/... bağlantıları kabul edilir.")
    path_parts = [x for x in parsed.path.split("/") if x]
    if len(path_parts) != 2 or path_parts[0] != "p" or not SHORTCODE.fullmatch(path_parts[1]):
        raise PostConversionError("Fotoğraflı bir gönderinin /p/ bağlantısını girin. Reels desteklenmiyor.")
    shortcode = path_parts[1]
    return f"https://www.instagram.com/p/{shortcode}/", shortcode


def natural_sort_key(path: Path) -> list[object]:
    return [int(segment) if segment.isdigit() else segment.casefold()
            for segment in re.split(r"(\d+)", path.name)]


def download_post_images(url: str, destination: Path, timeout_seconds: int = 150) -> list[Path]:
    """Download *image* slides from a public Instagram post using gallery-dl.

    Supports optional server-side login/session from environment variables.
    Credentials are never embedded in the browser or passed on the command line.
    Video slides are skipped.
    """
    canonical, _ = canonical_post_url(url)
    destination.mkdir(parents=True, exist_ok=True)
    # The URL has been canonicalized to instagram.com only (SSRF protection).
    # Cookies are preferable to username/password login, which Instagram may block.
    cookies_path = os.getenv("IG_COOKIES_FILE", "").strip()
    ig_username = os.getenv("IG_USERNAME", "").strip()
    ig_password = os.getenv("IG_PASSWORD", "")

    command = [
        sys.executable, "-m", "gallery_dl",
        "--config-ignore", "--no-input", "--no-colors",
        "--http-timeout", "20", "--retries", "2",
        "--filesize-max", "20M",
        "-D", str(destination),
        "-f", "{num:>03}.{extension}",
        "-o", "extractor.instagram.order-files=asc",
        "-o", "extractor.instagram.videos=false",
    ]
    with TemporaryDirectory(prefix="ig_session_cfg_") as auth_dir:
        # A private file keeps credentials out of process arguments and source.
        if cookies_path:
            cookie_file = Path(cookies_path).expanduser()
            if not cookie_file.is_file():
                raise PostConversionError("Instagram oturum çerezi dosyası sunucuda bulunamadı.")
            command.extend(["--cookies", str(cookie_file)])
        elif ig_username and ig_password:
            config_path = Path(auth_dir) / "auth.json"
            config_data = {"extractor": {"instagram": {
                "username": ig_username, "password": ig_password,
            }}}
            fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(config_data, output)
            command.extend(["--config", str(config_path)])
        command.append(canonical)
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise PostConversionError("Instagram yanıt vermedi. Bir süre sonra tekrar deneyin.") from exc
        except OSError as exc:
            raise PostConversionError("Instagram görselleri indirme aracı başlatılamadı.") from exc

    paths = sorted((p for p in destination.rglob("*")
                    if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
                   key=natural_sort_key)
    if result.returncode != 0:
        # Do not include raw logs, which can contain sensitive URLs.
        raise PostConversionError(
            "Gönderinin görselleri alınamadı. Instagram erişimi sınırlamış, "
            "gönderi gizli/silinmiş ya da oturum açma zorunlu olabilir. "
            "Sunucudaki Instagram oturumunu kontrol edin veya görselleri elle yükleyin."
        )
    if not paths:
        raise PostConversionError(
            "Bu gönderide indirilebilir fotoğraf bulunamadı. "
            "Video içerikler PDF'e eklenmiyor; Instagram erişimi de sınırlamış olabilir."
        )
    if len(paths) > MAX_FILES:
        raise PostConversionError("Çok fazla görsel bulundu; işlem güvenlik sınırını aştı.")
    if sum(p.stat().st_size for p in paths) > MAX_TOTAL_BYTES:
        raise PostConversionError("Görsellerin toplam boyutu 150 MB sınırını aşıyor.")
    return paths


def build_pdf(image_paths: list[Path], paper: str = "Orijinal oran") -> bytes:
    """Create one PDF page for each image; preserve file order and aspect ratio."""
    if not image_paths:
        raise PostConversionError("PDF oluşturulacak görsel bulunamadı.")
    output = io.BytesIO()
    canvas = Canvas(output, pageCompression=1)
    canvas.setTitle("Instagram Gönderisi")
    for path in image_paths:
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source)
                # Convert transparency to white instead of black on PDF pages.
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    layer = Image.new("RGBA", image.size, "white")
                    image = Image.alpha_composite(layer, image.convert("RGBA")).convert("RGB")
                else:
                    image = image.convert("RGB")
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise PostConversionError("Bozuk bir görsel bulundu.")
                if paper == "A4":
                    page_width, page_height = A4
                    pad = 28
                    scale = min((page_width - pad * 2) / width,
                                (page_height - pad * 2) / height)
                    draw_w, draw_h = width * scale, height * scale
                    x, y = (page_width - draw_w) / 2, (page_height - draw_h) / 2
                else:
                    # Make the PDF page match the image ratio without cropping;
                    # cap page dimensions for predictable PDF readers.
                    scale = min(1.0, 1200.0 / max(width, height))
                    page_width, page_height = width * scale, height * scale
                    draw_w, draw_h = page_width, page_height
                    x, y = 0, 0
                canvas.setPageSize((page_width, page_height))
                canvas.drawImage(ImageReader(image), x, y,
                                 width=draw_w, height=draw_h,
                                 preserveAspectRatio=True, mask="auto")
                canvas.showPage()
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise PostConversionError("Görsellerden biri okunamadı; PDF oluşturulamadı.") from exc
    canvas.save()
    return output.getvalue()
