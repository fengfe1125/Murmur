# 网易云“一起听”个人 PoC 运行手册

> 状态：实验运行手册｜适用：单用户、专用机器人账号、隔离 VPS｜核验：2026-08-31｜依据：本分支的 app_listen_together、app_music_links 实现与 server/tests 闸门；不代表网易云音乐已向 Murmur 授权，也不构成生产验收
>
> 禁止：生产、公测、商店分发、主账号 Cookie、验证码绕过、音频代理或版权限制绕过

## 1. 两段式闸门

当前分支已经实现曲库、链接解析、iOS 分享/卡片、公共房间 API、内存状态机、IPC、
幂等和故障收尾，但**没有实现或注入可访问网易云“一起听”的私有协议 transport**。
`ExperimentalNeteaseRoomAdapter` 在没有经过评审的 transport 时会返回
`room_protocol_unsupported`，不会创建假房间或伪报同步成功。因此当前可直接验收的是
搜歌、发歌和分享流程；真正的“一起听”仍停在 Phase 0 协议研究闸门。

仓库自带的隔离测试工具位于 `scripts/ops/netease-phase0/`。它与 Murmur worker 完全分离，
只监听 VPS 回环地址，并通过 SSH 隧道提供二维码登录和房间控制页面。专用测试账号的 Cookie
不会进入聊天、浏览器、日志或正式 Murmur 进程。

测试工具固定参考 `NeteaseCloudMusicApiEnhanced/api-enhanced` 提交
`f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd`（MIT）；它只作为 Phase 0 研究依赖，不链接进
正式 Murmur 服务，也不代表网易云已授权。

第一段只在独立测试 VPS 验证房间协议。测试实例不得使用 Murmur 生产数据库、生产
`.env`、用户照片或长期记忆。只有双账号真机验收通过，才允许把相同 adapter 接入现有
`murmur-app-worker`；接入发布时两个网易开关仍保持关闭。

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

`MURMUR_NETEASE_ROOM_PROTOCOL_BASE_URL` 是为 Phase 0 之后的受审 transport 预留的启动
闸门，并不等于当前代码已经实现 HTTP transport。隔离验证通过、真实 transport 接入并
通过自动化测试后，才能填写该入口、唯一 Murmur `user_id` 白名单并开启房间开关。
在此之前，即使误开房间开关，adapter 也必须保持 fail-closed。

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
5. 在隔离 harness 完成 Phase 0、真实 transport 经评审接入后，填写唯一用户白名单与
   协议入口，再开启房间并重启 worker、App API；当前分支不得执行这一步；
6. 完成创建、加入、状态、命令、手动结束与异常收尾验收；
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
- 50 次混合控制至少 95% 在两秒内确认两端同步；
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
