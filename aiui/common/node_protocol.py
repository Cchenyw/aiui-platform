"""远程执行协议的公共常量与严格信封校验。

注册/心跳允许携带可选的运行时与能力信息（runtime、slots_recommended、
mcp_capabilities）。这些字段只用于展示和运维，不参与调度授权；平台仍以
节点策略（token、CIDR、slots、MCP 白名单）为准。所有可选字段都经过白名单
和长度截断，绝不接收 token、API key、Profile 原文或完整环境变量。
"""
import re

VERSION = 1
MAX_BODY_BYTES = 64 * 1024 * 1024
HEARTBEAT_INTERVAL_S = 3
ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')

MAX_RUNTIME_TEXT = 200
MAX_CAPABILITIES = 32
MAX_TOOL_NAMES = 128
MAX_CAPABILITY_TEXT = 500

_RUNTIME_KEYS = (
    'agent_version', 'protocol', 'os', 'os_version', 'arch', 'python',
    'node', 'npm', 'playwright', 'chrome', 'chrome_detected',
    'chromium_configured', 'account', 'scheduled_task', 'started_at',
)
_RUNTIME_LLM_KEYS = ('configured', 'provider', 'model', 'base_url')
_RUNTIME_RESOURCE_KEYS = ('cpu_cores', 'memory_mb', 'disk_free_mb')


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


def _text(value, limit=MAX_RUNTIME_TEXT):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def sanitize_runtime(value):
    """白名单化可选的 runtime 展示信息；未知字段、key、env 一律丢弃。"""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in _RUNTIME_KEYS:
        if key not in value:
            continue
        if key in ('chrome_detected', 'chromium_configured'):
            if isinstance(value[key], bool):
                out[key] = value[key]
        elif key == 'started_at':
            number = _number(value[key])
            if number is not None:
                out[key] = number
        else:
            text = _text(value[key])
            if text is not None:
                out[key] = text
    llm = value.get('llm')
    if isinstance(llm, dict):
        safe_llm = {}
        for key in _RUNTIME_LLM_KEYS:
            if key not in llm:
                continue
            if key == 'configured':
                if isinstance(llm[key], bool):
                    safe_llm[key] = llm[key]
            else:
                text = _text(llm[key])
                if text is not None:
                    safe_llm[key] = text
        if safe_llm:
            out['llm'] = safe_llm
    resources = value.get('resources')
    if isinstance(resources, dict):
        safe_resources = {}
        for key in _RUNTIME_RESOURCE_KEYS:
            number = _number(resources.get(key))
            if number is not None and number >= 0:
                safe_resources[key] = number
        if safe_resources:
            out['resources'] = safe_resources
    return out


def sanitize_slots_recommended(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= 128:
        return None
    return value


def sanitize_mcp_capabilities(value):
    """白名单化执行机上报的 MCP 探测摘要；不接收 MCP 命令/环境变量。"""
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:MAX_CAPABILITIES]:
        if not isinstance(item, dict):
            continue
        mcp_id = item.get('id')
        if not isinstance(mcp_id, str) or not ID_RE.fullmatch(mcp_id):
            continue
        cap = {'id': mcp_id}
        if isinstance(item.get('available'), bool):
            cap['available'] = item['available']
        tool_count = _number(item.get('tool_count'))
        if tool_count is not None and 0 <= tool_count <= 100000:
            cap['tool_count'] = int(tool_count)
        tools = item.get('tools')
        if isinstance(tools, list):
            cap['tools'] = [x for x in tools[:MAX_TOOL_NAMES]
                            if isinstance(x, str) and x][:MAX_TOOL_NAMES]
        if isinstance(item.get('profile_capable'), bool):
            cap['profile_capable'] = item['profile_capable']
        version = _text(item.get('version'), MAX_CAPABILITY_TEXT)
        if version is not None:
            cap['version'] = version
        error = _text(item.get('error'), MAX_CAPABILITY_TEXT)
        if error is not None:
            cap['error'] = error
        probed_at = _number(item.get('probed_at'))
        if probed_at is not None:
            cap['probed_at'] = probed_at
        out.append(cap)
    return out


def sanitize_registration_extras(body):
    """注册/心跳可选的展示字段集合；旧节点不传则返回空字典。"""
    if not isinstance(body, dict):
        return {}
    out = {}
    runtime = sanitize_runtime(body.get('runtime'))
    if runtime:
        out['runtime'] = runtime
    recommended = sanitize_slots_recommended(body.get('slots_recommended'))
    if recommended is not None:
        out['slots_recommended'] = recommended
    capabilities = sanitize_mcp_capabilities(body.get('mcp_capabilities'))
    if capabilities:
        out['mcp_capabilities'] = capabilities
    return out
