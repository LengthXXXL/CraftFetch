"""Install Modrinth packs into staged, version-isolated launcher folders."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import zipfile

from .catalog import primary_file, remote_file
from .content import extract_file, identifier, transfer, zip_entries
from .core import DownloadError, File, InstallLock, safe_path
from .instances import prepare, publish_instance

HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
RESERVED = {"versions", "libraries", "assets", "runtime", ".craftfetch",
            "craftfetch-instance.json", "craftfetch-content.json", "craftfetch-导入说明.txt",
            "launcher_profiles.json", "launcher_accounts.json"}


def pack_path(root, name, instance):
    path = safe_path(root, name)
    if name.split("/")[0].casefold() in RESERVED or name.casefold() in {f"{instance}.json".casefold(), f"{instance}.jar".casefold()}:
        raise DownloadError(f"整合包试图覆盖下载器或启动配置：{name}")
    return path


def inspect_pack(archive, root, instance, optional=False):
    infos = zip_entries(archive)
    try:
        info = archive.getinfo("modrinth.index.json")
    except KeyError as exc:
        if "manifest.json" in archive.namelist():
            raise DownloadError("这是 CurseForge 整合包；当前支持 Modrinth .mrpack，请下载对应格式") from exc
        raise DownloadError("压缩包缺少 modrinth.index.json") from exc
    if info.file_size > 16 * 1024 ** 2:
        raise DownloadError("整合包索引过大")
    index = json.loads(archive.read(info))
    if index.get("formatVersion") != 1 or index.get("game") != "minecraft":
        raise DownloadError("不支持的整合包格式版本或游戏类型")
    dependencies = index["dependencies"]
    supported = {"minecraft", "fabric-loader", "quilt-loader", "forge", "neoforge"}
    if set(dependencies) - supported:
        raise DownloadError("整合包需要尚未支持的组件：" + ", ".join(set(dependencies) - supported))
    game = identifier(dependencies["minecraft"])
    loaders = [k for k in dependencies if k != "minecraft"]
    if len(loaders) > 1:
        raise DownloadError("整合包同时要求多个加载器，无法自动安装")
    loader_key = loaders[0] if loaders else "vanilla"
    loader = {"fabric-loader": "fabric", "quilt-loader": "quilt"}.get(loader_key, loader_key)
    version = identifier(dependencies[loader_key]) if loaders else ""
    files, paths = [], set()
    entries = index["files"]
    if len(entries) > 20000:
        raise DownloadError("整合包索引文件过多")
    for entry in entries:
        name = entry["path"]
        pack_path(root, name, instance)
        key = name.casefold()
        if key in paths:
            raise DownloadError(f"整合包索引包含重复路径：{name}")
        paths.add(key)
        env = entry.get("env", {}).get("client", "required")
        if env not in ("required", "optional", "unsupported"):
            raise DownloadError("整合包的客户端环境标记无效")
        urls = entry["downloads"]
        if not urls or any(urlsplit(u).scheme != "https" or urlsplit(u).hostname not in HOSTS for u in urls):
            raise DownloadError(f"整合包文件的下载域名不在允许列表：{name}")
        hashes = entry["hashes"]
        if not hashes.get("sha1") or not hashes.get("sha512"):
            raise DownloadError("整合包文件必须提供 SHA-1 和 SHA-512")
        item = File(name, urls[0], hashes["sha1"], int(entry["fileSize"]), sha512=hashes["sha512"], alternatives=tuple(urls[1:]))
        item.hashes()
        if item.size < 0:
            raise DownloadError("整合包文件大小无效")
        if env == "unsupported" or env == "optional" and not optional:
            continue
        files.append(item)
    if sum(f.size for f in files) > 16 * 1024 ** 3:
        raise DownloadError("整合包下载大小超过 16 GiB 限制")
    overrides = []
    for prefix in ("overrides/", "client-overrides/"):
        for info in infos:
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            relative = info.filename[len(prefix):]
            pack_path(root, relative, instance)
            overrides.append((info, relative))
    return index, game, loader, version, files, overrides


def install_pack(client, root, path, instance, *, optional=False, auto_java=True):
    root = root.expanduser().resolve()
    identifier(instance)
    if safe_path(root, f"versions/{instance}").exists():
        raise DownloadError("同名实例目录已存在，请给整合包填写新的实例名称")
    # Keep the same ZIP handle for validation and extraction.
    with zipfile.ZipFile(path) as archive:
        index, game, loader, version, files, overrides = inspect_pack(archive, root, instance, optional)
        if game == instance:
            raise DownloadError("整合包实例名称不能与原版版本号相同")
        base, profile, record = prepare(client, root, game, loader, version, auto_java)
        record["pack"] = {"name": index["name"], "version": index["versionId"], "optional": optional}
        with InstallLock(root):
            staging = safe_path(root, ".craftfetch/staging")
            staging.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="pack-", dir=staging) as folder:
                stage = Path(folder) / "game"
                stage.mkdir()
                # Cache verified pack files independently, so retry reuses them.
                cached_files = [File(f".craftfetch/cache/content/{f.sha512}", f.url, f.sha1, f.size,
                                     sha512=f.sha512, alternatives=f.alternatives) for f in files]
                transfer(client, root, cached_files)
                import shutil
                for item, cached in zip(files, cached_files):
                    client.check_cancel()
                    target = pack_path(stage, item.path, instance)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(safe_path(root, cached.path), target)
                client.event("phase", "正在应用整合包配置与客户端覆盖文件…")
                for info, relative in overrides:
                    extract_file(client, archive, info, pack_path(stage, relative, instance))
                return publish_instance(client, root, instance, stage, base, profile, record)


def download_pack(client, root, selected, instance, *, optional=False, auto_java=True):
    info = primary_file(selected)
    if not info["filename"].endswith(".mrpack"):
        raise DownloadError("所选整合包文件不是 .mrpack 格式")
    item = remote_file(f".craftfetch/cache/{selected['id']}.mrpack", info)
    safe_path(root, item.path)
    with InstallLock(root):
        client.download(root, item)
    return install_pack(client, root, safe_path(root, item.path), instance, optional=optional, auto_java=auto_java)
