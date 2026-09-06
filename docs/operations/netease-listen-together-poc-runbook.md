# 网易云“一起听”个人 PoC 运行手册

> 状态：实验运行手册｜适用：单用户、专用机器人账号、隔离 VPS｜核验：2026-09-06｜依据：生产基线 `093aa897` 与 PR #36 的搜索、版权和双向同步实现及测试；不代表网易云音乐已向 Murmur 授权，也不构成公开分发许可
>
> 禁止：生产、公测、商店分发、主账号 Cookie、验证码绕过、音频代理或版权限制绕过

## 1. 两段式闸门

当前代码已经实现曲库、链接解析、iOS 分享/卡片、公共房间 API、内存状态机、IPC、
幂等、故障收尾和到 Phase 0 的回环 HTTP transport。没有配置协议地址时
`ExperimentalNeteaseRoomAdapter` 仍返回 `room_protocol_unsupported`；配置地址、专用机器人
账号和单用户白名单后，房间仍只是非官方、可随时关闭的个人实验，不得据此扩大到公测或分发。

仓库自带的隔离测试工具位于 `scripts/ops/netease-phase0/`。它与 Murmur worker 完全分离，
只监听 VPS 回环地址，并通过 SSH 隧道提供二维码登录和房间控制页面。专用测试账号的 Cookie
不会进入聊天、浏览器、日志或正式 Murmur 进程。

测试工具固定参考 `NeteaseCloudMusicApiEnhanced/api-enhanced` 提交
`f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd`（MIT）；它只作为 Phase 0 研究依赖，不链接进
正式 Murmur 服务，也不代表网易云已授权。

Phase 0 服务只监听回环地址，随机能力路径不进入主 App API；机器人可播检查也只通过该
回环能力地址调用。测试实例不得接触用户照片或长期记忆。Phase 0 源码部署目录不受
`murmur-update` 管理，主服务更新与 Phase 0 更新必须分别授权、分别核对哈希和重启。

曲库和房间是独立能力：曲库失败不影响 Audius；房间失败也不能拖垮 App API 或 worker。

## 2. 机器人凭据

使用可丢弃的专用网易云账号人工扫码登录。不要把 Cookie、密码、验证码或扫码结果发进
Murmur、issue、Git、聊天记录或 shell 命令参数。将 adapter 需要的最小会话写入站外文件：

```bash
sudo install -d -o root -g murmur -m 0750 /etc/murmur
sudo install -o murmur -g murmur -m 0600 /dev/null \
  /etc/murmur/netease-bot.json
sudoedit /etc/murmur/netease-bot.json
```

应用启动时只验证文件存在且权限不宽于 `0600`。日志、SQLite、SSE、客户端和模型输入
都不得出现凭据。会话失效后停止房间实验，人工重新扫码；不自动尝试短信或验证码。

## 3. 默认关闭的配置

首次发布只加入下面配置，不开启功能：

```dotenv
MURMUR_NETEASE_CATALOG_ENABLED=0
MURMUR_NETEASE_CATALOG_BASE_URL=https://music.163.com
MURMUR_NETEASE_ROOM_EXPERIMENT_ENABLED=0
MURMUR_NETEASE_ROOM_USER_ALLOWLIST=
MURMUR_NETEASE_BOT_SECRET_PATH=/etc/murmur/netease-bot.json
MURMUR_NETEASE_ROOM_PROTOCOL_BASE_URL=
MURMUR_NETEASE_ROOM_DISCONNECT_GRACE_SECONDS=600
```

`MURMUR_NETEASE_ROOM_PROTOCOL_BASE_URL` 只接受受审的回环 Phase 0 地址。缺少地址、凭据文件、
唯一 Murmur `user_id` 白名单或显式房间开关中的任一项时，adapter 都必须 fail-closed。

## 4. 进程与状态

- 房间 manager 由现有 `murmur-app-worker` 持有，不增加 systemd unit；
- App API 通过 `/opt/murmur` 数据目录内的 `0600` Unix socket 调用 worker；
- 外部 room ID、协议序号、队列版本和会话只存在 worker 内存；
- worker graceful shutdown 会尽力关房；崩溃或重启后不恢复旧房间；
- 连续十分钟无法确认远端状态时，本地会话进入 ended/failed 并要求重新建房；
- 正常空闲不会自动关房，只有用户明确结束才关闭。

