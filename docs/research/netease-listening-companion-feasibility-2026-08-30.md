# “陪你听”连接网易云音乐可行性调研

> 状态：调研提案｜适用：产品决策、技术预研｜核验：2026-08-30｜依据：网易云公开页面、平台边界与 Murmur 现行架构；不代表平台授权、真机验收或功能承诺

## 结论

可以做，但应把第一版定义为：**用户主动选中或分享一首网易云音乐，Murmur 把用户送到网易云播放；用户回到 Murmur 后，音乐由网易云在后台继续播放，Murmur 围绕用户已选择的歌曲陪伴和交流。**

这一路径可在不读取其他 App 播放状态、不接触音频文件、不新增敏感权限、也不改变现有服务端 wire 契约的前提下完成，工程与合规风险最低，和 Murmur“从素材与讲述中帮助用户表达回忆”的定位也一致。

需要区分四个看似相近、实质不同的承诺：

| 用户理解 | 可行性 | 结论 |
|---|---:|---|
| 分享/选择一首歌，打开网易云播放，再回 Murmur 陪聊 | 高 | 推荐作为 MVP |
| 绑定网易云账号，搜索歌曲、读取红心/歌单、做推荐 | 条件可行 | 官方能力存在，但个人应用目前不能直连 API；必须走厂商商务与授权验收 |
| 自动知道网易云 iOS App 当前在播什么、播到哪里，并控制暂停/切歌 | 低 | iOS 公共 API 不提供读取或遥控另一音乐 App 的可靠能力，不应承诺 |
| 在 Murmur 内直接播放网易云完整音乐 | 待商务授权，不适合作为 MVP | 会把产品变成音乐播放器，涉及播放 SDK、曲库/会员权益、版权、后台音频和 App 审核 |

**建议决策：Go，但只批准“分享/选歌 + 播放跳转 + 陪听房间”的无账号 MVP；账号连接进入独立技术预研；自动播放同步、逆向接口和内嵌完整播放 No-Go。**

## 为什么这个版本适合 Murmur

Murmur 当前是 iPhone/iPad 主客户端，不支持 Mac Catalyst；产品入口固定为“聊天、当年今日、我的”，当前重点仍是可靠交流、照片回忆和用户确认后的日记，而不是扩展为音乐聚合器。[iOS 现状](../../apps/ios/MurmurApp/README.md)与[产品现状](../product/current-state.md)都不支持把桌面播放器能力当成现有基础。

“陪你听”与定位的连接点不应是“替代网易云播放”，而应是：

- 歌曲是用户主动带入的一份回忆线索，和照片、讲述处在相同的“素材”层；
- Murmur 可以问“这首歌让你想到谁/哪一年/哪个地方”，但不自动把回答称为日记；
- 用户明确说出或确认的歌曲信息才进入交流，不能后台持续收集听歌历史；
- 不增加第四个 Tab，入口放在聊天页的卡片、加号菜单或输入区附近。

## 外部能力核验

### 1. 网易云音乐已经有官方开放路径，但个人应用目前只开放 CLI

