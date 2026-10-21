"""Modrinth public API, compatibility filtering and required dependencies."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from .content import transfer
from .core import DownloadError, File, InstallLock, safe_path

API = "https://api.modrinth.com/v2"
KINDS = {"Mod": "mod", "整合包": "modpack", "数据包": "datapack",
         "资源包": "resourcepack", "光影包": "shader"}


def remote_file(path, info):
    hashes = info.get("hashes", {})
    item = File(path, info["url"], hashes.get("sha1", ""), int(info["size"]),
                sha512=hashes.get("sha512", ""))
    item.hashes()
    if item.size < 0:
        raise DownloadError("资源大小无效")
    if urlsplit(item.url).scheme != "https":
        raise DownloadError("资源链接必须使用 HTTPS")
    return item


def primary_file(version):
    files = version.get("files", [])
    if not files:
        raise DownloadError("这个资源版本没有可下载文件")
    return next((f for f in files if f.get("primary")), files[0])


def version_kind(version, project):
    loaders = version.get("loaders", [])
    # A project can offer both mods and datapacks. Classify the actual version.
    for kind, tags in (("modpack", {"mrpack"}), ("datapack", {"datapack"}),
                       ("shader", {"iris", "optifine", "canvas"}),
                       ("resourcepack", {"minecraft"})):  # resourcepack v2 loader
        if set(loaders) & tags:
            return kind
    return project["project_type"]


class Catalog:
    def __init__(self, client):
        self.client = client
        self.projects = {}

    def project(self, project):
        if project not in self.projects:
            self.projects[project] = self.client.api(f"{API}/project/{quote(project, safe='')}")
        return self.projects[project]

    def search(self, query, kind, game="", loader="", offset=0):
        if kind not in KINDS.values():
            raise DownloadError("未知的资源类型")
        facets = [[f"project_type:{kind}"]]
        if game:
            facets.append([f"versions:{game}"])
        if loader and kind in ("mod", "modpack"):
            facets.append([f"categories:{loader}"])
        params = {"query": query, "facets": json.dumps(facets), "limit": 30,
                  "offset": offset, "index": "relevance" if query else "downloads"}
        return self.client.api(f"{API}/search?{urlencode(params)}")

    def versions(self, project, game="", loader="", kind=""):
        params = {"include_changelog": "false"}
        if game:
            params["game_versions"] = json.dumps([game])
        if kind == "datapack":
            params["loaders"] = '["datapack"]'
        elif loader and kind in ("mod", "modpack"):
            params["loaders"] = json.dumps([loader])
        versions = self.client.api(f"{API}/project/{quote(project, safe='')}/version?{urlencode(params)}")
        return [v for v in versions if (not game or game in v.get("game_versions", []))
                and (not kind or version_kind(v, self.project(project)) == kind)
                and (not loader or kind not in ("mod", "modpack") or loader in v.get("loaders", []))]

    def resolve(self, selected, game, loader, kind):
        """Resolve first, then write; pinned conflicts and cycles fail before mutation."""
        chosen, active, ordered, incompatible = {}, set(), [], []

        def visit(version, expected_kind=None):
            self.client.check_cancel()
            project_id = version["project_id"]
            if project_id in chosen:
                if chosen[project_id]["id"] != version["id"]:
                    raise DownloadError(f"依赖要求同一个项目的不同版本：{project_id}")
                return
            if len(chosen) + len(active) >= 200:
                raise DownloadError("依赖超过 200 个项目的限制")
            if project_id in active:
                raise DownloadError("资源依赖存在循环")
            project = self.project(project_id)
            actual = version_kind(version, project)
            if actual == "modpack" or (expected_kind and actual != expected_kind):
                raise DownloadError("资源类型不匹配，请重新选择资源版本")
            if game not in version.get("game_versions", []):
                raise DownloadError(f"{project['title']} 不支持 Minecraft {game}")
            if actual == "mod" and (not loader or loader not in version.get("loaders", [])):
                raise DownloadError(f"{project['title']} 需要匹配的 Mod 加载器，当前为 {loader or '原版'}")
            if actual == "mod" and project.get("client_side") == "unsupported":
                raise DownloadError(f"{project['title']} 仅支持独立服务器")
            active.add(project_id)
            for dep in version.get("dependencies", []):
                mode = dep.get("dependency_type")
                if mode == "incompatible":
                    incompatible.append(dep)
                if mode != "required":
                    continue
                if dep.get("version_id"):
                    child = self.client.api(f"{API}/version/{quote(dep['version_id'], safe='')}")
                elif dep.get("project_id"):
                    child_project = self.project(dep["project_id"])
                    child_kind = child_project["project_type"]
                    if actual == "datapack" and child_kind == "mod":
                        child_kind = "datapack"
                    versions = self.versions(dep["project_id"], game, loader, child_kind)
                    versions = [v for v in versions if v.get("version_type") == "release"] or versions
                    if not versions:
                        raise DownloadError(f"找不到匹配的必需依赖：{child_project['title']}")
                    child = versions[0]
                else:
                    raise DownloadError(f"{project['title']} 的依赖需要手动提供文件：{dep.get('file_name')}")
                visit(child)
            active.remove(project_id)
            chosen[project_id] = version
            ordered.append((version, project, actual))

        visit(selected, kind)
        for dep in incompatible:
            if dep.get("project_id") in chosen or any(v["id"] == dep.get("version_id") for v in chosen.values()):
                raise DownloadError("所选资源的依赖存在明确的不兼容关系")
        return ordered, incompatible

    def install(self, root: Path, instance, selected, kind, world=""):
        from .instances import read_instance
        profile = read_instance(root, instance)
        game_dir = safe_path(root, f"versions/{instance}")
        ordered, incompatible = self.resolve(selected, profile["minecraft"], profile["loader"], kind)
        with InstallLock(root):
            receipt_path = safe_path(game_dir, "craftfetch-content.json")
            receipt = json.loads(receipt_path.read_text("utf-8")) if receipt_path.exists() else {}
            new_projects = {v["project_id"] for v, _, _ in ordered}
            new_versions = {v["id"] for v, _, _ in ordered}
            for old in receipt.values():
                for dep in old.get("incompatible", []):
                    if dep.get("project_id") in new_projects or dep.get("version_id") in new_versions:
                        raise DownloadError("已有资源声明与本次安装的资源不兼容")
            for dep in incompatible:
                if any(k.split("@")[0] == dep.get("project_id") for k in receipt) or any(r["version"] == dep.get("version_id") for r in receipt.values()):
                    raise DownloadError("当前版本已经安装了明确不兼容的资源")
            files, records = [], {}
            paths = set()
            for version, project, actual in ordered:
                info = primary_file(version)
                name = info["filename"]
                if "/" in name or "\\" in name:
                    raise DownloadError("资源文件名称包含路径")
                if actual == "datapack":
                    if not world or "/" in world or "\\" in world:
                        raise DownloadError("安装数据包及其依赖前，请选择一个已有存档")
                    world_dir = safe_path(game_dir, f"saves/{world}")
                    if not safe_path(world_dir, "level.dat").is_file():
                        raise DownloadError("所选存档不存在 level.dat；请先启动游戏创建存档")
                    folder = f"saves/{world}/datapacks"
                else:
                    folder = {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks"}.get(actual)
                    if folder is None:
                        raise DownloadError(f"不支持的依赖类型：{actual}")
                relative = f"{folder}/{name}"
                path = safe_path(game_dir, relative)
                item = remote_file(relative, info)
                if relative.casefold() in paths:
                    raise DownloadError("多个资源试图写入同名文件")
                paths.add(relative.casefold())
                project_id = version["project_id"]
                key = project_id + (f"@{world}" if actual == "datapack" else "")
                old = receipt.get(key)
                if old and old["version"] != version["id"]:
                    raise DownloadError(f"{project['title']} 已安装其他版本。请先移走旧文件并删除对应下载记录，或使用新实例。")
                if path.exists() and not self.client.valid(path, item):
                    raise DownloadError(f"同名文件内容不同，已保留原文件：{relative}")
                files.append(item)
                records[key] = {"version": version["id"], "title": project["title"], "path": relative,
                                "sha1": item.sha1, "sha512": item.sha512,
                                "incompatible": [d for d in version.get("dependencies", []) if d.get("dependency_type") == "incompatible"]}
            self.client.event("phase", f"正在下载资源及必需依赖，共 {len(files)} 个文件…")
            # A partial failed transfer is safe to retry; no existing differing file is replaced.
            result = transfer(self.client, game_dir, files)
            receipt.update(records)
            self.client._write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode())
        return {**result, "instance": instance, "directory": str(game_dir), "resources": len(files)}
