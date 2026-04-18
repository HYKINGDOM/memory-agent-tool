# 项目交付文档

## 概述

`memory-agent-tool` 当前已经从纯原型推进到“单机长期运行的项目级记忆共享平台”基础形态，关键交付点包括：

- 本地 FastAPI 服务
- SQLite/WAL/FTS5 持久层
- 规则优先的 recall 组装
- skills 提升与反馈闭环
- 独立 `Codex MCP` server
- `Copilot ACP` 真实接入
- `Trae CLI` 真实接入

## 已验证的真实路径

### Copilot ACP

- 真实启动：`copilot --acp --stdio`
- 真实握手：`initialize`
- 真实会话：`session/new`
- 真实 MCP 挂载：`project-memory`
- 平台 recall / feedback 端到端命令已跑通

### Trae CLI

- 真实启动：`trae --add-mcp ... --new-window`
- 真实挂载 `project-memory` MCP server
- 真实启动 `trae chat`
- 平台 recall / feedback 端到端命令已跑通

### Codex MCP

- 真实入口：`memory-agent-tool mcp serve`
- 支持 `initialize`
- 支持 `tools/list`
- 支持 `tools/call`

## 当前验收边界

- `Copilot ACP`：按真实协议链路验收
- `Trae CLI`：按真实挂载与真实 chat 打开能力验收
- `Trae chat` 当前不是同步文本 API，不对外宣称脚本化即时回答能力

## 状态回写

以下结果会回写到平台：

- `e2e-local`
- `client-acceptance`

其中 `client-acceptance` 会出现在 `/status/report` 的 `recent_client_acceptance_result` 字段。
