"""Live third-party APIs/CDNs and Windows loader installers, no game launch.

Downloads are kept in a disposable temporary directory, never committed.
Run with --forge to exercise real Forge and NeoForge on Windows.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

from craftfetch.catalog import Catalog, primary_file, remote_file
from craftfetch.content import transfer
from craftfetch.core import Downloader, MANIFEST_URL
from craftfetch.instances import read_instance
from craftfetch.loaders import install_loader
from craftfetch.packs import inspect_pack
from craftfetch.runtime import install_java


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--forge', action='store_true')
    parser.add_argument('--pack-install', action='store_true', help='also install an entire real modpack including vanilla assets')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = {'platform': os.name, 'checks': [], 'elapsed': 0}
    start = time.monotonic()
    def event(kind, data):
        if kind in ('phase', 'log'):
            print(data, flush=True)
    client = Downloader(event=event, timeout=50, attempts=3)
    catalog = Catalog(client)
    with tempfile.TemporaryDirectory(prefix='craftfetch-content-') as folder:
        root = Path(folder)
        instance = root / 'versions/Test'
        instance.mkdir(parents=True)
        (instance / 'Test.json').write_text('{}')
        (instance / 'craftfetch-instance.json').write_text(json.dumps({'minecraft': '1.20.1', 'loader': 'fabric', 'java_major': 17, 'java': ''}))
        world = instance / 'saves/Test world'
        world.mkdir(parents=True)
        (world / 'level.dat').write_bytes(b'test fixture; not a playable save')
        for kind, slug in [('mod', 'sodium'), ('resourcepack', 'fresh-animations'), ('shader', 'complementary-reimagined'), ('datapack', 'terralith')]:
            hits = catalog.search(slug, kind, '1.20.1', 'fabric')['hits']
            assert hits, (kind, 'no search hits')
            versions = catalog.versions(slug, '1.20.1', 'fabric', kind)
            assert versions, (kind, 'no versions')
            release = next((v for v in versions if v['version_type'] == 'release'), versions[0])
            result = catalog.install(root, 'Test', release, kind, 'Test world')
            report['checks'].append({'type': kind, 'project': slug, 'version': release['id'], 'files': result['resources']})
            print('PASS', kind, slug, result['resources'], flush=True)
        versions = catalog.versions('fabulously-optimized', '1.20.1', 'fabric', 'modpack')
        assert versions
        selected = versions[0]
        info = primary_file(selected)
        item = remote_file('test.mrpack', info)
        client.download(root, item)
        with zipfile.ZipFile(root / item.path) as archive:
            _, game, loader, version, files, overrides = inspect_pack(archive, root, 'PackTest')
            assert game == '1.20.1' and loader == 'fabric' and files
        report['checks'].append({'type': 'modpack', 'project': 'fabulously-optimized', 'version': selected['id'], 'files': len(files), 'overrides': len(overrides)})
        manifest = client.manifest()
        entry = next(v for v in manifest['versions'] if v['id'] == '1.20.1')
        base, raw = client.json_bytes(entry['url'], entry['sha1'])
        for loader, version in [('fabric', '0.16.14'), ('quilt', '0.28.1')]:
            profile = install_loader(client, root, base, loader, version, None)
            assert profile['inheritsFrom'] == '1.20.1'
            report['checks'].append({'type': 'loader', 'loader': loader, 'version': version, 'libraries': len(profile['libraries'])})
            print('PASS', loader, flush=True)
        java = install_java(client, root, 17)
        assert java.read_bytes()[:2] == b'MZ'
        assert install_java(client, root, 17) == java
        report['checks'].append({'type': 'java', 'major': 17, 'reuse_verified': True})
        if args.pack_install:
            from craftfetch.packs import download_pack
            result = download_pack(client, root, selected, 'Full-Pack-Test')
            game_dir = root / 'versions/Full-Pack-Test'
            assert (game_dir / 'Full-Pack-Test.json').is_file()
            assert (game_dir / 'Full-Pack-Test.jar').is_file()
            assert any((game_dir / 'mods').glob('*.jar'))
            assert read_instance(root, 'Full-Pack-Test')['minecraft'] == '1.20.1'
            report['checks'].append({'type': 'full-pack-install', 'project': 'fabulously-optimized',
                                     'version': selected['id'], 'mod_files': len(list((game_dir / 'mods').glob('*.jar')))})
        if args.forge:
            if os.name != 'nt':
                raise RuntimeError('--forge requires Windows')
            game_folder = root / 'versions/1.20.1'
            game_folder.mkdir(exist_ok=True)
            (game_folder / '1.20.1.json').write_bytes(raw)
            info = base['downloads']['client']
            from craftfetch.core import File
            client.download(root, File('versions/1.20.1/1.20.1.jar', info['url'], info['sha1'], info['size']))
            profile = install_loader(client, root, base, 'forge', '47.4.0', java)
            assert profile['mainClass'] and profile['libraries']
            report['checks'].append({'type': 'loader', 'loader': 'forge', 'version': '47.4.0', 'libraries': len(profile['libraries'])})
            entry = next(v for v in manifest['versions'] if v['id'] == '1.21.1')
            neo_base, raw = client.json_bytes(entry['url'], entry['sha1'])
            game_folder = root / 'versions/1.21.1'
            game_folder.mkdir()
            (game_folder / '1.21.1.json').write_bytes(raw)
            info = neo_base['downloads']['client']
            client.download(root, File('versions/1.21.1/1.21.1.jar', info['url'], info['sha1'], info['size']))
            java21 = install_java(client, root, 21)
            profile = install_loader(client, root, neo_base, 'neoforge', '21.1.172', java21)
            assert profile['mainClass'] and profile['libraries']
            report['checks'].append({'type': 'loader', 'loader': 'neoforge', 'version': '21.1.172', 'libraries': len(profile['libraries'])})
        report['elapsed'] = round(time.monotonic() - start, 1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
