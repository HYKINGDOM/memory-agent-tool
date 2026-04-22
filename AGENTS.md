# memory-agent-tool

## 概览

- 本地运行的项目级记忆共享平台。
- 关键入口：
  - `src/memory_agent_tool/cli.py` — CLI 入口
  - `src/memory_agent_tool/services/container.py` — 依赖注入容器
  - `src/memory_agent_tool/gateway.py` — 客户端适配器（Copilot/Trae）
  - `src/memory_agent_tool/mcp_server.py` — MCP stdio 服务

## 架构（重构后）

- `services/` 包替代原 `services.py`（~1835 行拆分为 9 个模块）：
  - `container.py` — AppContainer 依赖注入
  - `project_service.py` — ProjectRegistry
  - `conflict_service.py` — ConflictAndFeedbackService
  - `memory_service.py` — ProjectMemoryService
  - `session_service.py` — SessionArchiveService
  - `skill_service.py` — SkillPromotionService
  - `maintenance_service.py` — MemoryMaintenanceService
  - `retrieval_service.py` — RetrievalPipeline
  - `status_service.py` — StatusReporter
  - `utils.py` — 共享工具函数
- `config.py` — TrustConfig / ScoringConfig / AppSettings（信任分魔数已提取为配置）
- `logging.py` — 结构化日志系统（文本/JSON 双模式）
- `scoring.py` — 可插拔召回评分器（KeywordOverlap / TF-IDF / Semantic / Composite）
- `models.py` — Pydantic 模型替代 dict[str, Any]
- `database.py` — 含 async 方法（async_fetchone / async_execute 等）

## 高频命令

- `python -m pytest tests/ -q`
- `memory-agent-tool test e2e-local`
- `memory-agent-tool report status`
- `memory-agent-tool client report acceptance --format json`
- `memory-agent-tool client report acceptance --format markdown`

## 本地硬规则

- 真实客户端接入走已验证路径：`Copilot ACP`、`Trae CLI --add-mcp`。
- `Codex MCP` 的真实入口是 `memory-agent-tool mcp serve`。
- 真实客户端验收结果写回 `test_runs(run_type='client-acceptance')`，在 `/status/report` 可见。
- 长期知识写 `docs/`，跨任务方法写 skill，`AGENTS.md` 只保留短规则。

## 配置

- 信任分参数通过 `TrustConfig` 管理，支持环境变量覆盖（`MEMORY_TRUST_*`）。
- 召回评分策略通过 `ScoringConfig` 管理，支持 `keyword` / `tfidf` / `semantic` / `composite`。
- 日志级别：`MEMORY_AGENT_TOOL_LOG_LEVEL`，JSON 模式：`MEMORY_AGENT_TOOL_LOG_JSON=1`。

## 验证要求

- 改真实客户端链路时，跑相关定向测试。
- 改状态报告、CLI 或存储时，补跑 `pytest -q`、`test e2e-local` 和 `report status`。
- 依赖外部进程的测试已加 `@pytest.mark.skipif` 标记，CI 中自动跳过。
