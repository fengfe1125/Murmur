# 网易云“一起听”Phase 0 真机验证记录

> 状态：现状记录｜适用：个人隔离 PoC 可行性闸门｜核验：2026-08-31｜依据：VPS 隔离实例、两个独立网易云账号、iPhone 真机回执与本分支测试工具；不代表网易云已授权

## 结论

核心协议链路已经真机验证可行，但 Phase 0 尚未全部通过。已验证 VPS 机器人账号可以创建
一起听房间，iPhone 的另一个账号可以加入；机器人发出的播放、暂停、继续、上一首、下一首、
指定歌曲和活动房间内替换歌单命令能实际控制 iPhone。iPhone 在网易云 App 内手动切歌后，
VPS 可以从远端播放列表快照读取较新的权威状态并跟随。

尚未完成的门槛是连续两小时稳定运行、50 次混合控制统计、认证失效、十分钟连续失联和意外
进程退出。完成这些项目之前，不把私有房间协议接入正式 Murmur worker。

## 隔离环境

- VPS 专用系统账号：`murmur-netease-poc`。
- 独立目录：`/opt/murmur-netease-poc`；没有读取或修改 `/opt/murmur`。
- 页面只监听 `127.0.0.1:18763`，通过 SSH 隧道访问；没有公网监听和 systemd 单元。
- 机器人会话文件权限 `0600`，父目录 `0700`；Cookie 不返回浏览器、不写日志。
- Node.js `22.23.1` 官方归档经官方 `SHASUMS256.txt` 校验。
- 参考实现固定为 `NeteaseCloudMusicApiEnhanced/api-enhanced`
  `f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd`，按 MIT 许可证保留上游 LICENSE，仅作为研究依赖。

## 已验证行为

| 行为 | 结果 | 真机观察 |
|---|---|---|
| 二维码登录 | 通过 | 专用账号会话保存后重启工具仍可验证登录 |
| 建房与邀请 | 通过 | 房间显示 `CONNECTED`，两名成员时检查状态为 `FULL` |
| 播放 | 通过 | iPhone 开始实际播放 |
| 暂停 | 通过 | 修正进度后 iPhone 实际暂停 |
| 继续 | 通过 | 从暂停位置继续，不从头播放 |
| 下一首／上一首 | 通过 | 两首歌房间双向切换成功 |
| 指定歌曲 | 通过 | `GOTO` 目标歌曲并从头播放 |
| 活动房间换歌 | 通过 | 不结束房间、不重新邀请即可替换列表并切歌 |
| 手机手动切歌 | 通过 | VPS 读取新的 `serverSeq`、歌曲、状态与进度，后续心跳不切回旧歌 |
| 搜索结果可播放性 | 需分层 | 《晴天》仍有元数据但手机提示下架；新增只返回布尔值的可播放检查 |

## 关键协议事实

两个独立实现都指向相同的房间端点。建房使用
[`/api/listen/together/room/create`](https://github.com/neteasecloudmusicapienhanced/api-enhanced/blob/f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd/module/listentogether_room_create.js)，
播放控制使用
[`/api/listen/together/play/command/report`](https://github.com/neteasecloudmusicapienhanced/api-enhanced/blob/f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd/module/listentogether_play_command.js)，
心跳使用
[`/api/listen/together/heartbeat`](https://github.com/neteasecloudmusicapienhanced/api-enhanced/blob/f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd/module/listentogether_heatbeat.js)，
播放列表快照使用
[`/api/listen/together/sync/playlist/get`](https://github.com/neteasecloudmusicapienhanced/api-enhanced/blob/f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd/module/listentogether_sync_playlist_get.js)。

真机返回的播放列表快照包含 `playCommand.targetSongId`、`playStatus`、`progress`、`clientSeq`
和服务端权威顺序 `serverSeq`。本地只接受更大的 `serverSeq`，并验证歌曲必须出现在远端列表中；
账号、用户、房间和 RTC 字段不会进入公开状态。

## 发现并修正的问题

第一次暂停测试只有控制提示但没有暂停。原因是无音频的 VPS 主端一直上报 `progress=0`；播放
一段时间后，手机端不会采用这个不一致快照。改为以单调时钟累计虚拟播放进度后，暂停和继续
真机通过。

第二个问题是“有元数据”不代表“手机可播放”。《晴天》详情接口仍返回歌曲，但用户账号在
一起听中提示下架。因此曲库搜索、详情解析和播放能力必须是三层状态；房间选歌前增加独立
可播放布尔检查，但不向 Murmur 返回或保存播放器 URL。

第三个问题是最初只读取到手机手动切歌，却没有写回本地。修正后每次心跳先读取远端列表，
按 `serverSeq` 采用新状态，再发送心跳；一个完整心跳周期后未出现切回旧歌。

## 待完成门槛

- 两小时观察已启动，报告只含检查次数、成功/失败计数和时间。
- 50 次混合命令尚未执行，不能计算两秒内同步的 95% 指标。
- 认证失效、十分钟失联和异常退出尚未做破坏性真机测试。
- 当前运行的是隔离研究工具，不是 `ExperimentalNeteaseRoomAdapter` 的正式 transport。
- 未进行 App Store、公测、商业化或生产 VPS 发布验收。
