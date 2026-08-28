# 服务端开发

> 状态：现行规范｜适用：Python 服务与测试｜核验：2026-08-28｜依据：PR #14

所有命令从仓库根目录运行，`.env`、`.venv` 和相对数据路径仍以该目录为基准。

```sh
uv venv
uv pip install -e '.[dev]'
.venv/bin/python scripts/run_tests.py
.venv/bin/ruff check murmur tests scripts
```

测试是独立脚本与 unittest 混合，统一 runner 逐个执行；不要以 unittest discover 的结果替代全量验收。
测试使用合成数据；本地 HTTP 测试需要回环端口权限。不用生产数据库作为 fixture，不调用真实模型做普通 CI。
Python 最低 3.11，CI 同时覆盖 3.14；任一失败都不能合并。

`murmur app-api` 与 `murmur app-worker` 为正式服务入口，`murmur web` 为管理员面板。
配置以根目录 `.env.example` 为准，Debug 与生产配置显式隔离。
模型默认值是仓库配方，不代表当前提供商永远支持，也不证明部署主机用了相同值。

测试 Bot 见 [部署文档](../operations/deployment.md) 的隔离流程；根目录 `run.sh` 仅是本机测试通道监督器。
