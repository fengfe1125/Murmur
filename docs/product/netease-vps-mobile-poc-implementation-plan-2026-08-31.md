# Murmur 网易云“陪你听”VPS＋手机 PoC 实施计划

> 状态：实施提案｜适用：个人自用、隔离测试环境｜核验：2026-08-31
>
> 依据：Murmur 现有音乐 wire、网易云四项能力可行性分析、`netease-music-mcp` 锁定提交 `0e27816d6ad8dac59ae54fcfc71b69cb5a41171b`
>
> 边界：本计划不授权生产部署，不使用用户主账号 Cookie，不承诺 App Store/应用商店发布

VPS 的功能停用、失败更新自动恢复和成功发布后撤回流程见[网易云陪听 PoC：VPS 回滚计划](../operations/netease-poc-rollback-plan-2026-08-31.md)。

## 1. 目标

在“服务部署到 VPS、用户主要在手机使用”的条件下，验证以下完整闭环：

1. Murmur 根据聊天请求搜索网易云歌曲并发送歌曲卡片；
2. 用户从网易云分享歌曲给 Murmur，或先粘贴歌曲链接；
3. Murmur 为一次陪听创建邀请，并由手机本地打开网易云官方 App；
4. 房间建立后，用户能在 Murmur 聊天中要求播放、暂停、继续和切歌；
5. Murmur 只有在房间状态确认后才显示成功，不把“命令已发出”伪报成“手机已同步”。

PoC 成功只证明个人隔离实验可行。正式发布仍以网易书面授权和官方房间接口为前提。

## 2. 产品流程

```text
用户：给我放一首适合夜里散步的歌
       ↓
Murmur 搜歌并发送网易云歌曲卡片
       ↓
用户点击“和 Murmur 一起听”
       ↓
VPS 房间模块用专用机器人账号创建房间并返回邀请
       ↓
手机 Murmur 在用户点击后打开网易云官方 App
       ↓
用户加入；VPS 看到房间成员后标记“已连接”
       ↓
用户：下一首 / 暂停一下 / 继续播放
       ↓
VPS 发送房间命令并等待房间状态收敛
       ↓
Murmur：已经切到《……》 / 没同步成功，请在网易云重试
```

用户也可以从网易云分享歌曲给 Murmur。Murmur 生成同样的歌曲卡片，并允许把这首歌加入当前陪听房间。

## 3. 范围

### 3.1 PoC 必做

- VPS 搜索和歌曲详情；
- `TrackV1` 支持 `provider=netease`；
- iOS 歌曲卡片显示与“在网易云打开”；
- 聊天输入框粘贴网易云歌曲链接；
- iOS Share Extension；Android Share Intent 排在 iOS 闭环之后；
- 专用机器人账号的房间创建、状态、心跳、播放命令、歌单同步和结束；
- 手机本地打开邀请链接；
- 播放、暂停、继续、上一首、下一首和播放指定歌曲；
- 房间 feature gate、用户 allowlist 和一键停用；
- 两账号、两手机端到端验收。

### 3.2 PoC 不做

- 在 Murmur 内播放网易云音频；
- 下载、缓存、转码或代理歌曲音频；
- 把歌词或音频送入模型；
- 读取用户红心、私人歌单和历史记录；
- 自动读取用户主账号 Cookie；
- 房间文字、语音、表情或自动加好友；
- 多用户公测、正式商店发布和商业化；
- 用 iOS 私有系统能力、Android Accessibility 或通知监听遥控网易云；
- 声称 AI 真正听到了音频。PoC 中 AI 只知道曲目与房间状态。

## 4. 目标架构

```text
┌──────────────────────────────────────────────┐
│ 手机 Murmur                                  │
│ 聊天 · 歌曲卡 · 分享接收 · 打开邀请 · 控制入口 │
└──────────────────────┬───────────────────────┘
                       │ 现有 HTTPS / SSE
┌──────────────────────▼───────────────────────┐
│ Murmur VPS                                   │
│                                              │
│ MusicCatalog                                 │
│   └─ NeteaseCatalogAdapter                   │
│ MusicLink                                    │
│   └─ NeteaseLinkAdapter                      │
│ ListenTogetherRoom                           │
│   └─ ExperimentalNeteaseRoomAdapter          │
│                                              │
│ 聊天编排只调用三个小接口，不知道 Cookie、心跳、 │
│ clientSeq、私有端点和重连细节                 │
└──────────────┬──────────────────┬────────────┘
               │                  │
       网易云元数据接口     网易云一起听房间协议
                                  │
                         手机网易云官方 App
```

