import asyncio
import io
import os
from pathlib import Path

import fitz
import httpx
from PIL import Image
import pytest

import mobile_app
from converter import PostConversionError, canonical_post_url


def sample_jpg(color, size=(300, 200)):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, 'JPEG')
    return stream.getvalue()


@pytest.fixture(autouse=True)
def clear_jobs():
    mobile_app._jobs.clear()
    yield
    mobile_app._jobs.clear()


@pytest.mark.asyncio
async def test_main_page_upload_download_and_print():
    transport = httpx.ASGITransport(app=mobile_app.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver', follow_redirects=True) as client:
        r = await client.get('/')
        assert r.status_code == 200
        assert 'Instagram gönderilerini' in r.text
        import re
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text).group(1)
        pictures = [('images', ('first.jpg', sample_jpg('red'), 'image/jpeg')),
                    ('images', ('second.jpg', sample_jpg('blue'), 'image/jpeg')),
                    ('paper', (None, 'A4'))]
        result = await client.post('/api/upload', files=pictures, headers={'X-CSRF-Token': token})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['count'] == 2
        pdf = await client.get(data['pdf_url'])
        assert pdf.status_code == 200
        doc = fitz.open(stream=pdf.content, filetype='pdf')
        assert len(doc) == 2
        assert 590 < doc[0].rect.width < 600
        doc.close()
        preview = await client.get(data['print_url'])
        assert preview.status_code == 200
        assert 'window.print()' in preview.text
        assert '/image/' in preview.text
        thumbnail = await client.get(f"/image/{data['id']}/0")
        assert thumbnail.status_code == 200 and thumbnail.content.startswith(b'\xff\xd8')
        unauthorized = await client.get('/pdf/not_a_job')
        assert unauthorized.status_code == 404
        manifest = await client.get('/static/manifest.webmanifest')
        assert 'standalone' in manifest.text
        worker = await client.get('/sw.js')
        assert worker.status_code == 200


@pytest.mark.asyncio
async def test_bad_or_absent_csrf():
    transport = httpx.ASGITransport(app=mobile_app.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        await client.get('/')
        result = await client.post('/api/convert', json={'url': 'https://www.instagram.com/p/ABC123/'})
        assert result.status_code == 403


@pytest.mark.asyncio
async def test_invalid_ig_urls_blocked_even_if_csrf_valid():
    import re
    transport = httpx.ASGITransport(app=mobile_app.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        page = await client.get('/')
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text).group(1)
        result = await client.post('/api/convert', json={'url': 'https://instagram.com.evil.org/p/ABC123/'}, headers={'X-CSRF-Token': token})
        assert result.status_code == 400


def test_local_pdf_conversion_and_print_previews(tmp_path):
    red = sample_jpg('red')
    blue = sample_jpg('blue', (500, 600))
    id_, count, name = mobile_app.convert_from_upload('a_user', [red, blue], 'Orijinal oran')
    assert count == 2 and len(mobile_app._jobs[id_].preview_images) == 2
    pdf = mobile_app._jobs[id_].pdf
    doc = fitz.open(stream=pdf, filetype='pdf')
    assert len(doc) == 2
    assert doc[0].rect.width == 300 and doc[0].rect.height == 200
    doc.close()


def test_url_requires_ig_post():
    with pytest.raises(PostConversionError):
        canonical_post_url('https://instagram.com.evil.tld/p/ABC123/')


@pytest.mark.asyncio
async def test_link_route_with_stubbed_instagram(monkeypatch):
    """End-to-end route works with stand-in Instagram slides; no network required."""
    import re

    def fake_download(url, dest):
        assert url == 'https://www.instagram.com/p/abcDEF123/'
        files = []
        for i in range(1, 4):
            path = dest / f'{i:03}.jpg'
            path.write_bytes(sample_jpg(['red', 'green', 'blue'][i-1]))
            files.append(path)
        return files

    monkeypatch.setattr(mobile_app, 'download_post_images', fake_download)
    transport = httpx.ASGITransport(app=mobile_app.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        html = (await client.get('/')).text
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
        result = await client.post('/api/convert', json={
            'url': 'https://www.instagram.com/p/abcDEF123/?igsh=tracking',
            'paper': 'A4'
        }, headers={'X-CSRF-Token': token})
        assert result.status_code == 200, result.text
        assert result.json()['count'] == 3
        pdf = await client.get(result.json()['pdf_url'])
        doc = fitz.open(stream=pdf.content, filetype='pdf')
        assert len(doc) == 3
        doc.close()


def test_credentials_not_exposed_in_process_args(monkeypatch, tmp_path):
    import subprocess
    import converter

    secret_username = 'sample_account_for_tests'
    secret_password = 'sample_password_for_tests'
    monkeypatch.setenv('IG_USERNAME', secret_username)
    monkeypatch.setenv('IG_PASSWORD', secret_password)
    monkeypatch.delenv('IG_COOKIES_FILE', raising=False)

    def fake_run(command, **kwargs):
        assert secret_username not in ' '.join(command)
        assert secret_password not in ' '.join(command)
        assert '--config' in command
        config = Path(command[command.index('--config') + 1])
        assert secret_username in config.read_text()
        assert (config.stat().st_mode & 0o077) == 0
        (tmp_path / '001.jpg').write_bytes(sample_jpg('green'))
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(converter.subprocess, 'run', fake_run)
    files = converter.download_post_images('https://www.instagram.com/p/test123/', tmp_path)
    assert len(files) == 1
