# tests

## 测试结构

- `conftest.py` — 共享 fixture（container, client, settings）
- `test_project_memory_tool.py` — 核心功能测试
- `test_memory_lifecycle.py` — 记忆生命周期测试
- `test_retrieval_ranking.py` — 召回排序测试
- `test_provider_runtime.py` — Provider 运行时测试
- `test_skill_learning_loop.py` — Skill 学习循环测试
- `test_project_key_compatibility.py` — 项目键兼容性测试
- `test_platform_runtime.py` — 平台运行时测试
- `test_database_migrations.py` — 数据库迁移测试
- `test_client_and_mcp_contracts.py` — 客户端与 MCP 契约测试
- `test_codex_mcp_stdio_server.py` — MCP stdio 服务测试
- `test_copilot_real_adapter.py` — Copilot 真实适配器测试（需外部 CLI）
- `test_trae_real_adapter.py` — Trae 真实适配器测试（需外部 CLI）
- `test_real_client_commands.py` — 真实客户端命令测试（需外部 CLI）
- `test_client_acceptance_report.py` — 客户端验收报告测试（需外部 CLI）
- `test_acceptance_export_and_status.py` — 验收导出与状态测试（需外部 CLI）
- `test_provider_config_persistence.py` — Provider 配置持久化测试
- `test_cli_delivery_runtime.py` — CLI 交付运行时测试
- `test_trae_cli_flow.py` — Trae CLI 流程测试

## skipif 标记

- 依赖外部 CLI（copilot/trae）的测试已加 `@pytest.mark.skipif` 标记。
- CI 中自动跳过这些测试，本地安装对应 CLI 后可运行。

## 运行命令

- 全量测试：`python -m pytest tests/ -q`
- 仅核心测试（跳过外部依赖）：`python -m pytest tests/ -q -m "not skipif"`