## 5. 模块与接口

### 5.1 `MusicCatalog` 模块

接口保持两项能力：

```text
search(query, limit) -> [TrackV1]
resolve(track_ref)    -> TrackV1
```

实现内部负责超时、限流、字段归一化、缓存、下架判断和域名校验。调用方不接触网易返回结构。

首个新适配器为 `NeteaseCatalogAdapter`。PoC 可参考目标 MCP 的匿名搜索和详情实现；正式适配器必须替换为获批的网易开放平台接口。

现有 `TrackV1` wire 不增加未知字段：

```text
version, provider, track_id, title, artists, artwork_url,
canonical_url, duration_seconds, explicit
```

专辑名、查询时间和原始响应只在适配器内部使用。服务端与客户端同时允许 `provider=netease`，旧客户端继续依靠文字兜底。

### 5.2 `MusicLink` 模块

接口：

```text
parse_shared_text(text) -> TrackRef | NotMusic | Rejected
```

实现内部处理网易歌曲长链接、允许的官方短链接、无关 query、重定向上限和 SSRF 防护。分享文案里的歌名、艺人、封面一律不可信，解析出歌曲 ID 后必须经 `MusicCatalog.resolve()` 重取元数据。

### 5.3 `ListenTogetherRoom` 模块

这是房间复杂性的唯一外部 seam。接口只暴露产品需要的行为：

```text
create(initial_track)      -> RoomInvite
inspect(room_handle)       -> RoomSnapshot
command(room_handle, cmd)  -> CommandResult
close(room_handle)         -> Closed
```

`command` 只接受白名单：

```text
play, pause, resume, previous, next, play_track(track_id)
```

以下全部隐藏在实现内部：

- 机器人账号会话；
- 外部 room ID / inviter ID；
- heartbeat 定时器；
- client sequence；
- 队列版本；
- 幂等键；
- ACK、状态轮询和超时；
- 重连、房间过期与关闭。

先实现 `ExperimentalNeteaseRoomAdapter`。未来获得官方接口后，用 `OfficialNeteaseRoomAdapter` 替换；聊天编排和客户端不随协议更换。

### 5.4 手机外部动作

手机只负责用户可见动作：

- 接收分享；
- 展示发送确认；
- 用户点击后打开邀请或歌曲链接；
- 返回 Murmur 后显示 VPS 确认的房间状态。

VPS 不尝试远程唤起手机 App。手机也不持有 VPS 机器人 Cookie。

## 6. 数据与安全

### 6.1 账号

- 使用专用、可丢弃的测试账号作为 Murmur 房主；
- 用户主账号只存在于手机网易云官方 App；
- 若实验被风控、要求验证码或出现账号异常，立即停止自动化，不尝试绕过；
- 不把测试账号与 Murmur 正式用户身份、照片或私人记忆绑定。

### 6.2 凭据

- 机器人会话只存在 VPS 密钥文件或独立 secret store；
- 文件权限最小化，静态加密，支持人工轮换和立即撤销；
- Cookie/token 永不进入 Git、数据库正文、日志、SSE、聊天消息或模型输入；
- App 不接收机器人会话，只接收短期邀请 URL/room handle。

### 6.3 房间数据

只短期保存：

- Murmur 内部 room handle；
- 外部 room ID、成员 ID、当前曲目、队列版本；
- 命令幂等键、状态和时间；
- 最后心跳与过期时间。

房间结束后清除外部标识与命令明细。当前曲目只进入本次聊天上下文，不默认进入 dossier、长期记忆或收听画像。

## 7. 实施阶段

### Phase 0：房间协议可行性闸门（1–2 个工程日）

目标：在不修改 Murmur 正式代码的隔离环境中，先证明最危险的一层。

工作：

