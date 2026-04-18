# memory-agent-tool

项目级记忆系统首版实现，提供：

- 本地 FastAPI 服务
- CLI 调试与演示命令
- SQLite + FTS5 会话归档与检索
- 项目级 pinned memory / conflict / feedback / skill promotion
- 多客户端模拟 E2E
- 当前状态报告

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
memory-agent-tool serve
```

默认会在当前项目目录下创建 `./.memory-agent-tool/`，其中包含：

- `state.db`
- `skills/`
- `runtime/`

## HTTP API

服务启动后默认监听 `127.0.0.1:8765`。

首版接口：

- `POST /health`
- `POST /projects/resolve`
- `POST /sessions/start`
- `POST /sessions/{session_id}/events`
- `POST /memory/ingest`
- `POST /memory/recall`
- `POST /memory/feedback`
- `POST /skills/promote`
- `GET /status/report`
- `POST /test/e2e-local`

## CLI 命令

```bash
memory-agent-tool serve
memory-agent-tool demo seed
memory-agent-tool demo recall
memory-agent-tool report status
memory-agent-tool test e2e-local
memory-agent-tool test pytest
memory-agent-tool mcp serve
memory-agent-tool client copilot e2e
memory-agent-tool client trae mount
memory-agent-tool client trae chat-e2e
memory-agent-tool client report acceptance --format json
memory-agent-tool client report acceptance --format markdown
```

## 真实客户端

当前已支持两个真实客户端接入：

- `Copilot ACP`
- `Trae CLI`

详细边界、依赖和已验证链路见：

- [docs/integrations/real-clients.md](/Users/zc/Documents/memory-agent-tool/docs/integrations/real-clients.md)
- [docs/project/project-delivery.md](/Users/zc/Documents/memory-agent-tool/docs/project/project-delivery.md)
- [docs/usage/client-integration.md](/Users/zc/Documents/memory-agent-tool/docs/usage/client-integration.md)

## 验证

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/memory-agent-tool test e2e-local
./.venv/bin/memory-agent-tool report status
```
