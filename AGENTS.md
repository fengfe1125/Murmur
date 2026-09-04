# Murmur 协作入口

> 状态：现行规范｜适用：仓库开发与授权运维｜核验：2026-09-03｜依据：产品重定位、仓库重组与 2026-09-02 VPS 迁移验收

## 当前落点

- 生产域名是 `https://claude.sakuramu.edu.kg`，当前主机为 `193.106.250.61`；迁移基线是 `main@0d5f805c736417af3fd8b864664ce03ccf0358d9`。迁移后已通过公网 HTTPS、真实消息、SQLite 备份和 GitHub 更新链验收；生产操作前仍须重新读取实时 SHA 与服务状态。
- 生产只运行 Caddy、`murmur-app-api`、`murmur-app-worker` 和 `murmur-web`。Telegram、钉钉、微信、QQ 的凭据、环境文件和 systemd unit 均未迁入新 VPS。
- 网易云音乐已进入远端 `main` 与生产 VPS（2026-09-03）。曲库、歌曲卡与分享按 `MURMUR_APP_MUSIC_USER_ALLOWLIST` 单账号灰度；“一起听”的 transport 已接入，但仍是实验 PoC——协议非官方、依赖跑在隔离用户下的 Phase 0 服务、用可丢弃机器人账号，且未获网易云授权，不得据此推断可公测或分发。iOS 端「一起听」已从聊天页顶栏改为底部第二个常驻 tab（聊天 · 一起听 · 当年今日 · 我的），聊天页只留左上角一张小卡片；不在 `netease_room_user_allowlist` 内的账号看到的是「未开放」页，闸门本身未变。

## VPS 连接

- 当前唯一生产 SSH 入口是 `ssh -o BatchMode=yes murmur-new-vps`。本机别名指向 `root@193.106.250.61`，使用 `~/.ssh/murmur_new_vps`，且服务器只接受公钥登录；密码和私钥内容不得写入仓库。
- 旧 Google Cloud VPS 与其直连密钥/IAP 流程已退役，不再作为部署、检查或回滚目标。连接细节与权限边界以 [VPS 连接](docs/operations/vps-connection.md) 为准。

## 工作路由

- 产品或文案变更：先读 [定位与能力](docs/product/current-state.md) 和 [术语](CONTEXT.md)。日记成稿、用户确认与风格选择尚未上线；本次重组保持现有行为。
- 文件移动、构建、测试：读 [仓库地图](docs/development/repository-layout.md) 与 [协作规范](docs/development/workflow.md)，同步脚本、CI 和资源引用。
- API、上传、记忆或存储：读 [系统边界](docs/architecture/system-boundaries.md) 和 [数据生命周期](docs/architecture/data-lifecycle.md)；保持已有客户端 wire 契约与数据位置。
- VPS、更新器、systemd：先读 [VPS 连接](docs/operations/vps-connection.md)、[部署](docs/operations/deployment.md) 和 [布局切换](docs/operations/layout-transition.md)。生产变更另行授权；迁移 PR 必须等待桥接更新器上线确认。
- 去外部查平台能力、API、授权边界或做选型对比：结论写成一份 [调研笔记](docs/research/README.md)，放 `docs/research/`，不要散进产品或运维文档。
- 历史资料仅用于追溯；以现行规范和代码核验结果为准。发现冲突先指出，不按旧提案自动实现新功能。
- 用户的未提交文件、stash、本地配置和运行数据先保全；按明确路径暂存改动，不使用全仓库清理命令。
