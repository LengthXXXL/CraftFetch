"""Download vanilla files into the standard launcher directory layout.

No launcher code, account tokens or game binaries are distributed here.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import __version__

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MIRROR = "https://bmclapi2.bangbang93.com"
USER_AGENT = f"CraftFetch/{__version__} (Minecraft Java downloader)"


class Cancelled(Exception):
    pass


class DownloadError(Exception):
    pass


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal, Windows special paths and escaping symlinks."""
    parts = relative.split("/")
    if not relative or any(
        not p or p in (".", "..") or p.endswith((".", " "))
        or re.search(r'[\\:<>"|?*\x00-\x1f]', p)
        or p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(
            f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10))}
        for p in parts
    ):
        raise DownloadError(f"不安全的文件路径：{relative!r}")
    target = root.joinpath(*parts).resolve()
    if root.resolve() not in target.parents:
        raise DownloadError(f"文件路径越出了游戏目录：{relative!r}")
    return target


def check_hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DownloadError("元数据缺少有效的 SHA-1 校验值")
    return value


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def mirror_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname
    prefix = ""
    if host == "resources.download.minecraft.net":
        prefix = "/assets"
    elif host == "libraries.minecraft.net":
        prefix = "/maven"
    elif host not in {"piston-meta.mojang.com", "piston-data.mojang.com",
                       "launchermeta.mojang.com", "launcher.mojang.com"}:
        return url
    return MIRROR + prefix + parsed.path + ("?" + parsed.query if parsed.query else "")


@dataclass(frozen=True)
class File:
    path: str
    url: str
    sha1: str
    size: int


@dataclass
class Plan:
    version: str
    metadata: bytes
    files: list[File]
    copies: list[tuple[str, str]]
    java: int | None

    @property
    def total_bytes(self):
        return sum(f.size for f in self.files)


