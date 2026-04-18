# tests

## Scope

- 这里负责平台单测、集成测试、真实客户端命令测试和回归验证。

## Rules

- 新功能先补失败测试，再写实现。
- 能用本地 fixture 隔离的测试，统一使用 `conftest.py` 里的 `temp_home/settings/container/client`。
- 真实客户端测试要用 `skipif(shutil.which(...))` 控制环境依赖。
- 改 CLI / 状态报告 / 存储字段时，优先补 `tests/test_acceptance_export_and_status.py`、`tests/test_platform_runtime.py`、`tests/test_real_client_commands.py`。
- 真实命令测试结束后，注意避免残留外部进程影响后续回归。

## Do not

- 不要把真实客户端测试写成假阳性的纯字符串断言。
- 不要让测试依赖当前项目工作目录里的持久状态。
- 不要在测试里静默放宽真实客户端边界定义。
