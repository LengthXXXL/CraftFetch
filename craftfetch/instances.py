"""Launcher-neutral, version-isolated instances in a standard .minecraft root."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile

from .content import identifier
from .core import DownloadError, InstallLock, safe_path
from .loaders import choose_loader, install_loader, merge_profile
from .runtime import install_java, java_major


def read_instance(root, instance):
    identifier(instance)
    record_path = safe_path(root, f"versions/{instance}/craftfetch-instance.json")
    if record_path.is_file():
        record = json.loads(record_path.read_text("utf-8"))
        identifier(record["minecraft"])
        local_java = safe_path(root, f"runtime/java-{int(record['java_major'])}-windows-x64/bin/javaw.exe")
        if local_java.is_file():
            record["java"] = str(local_java)
        return record
    seen = set()

    def read(version):
        if version in seen or len(seen) > 10:
            raise DownloadError("版本配置继承存在循环或过深")
        seen.add(version)
        data = json.loads(safe_path(root, f"versions/{version}/{version}.json").read_text("utf-8"))
        if data.get("inheritsFrom"):
            parent = read(identifier(data["inheritsFrom"]))
            return merge_profile(parent, data, version)
        return data

    data = read(instance)
    game = data.get("clientVersion") or data.get("id", instance)
    loader = "vanilla"
    for lib in data.get("libraries", []):
        parts = lib.get("name", "").split(":")
        if parts[:2] == ["net.fabricmc", "intermediary"]:
            game = parts[2]
        if parts[:2] in (["net.fabricmc", "fabric-loader"], ["org.quiltmc", "quilt-loader"],
                          ["net.minecraftforge", "forge"], ["net.neoforged", "neoforge"]):
            loader = {"net.fabricmc": "fabric", "org.quiltmc": "quilt", "net.minecraftforge": "forge", "net.neoforged": "neoforge"}[parts[0]]
            if loader == "forge":
                game = parts[2].split("-")[0]
    if game == instance and loader != "vanilla":
        raise DownloadError("无法确定此已有实例的游戏版本；请使用 CraftFetch 创建的实例")
    return {"minecraft": game, "loader": loader, "java_major": java_major(data), "java": ""}


def list_instances(root):
    folder = root / "versions"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / f"{p.name}.json").is_file())


def prepare(client, root, game, loader="vanilla", loader_version="", auto_java=True):
    identifier(game)
    if loader not in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
        raise DownloadError("未知的 Mod 加载器")
    if loader in ("forge", "neoforge") and os.name != "nt":
        raise DownloadError("Forge / NeoForge 自动部署需要 Windows；请在 Windows 版中安装此实例")
    loader_version = choose_loader(client, loader, game, loader_version)
    manifest = client.manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == game), None)
    if not entry:
        raise DownloadError(f"Mojang 版本列表中没有 {game}")
    plan = client.plan(root, entry)
    client.event("plan", plan)
    client.install(root, plan)
    base = json.loads(plan.metadata)
    major = java_major(base)
    java = install_java(client, root, major) if auto_java or loader in ("forge", "neoforge") else None
    profile = install_loader(client, root, base, loader, loader_version, java)
    return base, profile, {"minecraft": game, "loader": loader, "loader_version": loader_version,
                           "java_major": major, "java": str(java) if java else ""}


def publish_instance(client, root, instance, stage, base, profile, record):
    descriptor = safe_path(stage, f"{instance}.json")
    if descriptor.exists() or (stage / f"{instance}.jar").exists():
        raise DownloadError("实例资源试图覆盖版本启动文件")
    merged = merge_profile(base, profile, instance)
    safe_path(stage, "mods").mkdir(exist_ok=True)
    client.check_cancel()
    shutil.copy2(safe_path(root, f"versions/{base['id']}/{base['id']}.jar"), stage / f"{instance}.jar")
    client._write(stage / "craftfetch-instance.json", json.dumps(record, ensure_ascii=False, indent=2).encode())
    client._write(descriptor, json.dumps(merged, ensure_ascii=False, indent=2).encode())
    instructions = (f"在 PCL 中添加游戏目录：{root}\n选择版本：{instance}\n"
                    "在该版本设置中将游戏目录设为：隔离（版本文件夹）。\n"
                    f"Java {record['java_major']} 路径：{record['java'] or '未自动部署'}\n"
                    "若 PCL 没有自动发现 Java，在该版本的 Java 设置中手动选择上面的 javaw.exe。\n"
                    "资源包和光影包还需在游戏设置中启用；光影需要 Iris / OptiFine 等支持。\n")
    client._write(stage / "CraftFetch-导入说明.txt", instructions.encode("utf-8-sig"))
    target = safe_path(root, f"versions/{instance}")
    target.parent.mkdir(parents=True, exist_ok=True)
    client.check_cancel()
    if target.exists():
        raise DownloadError("同名实例目录已存在，请使用新的实例名称")
    os.replace(stage, target)
    return {**record, "instance": instance, "directory": str(root), "game_directory": str(target)}


def create_instance(client, root, game, instance, loader="vanilla", loader_version="", auto_java=True):
    root = root.expanduser().resolve()
    identifier(instance)
    if safe_path(root, f"versions/{instance}").exists() or game == instance:
        raise DownloadError("实例名称需与原版版本号不同，且不能与已有目录重名")
    base, profile, record = prepare(client, root, game, loader, loader_version, auto_java)
    with InstallLock(root):
        staging = safe_path(root, ".craftfetch/staging")
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="instance-", dir=staging) as folder:
            stage = Path(folder) / "game"
            stage.mkdir()
            return publish_instance(client, root, instance, stage, base, profile, record)