class InstallLock:
    def __init__(self, root: Path):
        self.path = safe_path(root, ".craftfetch/install.lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise DownloadError("另一个下载器正在写入这个游戏目录，请稍后重试。") from exc
        return self

    def __exit__(self, *args):
        self.handle.close()


def library_allowed(library: dict) -> bool:
    """Evaluate Mojang rules for Windows x64, no launch-time features."""
    rules = library.get("rules")
    if rules is None:
        return True
    allowed = False
    for rule in rules:
        spec = rule.get("os", {})
        if spec.get("name", "windows") != "windows":
            continue
        if "arch" in spec and not re.search(spec["arch"], "amd64"):
            continue
        if "version" in spec and not re.search(spec["version"], "10.0"):
            continue
        if any(bool(expected) for expected in rule.get("features", {}).values()):
            continue
        allowed = rule.get("action") == "allow"
    return allowed


class Downloader:
    def __init__(self, source="official", workers=6, cancel=None, event=None,
                 timeout=25, attempts=3):
        if source not in ("official", "bmclapi"):
            raise ValueError("unknown source")
        self.source = source
        self.workers = max(1, min(int(workers), 16))
        self.cancel = cancel if cancel is not None else threading.Event()
        self.event = event or (lambda kind, data: None)
        self.timeout = timeout
        self.attempts = attempts
        self.context = ssl.create_default_context()
        # Keep OS trust roots and add certifi if installed; never disable TLS.
        try:
            import certifi
            self.context.load_verify_locations(certifi.where())
        except ImportError:
            pass

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled("下载已取消；再次下载会跳过已校验的完整文件。")

    def candidates(self, url):
        if urlsplit(url).scheme != "https":
            raise DownloadError("仅允许 HTTPS 下载地址")
        other = mirror_url(url)
        return list(dict.fromkeys(([other, url] if self.source == "bmclapi" else [url])))

    def _fetch(self, url, consume):
        errors = []
        for candidate in self.candidates(url):
            for attempt in range(self.attempts):
                self.check_cancel()
                try:
                    req = Request(candidate, headers={"User-Agent": USER_AGENT,
                                                     "Accept-Encoding": "identity"})
                    with urlopen(req, timeout=self.timeout, context=self.context) as response:
                        if urlsplit(response.geturl()).scheme != "https":
                            raise DownloadError("服务器重定向到了不安全的地址")
                        return consume(response)
                except Cancelled:
                    raise
                except (OSError, ValueError, DownloadError) as exc:
                    errors.append(f"{candidate}: {exc}")
                    self.event("log", f"下载失败，第 {attempt + 1}/{self.attempts} 次：{exc}")
                    if attempt + 1 < self.attempts and self.cancel.wait(min(2 ** attempt, 4)):
                        self.check_cancel()
        raise DownloadError("所有下载尝试均失败：\n" + "\n".join(errors[-3:]))

    def json_bytes(self, url, expected=None, size=None):
        def consume(response):
            data = bytearray()
            while True:
                self.check_cancel()
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 64 * 1024 * 1024:
                    raise DownloadError("元数据超过 64 MiB 限制")
            result = bytes(data)
            if expected and sha1(result) != check_hash(expected):
                raise DownloadError("元数据 SHA-1 校验失败")
            if size is not None and len(result) != size:
                raise DownloadError("元数据长度校验失败")
            try:
                parsed = json.loads(result)
            except (ValueError, UnicodeError) as exc:
                raise DownloadError("服务器返回的不是有效 JSON") from exc
            if not isinstance(parsed, dict):
                raise DownloadError("元数据格式错误")
            return parsed, result
        return self._fetch(url, consume)

    def manifest(self):
        self.event("phase", "正在获取 Minecraft 版本列表…")
        manifest, _ = self.json_bytes(MANIFEST_URL)
        if not isinstance(manifest.get("versions"), list):
            raise DownloadError("版本列表格式错误")
        return manifest

    def plan(self, root: Path, entry: dict) -> Plan:
        version = entry["id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,119}", version):
            raise DownloadError("版本名称含有不安全的字符")
        safe_path(root, f"versions/{version}/{version}.json")
        self.event("phase", "正在读取版本信息和资源索引…")
        metadata, raw = self.json_bytes(entry["url"], check_hash(entry["sha1"]))
        if metadata.get("id") != version or metadata.get("inheritsFrom"):
            raise DownloadError("仅支持独立的原版版本元数据")
        if "assetIndex" not in metadata or "client" not in metadata.get("downloads", {}):
            raise DownloadError("此远古版本缺少标准资源索引，首版暂不支持。请选择 1.6 及以后的版本。")
        files: dict[str, File] = {}

        def add(path, info):
            safe_path(root, path)
            item = File(path, info["url"], check_hash(info["sha1"]), int(info["size"]))
            if item.size < 0:
                raise DownloadError("元数据中的文件大小无效")
            if path in files and files[path] != item:
                raise DownloadError(f"元数据包含冲突文件：{path}")
            files[path] = item

        add(f"versions/{version}/{version}.jar", metadata["downloads"]["client"])
        for library in metadata.get("libraries", []):
            self.check_cancel()
            if not library_allowed(library):
                continue
            downloads = library.get("downloads", {})
            artifact = downloads.get("artifact")
            if artifact:
                add("libraries/" + artifact["path"], artifact)
            native = library.get("natives", {}).get("windows")
            if native:
                classifier = native.replace("${arch}", "64")
                info = downloads.get("classifiers", {}).get(classifier)
                if not info:
                    raise DownloadError(f"缺少 Windows 本地库：{library.get('name')}")
                add("libraries/" + info["path"], info)
            if not artifact and not native:
                raise DownloadError(f"依赖库缺少标准下载信息：{library.get('name')}")

        index_info = metadata["assetIndex"]
        index_path = f"assets/indexes/{index_info['id']}.json"
        index, _ = self.json_bytes(index_info["url"], check_hash(index_info["sha1"]), index_info["size"])
        add(index_path, index_info)
        copies = []
        for name, obj in index["objects"].items():
            digest = check_hash(obj["hash"])
            object_path = f"assets/objects/{digest[:2]}/{digest}"
            add(object_path, {"url": f"https://resources.download.minecraft.net/{digest[:2]}/{digest}",
                              "sha1": digest, "size": obj["size"]})
            for prefix, enabled in ((f"assets/virtual/{index_info['id']}", index.get("virtual")),
                                    ("resources", index.get("map_to_resources"))):
                if enabled:
                    target = f"{prefix}/{name}"
                    safe_path(root, target)
                    copies.append((object_path, target))
        # Index bytes are fetched again by the file downloader so they share
        # the same atomic-write and hash validation path as all other files.
        return Plan(version, raw, list(files.values()), copies,
                    metadata.get("javaVersion", {}).get("majorVersion"))

    def valid(self, path: Path, item: File):
        if not path.is_file() or path.stat().st_size != item.size:
            return False
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            while True:
                self.check_cancel()
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest() == item.sha1

    def download(self, root: Path, item: File):
        target = safe_path(root, item.path)
        if self.valid(target, item):
            return "cached"
        target.parent.mkdir(parents=True, exist_ok=True)

        def consume(response):
            fd, name = tempfile.mkstemp(prefix=".craftfetch-", suffix=".part", dir=target.parent)
            part = Path(name)
            try:
                digest = hashlib.sha1()
                received = 0
                with os.fdopen(fd, "wb") as output:
                    while True:
                        self.check_cancel()
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > item.size:
                            raise DownloadError("服务器返回的文件超过预期大小")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                self.check_cancel()
                if received != item.size or digest.hexdigest() != item.sha1:
                    raise DownloadError(f"文件完整性校验失败：{item.path}")
                os.replace(part, target)
                return "downloaded"
            finally:
                part.unlink(missing_ok=True)
        return self._fetch(item.url, consume)

    @staticmethod
    def _write(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".craftfetch-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def install(self, root: Path, plan: Plan):
        root = root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        with InstallLock(root):
            version_json = safe_path(root, f"versions/{plan.version}/{plan.version}.json")
            if version_json.exists() and version_json.read_bytes() != plan.metadata:
                raise DownloadError("同名版本已存在且配置不同。为保护已有版本，请选择另一个游戏目录。")
            completed = 0
            verified = 0
            cached = 0
            errors = []
            self.event("phase", f"正在下载并校验 {len(plan.files)} 个文件…")
            # Keep at most workers futures queued; cancellation never queues
            # thousands of unnecessary tasks or leaves a running writer behind.
            iterator = iter(plan.files)
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
                pending = {}

                def fill():
                    while len(pending) < self.workers and not self.cancel.is_set():
                        item = next(iterator, None)
                        if item is None:
                            break
                        pending[pool.submit(self.download, root, item)] = item

                try:
                    fill()
                    while pending:
                        done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in done:
                            item = pending.pop(future)
                            try:
                                result = future.result()
                                cached += result == "cached"
                                completed += 1
                                verified += item.size
                                self.event("progress", {"done": completed, "total": len(plan.files),
                                                        "bytes": verified, "total_bytes": plan.total_bytes,
                                                        "cached": cached, "file": item.path})
                            except Cancelled:
                                pass
                            except Exception as exc:
                                errors.append(f"{item.path}: {exc}")
                                self.event("log", errors[-1])
                        fill()
                except BaseException:
                    self.cancel.set()
                    raise
            self.check_cancel()
            if errors:
                raise DownloadError(f"{len(errors)} 个文件下载失败；再次下载会跳过已完成的文件。\n" + "\n".join(errors[:3]))
            for source, dest in plan.copies:
                self.check_cancel()
                # Legacy resource maps are part of the installation as well.
                self._write(safe_path(root, dest), safe_path(root, source).read_bytes())
            self.check_cancel()
            # Publish the launcher-visible descriptor only after all files pass.
            self._write(version_json, plan.metadata)
            self.event("phase", "安装完成。请在启动器中选择这个游戏目录并刷新版本列表。")
            return {"files": completed, "cached": cached, "bytes": verified}
