"""Shared verified transfers and bounded, path-safe ZIP extraction."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
import re
import stat
import zipfile

from .core import DownloadError, safe_path


def transfer(client, root, files):
    files = list(files)
    total = sum(max(0, f.size) for f in files)
    done = cached = size = 0
    iterator = iter(files)
    with ThreadPoolExecutor(max_workers=client.workers) as pool:
        pending = {}
        try:
            while True:
                client.check_cancel()
                while len(pending) < client.workers:
                    item = next(iterator, None)
                    if item is None:
                        break
                    pending[pool.submit(client.download, root, item)] = item
                if not pending:
                    break
                ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in ready:
                    item = pending.pop(future)
                    cached += future.result() == "cached"
                    done += 1
                    size += max(0, item.size)
                    client.event("progress", {"done": done, "total": len(files), "bytes": size,
                                             "total_bytes": total, "cached": cached, "file": item.path})
        except BaseException:
            client.cancel.set()
            for future in pending:
                future.cancel()
            raise
    client.check_cancel()
    return {"files": done, "cached": cached, "bytes": size}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,119}", value):
        raise DownloadError("版本名称请使用 1–120 个英文字母、数字、点、横线或下划线")
    safe_path(Path.cwd(), value)
    return value


def zip_entries(archive, *, max_size=8 * 1024 ** 3):
    """Validate *all* names before writing, including ignored server overrides."""
    infos = archive.infolist()
    if len(infos) > 50000 or sum(i.file_size for i in infos) > max_size:
        raise DownloadError("压缩包解压后的文件数量或大小超过限制")
    seen = set()
    for info in infos:
        name = info.filename.rstrip("/")
        safe_path(Path.cwd(), name)
        key = name.casefold()
        if key in seen:
            raise DownloadError(f"压缩包包含重复路径：{name}")
        seen.add(key)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise DownloadError(f"压缩包包含链接或特殊文件：{name}")
        if info.flag_bits & 1:
            raise DownloadError("不支持加密压缩包")
    return infos


def extract_file(client, archive, info, target):
    """Stream to an atomic sibling file instead of holding overrides in RAM."""
    import os
    import tempfile
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".craftfetch-", dir=target.parent)
    try:
        size = 0
        with os.fdopen(fd, "wb") as output, archive.open(info) as source:
            while True:
                client.check_cancel()
                chunk = source.read(128 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > info.file_size:
                    raise DownloadError("解压文件超过声明大小")
                output.write(chunk)
        if size != info.file_size:
            raise DownloadError("压缩文件不完整")
        client.check_cancel()
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