1. 准备专用机器人测试账号和第二个手机测试账号；
2. 在隔离 VPS 环境验证创建房间、邀请、加入、状态和结束；
3. 验证心跳维持 30 分钟；
4. 验证播放、暂停、继续、切歌与指定歌曲；
5. 记录两端曲目和状态是否同步；
6. 不接 Murmur 用户数据，不修改生产部署。

通过条件：两账号能稳定进入同一房间，30 分钟不掉线，基础命令多轮可同步到手机。

停止条件：需要用户主账号 Cookie、出现验证码/风控、邀请不能在手机打开、命令无远端确认、协议短时间内反复失效。停止后不进入 Phase 3–5。

### Phase 1：网易曲库与歌曲卡（2–3 个工程日）

1. 抽出 provider-neutral `MusicCatalog` seam；
2. 增加 `NeteaseCatalogAdapter`；
3. 扩展服务端 `TrackV1` provider 白名单和功能配置；
4. 扩展 iOS 卡片品牌、外部打开和文字降级；
5. 增加搜索、详情、下架、超时和恶意字段测试；
6. 保持 Audius 行为与已有 wire 兼容。

交付：用户说“给我放首歌”时，Murmur 能发网易云歌曲卡片，点击后打开歌曲页面。

### Phase 2：用户向 Murmur 发歌（2–3 个工程日）

1. 实现 `MusicLink` 模块；
2. 先支持聊天框粘贴歌曲链接；
3. 添加 iOS Share Extension 与主 App 草稿交接；
4. 分享后必须预览、确认，再调用现有 `/v1/moments`；
5. Android 后续按相同接口增加 Share Intent；
6. 验证冷启动、重复发送、离线恢复和恶意 URL。

交付：用户能从网易云分享菜单把一首歌发送给 Murmur，并在聊天中显示规范卡片。

### Phase 3：房间模块（3–5 个工程日，仅 Phase 0 通过后）

1. 定义 `ListenTogetherRoom` 接口和 fake adapter；
2. 实现实验适配器的创建、查看、命令和关闭；
3. 在模块内部完成心跳、clientSeq、幂等、重连和过期；
4. 增加房间短期状态存储；
5. 增加 user allowlist、feature gate、速率限制和 kill switch；
6. 所有调用错误返回确定状态，不向聊天层泄漏私有响应。

交付：VPS 能独立维持一个房间，并通过小接口完成房间生命周期。

### Phase 4：手机邀请与聊天控制（2–4 个工程日）

1. 歌曲卡增加“和 Murmur 一起听”；
2. 手机在用户点击后打开邀请链接；
3. 只有 `RoomSnapshot` 确认成员加入后，Murmur 才显示“已连接”；
4. 将明确的聊天意图映射为房间白名单命令；
5. 命令返回分为 `accepted`、`synchronized`、`failed`；
6. 增加当前房间卡、结束按钮和断线提示。

交付：用户可以在 Murmur 中发起陪听，并用自然语言控制房间。

### Phase 5：端到端验收与 VPS 包装（2–3 个工程日）

1. 两账号、两手机、iOS 前后台、锁屏和弱网测试；
2. 验证命令乱序、重复、超时、房间过期和机器人会话失效；
3. 验证日志与数据库无 Cookie/token；
4. 首版把房间模块作为 `murmur-app-worker` 内的受限模块运行，复用现有已验证的更新/回滚服务清单；模块独立持有配置、超时和网络 allowlist；
5. 编写启用、停用、轮换凭据和清除房间数据的运行手册；
6. 保持实验开关默认关闭，只允许指定 Murmur 用户。

交付：一个可随时停用、不会影响现有聊天/照片功能的个人 PoC。若以后改成独立 systemd 服务，必须先扩展更新器的安装、重启、成功发布后撤回和单元清理测试，不能只增加一个 `.service` 文件。

## 8. 工期与顺序

单人、iOS 优先的工程估算：

| 阶段 | 估算 | 是否可继续的闸门 |
| --- | ---: | --- |
| Phase 0 房间协议验证 | 1–2 天 | 不通过则停止房间方向 |
| Phase 1 搜歌与卡片 | 2–3 天 | 独立可交付 |
| Phase 2 用户发歌 | 2–3 天 | 独立可交付 |
| Phase 3 房间模块 | 3–5 天 | 仅 Phase 0 通过 |
| Phase 4 手机邀请/控制 | 2–4 天 | 仅 Phase 3 稳定 |
| Phase 5 验收/VPS 包装 | 2–3 天 | 进入个人日常试用前必做 |

