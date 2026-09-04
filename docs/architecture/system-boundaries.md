# 系统职责与契约边界

> 状态：现行规范｜适用：应用与共享服务端｜核验：2026-08-28｜依据：PR #14 与仓库重组决策

## 职责

- iOS：邀请与身份、本机照片选择、聊天记录、照片房间存档及显示；定义当前完整体验。
- Android：消费同一 App API，逐步对齐 iOS；当前只证明 Debug 开发链路，不宣称 Release 已可用。
- App API：请求鉴权、上传限制、moment 接受和 SSE 事件；不提供聊天历史同步。
- Worker：领取持久化任务、调用模型、完成记忆记录与主动投递；推送提供商按设备平台路由。
- 引擎与记忆：构造上下文、选择回应/安静、整理服务端私有记忆；不是日记成稿系统。
- 运维面板：管理员查看状态和诊断、余额快照；经已有 SSH 权限创建邀请码。不是用户端 Web App。
- 测试 Bot：复用引擎，但入口门禁、凭据、数据库和日志与正式 App 隔离。

## 保持兼容

目录重组保持 Python 包名 `murmur`、现有 CLI 子命令、HTTP 路由、JSON/SSE 字段及设备身份不变。
服务端注册请求省略 platform 时继续视作 iOS；Android token/签名规则按已认证设备平台处理。
不要因整理文件顺带更改认证失败关闭、上传清理、幂等、重试或数据库迁移行为。

`POST /v1/moments` 的普通交流与 `intent=photo_reading` 分支继续共用安全入口。
照片房间开场可以返回 bubble 和 angles；失败回落时客户端必须容忍没有 angles。
旧日期续聊使用现有上下文关联，不在本轮发明新的日记 API。

音乐（Audius）是同一条 wire 上的可选扩展，默认关闭，不新增 SSE 事件类型：
`POST /v1/moments` 多一个可选的 `music_track` 文本字段，歌曲与照片不能同条发送，
纯歌曲消息必须同时带文字兜底；歌曲卡片挂在既有 `bubble` 事件的额外字段上，
旧客户端忽略该字段后仍能显示那行文字。新增 `GET /v1/music/config` 与
`PUT /v1/music/playback-state` 两个认证接口，前者是客户端唯一的功能开关来源。
OAuth 不在服务端：用户的 Audius 令牌只存在手机 Keychain，服务端只用自己的
应用 key 查公开曲库元数据，且不代理音频。

网易云是这条 wire 上第二个 provider，两个开关各自独立、默认全关。曲库开关只放开
`music_track.provider=netease` 与元数据查询：网易歌曲卡不进 Murmur 原生播放器，
播放一律回到网易云官方 App。`GET /v1/music/config` 保留 `enabled`/`provider`/
`playback_reporting` 三个旧字段，新增的 `providers` 与 `listen_together` 只在开关
打开时出现，旧客户端忽略即可。`POST /v1/music/resolve-shared` 把用户粘贴或分享的
一条链接换成服务端重新查回的歌曲，分享文案里的歌名、艺人和封面一律不采信；解析结果
不进模型、不进 transcript，要由人自己确认后再发。

房间不再只由按钮开出来：白名单账号在聊天里点歌，只要拆解出一首网易云歌曲，
这一轮就顺带建房或换歌，并在它自己的话后面追加一句固定说明。歌照发，边界不变——
下面这些约束一条都没松。

一起听是隔离 PoC，四个接口 `POST /v1/listen-together/rooms`、
`GET /v1/listen-together/rooms/current`、`POST …/{handle}/commands`、
`DELETE …/{handle}` 都只有在房间开关加非空白名单之后才可用，关闭时统一拒绝。房间只活在
`murmur-app-worker` 进程内存里，App API 经数据目录下 `0600` 的 Unix socket 调用它，
不落库、不加 systemd unit、重启不恢复。对外只有 `RoomSnapshotV1`：网易账号、外部
房间 ID、Cookie 和协议序号都不过这条边界，确认不了的命令只会是 `accepted` 或
`failed`，绝不写成 `synchronized`。详见
[一起听运行手册](../operations/netease-listen-together-poc-runbook.md)。

现有存档与未来日记之间的领域区别见 [术语](../../CONTEXT.md)；数据流见 [数据生命周期](data-lifecycle.md)。
