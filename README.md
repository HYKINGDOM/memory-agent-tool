# memory-agent-tool

本地运行的项目级记忆共享平台，为 AI 编码助手（Copilot、Trae、Codex 等）提供跨会话的持久记忆能力。

## 核心特性

- **项目级记忆管理** — 自动摄入、分类、信任评分、冲突检测
- **多客户端支持** — Copilot ACP、Trae CLI、Codex MCP 等真实客户端接入
- **可插拔召回算法** — 关键词匹配 / TF-IDF / 语义向量 / 组合评分
- **Skill 晋升** — 高信任度记忆自动晋升为项目 Skill 文件
- **结构化日志** — 文本 / JSON 双模式，支持结构化字段
- **异步支持** — Database 提供 async 方法，适配高并发场景
- **Pydantic 模型** — 全链路类型安全，替代 `dict[str, Any]`
- **配置化信任分** — 所有魔数提取为 `TrustConfig`，支持环境变量覆盖

## 快速开始

### 使用 uv（推荐）

```bash
uv sync
uv run memory-agent-tool serve
```

### 使用 pip

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
memory-agent-tool serve
```

默认在当前项目目录下创建 `./.memory-agent-tool/`，包含：

- `state.db` — SQLite 数据库（含 FTS5 全文检索）
- `skills/` — 晋升后的 Skill 文件
- `runtime/` — 运行时数据

## 架构

```
src/memory_agent_tool/
├── cli.py              # CLI 入口
├── app.py              # FastAPI 应用工厂
├── config.py           # TrustConfig / ScoringConfig / AppSettings
├── logging.py          # 结构化日志系统
├── models.py           # Pydantic 请求/响应模型
├── database.py         # SQLite 封装（含 async 方法）
├── scoring.py          # 可插拔召回评分器
├── providers.py        # Provider 管理器 + 3 个内置 Provider
├── resolver.py         # ProjectKey 解析
├── rules.py            # AGENTS.md 规则加载与重叠检测
├── mcp.py              # CodexMCPServer 工具调用分发
├── mcp_server.py       # MCP stdio 协议服务
├── gateway.py          # 客户端适配器（Copilot/Trae）
├── copilot_acp.py      # Copilot ACP 协议处理
├── e2e.py              # 本地 E2E 测试流程
└── services/           # 业务服务包
    ├── container.py        # AppContainer 依赖注入
    ├── project_service.py  # 项目注册与别名
    ├── conflict_service.py # 冲突检测与反馈
    ├── memory_service.py   # 记忆摄入与分类
    ├── session_service.py  # 会话管理与搜索
    ├── skill_service.py    # Skill 晋升与刷新
    ├── maintenance_service.py # 过期审查与合并
    ├── retrieval_service.py  # 召回管线
    ├── status_service.py   # 状态报告
    └── utils.py            # 共享工具函数
