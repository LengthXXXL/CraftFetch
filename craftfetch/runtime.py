"""Deploy verified Temurin ZIP runtimes locally; no admin or PATH changes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import zipfile

from .content import extract_file, zip_entries
from .core import DownloadError, File, InstallLock, safe_path

ADOPTIUM = "https://api.adoptium.net/v3"


def file_sha256(client, path):
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            client.check_cancel()
            digest.update(chunk)
    return digest.hexdigest()


def java_major(metadata):
    value = metadata.get("javaVersion", {}).get("majorVersion", 8)
    if not isinstance(value, int) or isinstance(value, bool) or not 8 <= value <= 99:
        raise DownloadError("游戏元数据中的 Java 版本无效")
    return value


def install_java(client, root: Path, major: int):
    if not isinstance(major, int) or not 8 <= major <= 99:
        raise DownloadError("Java 版本必须是 8–99 之间的整数")
    root = root.resolve()
    target = safe_path(root, f"runtime/java-{major}-windows-x64")
    with InstallLock(root):
        # Validate every extracted file against the receipt on reuse.
        receipt = target / "craftfetch-runtime.json"
        if receipt.is_file():
            try:
                record = json.loads(receipt.read_text("utf-8"))
                valid = bool(record["files"]) and record["major"] == major
                for name, expected in record["files"].items():
                    client.check_cancel()
                    path = safe_path(target, name)
                    if not path.is_file() or file_sha256(client, path) != expected:
                        valid = False
                        break
                if valid and (target / "bin/javaw.exe").is_file() and (target / "bin/java.exe").is_file():
                    client.event("log", f"复用已校验的 Java {major}")
                    return target / "bin/javaw.exe"
            except (KeyError, ValueError, OSError, AttributeError):
                pass
        client.event("phase", f"正在获取 Windows x64 Java {major}…")
        assets = client.api(f"{ADOPTIUM}/assets/latest/{major}/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse")
        if not assets:
            assets = client.api(f"{ADOPTIUM}/assets/latest/{major}/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse")
        if not isinstance(assets, list) or not assets:
            raise DownloadError(f"Adoptium 没有提供 Java {major} 的 Windows x64 ZIP")
        asset = assets[0]
        binary = asset["binary"]
        if (asset["version"]["major"] != major or binary["os"] != "windows"
                or binary["architecture"] != "x64"):
            raise DownloadError("Java 下载元数据的平台或版本不匹配")
        package = binary["package"]
        if not package["name"].endswith(".zip"):
            raise DownloadError("Java 包不是 ZIP 格式")
        digest = package["checksum"]
        item = File(f".craftfetch/cache/java-{major}-{digest}.zip", package["link"], "",
                    int(package["size"]), sha256=digest)
        client.download(root, item)
        client.event("phase", f"正在部署 Java {major}…")
        staging_parent = safe_path(root, ".craftfetch/staging")
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="java-", dir=staging_parent) as folder:
            stage = Path(folder) / "runtime"
            stage.mkdir()
            with zipfile.ZipFile(safe_path(root, item.path)) as archive:
                infos = zip_entries(archive, max_size=2 * 1024 ** 3)
                roots = {i.filename.split("/")[0] for i in infos}
                if len(roots) != 1:
                    raise DownloadError("Java ZIP 缺少唯一的顶层目录")
                prefix = next(iter(roots)) + "/"
                hashes = {}
                for info in infos:
                    if info.is_dir():
                        continue
                    relative = info.filename.removeprefix(prefix)
                    path = safe_path(stage, relative)
                    extract_file(client, archive, info, path)
                    hashes[relative] = file_sha256(client, path)
            for name in ("bin/java.exe", "bin/javaw.exe"):
                with safe_path(stage, name).open("rb") as handle:
                    if handle.read(2) != b"MZ":
                        raise DownloadError("Java 包缺少有效的 Windows 可执行文件")
            record = {"major": major, "release": asset["release_name"], "archive_sha256": digest,
                      "files": hashes}
            client._write(stage / "craftfetch-runtime.json", json.dumps(record, indent=2).encode())
            client.check_cancel()
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = Path(folder) / "old"
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(stage, target)
            except BaseException:
                if backup.exists():
                    os.replace(backup, target)
                raise
        return target / "bin/javaw.exe"
