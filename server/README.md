# Murmur server

> 状态：现行规范｜适用：共享 Python 服务、运维面板与测试｜核验：2026-08-28｜依据：PR #14、#16 与目录迁移提交

`murmur/` 保留原 Python 模块组织和 `murmur` CLI；`tests/` 保留全部原有测试并增加仓库、更新器与打包验收。
App API 与 Worker 服务 iOS/Android；Web 面板只面向管理员，测试 Bot 不是正式产品入口。

所有命令从仓库根目录执行，不能从此目录启动服务，否则相对数据库路径可能指向另一处。

```sh
python -m pip install -e './server[dev]'
python scripts/check/run_tests.py
ruff check --config server/pyproject.toml server/murmur server/tests scripts
```

无开发工具的部署安装入口是 `pip install -e ./server`。
现有根目录 `.env`、`.venv`、数据库、照片和日志保持原位，导入名、CLI 子命令、HTTP/SSE 与数据格式不变。

详细流程见 [开发与测试](../docs/development/server.md)、[系统边界](../docs/architecture/system-boundaries.md)、
[部署](../docs/operations/deployment.md)。生产变更须另行授权；编译或模拟测试不等于部署验收。
