import asyncio
import base64
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiui.common import screenshots


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
ENCODED = base64.b64encode(PNG).decode('ascii')


class ScreenshotCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_mcps_return_image_bytes_with_correct_arguments(self):
        for name, expected in [
            ('screenshot', {}),
            ('browser_take_screenshot', {'type': 'png', 'fullPage': False}),
            ('playwright_screenshot', {'name': 'step', 'storeBase64': True,
                                       'savePng': False, 'fullPage': False}),
        ]:
            with self.subTest(name=name):
                class Session:
                    async def call_tool(self, tool, args, **kwargs):
                        actual = dict(args)
                        if name == 'playwright_screenshot':
                            self_dir = Path(actual.pop('downloadsDir'))
                            if not self_dir.is_dir():
                                raise AssertionError('截图临时目录不存在')
                        if tool != name or actual != expected:
                            raise AssertionError((tool, args))
                        return SimpleNamespace(is_error=False, content=[
                            SimpleNamespace(type='image', data=ENCODED, mime_type='image/png')])
                result = await screenshots.capture_step(Session(), [SimpleNamespace(name=name)])
                self.assertEqual(base64.b64decode(result['data']), PNG)
                self.assertEqual(result['mime_type'], 'image/png')
                self.assertLess(abs(time.time() - result['captured_at']), 2)

    async def test_ea_reads_screenshot_resource_without_using_returned_file_path(self):
        class Session:
            async def call_tool(self, name, args, **kwargs):
                return SimpleNamespace(is_error=False, content=[SimpleNamespace(
                    type='text', text='Screenshot saved to: ../../untrusted/path.png')])
            async def read_resource(self, uri):
                if uri != 'screenshot://step':
                    raise AssertionError(uri)
                return SimpleNamespace(contents=[SimpleNamespace(blob=ENCODED, mime_type='image/png')])
        result = await screenshots.capture_step(Session(), [SimpleNamespace(name='playwright_screenshot')])
        self.assertEqual(base64.b64decode(result['data']), PNG)

    async def test_builtin_json_and_camel_case_image_mime_are_supported(self):
        import json
        for content in [
            SimpleNamespace(type='text', text=json.dumps({'data': ENCODED, 'mime_type': 'image/png'})),
            SimpleNamespace(type='image', data=ENCODED, mimeType='image/png'),
        ]:
            class Session:
                async def call_tool(self, *args, **kwargs):
                    return SimpleNamespace(is_error=False, content=[content])
            result = await screenshots.capture_step(Session(), [SimpleNamespace(name='screenshot')])
            self.assertEqual(base64.b64decode(result['data']), PNG)

    async def test_unsupported_error_invalid_image_and_timeout_do_not_raise(self):
        class Session:
            async def call_tool(self, *args, **kwargs):
                return SimpleNamespace(is_error=True, content=[SimpleNamespace(type='text', text='closed')])
        result = await screenshots.capture_step(Session(), [])
        self.assertIn('error', result)
        self.assertIn('error', await screenshots.capture_step(Session(), [SimpleNamespace(name='screenshot')]))
        class SlowSession:
            async def call_tool(self, *args, **kwargs):
                await asyncio.sleep(1)
        self.assertIn('error', await screenshots.capture_step(
            SlowSession(), [SimpleNamespace(name='screenshot')], timeout=0.01))
        class InvalidSession:
            async def call_tool(self, *args, **kwargs):
                return SimpleNamespace(is_error=False, content=[
                    SimpleNamespace(type='image', data='not-base64', mime_type='image/png')])
        self.assertIn('error', await screenshots.capture_step(
            InvalidSession(), [SimpleNamespace(name='screenshot')]))

    async def test_temporary_file_cleanup_failure_does_not_discard_the_image(self):
        class Directory:
            name = '.'
            def cleanup(self):
                raise PermissionError('file still locked')
        class Session:
            async def call_tool(self, *args, **kwargs):
                return SimpleNamespace(is_error=False, content=[
                    SimpleNamespace(type='image', data=ENCODED, mime_type='image/png')])
        with patch.object(screenshots.tempfile, 'TemporaryDirectory', return_value=Directory()):
            result = await screenshots.capture_step(Session(), [SimpleNamespace(name='playwright_screenshot')])
        self.assertEqual(base64.b64decode(result['data']), PNG)

    async def test_zero_budget_skips_capture_and_oversized_image_is_rejected(self):
        class Session:
            async def call_tool(self, *args, **kwargs):
                return SimpleNamespace(is_error=False, content=[
                    SimpleNamespace(type='image', data=ENCODED, mime_type='image/png')])
        self.assertIn('error', await screenshots.capture_step(
            Session(), [SimpleNamespace(name='screenshot')], timeout=0))
        with patch.object(screenshots, 'MAX_IMAGE_BYTES', 10):
            self.assertIn('error', await screenshots.capture_step(Session(), [SimpleNamespace(name='screenshot')]))


class ScreenshotFilesTests(unittest.TestCase):
    def test_images_persist_without_base64_and_retries_have_distinct_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = []
            for _ in range(2):
                result = {'steps': [{'tool': 'click', 'ok': False, 'screenshot': {
                    'data': ENCODED, 'mime_type': 'image/png', 'captured_at': 123}}]}
                screenshots.persist_steps('T001', result, root=root)
                image = result['steps'][0]['screenshot']
                self.assertNotIn('data', image)
                self.assertEqual(screenshots.read_image('T001', image['file'], root=root), (PNG, 'image/png'))
                self.assertEqual(image['captured_at'], 123)
                results.append(image['file'])
            self.assertNotEqual(*results)
            with self.assertRaises((ValueError, FileNotFoundError)):
                screenshots.read_image('T002', results[0], root=root)

    def test_invalid_payload_and_path_traversal_cannot_be_saved_or_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for data, mime in [('!', 'image/png'), (ENCODED, 'text/html'),
                               (base64.b64encode(b'<html>').decode(), 'image/png'),
                               (base64.b64encode(b'\x89PNG\r\n\x1a\n').decode(), 'image/png'),
                               (base64.b64encode(b'\xff\xd8\xff\xd9').decode(), 'image/jpeg')]:
                result = {'steps': [{'screenshot': {'data': data, 'mime_type': mime}}]}
                screenshots.persist_steps('../../outside', result, root=root)
                self.assertIn('error', result['steps'][0]['screenshot'])
                self.assertNotIn('data', result['steps'][0]['screenshot'])
            self.assertEqual(list(root.rglob('*.png')), [])
            for name in ('../secret.png', '/secret.png', '..\\secret.png'):
                with self.assertRaises(ValueError):
                    screenshots.read_image('T001', name, root=root)


if __name__ == '__main__':
    unittest.main()
