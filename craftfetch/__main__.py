import argparse
import json
from pathlib import Path
import sys
import zipfile

from . import __version__
from .core import Cancelled, DownloadError, Downloader


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="CraftFetch：游戏、Mod、整合包、数据包、资源包、光影及 Java 下载器（Windows x64）")
    parser.add_argument('--list', action='store_true', help='列出原版正式版本')
    parser.add_argument('--snapshots', action='store_true', help='包含快照 / 预览版')
    parser.add_argument('--version', help='Minecraft 版本，例如 1.20.1')
    parser.add_argument('--directory', type=Path, help='目标 .minecraft 游戏根目录')
    parser.add_argument('--source', choices=('official', 'bmclapi'), default='official', help='原版文件来源；第三方资源使用其各自来源')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--plan', action='store_true', help='仅显示原版下载计划，不写入目录')
    parser.add_argument('--instance', help='目标 / 新建的隔离实例名（英文字母、数字、点、横线、下划线）')
    parser.add_argument('--loader', choices=('vanilla', 'fabric', 'quilt', 'forge', 'neoforge'), default='vanilla')
    parser.add_argument('--loader-version', default='', help='加载器版本号，留空自动选择')
    parser.add_argument('--no-java', action='store_true', help='不自动部署 Java；Forge / NeoForge 安装仍需要 Java')
    parser.add_argument('--java', type=int, help='单独部署指定 Java 主版本，例如 17')
    parser.add_argument('--search', metavar='QUERY', help='搜索 Modrinth 项目，空字符串显示热门')
    parser.add_argument('--type', choices=('mod', 'modpack', 'datapack', 'resourcepack', 'shader'), default='mod')
    parser.add_argument('--project', help='安装 Modrinth 项目 ID / slug 的兼容版本')
    parser.add_argument('--resource-version', help='准确的 Modrinth 版本 ID；需配合 --project')
    parser.add_argument('--pack', type=Path, help='导入本地 .mrpack 整合包')
    parser.add_argument('--world', default='', help='数据包目标实例内的已有存档目录名')
    parser.add_argument('--include-optional', action='store_true', help='包含整合包的客户端可选文件')
    parser.add_argument('--app-version', action='version', version=__version__)
    args = parser.parse_args()
    actions = [args.list, args.java is not None, args.search is not None, bool(args.project), bool(args.pack)]
    if not any(actions) and not args.version:
        from .gui import main as gui
        gui()
        return 0
    if sum(bool(a) for a in actions) > 1:
        parser.error('请每次只选择一种操作：--list / --java / --search / --project / --pack')
    if not args.list and args.search is None and args.directory is None:
        parser.error('安装 / 预览时需要 --directory 指定游戏根目录')
    if (args.project or args.pack) and not args.instance:
        parser.error('安装资源或整合包需要 --instance')
    if args.loader != 'vanilla' and not args.instance and args.search is None:
        parser.error('创建带加载器的版本需要 --instance 指定新实例名')
    if args.plan and (any(actions) or args.instance):
        parser.error('--plan 仅用于 --version 的原版文件计划')
    if not 1 <= args.workers <= 16:
        parser.error('--workers 必须在 1 到 16 之间')

    def event(kind, data):
        if kind in ('phase', 'log'):
            print(data, file=sys.stderr)
        elif kind == 'progress' and (data['done'] % 100 == 0 or data['done'] == data['total']):
            print(f"{data['done']}/{data['total']} · 复用 {data['cached']}", file=sys.stderr)

    client = Downloader(args.source, args.workers, event=event)
    try:
        if args.java is not None:
            from .runtime import install_java
            print(install_java(client, args.directory, args.java))
            return 0
        if args.search is not None:
            from .catalog import Catalog
            result = Catalog(client).search(args.search, args.type, args.version or '', '' if args.loader == 'vanilla' else args.loader)
        elif args.pack:
            from .packs import install_pack
            result = install_pack(client, args.directory, args.pack, args.instance, optional=args.include_optional, auto_java=not args.no_java)
        elif args.project:
            from .catalog import Catalog, API
            from .instances import read_instance
            catalog = Catalog(client)
            if args.type == 'modpack':
                game, loader = args.version or '', '' if args.loader == 'vanilla' else args.loader
            else:
                profile = read_instance(args.directory, args.instance)
                game, loader = profile['minecraft'], profile['loader']
            versions = catalog.versions(args.project, game, loader, args.type)
            if args.resource_version:
                selected = next((v for v in versions if v['id'] == args.resource_version), None)
            else:
                selected = next((v for v in versions if v['version_type'] == 'release'), versions[0] if versions else None)
            if not selected:
                raise DownloadError('没有匹配的资源版本，请检查 MC 版本、加载器与资源类型')
            if args.type == 'modpack':
                from .packs import download_pack
                result = download_pack(client, args.directory, selected, args.instance, optional=args.include_optional, auto_java=not args.no_java)
            else:
                result = catalog.install(args.directory, args.instance, selected, args.type, args.world)
        elif args.instance:
            from .instances import create_instance
            result = create_instance(client, args.directory, args.version, args.instance, args.loader, args.loader_version, not args.no_java)
        else:
            manifest = client.manifest()
            if args.list:
                for entry in manifest['versions']:
                    if entry['type'] == 'release' or args.snapshots and entry['type'] == 'snapshot':
                        print(f"{entry['id']}\t{entry['type']}\t{entry.get('releaseTime', '')[:10]}")
                return 0
            entry = next((v for v in manifest['versions'] if v['id'] == args.version), None)
            if not entry:
                raise DownloadError(f'未找到版本：{args.version}')
            plan = client.plan(args.directory, entry)
            if args.plan:
                result = {'version': plan.version, 'platform': 'windows-x64', 'files': len(plan.files),
                          'bytes': plan.total_bytes, 'java': plan.java or 8, 'directory': str(args.directory.expanduser().resolve())}
            else:
                result = client.install(args.directory, plan)
                if not args.no_java:
                    from .runtime import install_java, java_major
                    result['java'] = str(install_java(client, args.directory, java_major(json.loads(plan.metadata))))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        client.cancel.set()
        print('任务已中断，再次运行会校验并复用完整文件。', file=sys.stderr)
        return 130
    except (DownloadError, Cancelled, OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
