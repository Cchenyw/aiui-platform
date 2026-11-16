# -*- coding: utf-8 -*-
"""为一个远程执行节点生成私密 token 文件和网关策略；绝不打印 token。"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys


ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def create_credential(node_id, machine_id, allowed_ips, output_dir,
                      mcp_ids=None, max_slots=1, name=None):
    for label, value in (("node_id", node_id), ("machine_id", machine_id)):
        if not ID_RE.fullmatch(value):
            raise ValueError(f"{label} 只能使用 1–64 个字母、数字、点、下划线或短横线")
    addresses = [str(ipaddress.ip_network(ip, strict=False)) for ip in allowed_ips]
    if not addresses:
        raise ValueError("必须填写至少一个执行服务器来源 IP/CIDR")
    if not 1 <= max_slots <= 64:
        raise ValueError("max_slots 必须在 1–64 之间")
    mcp_ids = mcp_ids or ["builtin-browser"]
    if any(not ID_RE.fullmatch(mid) for mid in mcp_ids):
        raise ValueError("MCP ID 格式无效")
    directory = Path(output_dir).resolve()
    policy_path = directory / "gateway-policies.json"
    token_path = directory / f"{node_id}.token"
    policies = {"version": 1, "nodes": {}}
    if policy_path.exists():
        policies = json.loads(policy_path.read_text(encoding="utf-8"))
        if (not isinstance(policies, dict) or policies.get("version") != 1
                or not isinstance(policies.get("nodes"), dict)):
            raise ValueError("已有网关策略格式无效；不会覆盖")
    if node_id in policies["nodes"] or token_path.exists():
        raise ValueError(f"节点 {node_id} 的策略或 token 已存在；不会覆盖")
    token = secrets.token_urlsafe(32)
    policies["nodes"][node_id] = {
        "name": name or node_id, "machine_id": machine_id,
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "allowed_ips": addresses, "mcp_ids": list(dict.fromkeys(mcp_ids)),
        "max_slots": max_slots,
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".policy-{secrets.token_hex(8)}.tmp"
    token_created = False
    try:
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        token_created = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(token + "\n")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(policies, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, policy_path)
    except Exception:
        if token_created:
            token_path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return policy_path, token_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--allowed-ip", action="append", required=True)
    parser.add_argument("--mcp-id", action="append")
    parser.add_argument("--max-slots", type=int, default=1)
    parser.add_argument("--output-dir", default="runtime/remote-access")
    args = parser.parse_args()
    try:
        policy, token = create_credential(
            args.node_id, args.machine_id, args.allowed_ip, args.output_dir,
            args.mcp_id, args.max_slots, args.name)
    except (ValueError, OSError) as exc:
        print(f"凭证生成失败：{exc}", file=sys.stderr)
        return 1
    print(f"网关策略：{policy}")
    print(f"节点私密 token 文件：{token}")
    print("请通过受控方式将 token 文件交付对应服务器；限制目录 ACL。不要提交到 Git。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
