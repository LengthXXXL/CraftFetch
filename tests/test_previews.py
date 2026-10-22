import hashlib
import io
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from craftfetch.core import DownloadError
from craftfetch.previews import PreviewService, decode_preview, https_url, project_url


def picture(format='PNG'):
    data = io.BytesIO()
    Image.new('RGB', (320, 160), '#4fbe71').save(data, format)
    return data.getvalue()


class PreviewTests(unittest.TestCase):
    def test_png_jpeg_webp_are_bounded_and_keep_aspect_ratio(self):
        for format in ['PNG', 'JPEG', 'WEBP']:
            with self.subTest(format=format):
                image = decode_preview(picture(format), (80, 80))
                self.assertEqual(image.size, (80, 40))
                self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(decode_preview(picture(), (80, 80), cover=True).size, (80, 80))

    def test_invalid_oversized_and_large_pixel_images_rejected(self):
        for data in [b'<html>not an image</html>', b'x' * (10 * 1024 ** 2 + 1)]:
            with self.assertRaises(DownloadError):
                decode_preview(data, (64, 64))
        with patch('craftfetch.previews.MAX_PIXELS', 100), self.assertRaises(DownloadError):
            decode_preview(picture(), (64, 64))

    def test_source_links_never_open_local_or_executable_schemes(self):
        for url in ['file:///private/file', 'javascript:alert(1)', 'http://modrinth.com', 'https://user:pass@modrinth.com', 'https:///bad']:
            with self.subTest(url=url), self.assertRaises(DownloadError):
                https_url(url)
        self.assertEqual(project_url({'slug': 'sodium'}, 'mod'), 'https://modrinth.com/mod/sodium')
        self.assertEqual(project_url({'id': 'abcd'}, 'datapack'), 'https://modrinth.com/datapack/abcd')
        self.assertNotIn('/../', project_url({'slug': '../foo'}, 'mod'))

    def test_cached_image_reuse_and_corrupt_cache_repair(self):
        url = 'https://cdn.modrinth.com/icon.webp'
        data = picture('WEBP')
        with tempfile.TemporaryDirectory() as folder:
            messages = queue.Queue()
            service = PreviewService(messages, folder)
            self.addCleanup(service.close)
            path = Path(folder) / (hashlib.sha256(url.encode()).hexdigest()+'.img')
            with patch('craftfetch.previews.Downloader.small_bytes', return_value=data) as fetch:
                service._load(1, url, (64, 64), False)
                self.assertEqual(messages.get()[0], 'image')
                service._load(2, url, (64, 64), False)
                self.assertEqual(messages.get()[0], 'image')
                self.assertEqual(fetch.call_count, 1)
                path.write_bytes(b'corrupt cache')
                service._load(3, url, (64, 64), False)
                self.assertEqual(messages.get()[0], 'image')
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(path.read_bytes(), data)

    def test_missing_preview_does_not_raise_or_fail_download_task(self):
        with tempfile.TemporaryDirectory() as folder:
            messages = queue.Queue()
            service = PreviewService(messages, folder)
            self.addCleanup(service.close)
            with patch('craftfetch.previews.Downloader.small_bytes', side_effect=DownloadError('timeout')):
                service._load(7, 'https://cdn.modrinth.com/icon.png', (64, 64), False)
            self.assertEqual(messages.get(), ('image_missing', 7))


if __name__ == '__main__':
    unittest.main()
