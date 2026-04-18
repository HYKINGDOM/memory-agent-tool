# memory-agent-tool

## 概览

- 这是一个本地运行的项目级记忆共享平台。
- 关键入口：
  - `src/memory_agent_tool/cli.py`
  - `src/memory_agent_tool/services.py`
  - `src/memory_agent_tool/gateway.py`
  - `src/memory_agent_tool/mcp_server.py`

## 高频命令

- `./.venv/bin/python -m pytest -q`
- `./.venv/bin/memory-agent-tool test e2e-local`
- `./.venv/bin/memory-agent-tool report status`
- `./.venv/bin/memory-agent-tool client report acceptance --format json`
- `./.venv/bin/memory-agent-tool client report acceptance --format markdown`

## 本地硬规则

- 真实客户端接入优先走已验证路径：`Copilot ACP`、`Trae CLI --add-mcp`。
- `Codex MCP` 的真实入口是 `memory-agent-tool mcp serve`，不是进程内 `CodexMCPServer`。
- `Trae chat` 当前按“真实打开 chat + 平台 recall/feedback 闭环”验收，不把它描述成同步返回文本的脚本 API。
- 真实客户端验收结果需要写回 `test_runs(run_type='client-acceptance')`，并在 `/status/report` 中可见。
- 长期知识写 `docs/`，跨任务方法写 skill，`AGENTS.md` 只保留短规则。

## 目录优先级

- 更近目录的 `AGENTS.md` 优先于根文件。
- 当前仓库已下沉到：
  - `src/memory_agent_tool/AGENTS.md`
  - `tests/AGENTS.md`
  - `docs/AGENTS.md`

## 验证要求

- 改真实客户端链路时，至少跑相关定向测试。
- 改状态报告、CLI 或存储时，补跑 `pytest -q`、`test e2e-local` 和 `report status`。