总计约 **12–20 个工程日**，不含网易官方合作、审核等待、协议突然变化或 Android 完整对齐。

执行顺序必须是 `0 → 1/2 → 3 → 4 → 5`。Phase 1 和 2 在 Phase 0 失败后仍有独立产品价值。

## 9. 测试计划

### 9.1 单元测试

- 网易 URL allowlist 与短链重定向；
- TrackV1 provider、字段、大小和 URL 校验；
- 聊天控制意图到白名单命令的映射；
- 房间状态转换、clientSeq、幂等与过期；
- token/Cookie 脱敏。

### 9.2 契约测试

- 用固定录制样本验证网易响应映射，但不保存秘密；
- fake room adapter 覆盖成功、拒绝、超时、乱序和重连；
- 旧 iOS 客户端忽略网易卡片扩展后仍显示文字；
- 关闭 feature gate 后现有 Audius、聊天和照片流程不变。

### 9.3 真机验收

- iPhone：网易云已安装/未安装、已登录/未登录；
- 邀请链接打开、加入、返回 Murmur；
- 两账号房间中 play/pause/resume/previous/next/play_track；
- 前后台、锁屏、网络切换、来电/耳机中断；
- 房主离开、用户离开、歌曲不可播、机器人会话过期；
- Android 在 iOS 闭环稳定后使用同一房间接口补测。

## 10. PoC 验收指标

1. 搜索与分享生成的歌曲 ID、标题、艺人和官方链接一致；
2. 分享与粘贴发送前均有用户确认；
3. 邀请链接在目标手机网易云版本中稳定打开；
4. 房间状态确认两账号均在线后才显示“已连接”；
5. 正常网络下 95% 控制命令在 2 秒内让两端状态一致；
6. 重复命令不造成重复切歌，乱序命令不回退状态；
7. 断线、超时和房间过期均不伪报成功；
8. VPS 日志、数据库、SSE、模型输入中不存在 Cookie/token；
9. 关闭实验开关后，Murmur 现有功能完全可用；
10. 连续个人试用 7 天内没有账号风控、明显漂移或需要人工修协议。

第 10 项不代表接口已获得生产许可，只是判断个人 PoC 是否值得继续。

## 11. 发布与停止条件

### 11.1 允许进入个人试用

- 全部真机验收通过；
- 仅一个明确用户、专用测试账号、隔离 VPS；
- feature gate 默认关闭并有即时 kill switch；
- 用户理解 AI 只读取曲目/房间状态，没有接收音频；
- 不与正式 Murmur 生产数据和账号体系混用。

### 11.2 必须停止

- 网易出现验证码、封禁、异常登录或其他风控信号；
- 需要绕过会员、地区、登录或设备限制；
- 需要用户主账号 Cookie 才能工作；
- 私有协议连续变化、状态无法确认或误控频繁；
- 凭据进入日志、模型输入或客户端；
- 准备邀请其他真实用户、提交 TestFlight/商店或商业化。

最后一种情况不是继续加固私有适配器，而是转入网易开放平台厂商授权路线，并以 `OfficialNeteaseRoomAdapter` 替换实验实现。

## 12. 建议的开发提交顺序

1. `docs`: 冻结 PoC 目标、数据和停止条件；
2. `server`: 建立 provider-neutral `MusicCatalog` seam；
3. `server`: 增加网易曲库适配器与 TrackV1 校验；
4. `ios`: 网易歌曲卡与粘贴链接；
5. `ios`: Share Extension；
6. `server`: `ListenTogetherRoom` fake 与实验适配器；
7. `ios/server`: 手机邀请、房间状态和聊天控制；
8. `deploy`: 隔离进程、密钥、feature gate、kill switch；
9. `test/docs`: 双账号真机记录与个人试用手册。

每一步独立保持已有 wire 和旧客户端文字降级；不要用一次大提交同时改聊天、曲库、房间和部署。
