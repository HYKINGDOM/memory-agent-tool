# 客户端接入使用文档

## 前置条件

- 本机安装 `copilot`
- 本机安装 `trae`
- 已安装项目依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Codex MCP

启动独立 MCP server：

```bash
./.venv/bin/memory-agent-tool mcp serve
```

## Copilot

执行真实 recall/feedback 端到端：

```bash
./.venv/bin/memory-agent-tool client copilot e2e
```

## Trae

只挂载 MCP server：

```bash
./.venv/bin/memory-agent-tool client trae mount
```

执行真实 chat + recall/feedback 端到端：

```bash
./.venv/bin/memory-agent-tool client trae chat-e2e
```

## 统一验收报告

JSON 导出：

```bash
./.venv/bin/memory-agent-tool client report acceptance --format json
```

Markdown 导出：

```bash
./.venv/bin/memory-agent-tool client report acceptance --format markdown
```

## 查看状态

```bash
./.venv/bin/memory-agent-tool report status
```

状态报告中会包含最近一次真实客户端验收结果。