网易官方 GitHub 组织公开了 [NetEase/skills](https://github.com/NetEase/skills)，说明个人开发者可以入驻、获得 `appId` 和 `privateKey`，再通过 `ncm-cli login` 扫码完成用户授权。官方 skill 列出的能力包括搜索歌曲/歌单/专辑、歌单管理、每日推荐和查看用户信息。

官方 npm 包 [`@music163/ncm-cli`](https://www.npmjs.com/package/@music163/ncm-cli) 在本次核验时仍为 `0.1.7`。其公开说明包含搜索、歌单管理、推荐、用户登录、播放状态和本地播放控制；播放依赖本地 `mpv`，官方 skill 另列出仅 macOS 可用的 `orpheus`（云音乐 App）播放器后端。

开放平台的厂商文档还公开了 OAuth、用户信息、歌曲播放 URL、播放器 SDK、播放数据上报和“一起听”接口，说明完整接入在技术上有官方路径。但[个人开发者 FAQ](https://developer.music.163.com/st/developer/document?docId=3b75ab8e475d41ca93d91ebd4dfd383f)明确写明个人场景暂不支持直连开放平台 API、个人 demo 目前只提供 CLI；[厂商接入说明](https://developer.music.163.com/st/developer/document?docId=4d1bd372f8b444ddb6bbde7197bea19f)则要求提交公司、产品、终端和合作诉求进行项目评估。也就是说，**文档公开不等于 Murmur 已有调用权限**。

这些证据证明“官方能力存在”，但不能推导出 Murmur 当前能嵌入 iOS/Android SDK，也不能推导出开放平台能够远程控制用户手机上的网易云 App。

当前尚未获得厂商 App ID 的实际权限、授权 scope、调用额度、费用、数据留存条款和商用许可。因此账号连接必须把以下项目作为开工闸门，而不是边开发边猜：

1. Murmur 的公司/产品主体按厂商合作而非个人 demo 获准接入；
2. 官方明确允许“私人回忆/AI 陪伴”场景读取和处理相应用户数据；
3. 明确移动端登录/授权流程，而不把桌面 CLI 的本地状态直接搬上服务器；
4. 明确搜索、红心、歌单读取/写入、播放链接等 API 的 scope、额度、地区、会员和版权约束；
5. 获得可在 App Review 时提供的第三方服务授权证明。

### 2. iOS 能打开链接，但不能可靠读取另一 App 的播放状态

Apple 将 `MPNowPlayingInfoCenter` 定义为应用发布“本应用正在播放的媒体”信息的接口，[Media Player 文档](https://developer.apple.com/documentation/mediaplayer)也明确围绕本应用自己的播放内容与 remote command 展开。它不是读取网易云等其他 App 全局 Now Playing 的公共查询接口。

因此 iOS 上可以可靠设计的是：

- 用户分享网易云链接给 Murmur；Apple 的 [Share Extension](https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/Share.html)可以接收 URL；
- Murmur 打开网易云提供的 HTTPS 歌曲/歌单链接；若设备与网易云的 Universal Link 关联有效，会进入网易云，否则回落网页；
- 网易云开始播放后，用户返回 Murmur，音频由网易云继续在后台播放；
- Murmur 只显示用户已经选择的歌曲，不宣称“检测到正在播放”或“已同步进度”。

不应把未公开的 URL scheme、私有 `MediaRemote`、Accessibility 自动化、屏幕/通知抓取或抓包逆向当作产品接口。

### 3. Android 技术上能做更深的媒体会话联动，但不宜先做成平台差异

Android 的 [`MediaSessionManager`](https://developer.android.com/reference/android/media/session/MediaSessionManager) 可以列出其他应用发布的活跃媒体会话，但调用方需要系统级 `MEDIA_CONTENT_CONTROL`，或成为用户启用的 Notification Listener。后者意味着 Murmur 获得远超“只看网易云歌曲”的通知访问范围，[NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService)也确实是接收系统通知变化的服务。

这条路在 Android POC 中可以验证网易云是否正确发布 title/artist/playback state 和 transport controls，但不推荐成为首版：

- iOS 无对等能力，会造成核心体验分裂；
- 用户需要授予“通知使用权”，与一个私人回忆应用的合理预期不匹配；
- Google Play 要求敏感数据访问有醒目披露、明确同意和最小化处理；
- 网易云 App 的媒体会话字段与行为不是 Murmur 可控制的长期契约。

### 4. 逆向 API 和抓取方案不应进入生产

社区里存在大量对网易云内部 `weapi/eapi`、Cookie、二维码和音频地址的封装，但它们不是开放平台授权的稳定契约。网易云音乐[服务条款](https://st.music.163.com/official-terms/service)限制未经授权的第三方兼容软件、插件、互联和对客户端/服务交互数据的复制修改；Apple [App Review Guidelines 5.2.2/5.2.3](https://developer.apple.com/app-store/review/guidelines/)也要求第三方服务内容和流媒体取得明确许可，并能在审核时出示授权。

结论：生产环境只允许使用网易开放平台公开并授权给 Murmur 的接口；不使用逆向登录、Cookie 代持、网页抓取、音频地址提取或绕过会员/地区限制。

## 推荐产品方案

### MVP：一首歌，一个临时陪听空间

建议体验：

1. 用户在 Murmur 点“陪我听”，粘贴网易云歌曲链接；或从网易云分享菜单选择 Murmur；
2. Murmur 只接受 allowlist 中的网易云 HTTPS 域名和歌曲/歌单资源，展示可由用户确认的歌名、歌手与原始链接；
3. 用户点“去网易云播放”，系统打开网易云或网页；
4. 用户切回 Murmur，看到仍在的陪听卡片，可以边听边聊；
5. Murmur 围绕这首歌的情绪、人与回忆发问，不引用完整歌词、不假装听见声音、不声称知道播放进度；
6. 陪听状态默认只在本次会话内；只有用户点“告诉 Murmur”或实际发送文字后，确认过的歌名/歌手/链接才作为普通聊天素材进入现有服务端链路。

文案必须说“已为你打开网易云”“你选的是……”，不要说“正在同步播放”“我听到副歌了”。

### 第二阶段：厂商授权后的官方账号连接

只有厂商商务、API 组权限、版权和上线验收闸门全部通过后再增加：

- 账号授权与撤销；
- 在 Murmur 搜歌并把原始资源链接交给网易云播放；
- 用户主动选择时读取红心/歌单，帮助找到“那一年的歌”；
- 经用户逐次确认后创建或更新歌单；
- 不默认拉取或长期保存完整听歌历史。

播放仍优先交给网易云 App。官方 CLI 的本地 `mpv` 播放适合桌面 Agent，不应被改造成多用户服务端播放器，也不解决 iPhone 上的播放交接。

### 暂不做

- iOS 自动读取当前曲目、播放进度、暂停/切歌；
- 环境录音或听歌识曲来猜用户在听什么；
- 下载、缓存、转码、上传网易云音频；
- 保存完整歌词或用歌词训练/生成内容；
- 后台持续同步听歌历史；
- 未经用户确认自动改歌单；
- 用非官方 API 登录用户账号。

## 推荐技术边界

### 无账号 MVP

客户端新增独立 `ListeningCompanion` feature，而不是继续扩张已经承担聊天、照片、SSE 和设置状态的 `MurmurSessionModel`。建议至少隔离：

- `MusicLinkParser`：域名 allowlist、歌曲/歌单 ID 提取、规范化与恶意 scheme 拒绝；
- `MusicLinkOpening`：包装 `UIApplication.open`，方便注入 fake 测试；
- `ListeningSessionModel`：`idle → selected → opening → handedOff → returned/failed`；
- Share Extension：只接收 URL/少量文本，通过 App Group 或受控 deep link 把用户选择交给主 App；
- `ListeningCompanionView`：聊天页内卡片或 sheet，不增加第四个 Tab。

这一步不需要麦克风、音乐资料库、后台音频或通知监听权限。若不调用 `canOpenURL` 探测自定义 scheme，只打开 HTTPS 链接，也不需要 `LSApplicationQueriesSchemes`。

服务端默认不改：歌曲信息经过用户确认后可作为普通 `note` 走现有 `POST /v1/moments`。不能把音乐随意塞入现有 `intent`：客户端 enum、服务端 allowlist 和 SQLite `CHECK` 当前都只接受 `photo_reading`（见 [MurmurModels.swift](../../apps/ios/MurmurApp/MurmurModels.swift)、[app_store.py](../../server/murmur/app_store.py)）。也不能上传音频；当前 API 最多接收一个文件且只接受 `image/*`（见 [app_api.py](../../server/murmur/app_api.py)）。

### 官方账号阶段

建议在服务端增加独立的 provider 边界，而不是把网易凭据和响应散落进聊天模块：

```text
iOS / Android
  ├─ 网易授权启动、回调、撤销
  ├─ 搜索/选择歌曲
  └─ 打开原始网易资源链接
          │
          ▼
Murmur App API
  └─ NetEaseMusicProvider（只调用获准的官方 API）
       ├─ app privateKey：仅服务端密钥存储
       ├─ 用户 token：加密、最小 scope、可撤销
       └─ 元数据：按需获取、短缓存、明确 TTL
```

新接口应使用 `/v1/integrations/netease/...` 或同等隔离命名，不修改现有 `/v1/moments` multipart、JSON/SSE 字段、身份、幂等和重试语义。

## 数据与隐私边界

MVP 应坚持“用户选择触发”，而不是“后台观察”：

| 数据 | MVP 建议 | 账号阶段建议 |
|---|---|---|
| 网易歌曲/歌单链接 | 本机临时保存；用户发送后才进入聊天 | 可保存规范化资源 ID，保留期明确 |
| 歌名/歌手 | 用户确认；不确定时允许编辑 | 来自官方 API，标注来源 |
| 专辑封面 | 不抓取、不永久缓存 | 仅按开放平台授权方式展示，短缓存 |
| 网易账号 token | 不存在 | 仅服务端加密保存，支持撤销与删除 |
| 红心/歌单 | 不读取 | 用户主动触发、最小 scope，不默认全量持久化 |
| 当前播放/进度 | 不读取 | 仍不在 iOS 承诺；Android POC 也默认本机处理 |
| 音频/歌词 | 不接收 | 未有明确授权前仍不接收 |

当前聊天记录在设备端持久化，但服务端仍会保存交流相关记忆；因此歌曲元数据一旦作为 note 发送，就不能宣传为“只在本机”。应同步更新隐私政策、App Store/Google Play 数据披露、连接撤销和账户删除路径。Apple 也要求隐私政策写清收集、用途、第三方共享、保留/删除和撤销方式（[App Review Guidelines 5.1](https://developer.apple.com/app-store/review/guidelines/)）。

## 验证计划与硬闸门

### Spike A：无账号真机链路（3–5 个开发日）

- 在当前支持的 iOS 版本与两到三个网易云 App 版本上，记录歌曲/歌单分享 payload 的真实 UTType、URL 和文本；
- 验证 `music.163.com`、`y.music.163.com` 等实际分享链接的跳转、未安装回落、会员/地区不可播放情况；
- 验证网易云播放后切回 Murmur 是否继续后台播放；
- 做一个不联网的陪听卡片，验证用户是否理解“它在陪聊，不是在同步播放器”；
- 不提交生产代码、不申请额外权限。

通过标准：分享/粘贴/打开/返回链路在目标真机稳定，失败有明确回落，界面从不虚报播放状态。

### Spike B：官方厂商接入（外部审核时间不计入，工程 3–5 个开发日）

- 向网易提交公司、产品、终端与合作诉求，并保存官方批准记录；个人 CLI 凭证不能替代厂商授权；
- 获取完整 API/SDK 文档、协议、scope、费用、限流、回调和数据删除要求；
- 获得沙箱/测试 App ID 后，用隔离测试账号验证搜索、用户信息、红心/歌单读写、登录刷新与撤销；
- 确认批准的 API 组允许服务端多用户应用，而不是只允许本机个人 CLI；
- 向网易确认 Murmur 的 AI 陪伴/回忆场景和 App Store 分发是否在许可内。

通过标准：有书面/控制台证据支持目标 scope，能说明 token 生命周期、配额、错误模型、版权和商用权限；否则账号阶段停止。

### MVP 实现（约 8–12 个开发日，不含产品视觉与外部审核）

- iOS Share Extension、链接校验、跳转、临时陪听会话；
- “告诉 Murmur”前的显式确认和普通聊天提交；
- 单元测试：URL allowlist/编码/回落、状态机、重复点击、失败；
- UI 测试：三 Tab 可达性不变、VoiceOver、Dynamic Type、Reduce Motion；
- 数据测试：默认不写 transcript、不调用服务端；确认后仍保持原 multipart 契约；
- 真机验收：已安装/未安装网易云、前后台、锁屏/耳机中断。

Android 分享入口可随后对齐；不要为了追求“更自动”而先申请 Notification Listener。

## Go / No-Go 判据

批准 MVP 的条件：

- 用户能接受“选歌后陪聊”，而不是要求逐秒同步；
- 真机证明网易云分享和 HTTPS 播放跳转稳定；
- MVP 不依赖官方账号接口也能提供独立价值；
- 歌曲信息只有在用户明确确认后才发送给 Murmur。

账号连接进入开发的条件：

- 开放平台批准 Murmur 场景；
- 官方文档明确提供所需能力、配额和移动端授权方案；
- 有可向 App Review 提供的使用授权；
- 完成 token、撤销、删除、隐私披露和故障降级设计。

出现以下任一情况则停止对应方案：

- 只能依靠逆向 API、用户 Cookie 或抓包；
- 需要绕过会员、地区或版权限制；
- iOS 体验依赖读取另一 App 的 Now Playing；
- 需要把完整音频/歌词送入 Murmur 或模型，但没有明确授权；
- “陪你听”会挤占当前已确定的日记确认、风格选择和 iOS 体验验收优先级，却没有小范围用户证据。

## 证据边界与待确认项

本报告已核验仓库代码、Apple/Android 官方文档、网易官方 GitHub skill、官方 npm 包说明与公开开放平台文档。更细的外部证据矩阵见[网易云音乐集成来源](netease-cloud-music-integration-sources.md)。公开目录证明能力存在，但只有实际获批的厂商控制台能证明 Murmur 可用；因此以下内容仍是未知项：

- API 的正式协议、费用、速率限制和生产 SLA；
- Murmur 厂商主体最终可获批的 API 组与 scope；
- 是否有可直接供 iOS/Android 集成的 SDK；
- 移动 App 的授权回调和 token 托管要求；
- 封面、歌词、试听/完整音频的展示与缓存授权；
- 用户会员权益、地区版权与开放 API 返回结果之间的关系。

这些未知项不影响无账号 MVP 的技术判断，但会阻断账号连接和内嵌播放的上线决策。

## 主要来源（访问于 2026-08-30）

- [网易官方 Agent Skills](https://github.com/NetEase/skills)
- [网易云音乐官方 CLI npm 包](https://www.npmjs.com/package/@music163/ncm-cli)
- [网易云音乐开放平台个人开发者入口](https://developer.music.163.com/st/developer/apply/account?type=INDIVIDUAL)
- [网易云音乐个人开发者 FAQ](https://developer.music.163.com/st/developer/document?docId=3b75ab8e475d41ca93d91ebd4dfd383f)
- [网易云音乐厂商接入说明](https://developer.music.163.com/st/developer/document?docId=4d1bd372f8b444ddb6bbde7197bea19f)
- [网易云音乐服务条款](https://st.music.163.com/official-terms/service)
- [Apple Media Player](https://developer.apple.com/documentation/mediaplayer)
- [Apple Share Extension](https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/Share.html)
- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Android MediaSessionManager](https://developer.android.com/reference/android/media/session/MediaSessionManager)
- [Android NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService)
- [Google Play：醒目披露与同意](https://support.google.com/googleplay/android-developer/answer/11150561)
