# Murmur 协作入口

> 状态：现行规范｜适用：仓库开发｜核验：2026-08-28｜依据：产品重定位与仓库重组计划

- 产品或文案变更：先读 [定位与能力](docs/product/current-state.md) 和 [术语](CONTEXT.md)。日记成稿、用户确认与风格选择尚未上线；本次重组保持现有行为。
- 文件移动、构建、测试：读 [仓库地图](docs/development/repository-layout.md) 与 [协作规范](docs/development/workflow.md)，同步脚本、CI 和资源引用。
- API、上传、记忆或存储：读 [系统边界](docs/architecture/system-boundaries.md) 和 [数据生命周期](docs/architecture/data-lifecycle.md)；保持已有客户端 wire 契约与数据位置。
- VPS、更新器、systemd：读 [部署](docs/operations/deployment.md) 和 [布局切换](docs/operations/layout-transition.md)。生产变更另行授权；迁移 PR 必须等待桥接更新器上线确认。
- 去外部查平台能力、API、授权边界或做选型对比：结论写成一份 [调研笔记](docs/research/README.md)，放 `docs/research/`，不要散进产品或运维文档。
- 历史资料仅用于追溯；以现行规范和代码核验结果为准。发现冲突先指出，不按旧提案自动实现新功能。
- 用户的未提交文件、stash、本地配置和运行数据先保全；按明确路径暂存改动，不使用全仓库清理命令。
