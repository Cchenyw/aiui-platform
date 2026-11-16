# Step Screenshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 在运行详情逐步展示执行后的页面截图，包括失败步骤。

**Architecture:** 共用截图适配器在任务现有 MCP 会话中取图。Agent 将图片随步骤回传，平台剥离 base64 并持久化文件，HTTP 路由按运行引用提供图片，服务端页面展示缩略图和原图链接。

**Tech Stack:** Python 标准库、MCP、Playwright、SQLite、HTML/CSS。

**Spec:** docs/superpowers/specs/2026-11-10-step-screenshots-design.md

## Global Constraints

- 不新增依赖，中文界面；MCP stdout 仅用于协议。
- 图片不进入模型输入；截图失败不能改变步骤结果。
- 单张最多 8 MiB；采集最多 5 秒并受任务剩余时间约束。
- 兼容无截图的旧运行；图片只能从该运行记录引用中读取。

## Review Focus

- 外部 MCP 返回图片内容而不是本地路径；三种工具参数各异。
- 错误步骤、任务剩余时间不足时仍保留既有 trace。
- 重试不能覆盖已有图片；结果重复回传应忽略。
- 路径穿越、异常 base64 和过大图片不可落盘或公开。
- 历史运行、截图失败、AI finish 的界面应明确，不显示损坏图片。

## Task 1: 截图采集与文件存储

**Files:** aiui/common/screenshots.py (新增), aiui/common/paths.py, aiui/toolservers/builtin_browser.py, tests/test_screenshots.py (新增)。

**Interfaces:** `capture_step(session, tools, timeout=5)` 返回 `{data,mime_type,captured_at}` 或 `{error}`；`persist_steps(task_id, result)` 原地把步骤图片转换为 `{file,mime_type,captured_at}`；`read_image(task_id, filename)` 返回 `(bytes,mime)`。

- [x] 写入并运行测试，确认缺少实现时失败。
- [x] 实现三种 MCP 参数适配、图片内容提取、限时和图片大小/签名校验。
- [x] 实现内置 screenshot、运行隔离和安全文件读取。
- [x] 运行 `python -m unittest discover -s tests -v`，确认采集、异常和存储测试通过。

## Task 2: 步骤关联及运行页面

**Files:** aiui/agent/runner.py, aiui/agent/ai_executor.py, aiui/platform/server.py, aiui/platform/ui.py, tests/test_step_screenshots.py (新增), README.md, CLAUDE.md。

**Interfaces:** 消费 Task 1 的采集/存储 API；步骤 screenshot 字段；`GET /api/runs/{id}/screenshots/{file}`。

- [x] 先写失败步骤的关联测试、HTTP 读取/拒绝非法引用测试和页面预览测试，运行确认失败。
- [x] 两种执行模式在实际工具步骤后截图；平台在接受结果后持久化图片。
- [x] 增加图片路由、缩略图/原图链接和无截图占位，更新文档。
- [x] 运行完整单元测试、真实 scripted / Mock AI 演示，检查通过和故意失败步骤的图片。
- [x] 浏览器检查布局和图片可读性，独立代码审查后处理重要问题。

## Progress

- 方案由本轮用户需求确定；实现中采用上述默认技术选择。
- 在当前项目的 codex/step-screenshots 功能分支实施，保留工作区结果供用户审阅，不自动提交。
- Task 1 complete: 三种 MCP 适配、EA 资源读取、限时、文件隔离和安全读取完成；首轮采集/存储测试 RED→GREEN。
- Task 2 complete: 两种执行器关联、平台图片路由和页面预览完成；真实浏览器六条用例共采集 23 张图片，原图链接已验证。
- Review fixed: 整体超时前先登记在飞步骤，保留已采集证据和未能截图原因；两种执行器超时回归 RED→GREEN。
- Review fixed: 校验 PNG 块/CRC/结束标记和 JPEG 头部结构，拒绝仅有签名的截断图片；损坏图片回归 RED→GREEN。
- Review fixed: 增加 dispatch_id 防止同机重试后的旧结果覆盖当前执行证据，保留未实现该字段的外部 Agent 兼容路径；同机重派发旧结果回归 RED→GREEN。
- Ruling: 图片用作人工证据，截图工具从 AI 规划目录排除；不引入视觉模型输入。
- Ruling: 不自动重启用户当前仍在运行的服务；验证使用独立端口/数据库/审计/Agent 环境，保持用户 LLM 配置原样。
- Final verification: 16/16 unittest 通过；独立浏览器回归在三种 MCP、两种执行模式和失败重试中保存并读取 23 张最终步骤图片，页面缩略图和原图弹出验证通过。结果记录：runtime/screenshots-check-245cd872/runs.json；效果图：同目录 run-detail.png。
- 2026-11-11：用户要求提交当前版本；提交范围包含截图、执行节点界面命名、交互浏览器配置及文档。后续 MCP 全局并发配额和会话池仍处于设计阶段，未实现。