## 5. 启用顺序

1. 确认当前提交已通过统一测试和更新器回滚测试；
2. 确认机器人凭据属于专用测试账号，文件权限为 `0600`；
3. 保持房间关闭，只开启曲库并重启 App API/worker；
4. 用 allowlist 用户完成搜歌、卡片、粘贴和 iOS 分享测试；
5. 经单独生产授权，核对 Phase 0 文件哈希、填写唯一用户白名单与回环协议入口，再分别
   重启 Phase 0、worker 和 App API；
6. 完成创建、加入、双方切歌、版权冲突回退、状态刷新、手动结束与异常收尾验收；
7. 记录提交 SHA、开关、测试账号别名、测试时间和结果，绝不记录会话值。

## 6. Kill switch

出现验证码、封禁、异常登录、误控、状态漂移、邀请失效或凭据泄漏迹象时：

1. 立即把 `MURMUR_NETEASE_ROOM_EXPERIMENT_ENABLED` 改为 `0`；
2. 重启 `murmur-app-worker` 和 `murmur-app-api`；
3. 确认不能新建房、不能下发命令，普通聊天、照片与 Audius 正常；
4. 到网易云官方入口撤销机器人会话；
5. 保存脱敏错误类型和时间，不保存响应正文或 Cookie。

如曲库本身异常，再关闭 `MURMUR_NETEASE_CATALOG_ENABLED`。历史网易歌曲卡只保留文字
与官方链接降级，不能导致旧聊天无法加载。

## 7. 代码回滚

生产更新器只接受 `main` 快进提交，不在 VPS 切功能分支、检出旧 SHA 或执行 hard reset。

1. 先用 feature flag 停用网易能力；
2. 在仓库对问题提交创建正常 `git revert`；
3. 通过 CI 与 `murmur-update` 发布新的快进提交；
4. 核验最终 SHA、App API、worker、web、聊天、照片、Audius 和历史网易卡；
5. 数据库快照保留作证据，不自动覆盖正在运行的数据库。

房间状态不落数据库，不需要房间数据迁移或恢复。机器人 secret 位于 `/etc/murmur`，代码
回滚不会删除；撤销会话必须由管理员单独执行。

## 8. 个人试用通过标准

- 双账号连续两小时不异常掉线；
- 50 次混合控制全部得到同步或明确失败，其中至少 95% 的成功命令在两秒内确认；
- 每次命令后的独立状态读取 P95 不超过两秒，结合三秒客户端轮询后，参与者切歌到
  Murmur 显示的端到端 P95 不超过五秒；
- 搜索、可播检查、歌单写入、播放命令写入、确认与状态刷新只记录阶段、耗时和结果，
  不记录用户、搜索词、歌曲、账号或房间标识；
- 参与者切到任一方不可播歌曲时恢复上一首共同可播歌曲，房间不断开，并显示
  `counterpart_rights_unavailable`；下一次成功切歌清除提示；
- 超时、拒绝和断线绝不显示为 synchronized；
- 手动结束后停止心跳；异常失联十分钟后自动收尾；
- 七天内无风控、明显漂移或需要人工修协议；
- 日志、数据库、SSE、模型输入和客户端均扫描不到 Cookie/token。

这些结果只证明个人隔离 PoC 值得继续，不构成网易授权或生产可用结论。

## 9. iOS 真机签名

分享扩展通过 App Group `group.com.sakura.Murmur` 把未信任的原始分享文本交给主 App，
扩展本身不访问 VPS、不自动发送，也不自动建房。真机或 TestFlight 构建前，必须在 Apple
Developer 中为主 App 和 Share Extension 两个 App ID 同时注册该 App Group，并刷新两者
的 provisioning profile；否则分享扩展无法读取主 App 的草稿容器。

iOS 不保证分享扩展可以自动打开主 App。用户从网易云分享后，扩展会保存草稿并正常
返回网易云；用户打开 Murmur 后才进行服务端解析、预览和确认发送。
