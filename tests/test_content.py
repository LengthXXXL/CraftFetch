import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from craftfetch.catalog import API, Catalog, version_kind
from craftfetch.core import Cancelled, DownloadError, Downloader, File
from craftfetch.instances import create_instance, read_instance
from craftfetch.loaders import merge_profile, library_file, choose_loader
from craftfetch.packs import inspect_pack, install_pack
from craftfetch.runtime import ADOPTIUM, install_java, java_major
from tests.test_core import Response


def sha(data, algorithm='sha1'):
    return hashlib.new(algorithm, data).hexdigest()


def zipped(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


class ContentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.client = Downloader(attempts=1, workers=2)
        self.payloads = {}
        self.calls = []
        self.patch = patch('craftfetch.core.urlopen', self.opener)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def opener(self, request, **kwargs):
        self.calls.append(request.full_url)
        payload = self.payloads[request.full_url]
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(payload, request.full_url)

    def file(self, name='mod.jar', data=b'verified mod'):
        url = 'https://cdn.modrinth.com/data/' + name
        self.payloads[url] = data
        return {'filename': name, 'url': url, 'size': len(data), 'primary': True,
                'hashes': {'sha1': sha(data), 'sha512': sha(data, 'sha512')}}

    def version(self, project='project1', version='v1', loaders=None, kind='mod'):
        self.payloads[f'{API}/project/{project}'] = {'id': project, 'title': project, 'project_type': kind, 'client_side': 'required'}
        return {'id': version, 'project_id': project, 'loaders': loaders or ['fabric'],
                'game_versions': ['1.20.1'], 'files': [self.file(project + '.jar')],
                'dependencies': [], 'version_type': 'release'}

    def instance(self, loader='fabric'):
        folder = self.root / 'versions/test'
        folder.mkdir(parents=True)
        (folder / 'test.json').write_text('{}')
        (folder / 'craftfetch-instance.json').write_text(json.dumps({'minecraft': '1.20.1', 'loader': loader, 'java_major': 17, 'java': ''}))
        return folder

    def pack(self, extra=None, files=None, dependencies=None):
        index = {'formatVersion': 1, 'game': 'minecraft', 'name': 'Test pack', 'versionId': '1',
                 'dependencies': dependencies or {'minecraft': '1.20.1', 'fabric-loader': '0.16.14'}, 'files': files or []}
        return zipped({'modrinth.index.json': json.dumps(index), **(extra or {})})

    def pack_entry(self, path='mods/test.jar', env=None):
        info = self.file()
        return {'path': path, 'hashes': info['hashes'], 'fileSize': info['size'], 'downloads': [info['url']], **({'env': env} if env else {})}

    def java_fixture(self, major=17, extra=None):
        data = zipped({'jdk/bin/java.exe': b'MZjava', 'jdk/bin/javaw.exe': b'MZjavaw',
                       'jdk/lib/modules': b'java runtime', **(extra or {})})
        url = f'https://github.com/adoptium/java{major}.zip'
        self.payloads[url] = data
        self.payloads[f'{ADOPTIUM}/assets/latest/{major}/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse'] = [
            {'binary': {'os': 'windows', 'architecture': 'x64', 'package': {'link': url, 'name': f'java{major}.zip', 'checksum': sha(data, 'sha256'), 'size': len(data)}},
             'version': {'major': major}, 'release_name': f'test-{major}'}]
        return url

    def test_java_deploy_reuse_and_repair(self):
        url = self.java_fixture()
        path = install_java(self.client, self.root, 17)
        self.assertEqual(path.read_bytes(), b'MZjavaw')
        calls = len(self.calls)
        self.assertEqual(install_java(self.client, self.root, 17), path)
        self.assertEqual(len(self.calls), calls)
        path.write_bytes(b'corrupt')
        install_java(self.client, self.root, 17)
        self.assertEqual(path.read_bytes(), b'MZjavaw')
        self.assertEqual(self.calls.count(url), 1, 'repair should reuse verified archive')

    def test_java_corrupt_archive_not_deployed(self):
        url = self.java_fixture()
        self.payloads[url] = b'bad data'
        with self.assertRaises(DownloadError):
            install_java(self.client, self.root, 17)
        self.assertFalse((self.root / 'runtime/java-17-windows-x64').exists())

    def test_java_jdk_fallback_when_jre_unavailable(self):
        self.java_fixture(16)
        jre = f'{ADOPTIUM}/assets/latest/16/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse'
        self.payloads[jre.replace('image_type=jre', 'image_type=jdk')] = self.payloads[jre]
        self.payloads[jre] = []
        self.assertTrue(install_java(self.client, self.root, 16).is_file())

    def test_java_zip_traversal(self):
        self.java_fixture(extra={'jdk/../../escape': b'bad'})
        with self.assertRaises(DownloadError):
            install_java(self.client, self.root, 17)
        self.assertFalse((self.root / 'escape').exists())

    def test_java_cancel_preserves_existing_deployment(self):
        self.java_fixture()
        path = install_java(self.client, self.root, 17)
        self.client.cancel.set()
        with self.assertRaises(Cancelled):
            install_java(self.client, self.root, 17)
        self.assertTrue(path.exists())

    def test_java_major_from_metadata(self):
        self.assertEqual(java_major({}), 8)
        self.assertEqual(java_major({'javaVersion': {'majorVersion': 25}}), 25)
        for value in [True, 7, '21', 100]:
            with self.assertRaises(DownloadError):
                java_major({'javaVersion': {'majorVersion': value}})

    def test_all_advertised_hashes_verified(self):
        info = self.file()
        item = File('mod.jar', info['url'], info['hashes']['sha1'], info['size'], sha512='0' * 128)
        with self.assertRaises(DownloadError):
            self.client.download(self.root, item)
        self.assertFalse((self.root / 'mod.jar').exists())

    def test_unknown_length_maven_file_still_checks_hash(self):
        data = b'jar payload'
        url = 'https://maven.fabricmc.net/g/a/1/a-1.jar'
        self.payloads[url] = data
        self.payloads[url + '.sha1'] = (sha(data) + '\n').encode()
        item = library_file(self.client, {'name': 'g:a:1', 'url': 'https://maven.fabricmc.net/'})
        self.client.download(self.root, item)
        self.assertEqual((self.root / item.path).read_bytes(), data)
        self.assertTrue(self.client.valid(self.root / item.path, item))

    def test_mod_required_dependency_installs_in_isolated_folder(self):
        folder = self.instance()
        parent, child = self.version(), self.version('dependency', 'v2')
        parent['dependencies'] = [{'dependency_type': 'required', 'version_id': 'v2'}]
        self.payloads[f'{API}/version/v2'] = child
        result = Catalog(self.client).install(self.root, 'test', parent, 'mod')
        self.assertEqual(result['resources'], 2)
        self.assertTrue((folder / 'mods/project1.jar').exists())
        self.assertTrue((folder / 'mods/dependency.jar').exists())
        self.assertFalse((self.root / 'mods').exists())

    def test_dependency_conflict_before_any_content_write(self):
        folder = self.instance()
        parent = self.version()
        a, b = self.version('dep', 'v2'), self.version('dep', 'v3')
        parent['dependencies'] = [{'dependency_type': 'required', 'version_id': 'v2'}, {'dependency_type': 'required', 'version_id': 'v3'}]
        self.payloads[f'{API}/version/v2'], self.payloads[f'{API}/version/v3'] = a, b
        with self.assertRaises(DownloadError):
            Catalog(self.client).install(self.root, 'test', parent, 'mod')
        self.assertFalse((folder / 'mods').exists())

    def test_wrong_loader_rejected(self):
        folder = self.instance('forge')
        with self.assertRaises(DownloadError):
            Catalog(self.client).install(self.root, 'test', self.version(), 'mod')
        self.assertFalse((folder / 'mods').exists())

    def test_dependency_cycle_rejected(self):
        a, b = self.version('a', 'a1'), self.version('b', 'b1')
        a['dependencies'] = [{'dependency_type': 'required', 'version_id': 'b1'}]
        b['dependencies'] = [{'dependency_type': 'required', 'version_id': 'a1'}]
        self.payloads[f'{API}/version/a1'], self.payloads[f'{API}/version/b1'] = a, b
        with self.assertRaisesRegex(DownloadError, '循环'):
            Catalog(self.client).resolve(a, '1.20.1', 'fabric', 'mod')

    def test_existing_incompatibility_checked_in_reverse_direction(self):
        folder = self.instance()
        a, b = self.version('a'), self.version('b', 'b1')
        a['dependencies'] = [{'dependency_type': 'incompatible', 'project_id': 'b'}]
        Catalog(self.client).install(self.root, 'test', a, 'mod')
        with self.assertRaisesRegex(DownloadError, '不兼容'):
            Catalog(self.client).install(self.root, 'test', b, 'mod')
        self.assertFalse((folder / 'mods/b.jar').exists())

    def test_resource_and_shader_destinations(self):
        folder = self.instance()
        for kind, loaders, dest in [('resourcepack', ['minecraft'], 'resourcepacks'), ('shader', ['iris'], 'shaderpacks')]:
            version = self.version(kind, kind, loaders, kind)
            version['files'] = [self.file(kind + '.zip')]
            Catalog(self.client).install(self.root, 'test', version, kind)
            self.assertTrue((folder / dest / (kind + '.zip')).exists())

    def test_datapack_dual_project_targets_selected_world(self):
        folder = self.instance('vanilla')
        world = folder / 'saves/My world'
        world.mkdir(parents=True)
        (world / 'level.dat').write_bytes(b'world')
        version = self.version(loaders=['datapack'])
        version['files'] = [self.file('pack.zip')]
        self.assertEqual(version_kind(version, {'project_type': 'mod'}), 'datapack')
        Catalog(self.client).install(self.root, 'test', version, 'datapack', 'My world')
        self.assertTrue((world / 'datapacks/pack.zip').exists())
        self.assertEqual((world / 'level.dat').read_bytes(), b'world')

    def test_datapack_missing_or_escaping_world_rejected(self):
        folder = self.instance('vanilla')
        version = self.version(loaders=['datapack'])
        for world in ['', '../escape', 'missing']:
            with self.assertRaises(DownloadError):
                Catalog(self.client).install(self.root, 'test', version, 'datapack', world)
        self.assertFalse((folder / 'saves').exists())

    def test_resource_differing_existing_file_preserved(self):
        folder = self.instance()
        (folder / 'mods').mkdir()
        old = folder / 'mods/project1.jar'
        old.write_bytes(b'existing custom mod')
        with self.assertRaises(DownloadError):
            Catalog(self.client).install(self.root, 'test', self.version(), 'mod')
        self.assertEqual(old.read_bytes(), b'existing custom mod')

    def test_pack_environment_and_overrides_order(self):
        entries = [self.pack_entry('mods/client.jar'), self.pack_entry('mods/optional.jar', {'client': 'optional'}),
                   self.pack_entry('mods/server.jar', {'client': 'unsupported'})]
        data = self.pack({'overrides/options.txt': 'common', 'client-overrides/options.txt': 'client', 'server-overrides/ignored.txt': 'server'}, entries)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _, game, loader, version, files, overrides = inspect_pack(archive, self.root, 'Test')
            self.assertEqual((game, loader, version), ('1.20.1', 'fabric', '0.16.14'))
            self.assertEqual([f.path for f in files], ['mods/client.jar'])
            self.assertEqual([i.filename for i, _ in overrides], ['overrides/options.txt', 'client-overrides/options.txt'])
            self.assertEqual(len(inspect_pack(archive, self.root, 'Test', optional=True)[4]), 2)

    def test_pack_rejects_zip_traversal_and_reserved_paths(self):
        for name in ['overrides/../../escape', 'overrides/C:/bad', 'overrides/craftfetch-instance.json', 'overrides/Test.json', 'overrides/versions/foo.json']:
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(self.pack({name: 'bad'}))) as archive:
                with self.assertRaises(DownloadError):
                    inspect_pack(archive, self.root, 'Test')

    def test_pack_rejects_bad_hosts_hashes_and_duplicates(self):
        entry = self.pack_entry()
        for changes in [{'downloads': ['https://localhost/private']}, {'hashes': {'sha1': 'a' * 40}}, {'path': '../bad'}]:
            with zipfile.ZipFile(io.BytesIO(self.pack(files=[{**entry, **changes}]))) as archive, self.assertRaises(DownloadError):
                inspect_pack(archive, self.root, 'Test')
        with zipfile.ZipFile(io.BytesIO(self.pack(files=[entry, {**entry, 'path': entry['path'].upper()}]))) as archive, self.assertRaises(DownloadError):
            inspect_pack(archive, self.root, 'Test')

    def test_pack_rejects_unknown_or_multiple_loaders(self):
        for dependencies in [{'minecraft': '1.20.1', 'unknown': '1'}, {'minecraft': '1.20.1', 'forge': '47.0.0', 'fabric-loader': '0.16.14'}]:
            with zipfile.ZipFile(io.BytesIO(self.pack(dependencies=dependencies))) as archive, self.assertRaises(DownloadError):
                inspect_pack(archive, self.root, 'Test')

    def test_pack_full_staged_publish_and_client_override(self):
        entry = self.pack_entry()
        path = self.root / 'test.mrpack'
        path.write_bytes(self.pack({'overrides/options.txt': 'common', 'client-overrides/options.txt': 'client'}, [entry]))
        base_dir = self.root / 'versions/1.20.1'
        base_dir.mkdir(parents=True)
        (base_dir / '1.20.1.jar').write_bytes(b'client')
        base = {'id': '1.20.1', 'libraries': [], 'javaVersion': {'majorVersion': 17}}
        record = {'minecraft': '1.20.1', 'loader': 'fabric', 'java_major': 17, 'java': ''}
        profile = {'mainClass': 'net.fabricmc.loader.impl.launch.knot.KnotClient', 'libraries': []}
        with patch('craftfetch.packs.prepare', return_value=(base, profile, record)):
            result = install_pack(self.client, self.root, path, 'Test', auto_java=False)
        folder = self.root / 'versions/Test'
        self.assertEqual((folder / 'options.txt').read_text(), 'client')
        self.assertTrue((folder / 'mods/test.jar').exists())
        self.assertEqual((folder / 'Test.jar').read_bytes(), b'client')
        self.assertEqual(json.loads((folder / 'Test.json').read_text())['mainClass'], profile['mainClass'])
        self.assertEqual(read_instance(self.root, 'Test')['minecraft'], '1.20.1')
        self.assertEqual(result['instance'], 'Test')

    def test_pack_failure_never_publishes_instance(self):
        entry = self.pack_entry()
        self.payloads[entry['downloads'][0]] = b'corrupt'
        path = self.root / 'bad.mrpack'
        path.write_bytes(self.pack(files=[entry]))
        base = {'id': '1.20.1', 'libraries': []}
        record = {'minecraft': '1.20.1', 'loader': 'fabric', 'java_major': 17, 'java': ''}
        with patch('craftfetch.packs.prepare', return_value=(base, {}, record)), self.assertRaises(DownloadError):
            install_pack(self.client, self.root, path, 'Test', auto_java=False)
        self.assertFalse((self.root / 'versions/Test').exists())
        self.assertEqual(list((self.root / '.craftfetch/staging').iterdir()), [])

    def test_profile_merge_overrides_same_library_and_removes_inheritance(self):
        base = {'id': '1.20.1', 'libraries': [{'name': 'g:a:1'}, {'name': 'g:b:1'}],
                'arguments': {'game': ['--username', '${auth_player_name}'], 'jvm': ['-Xss1M']}}
        loader = {'id': 'fabric', 'inheritsFrom': '1.20.1', 'libraries': [{'name': 'g:a:2'}],
                  'mainClass': 'loader.Main', 'arguments': {'game': ['--modded']}}
        result = merge_profile(base, loader, 'MyGame')
        self.assertNotIn('inheritsFrom', result)
        self.assertEqual(result['id'], 'MyGame')
        self.assertEqual([v['name'] for v in result['libraries']], ['g:a:2', 'g:b:1'])
        self.assertEqual(result['arguments']['game'][-1], '--modded')
        self.assertEqual(result['arguments']['jvm'], ['-Xss1M'])
        self.assertEqual(base['libraries'][0]['name'], 'g:a:1')

    def test_existing_instance_rejected_before_network(self):
        self.instance()
        with self.assertRaises(DownloadError):
            create_instance(self.client, self.root, '1.20.1', 'test')
        self.assertEqual(self.calls, [])

    def test_neoforge_automatic_versions_support_new_minecraft_numbers(self):
        url = 'https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml'
        self.payloads[url] = b'<metadata><versioning><versions><version>21.1.172</version><version>21.1.264</version><version>26.3.0.94</version><version>26.3.0.95</version><version>26.3.0.96-beta</version><version>26.3.1.1</version></versions></versioning></metadata>'
        self.assertEqual(choose_loader(self.client, 'neoforge', '1.21.1'), '21.1.264')
        self.assertEqual(choose_loader(self.client, 'neoforge', '26.3'), '26.3.0.95')
        self.assertEqual(choose_loader(self.client, 'neoforge', '26.3.1'), '26.3.1.1')


if __name__ == '__main__':
    unittest.main()
