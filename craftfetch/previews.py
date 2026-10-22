"""Bounded author-image downloads and decoding; no Tk calls on workers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
from pathlib import Path
import threading
from urllib.parse import quote, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from .core import Cancelled, DownloadError, Downloader

MAX_BYTES = 10 * 1024 ** 2
MAX_PIXELS = 20_000_000


def https_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DownloadError("仅支持有效的 HTTPS 来源链接")
    return url


def project_url(project, kind):
    slug = project.get("slug") or project.get("id") or project.get("project_id")
    if not slug or kind not in ("mod", "modpack", "datapack", "resourcepack", "shader"):
        raise DownloadError("项目缺少有效的来源信息")
    return f"https://modrinth.com/{kind}/{quote(slug, safe='')}"


def decode_preview(data, size, cover=False):
    if len(data) > MAX_BYTES:
        raise DownloadError("预览图超过 10 MiB")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise DownloadError("预览图像素过大")
            source.seek(0)
            picture = ImageOps.exif_transpose(source).convert("RGBA")
            if cover:
                return ImageOps.fit(picture, size, Image.Resampling.LANCZOS)
            picture.thumbnail(size, Image.Resampling.LANCZOS)
            return picture.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DownloadError("图片暂时无法预览") from exc


def default_cache():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "CraftFetch/previews"


class PreviewService:
    def __init__(self, messages, cache=None):
        self.messages = messages
        self.cache = Path(cache) if cache is not None else default_cache()
        self.cancel = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="preview")
        self.pending = set()
        self.closed = False

    def request(self, token, url, size, cover=False):
        if self.closed or not url:
            return
        self.pending = {f for f in self.pending if not f.done()}
        if len(self.pending) >= 64:
            return
        self.pending.add(self.pool.submit(self._load, token, url, size, cover))

    def _load(self, token, url, size, cover):
        try:
            https_url(url)
            client = Downloader(timeout=12, attempts=1, cancel=self.cancel)
            digest = hashlib.sha256(url.encode()).hexdigest()
            target = self.cache / (digest + ".img")
            data = None
            if target.is_file() and target.stat().st_size <= MAX_BYTES:
                try:
                    data = target.read_bytes()
                    picture = decode_preview(data, size, cover)
                except (OSError, DownloadError):
                    data = None
                    target.unlink(missing_ok=True)
            if data is None:
                data = client.small_bytes(url, MAX_BYTES)
                picture = decode_preview(data, size, cover)
                client.check_cancel()
                try:
                    client._write(target, data)
                except OSError:
                    pass  # A read-only cache must not hide a valid author image.
            client.check_cancel()
            self.messages.put(("image", (token, picture)))
        except (Cancelled, DownloadError, OSError, ValueError):
            self.messages.put(("image_missing", token))

    def close(self):
        self.closed = True
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def cancel_pending(self):
        for future in self.pending:
            future.cancel()
        self.pending = {f for f in self.pending if not f.done()}
