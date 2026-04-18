# src/memory_agent_tool

## Scope

- 这里负责运行时、HTTP API、CLI、SQLite、MCP、真实客户端接入和状态报告。

## Rules

- CLI 行为改动优先落在 `cli.py`，不要把命令逻辑分散到无关模块。
- 状态报告字段变更要同步更新 `models.py`、`services.py` 和相关测试。
- 真实客户端链路优先复用 `gateway.py` 已验证路径，不新增第二套协议实现。
- `Codex MCP` 的独立入口保持在 `mcp_server.py`，不要退回进程内专用实现。
- `record_test_run()` 写回的平台结果要保持 `run_type` 稳定，避免改名造成状态报告失联。

## Do not

- 不要把 `Trae chat` 描述成同步文本 API。
- 不要绕开 `AppContainer` 直接拼装核心服务依赖。
- 不要把长期说明写进源码注释里，改放 `docs/`。
