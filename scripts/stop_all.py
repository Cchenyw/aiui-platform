# -*- coding: utf-8 -*-
"""
一键清理：按端口杀掉 demo 相关进程（Windows / Linux / macOS 通用）
用法：python scripts/stop_all.py
"""
import os
import subprocess
import sys
import time

PORTS = (8081, 8000, 8001, 8002, 8099)


def pids_on(port):
    pids = set()
    try:
        if os.name == "nt":
            # 中文 Windows 的 netstat 输出是 OEM 代码页（GBK），不能按 UTF-8 解码
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                 encoding="oem", errors="replace").stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line.upper():
                    pids.add(line.split()[-1])
        else:
            out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                if f":{port} " in line and "pid=" in line:
                    for seg in line.split("pid=")[1:]:
                        pids.add(seg.split(",")[0].strip())
    except Exception as e:
        print(f"  查询端口 {port} 失败: {e}")
    return {p for p in pids if p.isdigit()}


def kill(pid):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        else:
            os.kill(int(pid), 15)
        return True
    except Exception:
        return False


def alive(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def main():
    killed = 0
    for p in PORTS:
        for pid in pids_on(p):
            if kill(pid):
                print(f"  端口 {p}: 已结束进程 {pid}")
                killed += 1
    time.sleep(0.8)
    left = [p for p in PORTS if alive(p)]
    print(f"共结束 {killed} 个进程")
    print("端口状态: " + "  ".join(f"{p}={'仍占用' if p in left else '已释放'}" for p in PORTS))
    if left:
        print("⚠ 仍有端口占用，请用任务管理器 / ps 手工确认")
        sys.exit(1)


if __name__ == "__main__":
    main()
