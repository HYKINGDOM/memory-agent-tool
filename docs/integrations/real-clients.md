# 真实客户端接入说明

## 概述

当前项目已经交付两个真实客户端接入：

- `Copilot`
- `Trae`

这两个接入都以 `project_key` 为统一边界，共享本项目的本地 `project-memory` MCP server 与平台侧 SQLite 持久化能力。

## 依赖

运行这些真实接入前，需要本机具备：

- Python 3.11+
- 已安装本项目依赖：`pip install -e ".[dev]"`
- `copilot` CLI
- `trae` CLI

项目内关键入口：

- `memory-agent-tool mcp serve`
- `memory-agent-tool client copilot e2e`
- `memory-agent-tool client trae mount`
- `memory-agent-tool client trae chat-e2e`
- `memory-agent-tool client report acceptance`

## Copilot

### 真实边界

`Copilot` 当前走真实 `ACP` 链路：

- 真实启动 `copilot --acp --stdio`
- 真实完成 `initialize`
- 真实完成 `session/new`
- 可真实挂载本项目 `project-memory` MCP server

### 已验证链路

已经验证：

- ACP 握手
- MCP 挂载
- 平台 session 写入
- recall
- helpful feedback

对应命令：

```bash
memory-agent-tool client copilot e2e
```

### 注意事项

`Copilot` 在自由 prompt 下不保证始终优先选择 `project-memory` tool，因此当前硬验收以“真实握载 + 平台 recall/feedback 端到端命令”为准。

## Trae

### 真实边界

`Trae` 当前走真实 CLI 接入：

- 真实调用 `trae --add-mcp`
- 真实把 `project-memory` MCP server 挂到 Trae
- 真实调用 `trae chat`

### 已验证链路

已经验证：

- MCP 挂载成功
- 可打开真实 Trae chat 会话
- 平台 session 写入
- recall
- helpful feedback

对应命令：

```bash
memory-agent-tool client trae mount
memory-agent-tool client trae chat-e2e
```

### 注意事项

当前 `Trae chat` 更接近“打开真实聊天窗口”的 CLI，而不是同步返回模型文本输出的脚本接口。因此当前验收标准定义为：

- 真实 CLI 挂载成功
- 真实 chat 会话成功打开
- 平台侧 recall/feedback 流程跑通

这已经属于真实接入，但还不是 Trae 内部私有 agent 协议级桥接。

## 统一验收

统一真实客户端验收报告命令：

```bash
memory-agent-tool client report acceptance
```

报告会覆盖：

- Copilot 握手与挂载状态
- Trae 挂载与 chat 打开状态
- 当前已验证链路摘要
