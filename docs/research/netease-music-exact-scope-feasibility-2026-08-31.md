# Murmur 网易云音乐四项能力可行性分析（VPS + 手机主路径）

> 状态：产品与技术调研，不代表网易云音乐已向 Murmur 授权｜适用：网易云音乐四项能力（VPS + 手机主路径）｜核验：2026-08-31（Asia/Shanghai）｜依据：网易云官方公开资料与 [`tianyupaipai-cmd/netease-music-mcp`](https://github.com/tianyupaipai-cmd/netease-music-mcp) 锁定提交 [`0e27816d6ad8dac59ae54fcfc71b69cb5a41171b`](https://github.com/tianyupaipai-cmd/netease-music-mcp/tree/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b)
>
> 目标部署：Murmur 服务与 MCP 在 VPS；主要入口是 iOS/Android Murmur App

## 结论

用户要求的四项能力不能视为一个整体同时 Go，应拆成两层：

1. **搜歌、歌曲卡片和接收分享：有条件 Go。** VPS 可以搜索/规范化网易云歌曲元数据，Murmur 现有歌曲卡片 wire 和 iOS 界面可扩展到 `provider=netease`；手机端需要新增 iOS Share Extension 与 Android Share Intent 接收入口。
2. **打开官方“一起听”：只可做手机本地、真机验证后的跳转。** VPS 无法打开用户手机上的网易云 App。目标 MCP 的 `orpheus://...` 只在 macOS 代码路径中使用，不能据此宣称 iOS/Android 支持。移动端必须使用网易正式允许的 deep link/universal link，或退化为提示用户手动进入“一起听”。
3. **在一起听房间播放、暂停、切歌：当前 No-Go。** VPS 上的 MCP 不能向手机的网易云 App 发送本地媒体命令，也没有一起听房间 ID、成员、心跳、播放命令或同步状态。只有获得网易云音乐开放平台对 Murmur App 的正式房间 API/SDK 权限后，才有可能可靠实现。

因此，当前可以交付的最小移动 PoC 是：

```text
Murmur 搜歌/接收网易云分享 → 聊天歌曲卡片 → 用户点击“在网易云打开”
                                            → 本地尝试打开“一起听”入口
                                            → 用户在官方 App 内完成邀请和播放控制
```

当前不能承诺的体验是：

```text
用户在 Murmur 聊天里说“暂停/下一首” → VPS 直接控制手机网易云一起听房间
```

这条链路在 iOS、Android 上均没有可用的目标 MCP 实现。

### 三条实现路线必须分开判断

| 路线 | 四项需求能做到什么 | 结论 |
| --- | --- | --- |
| **A. 只把目标 `netease-music-mcp` 部署到 VPS** | 能做搜歌、详情和歌曲卡片后端；不能接收手机系统分享，不能打开手机 App，不能创建/加入房间或控制房间 | **只满足第 1 项后端部分；第 2 项还需手机开发；第 3、4 项 No-Go** |
| **B. VPS 增加社区私有房间协议 + 一个真实网易云机器人账号** | 技术上可为个人实验补齐房间创建/加入、邀请/二维码、心跳、歌单同步和播放命令；用户手机仍需本地接收分享和打开官方 App | **四项技术 PoC 可做，但生产明确 No-Go** |
| **C. Murmur 获批网易开放平台正式搜索与房间 API/SDK** | 可按正式身份、scope、房间事件和播放命令实现完整闭环 | **唯一可生产的条件路线** |

路线 B 不是“在目标 MCP 上加几个工具”这么简单。至少还需要：第二个真实网易账号及其会话、房间生命周期状态机、心跳、重连、主持人权限、播放命令 ACK、队列同步和账号风控处理。社区接口清单可证明这些协议环节客观存在，例如社区 `ncm-api-rs` 列出的[一起听接口](https://github.com/SPlayer-Dev/ncm-api-rs#%E4%B8%80%E8%B5%B7%E5%90%AC)；它不能证明网易允许 Murmur 使用，也不能成为生产授权依据。

路线 B 的个人 PoC 形态会是：VPS 机器人账号作为房主或获准控制者创建房间并生成邀请；手机 Murmur 将邀请交给本地网易云 App；用户加入后，聊天命令发送到 VPS，由机器人账号发房间播放命令。它可能技术上形成“AI 和用户是两个网易账号参与者”，但会持有高价值账号会话，并依赖随时可能改变的私有协议。账号封禁、会话泄漏、版权/条款、接口变更和无法上架的风险决定了它只能隔离实验，不能进入 Murmur 正式用户链路。

```text
手机 Murmur（聊天、发歌、打开邀请链接）
                │
                ▼
VPS Murmur API ──► 网易云 Catalog MCP（搜索/详情）
                │
                └──► Room Controller（创建房间/心跳/播放命令）
                              │ 专用机器人账号会话
                              ▼
                       网易云一起听房间
                              │
                              ▼
                    手机网易云官方 App 播放
```

这个结构中，VPS 控制的是“房间协议”，不是远程按手机媒体键；手机音频始终由网易云官方 App 播放。AI 可以根据曲目 ID、队列和房间事件陪聊，但没有音频输入时不能声称自己听到了声音或准确识别某一秒的音乐内容。

## 一、总能力矩阵

| 用户要求 | VPS | iOS Murmur | Android Murmur | 当前 `netease-music-mcp` | 评级 |
| --- | --- | --- | --- | --- | --- |
| Murmur 搜歌并发网易云歌曲卡 | 可做搜索、规范化和卡片下发 | 可复用现有歌曲卡 UI | 需补齐音乐卡 UI | 已有匿名搜索/详情，无正式 API 合同 | **PoC Go；生产需官方授权** |
| 用户把网易云歌曲发给 Murmur | 可解析 URL/歌曲 ID并重取元数据 | 需 Share Extension，亦可先做粘贴链接 | 需处理 `ACTION_SEND`/`ACTION_VIEW` | 没有手机分享入口；已有歌曲详情查询 | **Go** |
| 打开“一起听”邀请页面 | 不可远程打开手机 App | 只能由本地 App 打开已获准链接 | 只能由本地 Intent 打开已获准链接 | 仅实现 macOS `open` 私有 URI | **真机探索；未验证前不可承诺** |
| 一起听期间播放/暂停/切歌 | 没有房间 API则不可做 | 不能通用地遥控另一个 iOS App | 通用遥控需高风险权限，仍非房间 API | Mac UI 自动化/系统命令；明确不是房间控制 | **当前 No-Go；官方房间授权后重评** |

Mac companion 只保留为技术参考，不是本方案的目标路径。即使 Mac 端菜单点击偶尔能让官方客户端在房间中同步，它也不能证明 VPS + 手机链路成立。

## 二、需求 1：Murmur 搜歌并在聊天里发网易云歌曲卡片

### 2.1 当前参考项目能做什么

目标 MCP 通过以下非官方 Web 接口取得匿名元数据：

- 搜索：`/api/search/get/web`，见 [`src/netease.js#L223-L238`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L223-L238)；
- 歌曲详情：`/api/song/detail/`，见 [`src/netease.js#L240-L246`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L240-L246)；
- 归一化结果只有 ID、歌名、艺人、专辑、时长和官方页面 URL，见 [`src/netease.js#L154-L164`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L154-L164)。

它不返回合法可用于 Murmur 播放的音频流，也不提供生产 SLA。仓库明确是非官方项目，MIT 许可证只覆盖仓库代码，不等于获得网易曲库、接口和内容的商业授权。

### 2.2 Murmur 需要新增什么

Murmur 已有 Audius 的 `MusicTrackAttachmentV1`、聊天 `bubble.music_track`、iOS picker/卡片/存档和服务端 catalog 验证。建议保留 wire 形状，新增 provider adapter，而不是把网易逻辑塞进 Audius 类：

```text
MusicCatalogAdapter
  ├─ AudiusCatalogAdapter
  └─ NeteaseCatalogAdapter

MusicLinkParser
  └─ NeteaseMusicLinkParser

MusicExternalActionAdapter
  ├─ iOSNeteaseActionAdapter
  └─ AndroidNeteaseActionAdapter
```

`TrackV1(netease)` 应优先保持现有 wire 字段不变：

```text
version, provider, track_id, title, artists, artwork_url,
canonical_url, duration_seconds, explicit
```

专辑名与 `metadata_checked_at` 可放在 provider 内部结果或缓存记录中；若确实需要跨客户端传输，应升版或先完成兼容设计，不能直接给 V1 增加服务端当前会拒绝的未知字段。

需要改动的现有边界包括：

- 服务端 `app_music.py` 目前只接受 `provider == audius`；
- `/v1/music/config` 目前只返回 `audius`；
- iOS `MusicModule`、`AudiusClient` 和播放控制器目前把“曲库、授权、播放”绑定在一起；网易模式必须是**外部 App 操作**，不能走 Audius 的 `AVPlayer`；
- Android 当前没有对齐 iOS 的音乐功能，需要单独完成 picker、卡片、分享接收和外部打开。

### 2.3 登录数据

- **用当前匿名 MCP 做搜索：** 不需要用户网易云登录，但接口是非官方、可变的。
- **生产正式路线：** 网易官方 [`NetEase/skills`](https://github.com/NetEase/skills#%E5%89%8D%E7%BD%AE%E6%9D%A1%E4%BB%B6)说明 `ncm-cli` 的所有命令需要先入驻开放平台、申请 `appId/privateKey`，并完成用户登录授权。Murmur 应先申请开放平台能力，再按获批文档决定搜索是否需要用户授权。
- `privateKey` 只能保存在 VPS 密钥环境，不能放进 App。
- 不应把 `MUSIC_U`、`__csrf` 或网易云 App Cookie 导入 Murmur 服务。目标 MCP 的 Cookie 只用于其非官方歌单 EAPI，并未用于搜索或一起听。

### 2.4 验收标准

满足以下条件才可把“搜歌并发卡”标记为 PoC 通过：

1. 中文、英文、艺人 + 歌名和同名歌曲样本均能稳定返回候选；
2. 卡片 `track_id`、标题、艺人、时长和 canonical URL 与网易云官方页面一致；
3. 发送前按 `track_id` 重取元数据，伪造 provider、域名和超大字段会被拒绝；
4. 旧版客户端忽略卡片字段后仍看到中性文字兜底；
5. 超时、限流、无结果、下架、地区不可用都有明确错误态；
6. 查询词、URL、token/Cookie 不进入日志；
7. 生产发布前取得搜索、元数据展示、封面缓存和 AI 使用边界的书面授权。

### 2.5 判断

- **邀请制内部 PoC：条件 Go。** 可参考目标 MCP 的结构，最好尽快改用官方开放平台凭据验证。
- **公开生产：未授权前 No-Go。** 不能把匿名内部 Web 端点当长期产品合同。

## 三、需求 2：用户把网易云歌曲发给 Murmur

### 3.1 正确的移动入口

VPS 无法接收手机系统分享面板事件；入口必须在 Murmur 客户端：

#### iOS

- 正式体验：新增 Murmur Share Extension，接受网易云分享出的 URL/文本；
- 当前 iOS 工程只有 `Murmur`、`MurmurTests` 和 `MurmurUITests` 三个 target，**尚无 Share Extension**；
- 最小 PoC：聊天输入框支持粘贴网易云链接并识别；
- Share Extension 只负责提取与校验链接，将草稿安全交给主 App；真正提交仍由用户在 Murmur 中确认；
- 扩展不能携带 Murmur/网易登录秘密到普通日志或共享剪贴板。

#### Android

- 接收 `ACTION_SEND` 的 `text/plain` 分享；
- 接收经过产品确认的 `ACTION_VIEW` URL；
- 展示发送前确认页，不在后台收到 Intent 后直接给 AI 发消息；
- 严格限制域名、scheme、重定向次数和响应大小，防止任意 URL 抓取和 SSRF。

### 3.2 服务端处理链

```text
系统分享/粘贴文本
      ↓
客户端只提取允许的网易云 URL 或歌曲 ID
      ↓
VPS NeteaseMusicLinkParser 规范化
      ↓
NeteaseCatalogAdapter 按 ID 重取元数据
      ↓
TrackV1(netease) + 用户可见文字
      ↓
现有 POST /v1/moments → 聊天卡片
```

不要相信分享文案里的标题、艺人和封面；它们只能作为提示，服务端必须重取权威元数据。短链只有在确认官方域名并限制重定向目标后才解析。

### 3.3 目标 MCP 的缺口

它已有“按歌曲 ID 取详情”，因此可参考元数据归一化；但没有：

- iOS Share Extension；
- Android Share Intent；
- 网易分享短链/长链 parser；
- Murmur 的 `TrackV1` 与消息 wire；
- 发送前确认和聊天卡片。

### 3.4 登录与数据边界

- 接收公开分享链接不需要网易登录；
- 不从分享页面、WebView 或官方 App 抽取 Cookie；
- Murmur 服务端只保存卡片必要元数据，不保存音频 URL、歌词或网易登录数据；
- 网易元数据是否可以进入模型、长期聊天记录或推荐画像，需要网易书面确认。确认前可让模型只看到“用户分享了一首网易云歌曲”，卡片详情由确定性 UI 展示。

### 3.5 验收标准

1. iOS 从网易云分享面板选择 Murmur 后可预览并确认发送；
2. Android 从网易云分享后可预览并确认发送；
3. 长链接、允许的官方短链接、带无关 query 的链接都只得到一个规范歌曲 ID；
4. 歌单、专辑、个人页、恶意 URL 不被误识别为歌曲；
5. 下架、无权限、跳转到非允许域名时停止，不生成伪卡；
6. 重复分享、离线重试和冷启动恢复不会重复发送；
7. 旧客户端能显示中性文字而不崩溃。

### 3.6 判断

- **iOS：Go。** Share Extension 是中等规模的客户端新增，不依赖跨 App 控制权限。
- **Android：Go。** Share Intent 是标准能力，但当前 Android 音乐 UI 尚需补齐。

## 四、需求 3：打开官方“一起听”邀请页面

### 4.1 目标 MCP 实际做了什么

目标 MCP 把邀请 URI 写死为：

```text
orpheus://nm/play/listenTogether?refer=mcp
```

随后在 macOS 调用：

```text
/usr/bin/open -b com.netease.163music <URI>
```

源码见 [`src/netease.js#L24-L31`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L24-L31) 与 [`src/netease.js#L341-L351`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L341-L351)。它只证明 Mac 的 `open` 命令已执行；不证明邀请页显示、房间创建、邀请发出或对方加入。

更重要的是，这个函数主动拒绝非 macOS 平台。不能把它复制到 VPS，期望 VPS 打开用户手机上的网易云 App。

### 4.2 VPS + 手机的正确职责

```text
VPS：返回“允许显示邀请入口”的功能配置/可选链接
手机 Murmur：用户点击后，本地调用系统 URL/Intent
网易云官方 App：处理登录、邀请界面、好友选择和房间加入
```

#### iOS

- 只有 Murmur 前台且用户主动点击时才尝试 `openURL`；
- custom scheme 需要真机验证，并可能需要 `LSApplicationQueriesSchemes`；
- 优先使用网易正式文档允许的 Universal Link/SDK；
- 无法确认网易 App 安装、链接失效或跳转被拒绝时，显示“在网易云中手动打开一起听”，不要循环跳转；
- iOS 不允许 VPS 在后台替用户唤起另一个 App。

#### Android

- 使用经网易确认的 HTTPS App Link 或显式/受限 Intent；
- 先验证可处理该 Intent 的包，避免恶意 App 劫持 custom scheme；
- 未安装或 Intent 不可解析时跳官方安装页或显示手动步骤；
- 不从 VPS 直接发送 shell/ADB/无障碍指令控制手机。

### 4.3 登录数据

- 邀请页依赖**网易云官方 App 自己的登录状态**；Murmur 不需要、也不应读取该 App 的 Cookie；
- 当前 URI 没有账号、好友、房间或曲目参数；用户仍需在网易云界面完成操作；
- Murmur 最多知道“已发起外部跳转”，不能把它记成“已创建房间”或“邀请成功”。

### 4.4 验收标准

按 iOS/Android × 网易云已安装/未安装 × 已登录/未登录 × 当前有房/无房建立真机矩阵。只有满足以下条件才能将某一平台标记为支持：

1. 链接属于网易正式允许的第三方入口，或已取得书面确认；
2. 点击来自用户手势，并准确进入邀请页而非网易云首页；
3. 未登录时进入官方登录流程，不泄露凭据给 Murmur；
4. 已有房间时行为可预测，不会误建第二房间；
5. Murmur 返回前台后只显示“已打开网易云”，不伪报邀请结果；
6. 网易云不同稳定版本和主流系统版本均通过回归。

### 4.5 判断

- **当前 iOS：探索性 PoC，未验证前 No-Go。** Mac URI 不能作为 iOS 支持证据。
- **当前 Android：探索性 PoC，未验证前 No-Go。** 需要确认官方 Intent/App Link。
- **VPS：No-Go。** VPS 不能打开或操作用户手机 App。

## 五、需求 4：在官方一起听期间控制播放、暂停和切歌

### 5.1 当前 MCP 不能完成

目标 MCP 的 `netease_control` 在 Mac 上用 JXA/`System Events` 点击“播放/暂停、上一首、下一首”菜单，见 [`src/netease.js#L354-L415`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L354-L415)。`netease_next_track` 则调用 macOS 私有 `MediaRemote.framework`，返回值明确写着 `roomControl: false`，见 [`src/netease.js#L417-L440`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L417-L440)。README 同样声明它只用于普通播放，房间是否接受由官方客户端决定：[`README.md#L197-L201`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/README.md#L197-L201)。

整个仓库没有：

- 房间 ID 与用户角色；
- 创建/加入/退出房间请求；
- 主播/听众控制权限；
- 播放、暂停、切歌的房间命令；
- 当前曲目、队列、进度和远端确认；
- 心跳、断线重连和冲突解决。

因此“发送了一个系统媒体键”不等于“用一起听控制歌曲”。

### 5.2 为什么 VPS 无法替手机做本地遥控

VPS 和手机网易云 App 不在同一个操作系统会话中：

- VPS 的媒体命令只会作用于 VPS 自己，不会跨网络变成手机媒体键；
- MCP 的 Mac UI 自动化要求本机网易云客户端和辅助功能权限，部署到 Linux VPS 后该路径不存在；
- iOS App 不能通用地向另一个 App 的私有播放器发送 play/pause/next 命令；Apple 的 [`MPRemoteCommandCenter`](https://developer.apple.com/documentation/mediaplayer/mpremotecommandcenter)用于让当前播放媒体的 App 注册并处理系统遥控事件，不是第三方 App 任意遥控网易云的接口；
- Android 的 MediaSession、通知监听或 Accessibility 即使技术上可能影响当前媒体 App，也需要高敏感权限、会受系统/厂商限制，且仍没有一起听房间级确认；官方 [`MediaSessionManager.getActiveSessions`](https://developer.android.com/reference/android/media/session/MediaSessionManager#getActiveSessions(android.content.ComponentName))要求系统级 `MEDIA_CONTENT_CONTROL`，或用户启用 Notification Listener，不适合成为 Murmur 的生产基础。

### 5.3 唯一可靠方向：官方房间 API/SDK

网易云音乐开放平台公开文档目录存在[创建一起听房间](https://developer.music.163.com/st/developer/document?docId=09cb77284e224545a9e457b9fbd4bd03)、[多人房间二维码](https://developer.music.163.com/st/developer/document?docId=c31bba05ee5c4eedb8537db826eba987)和[房间用户](https://developer.music.163.com/st/developer/document?docId=d48b4bc986a44015b8537db826eba987)等页面。这说明正式合作体系存在房间能力，但**文档可见不等于 Murmur 的 App ID 已获权，也不证明默认开放 AI 机器人、播放控制或商用分发**。

Murmur 必须向网易逐项确认并取得书面授权：

1. iOS/Android App 是否能创建、加入消费端网易云音乐“一起听”房间；
2. Murmur 用户身份如何绑定网易账号，是否允许 AI/机器人身份参与；
3. 主持人/听众分别能执行哪些播放、暂停、跳转、上一首/下一首和队列命令；
4. 是否返回房间命令 ACK、当前曲目、时间轴、队列和成员事件；
5. 心跳、重连、幂等、冲突和房间结束规则；
6. 音乐版权、会员、地区、未成年人、数据存储、模型输入和运营展示边界；
7. App Store/安卓商店分发、商业化和风控条件。

若只获批“创建房间/二维码/房间用户”，但没有播放命令与状态订阅，仍不能完成本需求。

### 5.3.1 社区私有房间协议为什么只能做个人 PoC

从技术组成看，路线 B 可以把第四项做出来：VPS 上运行一个已登录的网易云机器人账号，调用私有房间接口创建/加入房间、维持心跳、同步歌单并发送播放/切歌命令；用户的官方手机 App 是另一个真实参与者。这与“VPS 发送手机媒体键”不同，它控制的是服务端房间协议。

但该路线的前提和代价是：

- 必须把机器人账号的 Cookie/会话放在 VPS，泄漏后等同账号接管风险；
- 接口来自客户端抓包/逆向，没有兼容合同、SLA 或商用授权；
- 房间协议、加密、设备参数和风控可随时变化；
- AI 机器人参与、自动邀请、自动切歌和曲库使用是否允许均未知；
- 一旦协议变化，Murmur 可能出现“卡片成功但房间失控”的误导状态；
- 无法据此通过 App Store、安卓商店、隐私和版权评审。

因此路线 B 的评级固定为：**个人、隔离、一次性研究 PoC 可行；邀请制真实用户测试和生产发布 No-Go。** 不得导入用户主账号 Cookie；若确需研究，应使用专用低价值测试账号、独立环境、最小会话权限和随时可销毁的数据，并另行取得明确实验授权。

### 5.4 登录数据架构（获批后的建议）

具体以网易合同和 SDK 为准，安全底线为：

- 开放平台 `appId/privateKey`：只在 VPS；
- 用户授权 token：优先存在 iOS Keychain/Android Keystore；如协议要求 VPS 代持 refresh token，必须加密、最小 scope、可撤销并绑定 Murmur 用户；
- 网易云官方 App Cookie、`MUSIC_U`、`__csrf`：不导入、不上传；
- 房间 ID、用户角色和命令幂等键：短期会话数据，房间结束后按协议清理；
- 当前曲目与播放状态：只为当前会话使用，不默认写入 dossier、长期记忆或收听画像；
- token、房间凭据和原始房间事件：不进入 LLM prompt、聊天正文或日志。

### 5.5 获批后的真实验收标准

“按钮点了”不是验收。至少需要两账号、两真机、真实房间的端到端测试：

1. 发起者与受邀者都能确认同一 room ID 和角色；
2. play/pause/next/previous 每种命令至少多轮执行，有唯一幂等键和服务端 ACK；
3. 两端最终曲目 ID、播放状态和队列一致；
4. 设定并达成明确同步延迟 SLO，例如正常网络下 95% 命令两端在 2 秒内一致；
5. 听众无主持权限时返回明确拒绝，不伪报成功；
6. App 切后台、锁屏、耳机切换、来电、网络切换和重连后状态能够重新收敛；
7. 一端退出、账号过期、歌曲下架、会员/地区不可播、房间结束均有确定状态；
8. iOS/Android 交叉房间也通过，而不是只测同平台；
9. 所有控制均通过已获准 API/SDK，没有 Accessibility、模拟点击、私有系统框架或用户 Cookie。

### 5.6 判断

- **当前 VPS + iOS：No-Go。** 没有跨 App 控制能力，也没有获批房间 API。
- **当前 VPS + Android：No-Go。** 不建议用 Accessibility/通知监听作为产品方案。
- **官方房间 API/SDK 获批后：条件 Go。** 需在拿到实际 scope、播放命令和状态事件后重新设计并真机验收。
- **Mac 本地自动化：仅实验参考。** 不属于手机主路径，不作为产品兜底。

## 六、推荐的最小移动 PoC

### Phase A：不碰账号与房间控制

目标：验证“聊天里递歌/收歌”是否有产品价值。

1. VPS 增加 `NeteaseCatalogAdapter`，仅输出最小 TrackV1；
2. iOS 复用歌曲卡，新增“在网易云打开”；Android补齐卡片；
3. 先支持粘贴网易云歌曲链接，再做 iOS Share Extension / Android Share Intent；
4. 不播放音频、不导 Cookie、不创建房间、不上报伪播放状态；
5. 功能由服务端 feature gate 控制。

**Phase A Go 条件：** 正式 API 申请已发起；内部测试接受非官方接口可能停用；数据不进入日志或长期画像。

### Phase B：只验证官方 App 跳转

目标：证明手机本地能否可靠进入一起听邀请页。

1. 向网易确认允许的 Universal Link/deep link/SDK；
2. 在 iOS、Android 各做独立真机矩阵；
3. 链接不可靠时退化为“打开网易云”并给出两步手动指引；
4. Murmur 只记录 `external_open_attempted`，不记录 `room_created`。

**Phase B Go 条件：** 链接获得官方允许并通过真机回归；否则保持手动入口。

### Phase C：官方授权后实现房间控制

目标：在 Murmur 内发送房间级 play/pause/skip 命令并获得状态确认。

1. 获得 Murmur App 的正式房间 API/SDK scope；
2. 建立 `NeteaseRoomSessionAdapter`，与本地外部 App 跳转 adapter 分离；
3. 后端负责签名、授权校验、幂等与会话状态，App 负责用户手势和当前 UI；
4. 完成两账号、双平台、弱网与权限矩阵。

**Phase C Go 条件：** API 权限、曲库/播放授权、AI 身份和商店分发均书面明确；所有房间命令有 ACK 和状态收敛。

## 七、最终 Go / No-Go 决策

| 决策项 | 当前决定 |
| --- | --- |
| VPS 搜歌并让 Murmur 发网易云卡片 | **条件 Go**：内部 PoC 可参考 MCP；生产改走官方开放平台 |
| 用户通过手机分享网易云歌曲给 Murmur | **Go**：新增 iOS Share Extension / Android Share Intent，服务端重取元数据 |
| 手机 Murmur 打开官方一起听邀请页 | **探索性 PoC**：只允许本地用户手势；链接需官方确认和真机验证 |
| VPS 直接打开用户手机的网易云 App | **No-Go** |
| VPS/MCP 直接遥控手机一起听的播放、暂停、切歌 | **No-Go** |
| 使用 Android Accessibility 或私有 iOS 手段兜底 | **No-Go** |
| 获得网易正式房间 API/SDK 后做控制 | **条件 Go，重新评审** |
| 导入网易 App Cookie 或逆向房间协议做生产 | **No-Go** |

按路线汇总：

- **A 仅目标 MCP + VPS：** 做到搜索/元数据/卡片后端；手机分享另开发；邀请与房间控制做不到。
- **B 私有协议 + VPS 机器人账号：** 四项在个人隔离实验中技术上可拼成闭环；生产 No-Go。
- **C 正式开放平台房间授权：** 唯一值得进入产品实现与发布评审的路线。

四项需求的准确产品承诺应写成：

> Murmur 可以帮你搜索和分享网易云歌曲，并把你带到网易云官方 App；一起听邀请与播放控制当前由网易云官方 App 完成。待 Murmur 获得网易正式房间接口授权后，再提供聊天内的播放、暂停和切歌控制。

不应写成：

> Murmur 已经加入网易云一起听，并能从 VPS 控制你手机上的房间。

## 八、证据边界与风险

- **官方开放平台风险：** 页面存在不代表自动获权，接口 scope、个人/企业准入、商用和 AI 场景都需书面确认。
- **非官方接口风险：** 目标 MCP 的匿名接口、EAPI、Cookie 和私有 URI 可随客户端或服务端更新失效。
- **跨 App 控制风险：** VPS 没有手机本地执行权；iOS 沙箱和 Android 权限模型不能被 MCP 绕过。
- **数据风险：** 网易账号 token/Cookie、房间凭据和播放状态不得进入模型、日志或长期记忆；元数据进入 AI 的许可仍需确认。
- **版权风险：** 歌曲卡元数据、封面、歌词和音频是不同权利层；本报告只评估元数据卡和外部控制，不授权 Murmur 播放音频或向模型提供完整歌词。
- **产品表达风险：** “已打开外部页面”“已发送房间命令”“远端已同步”必须是三个独立状态，不能合并成一个成功提示。

社区逆向项目只能说明真正的一起听需要创建/加入、心跳、播放命令、歌单同步和结束房间等完整协议；它不能替代网易正式授权，也不应成为 Murmur 生产依赖。更完整的源码证据见同目录的 [`netease-music-mcp-listen-together-audit-2026-08-31.md`](netease-music-mcp-listen-together-audit-2026-08-31.md)。
