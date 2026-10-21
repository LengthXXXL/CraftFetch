"""Independent loader integration using each loader's public service/installer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET

from .content import identifier, transfer
from .core import DownloadError, File, InstallLock, check_hash, library_allowed, safe_path

META = {"fabric": "https://meta.fabricmc.net/v2", "quilt": "https://meta.quiltmc.org/v3"}


def maven_path(name):
    parts = name.split(":")
    if len(parts) not in (3, 4):
        raise DownloadError(f"无法识别 Maven 依赖：{name}")
    group, artifact, version, *classifier = parts
    path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}"
    path += ("-" + classifier[0] if classifier else "") + ".jar"
    safe_path(Path.cwd(), path)
    return path


def library_file(client, library):
    artifact = library.get("downloads", {}).get("artifact")
    if artifact:
        return File("libraries/" + artifact["path"], artifact["url"], check_hash(artifact["sha1"]), int(artifact.get("size", -1)))
    relative = maven_path(library["name"])
    url = library.get("url", "https://libraries.minecraft.net/").rstrip("/") + "/" + relative
    digest = library.get("sha1")
    if not digest:
        digest = client.small_bytes(url + ".sha1", 4096).decode("ascii").strip().split()[0]
    return File("libraries/" + relative, url, check_hash(digest), int(library.get("size", -1)))


def choose_loader(client, loader, game, version=""):
    if loader == "vanilla":
        return ""
    if version:
        return identifier(version)
    if loader in META:
        versions = client.api(f"{META[loader]}/versions/loader/{quote(game, safe='')}")
        if not versions:
            raise DownloadError(f"{loader} 暂不支持 Minecraft {game}")
        stable = [v for v in versions if v["loader"].get("stable", True)] or versions
        return stable[0]["loader"]["version"]
    if loader == "forge":
        promos = client.api("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")["promos"]
        result = promos.get(f"{game}-recommended") or promos.get(f"{game}-latest")
        if result:
            return result
    if loader == "neoforge":
        parts = game.split(".")
        if len(parts) >= 2 and parts[0] == "1":
            prefix = parts[1] + "." + (parts[2] if len(parts) > 2 else "0") + "."
            xml = client.small_bytes("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml", 4 * 1024 ** 2)
            versions = [v.text for v in ET.fromstring(xml).findall("./versioning/versions/version")
                        if v.text and v.text.startswith(prefix) and re.fullmatch(r"[0-9.]+", v.text)]
            if versions:
                return max(versions, key=lambda v: tuple(map(int, v.split("."))))
    raise DownloadError(f"无法为 {game} 自动选择 {loader}；请填写加载器的准确版本号")


def install_loader(client, root, base, loader, version, java):
    if loader == "vanilla":
        return {}
    if loader not in (*META, "forge", "neoforge"):
        raise DownloadError(f"不支持的加载器：{loader}")
    game = base["id"]
    version = identifier(version)
    client.event("phase", f"正在安装 {loader} {version}…")
    if loader in META:
        profile = client.api(f"{META[loader]}/versions/loader/{quote(game, safe='')}/{quote(version, safe='')}/profile/json")
        if profile.get("inheritsFrom") != game:
            raise DownloadError("加载器返回了不匹配的 Minecraft 版本")
        files = [library_file(client, lib) for lib in profile.get("libraries", []) if library_allowed(lib)]
        with InstallLock(root):
            transfer(client, root, files)
        return profile
    if os.name != "nt":
        raise DownloadError("Forge / NeoForge 安装需要在 Windows 上运行本程序；Fabric / Quilt 可跨平台下载 Windows 文件")
    if loader == "forge" and game.startswith("1.") and int(game.split(".")[1]) < 13:
        raise DownloadError("自动 Forge 安装目前支持 Minecraft 1.13 及以后；旧版 Forge 请使用已有实例")
    if not java or not Path(java).is_file():
        raise DownloadError("Forge / NeoForge 安装需要先部署 Java")
    full = f"{game}-{version}" if loader == "forge" else version
    artifact = "forge" if loader == "forge" else "neoforge"
    group = "net/minecraftforge/forge" if loader == "forge" else "net/neoforged/neoforge"
    host = "https://maven.minecraftforge.net" if loader == "forge" else "https://maven.neoforged.net/releases"
    url = f"{host}/{group}/{full}/{artifact}-{full}-installer.jar"
    digest = check_hash(client.small_bytes(url + ".sha1", 4096).decode("ascii").strip().split()[0])
    item = File(f".craftfetch/cache/{artifact}-{full}-installer.jar", url, digest, -1)
    with InstallLock(root):
        client.download(root, item)
        staging = safe_path(root, ".craftfetch/staging")
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=loader + "-", dir=staging) as folder:
            stage = Path(folder)
            base_dir = safe_path(stage, f"versions/{game}")
            base_dir.mkdir(parents=True)
            for suffix in ("json", "jar"):
                shutil.copy2(safe_path(root, f"versions/{game}/{game}.{suffix}"), base_dir / f"{game}.{suffix}")
            client._write(stage / "launcher_profiles.json", b'{"profiles":{}}')
            # Seed only ordinary files; do not let installer outputs mutate hardlinks.
            library_root = root / "libraries"
            if library_root.is_dir():
                for path in library_root.rglob("*"):
                    client.check_cancel()
                    if path.is_file():
                        relative = path.relative_to(root).as_posix()
                        source = safe_path(root, relative)
                        dest = safe_path(stage, relative)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, dest)
            log = safe_path(root, f".craftfetch/logs/{artifact}-{full}.log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("wb") as output:
                command = [str(Path(java).with_name("java.exe")), "-Djava.awt.headless=true", "-jar",
                           str(safe_path(root, item.path)), "--installClient", str(stage)]
                process = subprocess.Popen(command, cwd=stage, stdout=output, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                start, announced = time.monotonic(), 0
                try:
                    while process.poll() is None:
                        client.check_cancel()
                        elapsed = time.monotonic() - start
                        if elapsed > 1800:
                            raise DownloadError(f"加载器安装超时，日志：{log}")
                        if elapsed // 10 > announced:
                            announced = elapsed // 10
                            client.event("phase", f"{loader} 安装器正在下载依赖及处理客户端（{int(elapsed)} 秒）…")
                        client.cancel.wait(0.2)
                finally:
                    if process.poll() is None:
                        if os.name == "nt":
                            # Forge processors may be child JVMs; stop the whole tree.
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
                        else:
                            process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            if process.returncode:
                tail = log.read_bytes()[-3000:].decode("utf-8", errors="replace")
                raise DownloadError(f"{loader} 安装失败。日志：{log}\n{tail}")
            profiles = [p for p in (stage / "versions").glob("*/*.json") if p.parent.name != game]
            if len(profiles) != 1:
                raise DownloadError(f"安装器未生成唯一的客户端配置。日志：{log}")
            profile = json.loads(profiles[0].read_text("utf-8"))
            if profile.get("inheritsFrom") != game:
                raise DownloadError("安装器生成的客户端不匹配所选 Minecraft 版本")
            for lib in profile.get("libraries", []):
                if not library_allowed(lib):
                    continue
                item = library_file(client, lib)
                path = safe_path(stage, item.path)
                if not client.valid(path, item):
                    client.download(stage, item)
            for path in (stage / "libraries").rglob("*"):
                client.check_cancel()
                if path.is_file():
                    relative = path.relative_to(stage).as_posix()
                    source, dest = safe_path(stage, relative), safe_path(root, relative)
                    client._write(dest, source.read_bytes())
            return profile


def merge_profile(base, loader, instance):
    """Produce a standalone descriptor, so PCL need not resolve inheritance."""
    merged = json.loads(json.dumps(base))
    merged.update({k: v for k, v in loader.items() if k not in ("id", "inheritsFrom", "libraries", "arguments")})
    libraries = {}
    for lib in (*base.get("libraries", []), *loader.get("libraries", [])):
        parts = lib["name"].split(":")
        key = tuple(parts[:2] + parts[3:])
        libraries[key] = lib
    merged["libraries"] = list(libraries.values())
    if loader.get("arguments") is not None:
        merged["arguments"] = {key: base.get("arguments", {}).get(key, []) + loader["arguments"].get(key, [])
                               for key in ("game", "jvm")}
    if "minecraftArguments" in loader:
        merged.pop("arguments", None)
    merged.pop("inheritsFrom", None)
    merged.pop("jar", None)
    merged["id"] = identifier(instance)
    merged["clientVersion"] = base.get("clientVersion", base["id"])
    return merged
