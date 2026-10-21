import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from craftfetch.core import (Cancelled, DownloadError, Downloader, File, InstallLock,
                             library_allowed, mirror_url, safe_path)


def digest(data):
    return hashlib.sha1(data).hexdigest()


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payloads = {}
        self.calls = []
        self.client = Downloader(attempts=1, workers=3)

    def info(self, name, data):
        url = f"https://example.test/{name}"
        self.payloads[url] = data
        return {"url": url, "sha1": digest(data), "size": len(data)}

    def fixture(self, legacy=False, extra_objects=None):
        client = self.info("client.jar", b"game jar fixture")
        artifact = self.info("library.jar", b"library fixture")
        artifact["path"] = "group/lib/1/lib-1.jar"
        native = self.info("native.jar", b"windows native fixture")
        native["path"] = "group/lib/1/lib-1-natives-windows.jar"
        sound = b"sound fixture"
        sound_hash = digest(sound)
        self.payloads[f"https://resources.download.minecraft.net/{sound_hash[:2]}/{sound_hash}"] = sound
        objects = {"sounds/test.ogg": {"hash": sound_hash, "size": len(sound)}}
        objects.update(extra_objects or {})
        index = {"objects": objects, "virtual": legacy, "map_to_resources": legacy}
        index_info = self.info("index.json", json.dumps(index).encode())
        index_info["id"] = "test-index"
        metadata = {"id": "test-1", "downloads": {"client": client}, "assetIndex": index_info,
                    "javaVersion": {"majorVersion": 17}, "libraries": [
                        {"name": "group:lib:1", "downloads": {"artifact": artifact, "classifiers": {"natives-windows-64": native}},
                         "natives": {"windows": "natives-windows-${arch}"}},
                        {"name": "linux:only:1", "rules": [{"action": "allow", "os": {"name": "linux"}}]}]}
        info = self.info("metadata.json", json.dumps(metadata).encode())
        return {"id": "test-1", **info}, sound_hash

    def opener(self, request, **kwargs):
        url = request.full_url
        self.calls.append(url)
        return Response(self.payloads[url], url)

    def test_complete_install_cache_and_repair(self):
        entry, _ = self.fixture()
        with patch("craftfetch.core.urlopen", self.opener):
            plan = self.client.plan(self.root, entry)
            self.assertEqual(plan.java, 17)
            self.assertEqual(len(plan.files), 5)
            result = self.client.install(self.root, plan)
            self.assertEqual(result["files"], 5)
            metadata_path = self.root / "versions/test-1/test-1.json"
            self.assertEqual(metadata_path.read_bytes(), plan.metadata)
            calls = len(self.calls)
            result = self.client.install(self.root, plan)
            self.assertEqual(result["cached"], 5)
            self.assertEqual(len(self.calls), calls)
            jar = self.root / "versions/test-1/test-1.jar"
            jar.write_bytes(b"X" * jar.stat().st_size)
            result = self.client.install(self.root, plan)
            self.assertEqual(result["cached"], 4)
            self.assertEqual(len(self.calls), calls + 1)

    def test_failure_never_publishes_version_or_bad_file(self):
        entry, _ = self.fixture()
        with patch("craftfetch.core.urlopen", self.opener):
            plan = self.client.plan(self.root, entry)
            self.payloads[plan.files[0].url] = b"bad"
            with self.assertRaises(DownloadError):
                self.client.install(self.root, plan)
        self.assertFalse((self.root / "versions/test-1/test-1.json").exists())
        self.assertFalse((self.root / plan.files[0].path).exists())
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_metadata_hash_verified(self):
        entry, _ = self.fixture()
        entry["sha1"] = "0" * 40
        with patch("craftfetch.core.urlopen", self.opener), self.assertRaises(DownloadError):
            self.client.plan(self.root, entry)

    def test_existing_custom_version_preserved(self):
        entry, _ = self.fixture()
        with patch("craftfetch.core.urlopen", self.opener):
            plan = self.client.plan(self.root, entry)
            path = self.root / "versions/test-1/test-1.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"custom config")
            with self.assertRaises(DownloadError):
                self.client.install(self.root, plan)
            self.assertEqual(path.read_bytes(), b"custom config")
            self.assertFalse((path.parent / "test-1.jar").exists())

    def test_cancel_cleanup_and_no_descriptor(self):
        entry, _ = self.fixture()
        with patch("craftfetch.core.urlopen", self.opener):
            plan = self.client.plan(self.root, entry)
            self.client.cancel.set()
            with self.assertRaises(Cancelled):
                self.client.install(self.root, plan)
        self.assertFalse((self.root / "versions/test-1/test-1.json").exists())

    def test_cancel_in_middle_of_file(self):
        payload = b"a" * (300 * 1024)
        info = self.info("large.jar", payload)
        item = File("libraries/large.jar", **info)
        client = self.client

        class CancellingResponse(Response):
            def read(self, *args):
                chunk = super().read(*args)
                client.cancel.set()
                return chunk

        with patch("craftfetch.core.urlopen", return_value=CancellingResponse(payload, info["url"])):
            with self.assertRaises(Cancelled):
                client.download(self.root, item)
        self.assertFalse((self.root / item.path).exists())
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_legacy_assets_are_materialized(self):
        entry, _ = self.fixture(legacy=True)
        with patch("craftfetch.core.urlopen", self.opener):
            plan = self.client.plan(self.root, entry)
            self.client.install(self.root, plan)
        for path in ("assets/virtual/test-index/sounds/test.ogg", "resources/sounds/test.ogg"):
            self.assertEqual((self.root / path).read_bytes(), b"sound fixture")

    def test_legacy_asset_path_traversal_rejected(self):
        entry, _ = self.fixture(legacy=True, extra_objects={"../../../../escape": {"hash": "a" * 40, "size": 1}})
        with patch("craftfetch.core.urlopen", self.opener), self.assertRaises(DownloadError):
            self.client.plan(self.root, entry)

    def test_paths_and_symlinks(self):
        for path in ("../escape", "/absolute", "C:/evil", "lib/../evil", "lib\\evil", "CON.jar", "lib/a.", "lib/a ", "a//b"):
            with self.subTest(path=path), self.assertRaises(DownloadError):
                safe_path(self.root, path)
        with tempfile.TemporaryDirectory() as other:
            try:
                (self.root / "link").symlink_to(other, target_is_directory=True)
            except OSError:
                return  # Windows runners may not grant symlink privilege.
            with self.assertRaises(DownloadError):
                safe_path(self.root, "link/evil.jar")

    def test_concurrent_installer_lock(self):
        with InstallLock(self.root), self.assertRaises(DownloadError):
            with InstallLock(self.root):
                pass
        with InstallLock(self.root):
            pass

    def test_root_alias_uses_directory_identity(self):
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError:
            return
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == self.root:
                return alias
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            self.assertEqual(safe_path(self.root, 'libraries/a.jar'), self.root / 'libraries/a.jar')

    def test_rules_use_last_matching_action(self):
        self.assertTrue(library_allowed({}))
        self.assertFalse(library_allowed({"rules": []}))
        self.assertTrue(library_allowed({"rules": [{"action": "allow"}, {"action": "disallow", "os": {"name": "osx"}}]}))
        self.assertFalse(library_allowed({"rules": [{"action": "allow"}, {"action": "disallow", "os": {"name": "windows"}}]}))
        self.assertFalse(library_allowed({"rules": [{"action": "allow", "features": {"is_demo_user": True}}]}))
        self.assertTrue(library_allowed({"rules": [{"action": "allow", "features": {"has_custom_resolution": False}}]}))

    def test_mirror_mapping(self):
        self.assertEqual(mirror_url("https://resources.download.minecraft.net/ab/hash"), "https://bmclapi2.bangbang93.com/assets/ab/hash")
        self.assertEqual(mirror_url("https://libraries.minecraft.net/a/b.jar"), "https://bmclapi2.bangbang93.com/maven/a/b.jar")
        self.assertEqual(mirror_url("https://piston-data.mojang.com/v1/x.jar"), "https://bmclapi2.bangbang93.com/v1/x.jar")
        self.assertEqual(mirror_url("https://example.test/a"), "https://example.test/a")

    def test_mirror_failure_falls_back_to_official(self):
        payload = b"verified data"
        url = "https://libraries.minecraft.net/a.jar"
        client = Downloader(source="bmclapi", attempts=1)
        called = []

        def opener(request, **kwargs):
            called.append(request.full_url)
            if "bmclapi2" in request.full_url:
                return Response(b"corrupt mirror data", request.full_url)
            return Response(payload, request.full_url)

        with patch("craftfetch.core.urlopen", opener):
            client.download(self.root, File("libraries/a.jar", url, digest(payload), len(payload)))
        self.assertEqual(called, [mirror_url(url), url])
        self.assertEqual((self.root / "libraries/a.jar").read_bytes(), payload)

    def test_insecure_download_rejected(self):
        with self.assertRaises(DownloadError):
            self.client.candidates("http://example.test/a")

    def test_http_downgrade_rejected(self):
        with patch("craftfetch.core.urlopen", return_value=Response(b"{}", "http://example.test/a")):
            with self.assertRaises(DownloadError):
                self.client.json_bytes("https://example.test/a")

    def test_large_unexpected_body_preserves_existing_file(self):
        item = File("libraries/a.jar", "https://example.test/a", "0" * 40, 2)
        target = self.root / item.path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old data")
        with patch("craftfetch.core.urlopen", return_value=Response(b"oversized", item.url)):
            with self.assertRaises(DownloadError):
                self.client.download(self.root, item)
        self.assertEqual(target.read_bytes(), b"old data")


if __name__ == "__main__":
    unittest.main()
