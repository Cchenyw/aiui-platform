# -*- coding: utf-8 -*-
"""项目路径约定。

目录分工：
  aiui/          源码（可 import 的包）
  scripts/       演示与运维脚本（不作为包导入）
  docs/          静态文档样例
  runtime/       数据库、审计、Agent/MCP 日志（运行时生成，git 忽略）
  artifacts/     生成的图片/HTML/docx（运行时生成，git 忽略）
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_runtime_override = os.environ.get("AIUI_RUNTIME_DIR")
RUNTIME_DIR = (Path(_runtime_override).expanduser().resolve()
               if _runtime_override else ROOT / "runtime")
LOGS_DIR = RUNTIME_DIR / "logs"
SCREENSHOTS_DIR = RUNTIME_DIR / "screenshots"
PROFILES_DIR = RUNTIME_DIR / "profiles"
ARTIFACTS_DIR = ROOT / "artifacts"
DOCS_DIR = ROOT / "docs"


def ensure_runtime_dirs():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def ensure_artifacts_dir():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
