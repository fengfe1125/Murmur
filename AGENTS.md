# Murmur 协作入口

> 状态：现行规范｜适用：仓库开发与授权运维｜核验：2026-09-13｜依据：581b332 本地代码、脚本与文档核对；本轮未核验生产

## 项目边界

- iOS 定义体验，Android 消费同一 App API。保持客户端 wire 契约、设备身份和现有数据位置；产品能力见 [当前能力](docs/product/current-state.md)。
- 网易云曲库与一起听按各自开关及白名单开放；一起听仍是隔离 PoC，依赖非官方协议与可丢弃机器人账号，未获网易云授权。实现或测试通过不代表可公测、分发或扩大白名单。
- 生产唯一 SSH 入口是 `ssh -o BatchMode=yes murmur-new-vps`；连接方式与迁移记录见 [VPS 连接](docs/operations/vps-connection.md)。旧 Google Cloud VPS 已退役。生产操作前重新读取实时 SHA 与服务状态，历史验收不替代当前证据。
- 生产操作按本次任务的明确授权执行，已有授权无需重复确认。普通开发不自动部署、启动测试 Bot 或创建生产邀请码；凭据存在不构成启动授权。
- 保全用户未提交文件、stash、本地配置、数据库、照片、日志和密钥；按明确路径暂存，不使用全仓库清理命令。凭据和运行数据不入 Git。

## 按需查阅

只读取本次任务涉及的文档，已读且未变化的内容无需重复读取。

- 产品含义、功能范围或文案术语：[当前能力](docs/product/current-state.md)、[术语](CONTEXT.md)。局部错字修正无需重读产品全貌。
- 目录或构建入口变化：[仓库地图](docs/development/repository-layout.md)；分支、PR 与协作流程：[协作规范](docs/development/workflow.md)。只同步受影响的脚本、CI 和资源引用。
- 认证、API、上传、记忆或存储行为变化：[系统边界](docs/architecture/system-boundaries.md)、[数据生命周期](docs/architecture/data-lifecycle.md)。
- 准备部署或修改 VPS、更新器、systemd：[VPS 连接](docs/operations/vps-connection.md)、[部署](docs/operations/deployment.md)。仅依赖旧布局升级桥接的目录迁移另查 [布局切换](docs/operations/layout-transition.md)，并在合并前确认目标 VPS 的桥接安装证据。
- 影响项目选型、架构或授权判断的外部调研：按 [调研笔记](docs/research/README.md) 保存至 `docs/research/`。零散事实查询直接回答并附来源。

## 验证与完成

- 在授权范围内完成实现、相关验证及本次改动引入的问题修复；产品范围、数据留存或对外行为存在影响实施的未决选择时再询问。
- 本地验证按改动影响选择；使用隔离测试数据的检查可执行、修复并重跑，无需逐步确认。服务端使用 `scripts/check/run_tests.py`，支持指定测试文件；iOS 命令见 [iOS README](apps/ios/MurmurApp/README.md)。正式 CI 和发布检查仍按仓库门禁执行。
- 常驻开发服务以就绪和请求的验证完成为判断依据，不等待进程自行退出。仅清理本次创建且不再需要的进程。
- 交付说明行为变化、实际检查结果和未验证部分。构建、测试、安装、真实界面验收与生产验收分别报告。
- 历史资料只用于追溯。代码与文档冲突时核验并指出差异，不按旧提案自动实现新功能，也不为符合过时说明回退现有行为。
