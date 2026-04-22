# src/memory_agent_tool

## 模块职责

- `cli.py` — CLI 入口，所有子命令定义
- `app.py` — FastAPI 应用工厂
- `config.py` — TrustConfig / ScoringConfig / AppSettings
- `logging.py` — 结构化日志（文本/JSON 双模式）
- `models.py` — Pydantic 请求/响应模型
- `database.py` — SQLite 封装（含 async 方法）
- `scoring.py` — 可插拔召回评分器
- `providers.py` — Provider 管理器 + 3 个内置 Provider
- `resolver.py` — ProjectKey 解析
- `rules.py` — AGENTS.md 规则加载与重叠检测
- `mcp.py` — CodexMCPServer 工具调用分发
- `mcp_server.py` — MCP stdio 协议服务
- `gateway.py` — 客户端适配器（Copilot/Trae）
- `copilot_acp.py` — Copilot ACP 协议处理
- `e2e.py` — 本地 E2E 测试流程

## services/ 包

- `container.py` — AppContainer 依赖注入
- `project_service.py` — 项目注册与别名
- `conflict_service.py` — 冲突检测与反馈
- `memory_service.py` — 记忆摄入与分类
- `session_service.py` — 会话管理与搜索
- `skill_service.py` — Skill 晋升与刷新
- `maintenance_service.py` — 过期审查与合并
- `retrieval_service.py` — 召回管线
- `status_service.py` — 状态报告
- `utils.py` — 共享工具函数

## 编码规范

- 所有返回值使用 Pydantic 模型，不使用 `dict[str, Any]`。
- 日志使用 `logging.py` 的 `get_logger(name)` 获取。
- 信任分参数从 `TrustConfig` 读取，不硬编码。
- 召回评分使用 `scoring.py` 的 `create_scorer(strategy)` 创建。
