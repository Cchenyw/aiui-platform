"""远程执行协议的公共常量与严格信封校验。"""
import re

VERSION = 1
MAX_BODY_BYTES = 64 * 1024 * 1024
HEARTBEAT_INTERVAL_S = 3
ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')


def validate_envelope(node_id, body):
    if not isinstance(body, dict):
        raise ValueError('请求体必须是 JSON 对象')
    if type(body.get('version')) is not int or body['version'] != VERSION:
        raise ValueError('不支持的协议版本')
    if body.get('node_id') != node_id:
        raise ValueError('节点身份不匹配')
    for value in (node_id, body.get('instance_id')):
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise ValueError('节点或实例 ID 无效')
    return body
