# -*- coding: utf-8 -*-
"""逐步截图：复用任务 MCP 会话采集，平台保存图片并按运行隔离读取。"""
import asyncio
import base64
import hashlib
import json
import re
import struct
import tempfile
import time
import uuid
import zlib
from pathlib import Path

from aiui.common import mcp_runtime
from aiui.common.paths import RUNTIME_DIR, SCREENSHOTS_DIR

MAX_IMAGE_BYTES = 8 * 1024 * 1024
SCREENSHOT_TOOLS = ('screenshot', 'browser_take_screenshot', 'playwright_screenshot')
IMAGE_TYPES = {'image/png': ('png', b'\x89PNG\r\n\x1a\n'),
               'image/jpeg': ('jpg', b'\xff\xd8\xff')}
FILE_RE = re.compile(r'^[0-9a-f]{32}\.(png|jpg)$')


def _valid_structure(raw, mime):
    """拒绝截断文件；无需引入图片库即可核对 PNG 块/CRC 和 JPEG 头部结构。"""
    if mime == 'image/png':
        offset, header, pixels = 8, False, False
        while offset + 12 <= len(raw):
            size = struct.unpack_from('>I', raw, offset)[0]
            end = offset + 12 + size
            if end > len(raw):
                return False
            kind = raw[offset + 4:offset + 8]
            crc = struct.unpack_from('>I', raw, end - 4)[0]
            if zlib.crc32(raw[offset + 4:end - 4]) != crc:
                return False
            if not header:
                if kind != b'IHDR' or size != 13:
                    return False
                width, height = struct.unpack_from('>II', raw, offset + 8)
                if not width or not height:
                    return False
                header = True
            if kind == b'IDAT' and size:
                pixels = True
            if kind == b'IEND':
                return header and pixels and size == 0 and end == len(raw)
            offset = end
        return False
    if not raw.endswith(b'\xff\xd9'):
        return False
    offset, frame = 2, False
    while offset + 4 <= len(raw):
        if raw[offset] != 0xff:
            return False
        while offset < len(raw) and raw[offset] == 0xff:
            offset += 1
        if offset + 3 > len(raw):
            return False
        marker = raw[offset]
        offset += 1
        size = int.from_bytes(raw[offset:offset + 2], 'big')
        if size < 2 or offset + size > len(raw):
            return False
        if marker in (0xc0, 0xc1, 0xc2):
            if size < 8 or not all(struct.unpack_from('>HH', raw, offset + 3)):
                return False
            frame = True
        if marker == 0xda:
            return frame and offset + size < len(raw) - 2
        offset += size
    return False


def _decode(data, mime):
    if mime not in IMAGE_TYPES:
        raise ValueError('截图格式必须是 PNG 或 JPEG')
    if not isinstance(data, str) or len(data) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError('截图为空或超过 8 MiB 限制')
    raw = base64.b64decode(data, validate=True)
    if (len(raw) > MAX_IMAGE_BYTES or not raw.startswith(IMAGE_TYPES[mime][1])
            or not _valid_structure(raw, mime)):
        raise ValueError('截图内容无效或超过 8 MiB 限制')
    return raw


async def capture_step(session, tools, timeout=5.0):
    """返回可随任务结果传输的图片；采集错误只作证据缺失，不影响步骤判定。"""
    names = {t.name for t in tools}
    tool = next((name for name in SCREENSHOT_TOOLS if name in names), None)
    if not tool:
        return {'error': '当前 MCP 未提供截图工具'}
    if timeout <= 0:
        return {'error': '任务剩余时间不足，未采集截图'}
    args = {}
    if tool == 'browser_take_screenshot':
        args = {'type': 'png', 'fullPage': False}
    elif tool == 'playwright_screenshot':
        args = {'name': 'step', 'storeBase64': True, 'savePng': False, 'fullPage': False}
    directory = None
    deadline = time.monotonic() + min(5.0, timeout)
    try:
        if tool == 'playwright_screenshot':
            # 部分 EA 版本即使 savePng=false 仍写文件，限定到本任务临时目录并在取图后清理。
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            directory = tempfile.TemporaryDirectory(prefix='mcp-shot-', dir=RUNTIME_DIR)
            args['downloadsDir'] = directory.name
        result = await asyncio.wait_for(
            session.call_tool(tool, args, read_timeout_seconds=min(5.0, timeout)),
            timeout=min(5.0, timeout))
        if getattr(result, 'is_error', getattr(result, 'isError', False)):
            raise ValueError(mcp_runtime.content_to_text(result)[:300] or '截图工具执行失败')
        for item in getattr(result, 'content', []) or []:
            if getattr(item, 'type', '') == 'image':
                data = item.data
                mime = getattr(item, 'mime_type', None) or getattr(item, 'mimeType', '')
            elif getattr(item, 'type', '') == 'text':
                try:
                    obj = json.loads(item.text)
                except (ValueError, TypeError):
                    continue
                if not isinstance(obj, dict) or not obj.get('data'):
                    continue
                data, mime = obj['data'], obj.get('mime_type', '')
            else:
                continue
            _decode(data, mime)
            return {'data': data, 'mime_type': mime, 'captured_at': time.time()}
        if tool == 'playwright_screenshot':
            resources = await asyncio.wait_for(
                session.read_resource('screenshot://step'),
                timeout=max(0.001, deadline - time.monotonic()))
            for item in resources.contents:
                data = getattr(item, 'blob', None)
                mime = getattr(item, 'mime_type', None) or getattr(item, 'mimeType', '')
                if data:
                    _decode(data, mime)
                    return {'data': data, 'mime_type': mime, 'captured_at': time.time()}
        raise ValueError('截图工具未返回图片内容')
    except Exception as exc:
        return {'error': '截图采集失败: ' + mcp_runtime.describe_exc(exc)[:400]}
    finally:
        if directory:
            try:
                directory.cleanup()
            except OSError:
                pass  # Windows 文件仍被 MCP 占用时保留临时文件，不能改变任务结果


def _image_path(task_id, filename, root):
    if not isinstance(filename, str) or not FILE_RE.fullmatch(filename):
        raise ValueError('截图文件名无效')
    root = Path(root).resolve()
    task_dir = hashlib.sha256(str(task_id).encode('utf-8')).hexdigest()
    path = (root / task_dir / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError('截图路径无效')
    return path


def persist_steps(task_id, result, root=None):
    """剥离结果里的 base64，只持久化平台生成的图片引用；不信任 Agent 的文件路径。"""
    root = SCREENSHOTS_DIR if root is None else root
    for step in result.get('steps') or []:
        image = step.get('screenshot')
        if image is None:
            continue
        try:
            if not isinstance(image, dict):
                raise ValueError('截图数据格式无效')
            if image.get('error'):
                step['screenshot'] = {'error': str(image['error'])[:500]}
                continue
            mime = image.get('mime_type')
            raw = _decode(image.get('data'), mime)
            filename = uuid.uuid4().hex + '.' + IMAGE_TYPES[mime][0]
            path = _image_path(task_id, filename, root)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
            step['screenshot'] = {'file': filename, 'mime_type': mime,
                                  'captured_at': image.get('captured_at')}
        except Exception as exc:
            step['screenshot'] = {'error': '截图保存失败: ' + mcp_runtime.describe_exc(exc)[:400]}


def read_image(task_id, filename, root=None):
    root = SCREENSHOTS_DIR if root is None else root
    path = _image_path(task_id, filename, root)
    mime = 'image/png' if filename.endswith('.png') else 'image/jpeg'
    return path.read_bytes(), mime
