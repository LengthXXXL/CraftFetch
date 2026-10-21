import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .core import Cancelled, DownloadError, Downloader


def main():
    parser = argparse.ArgumentParser(description="CraftFetch：独立 Minecraft Java 原版下载器（Windows x64 游戏文件）")
    parser.add_argument("--list", action="store_true", help="列出正式版本")
    parser.add_argument("--snapshots", action="store_true", help="包含快照 / 预览版")
    parser.add_argument("--version", help="要下载的 Minecraft 版本，例如 1.20.1")
    parser.add_argument("--directory", type=Path, help="目标 .minecraft 游戏目录")
    parser.add_argument("--source", choices=("official", "bmclapi"), default="official")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--plan", action="store_true", help="仅显示下载计划，不写入游戏目录")
    parser.add_argument("--app-version", action="version", version=__version__)
    args = parser.parse_args()
    if not args.list and not args.version:
        from .gui import main as gui
        gui()
        return 0
    if args.version and args.directory is None:
        parser.error("下载 / 预览时需要 --directory 指定游戏目录")
    if not 1 <= args.workers <= 16:
        parser.error("--workers 必须在 1 到 16 之间")

    def event(kind, data):
        if kind in ("phase", "log"):
            print(data, file=sys.stderr)
        elif kind == "progress" and (data["done"] % 100 == 0 or data["done"] == data["total"]):
            print(f"{data['done']}/{data['total']} · 复用 {data['cached']}", file=sys.stderr)

    client = Downloader(args.source, args.workers, event=event)
    try:
        manifest = client.manifest()
        if args.list:
            for entry in manifest["versions"]:
                if entry["type"] == "release" or args.snapshots and entry["type"] == "snapshot":
                    print(f"{entry['id']}\t{entry['type']}\t{entry.get('releaseTime', '')[:10]}")
            return 0
        entry = next((v for v in manifest["versions"] if v["id"] == args.version), None)
        if not entry:
            raise DownloadError(f"未找到版本：{args.version}")
        plan = client.plan(args.directory, entry)
        if args.plan:
            print(json.dumps({"version": plan.version, "platform": "windows-x64", "files": len(plan.files),
                              "bytes": plan.total_bytes, "java": plan.java,
                              "directory": str(args.directory.expanduser().resolve())}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(client.install(args.directory, plan), ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        client.cancel.set()
        print("任务已中断，再次运行会校验并复用完整文件。", file=sys.stderr)
        return 130
    except (DownloadError, Cancelled, OSError, KeyError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