```

## HTTP API

服务启动后默认监听 `127.0.0.1:8765`。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | POST | 健康检查 |
| `/projects/resolve` | POST | 解析项目 |
| `/sessions/start` | POST | 启动会话 |
| `/sessions/{id}/events` | POST | 追加事件 |
| `/sessions/{id}/end` | POST | 结束会话 |
| `/memory/ingest` | POST | 直接摄入记忆 |
| `/memory/recall` | POST | 召回记忆 |
| `/memory/feedback` | POST | 提交反馈 |
| `/skills/promote` | POST | 晋升 Skill |
| `/skills/{id}/feedback` | POST | Skill 反馈 |
| `/skills/{id}/refresh` | POST | 刷新 Skill |
| `/status/report` | GET | 状态报告 |
| `/providers/status` | GET | Provider 状态 |
| `/providers/observability` | GET | Provider 可观测性 |
| `/providers/config` | POST | 配置 Provider |
| `/projects/aliases` | POST | 注册项目别名 |
| `/maintenance/review-stale/{key}` | POST | 审查过期记忆 |
| `/maintenance/consolidate/{key}` | POST | 合并项目记忆 |
| `/summaries/rebuild` | POST | 重建会话摘要 |
| `/test/e2e-local` | POST | 运行本地 E2E |

## CLI 命令

```bash
memory-agent-tool serve                                    # 启动 HTTP 服务
memory-agent-tool demo seed                                # 种子演示数据
memory-agent-tool demo recall                              # 召回演示数据
memory-agent-tool report status                            # 状态报告
memory-agent-tool report providers                         # Provider 可观测性
memory-agent-tool report project-scope <key>               # 项目范围
memory-agent-tool test e2e-local                           # 本地 E2E 测试
memory-agent-tool test pytest                              # 运行 pytest
memory-agent-tool mcp serve                                # 启动 MCP stdio 服务
memory-agent-tool client copilot e2e                       # Copilot E2E
memory-agent-tool client trae mount                        # Trae 挂载
memory-agent-tool client trae chat-e2e                     # Trae Chat E2E
memory-agent-tool client report acceptance --format json   # 验收报告 (JSON)
memory-agent-tool client report acceptance --format markdown # 验收报告 (Markdown)
memory-agent-tool maintenance review-stale <key>           # 审查过期
memory-agent-tool maintenance consolidate <key>            # 合并记忆
memory-agent-tool maintenance rebuild-summaries [key]      # 重建摘要
memory-agent-tool skills feedback <id> --helpful           # Skill 反馈
memory-agent-tool skills refresh <id>                      # 刷新 Skill
memory-agent-tool projects alias <alias> <canonical>       # 注册别名
memory-agent-tool providers config [key] [value]           # Provider 配置
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MEMORY_AGENT_TOOL_HOME` | 当前目录 | 项目根目录 |
| `MEMORY_AGENT_TOOL_HOST` | `127.0.0.1` | 服务监听地址 |
| `MEMORY_AGENT_TOOL_PORT` | `8765` | 服务监听端口 |
| `MEMORY_AGENT_TOOL_LOG_LEVEL` | `INFO` | 日志级别 |
| `MEMORY_AGENT_TOOL_LOG_JSON` | `false` | JSON 日志模式 |
| `MEMORY_TRUST_POSITIVE_DELTA` | `0.15` | 正反馈信任增量 |
| `MEMORY_TRUST_NEGATIVE_DELTA` | `-0.20` | 负反馈信任减量 |
| `MEMORY_TRUST_INITIAL` | `0.6` | 初始信任分 |
| `MEMORY_TRUST_AUTO_PROMOTE` | `0.75` | 自动晋升阈值 |
| `MEMORY_TRUST_DEGRADE` | `0.4` | 降级阈值 |
| `MEMORY_TRUST_LOW` | `0.2` | 低信任阈值 |
| `MEMORY_STALE_FRESHNESS` | `0.2` | 过期新鲜度阈值 |
| `MEMORY_STALE_TRUST` | `0.55` | 过期信任阈值 |
| `MEMORY_SCORING_STRATEGY` | `composite` | 召回评分策略 |

### 召回评分策略

| 策略 | 说明 |
|------|------|
| `keyword` | 纯关键词重叠匹配 |
| `tfidf` | TF-IDF 加权匹配 |
| `semantic` | 语义向量匹配（需 sentence-transformers） |
| `composite` | 组合评分（默认：60% keyword + 40% tfidf） |

## 真实客户端

已支持的真实客户端接入：

- **Copilot ACP** — 通过 ACP 协议接入
- **Trae CLI** — 通过 `--add-mcp` 接入

## 验证

```bash
python -m pytest tests/ -q
memory-agent-tool test e2e-local
memory-agent-tool report status
```

依赖外部 CLI 的测试已加 `@pytest.mark.skipif` 标记，CI 中自动跳过。

## 依赖

- Python >= 3.11
- FastAPI + Uvicorn
- Pydantic >= 2.0
- SQLite（内置 FTS5）

可选依赖：

- `sentence-transformers` — 语义评分（`semantic` 策略）
- `httpx` — 测试客户端
- `pytest` — 测试框架
