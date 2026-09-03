# 网易云音乐“陪你听”集成：外部能力与可行性证据

> 状态：调研提案｜适用：Murmur iOS、Android 与可能的桌面原型｜核验：2026-08-30｜依据：网易云音乐开放平台及其官方 GitHub、Apple/Android 官方文档、Murmur 现行产品与数据边界；不代表已获网易云音乐授权、功能承诺或生产验收

## 结论先行

**可以做，但要区分三个完全不同的版本。**

1. **现在即可做、风险最低：跳转式“陪你听”。** Murmur 推荐一首歌或歌单，用户点击后在网易云音乐中播放；用户主动把歌曲链接或名称带回 Murmur 交流。Murmur 不读取账号、当前曲目或音频流。这是移动端最小可行产品。
2. **可以做桌面体验原型：官方 `ncm-cli`。** 网易云音乐 2026 年已公开个人开发者入驻和官方 CLI；CLI 可扫码授权、搜索、推荐、管理歌单，并用 mpv 或 macOS 网易云音乐客户端播放、暂停、切歌、读播放状态。它适合先验证“AI 陪伴 + 音乐控制”的体验，不等于 iOS/Android 可嵌入能力。
3. **完整移动端集成有技术入口，但当前被商务授权卡住。** 官方平台确有 OAuth、音乐 API、播放 URL、SDK、播放数据回传和“一起听”接口；但官方个人开发者 FAQ 明确写明：个人场景暂不支持直接接入开放平台 API，目前个人应用只提供 `ncm-cli`，厂商需联系网易云音乐商务。因此，未取得厂商接入、API 组权限、版权/结算规则和上线验收前，不能承诺 Murmur 能读取当前曲目、控制网易云客户端、在 App 内播放完整版权音乐，或把官方“一起听”直接做成 AI 陪听。

对当前 iOS 优先的 Murmur，建议把正式 MVP 定为“**用户主动选择/接受一首歌 → 跳转网易云播放 → 返回后围绕这首歌交流**”；把官方 CLI 做成内部桌面研究原型；把 OAuth、播放器 SDK/音频流和一起听 API 设为必须拿到书面授权后才启动的第二阶段。

## 证据口径

- **已证实事实**：以下表格中有一手文档直接支持的内容。
- **推断**：根据公开接口边界与操作系统限制形成的工程判断，仍需原型或书面确认。
- **未知项**：公开页面不足以证明，必须由网易云音乐商务、控制台实际权限或真机测试确认。
- 开放平台文档虽可公开浏览，但“文档存在”不代表某个 App ID 已被授权调用该 API；个人开发者 FAQ 是判断当前可用范围的优先证据。

## 官方能力矩阵

| 能力 | 官方证据 | 面向个人开发者 | 对 Murmur 的判断 |
|---|---|---|---|
| 开放平台与个人入驻 | [个人开发者入驻](https://developer.music.163.com/st/developer/document?docId=46be8cfe3f8b476bb1e8b98e765b75ab)说明成年实名用户可入驻、初始化应用并取得 `appId`/`privateKey`，且有每日调用次数上限 | 是，但范围受限 | 可以取得个人 CLI 凭证，不应据此推断移动 App 可直接调用全部 API |
| 厂商商务准入 | [新功能发布记录](https://developer.music.163.com/st/developer/document?docId=4d1bd372f8b444ddb6bbde7197bea19f)说明音乐服务并非免费提供，需提交公司、产品、终端和合作诉求，项目评估通常为 3–5 个工作日 | 企业/厂商评估 | 完整移动集成首先是合作与授权问题，不只是 SDK 工程问题 |
| 个人直接调用 Web/API/SDK | [个人开发者 FAQ](https://developer.music.163.com/st/developer/document?docId=3b75ab8e475d41ca93d91ebd4dfd383f)明确：“个人场景：暂不支持……仅可使用 ncm-cli”；“针对个人 demo 只有 CLI” | **否** | 当前移动产品的硬闸门；厂商入驻需联系网易云音乐商务 |
| 官方 CLI | [CLI 技术指南](https://developer.music.163.com/st/developer/document?docId=2327e302009c437eb02af48f63d6e514)与 [NetEase/skills](https://github.com/NetEase/skills)列出搜索、每日推荐、歌单管理、播放/暂停/切歌/seek/音量/队列/状态；需扫码登录，可用 mpv，macOS 可联动最新网易云音乐客户端 | 是 | 适合 macOS/Linux/Windows 的研究原型；不是 iOS/Android SDK，也不是可随 Murmur App 分发的已证实方案 |
| OAuth / 账号连接 | [H5 登录与唤端登录](https://developer.music.163.com/st/developer/document?docId=0adf20d426564597b9219d4c12bf00f7)提供授权页、`code` 回调、`orpheus://openurl` 唤起；唤回第三方 App 的 schema 需交由网易云音乐配置；[code 换 token](https://developer.music.163.com/st/developer/document?docId=2fa5a885d2644910a4b823dba0c5acf5)与[刷新 token](https://developer.music.163.com/st/developer/document?docId=2066d57bbed2445baeb18429e44b8689)说明 access token 默认 7 天、refresh token 默认 20 天 | 厂商/获授权应用 | 技术上可连接账号；Murmur 尚无可用权限。令牌交换与刷新应在服务端完成，不能把私钥放进客户端 |
| 用户基本信息 | [用户基本信息](https://developer.music.163.com/st/developer/document?docId=8ed9b2f123e44923979596a277733421)返回昵称、头像与会员明细 | 厂商/获授权应用 | 并非“陪你听”首版必需；应最小化申请范围 |
| 搜索、推荐、歌单 | 官方 [CLI 指南](https://developer.music.163.com/st/developer/document?docId=f5b49eae2ab14104b279b6f77902ccb8)与 [Agent Skills](https://github.com/NetEase/skills/blob/master/README.md)证实搜索、每日推荐、红心/歌单分析及歌单创建/写入能力 | CLI 可用；直连 API 需厂商权限 | 桌面原型可验证推荐和建单；移动端先用公开歌曲/歌单链接跳转 |
| 播放控制与播放状态 | 官方 CLI 提供 `pause`、`resume`、`stop`、`prev`、`next`、`seek`、`volume`、`state`；[netease-music-cli skill](https://github.com/NetEase/skills/blob/master/netease-music-cli/SKILL.md)说明可操作 mpv 或 macOS 网易云客户端 | CLI 可用 | 已证实的是 CLI 所控制播放器的状态，不是任意 iPhone/Android 网易云客户端的全局“当前歌曲”接口 |
| 最近播放 / 跨端续播 | 开放平台文档索引包含[最近播放歌曲](https://developer.music.163.com/st/developer/document?docId=1811d8f3db124c65a66edddcef7e70fc)、[跨端续播查询](https://developer.music.163.com/st/developer/document?docId=b8b94936a02241328cdc23a1b572bae7) | 厂商/获授权应用 | 可作为授权后补充上下文，但它们不等同于低延迟“当前正在播放”订阅；实时性和个人权限未知 |
| 版权音乐流 | [获取歌曲播放 URL](https://developer.music.163.com/st/developer/document?docId=3d2c9f695ff24f4ea37611614b7f7856)可返回短期 URL、码率和试听区间；文档明确存在当前端无版权、需要 VIP/单独购买等结果，高品质需另行开通；播放地址通常按音频时长或约 25 分钟有效 | 厂商/获授权应用 | 技术存在，授权和版权是决定条件。不得抓取、缓存或转发流；不能承诺“账号有会员就能在 Murmur 播放全部歌曲” |
| 播放数据上报 | [音乐/长音频播放数据回传](https://developer.music.163.com/st/developer/document?docId=eb0ddaf2efc649e99dffe0677472466a)要求接入方上报开始、结束、实际播放时长、来源、设备等，并参与最近播放/听歌报告 | 厂商/获授权应用，且上线验收必做 | 完整播放器不是只拿 URL；还要实现数据、设备、账号切换与验收规则，工作量和隐私范围显著增加 |
| 官方“一起听” | [创建一起听房间](https://developer.music.163.com/st/developer/document?docId=09cb77284e224545a9e457b9fbd4bd03)、[多人房间二维码](https://developer.music.163.com/st/developer/document?docId=c31bba05ee5c4eedb8537db826eba987)、[房间用户](https://developer.music.163.com/st/developer/document?docId=d48b4bc986a44015b8537db826eba987)以及切歌/结算上报文档公开存在 | 未证实个人可申请；业务 `source` 需“联系业务确定” | 这是多人同步/记录基础设施，不是“AI 作为陪伴者听歌”的现成产品。可在商务沟通时询问，但不能作为当前 MVP 依赖 |
| SDK | 文档中心列出 [SDK 介绍](https://developer.music.163.com/st/developer/document?docId=569f852e98564ea98c9fc033291d2eef)、[播放器](https://developer.music.163.com/st/developer/document?docId=49f5a41f9ec64207ba78d91e96119d59)等厂商文档 | 个人 FAQ 不支持直接接入 | 公开目录证明 SDK 体系存在；Murmur 是否可取得 iOS/Android 包、许可、包体/最低系统要求及审核支持仍未知 |

## 移动端与桌面替代路径

### 1. 移动端深链/网页链接：推荐首版

- 使用 `https://music.163.com/#/song?id=...` 或歌单/专辑链接，让系统交给网易云音乐 App 或浏览器打开。Apple 的 [`UIApplication.open`](https://developer.apple.com/documentation/uikit/uiapplication/open%28_%3Aoptions%3Acompletionhandler%3A%29)只保证把 URL 交给能处理它的 App；[`canOpenURL`](https://developer.apple.com/documentation/uikit/uiapplication/canopenurl%28_%3A%29)可检测自定义 scheme，但需要在 `Info.plist` 声明查询 scheme。
- 官方 OAuth 文档还公开了 `orpheus://openurl?...`，但该路径主要用于在网易云音乐内打开授权页；歌曲/歌单具体 scheme、回跳行为和 App Store 版本兼容性必须真机验证。
- 这条路径能“带用户去听”，不能可靠读取正在播放什么、监听暂停/切歌或控制网易云客户端。

### 2. iOS 系统媒体会话：不能当跨 App 读取接口

- Apple 将 [`MPNowPlayingInfoCenter`](https://developer.apple.com/documentation/mediaplayer/mpnowplayinginfocenter)定义为设置“**你的 App 播放的媒体**”的 Now Playing 信息；[`MPRemoteCommandCenter`](https://developer.apple.com/documentation/mediaplayer/mpremotecommandcenter)用于让“你的 App”响应系统/配件的媒体命令。
- [`AVAudioSession.isOtherAudioPlaying`](https://developer.apple.com/documentation/avfaudio/avaudiosession/isotheraudioplaying)最多只能表明其他 App 是否正在播放音频，不能提供歌曲、进度或来源；MusicKit 的 [`SystemMusicPlayer`](https://developer.apple.com/documentation/musickit/systemmusicplayer)控制的是 Apple Music App，也不能替代网易云集成。
- **推断**：这些公开 API 不能让 Murmur 任意读取或控制网易云音乐的 Now Playing 会话。未找到 Apple 提供给普通第三方 App 的跨 App 当前曲目/控制 API。辅助功能、私有 API、读其他进程或模拟点击不应作为 App Store 产品方案。

### 3. Android MediaSession：技术可试，但不适合作为默认核心路径

- Android 的 [`MediaSessionManager.getActiveSessions`](https://developer.android.com/reference/android/media/session/MediaSessionManager)可取得活跃媒体控制器；访问其他 App 会话需系统级 `MEDIA_CONTENT_CONTROL`，或让用户显式启用 `NotificationListenerService`。[`MediaController`](https://developer.android.com/reference/android/media/session/MediaController)可读取发布出的 metadata/playback state 并调用 transport controls。
- [`NotificationListenerService`](https://developer.android.com/reference/android/service/notification/NotificationListenerService)需要用户在系统设置中显式授予通知访问；这不是普通运行时权限。
- **推断**：如果网易云音乐 Android 版发布标准媒体会话，Murmur 在用户授予通知访问后可能读到当前曲目并尝试暂停/切歌；但元数据完整性、厂商 ROM、后台限制和网易云版本兼容性都未验证。通知读取权限与私人回忆产品的隐私定位不匹配，不应成为默认或 iOS/Android 一致体验。

### 4. 桌面官方 CLI：适合体验验证

- 用独立的内部 macOS 原型调用 `ncm-cli`，让 AI 在用户明确命令后搜索、推荐、播放、暂停、切歌和读状态。
- 只在本机保存 CLI 登录和偏好；不把网易云私钥、token 或听歌历史放进 Murmur 现有数据库；不把该原型宣传为移动端能力。
- CLI 的播放器后端可以是 mpv，也可以是 macOS 网易云音乐客户端；Windows/Linux 的网易云客户端播控未获官方证实。

## 第三方与逆向方案风险

| 方案 | 可行性 | 风险判断 |
|---|---|---|
| 非官方 `NeteaseCloudMusicApi`、抓包复刻 Web 接口 | 技术样例多、能力看似完整 | **不采用。** 登录加密、cookie、风控、接口随时变化；没有官方授权和音乐转授权证据，存在账号封禁、隐私、著作权与服务中断风险 |
| 抓取/转发音频播放 URL | 短期可能工作 | **禁止进入产品。** URL 有版权、端类型、会员与有效期边界；转发或缓存会把 Murmur 变成未授权音乐分发者 |
| iOS 辅助功能/私有 API/进程数据读取 | 上架 App 基本不可行 | 审核、沙箱、稳定性和用户信任风险高；不应原型化为正式路线 |
| Android 通知监听 + MediaSession | 需用户高敏权限，部分设备可能工作 | 仅可做隔离技术实验；必须解释权限、只读取网易云媒体通知并可随时关闭，不上传通知全文 |
| 桌面 UI 自动化/AppleScript 模拟点击 | 可能控制当前客户端 | 脆弱且可能触碰未经授权的兼容/插件边界；官方 `ncm-cli` 已存在，应优先用官方 CLI，不走 UI 逆向 |

网易云音乐[服务条款](https://st.music.163.com/official-terms/service)包含对未经授权第三方兼容软件、插件、逆向工程及复制/修改客户端与服务交互数据的限制。[隐私政策](https://st.music.163.com/official-terms/privacy)说明平台会处理播放历史、收藏、搜索和设备日志，“一起听”还可能涉及位置用于展示距离。法律与音乐版权结论仍需正式法务/商务确认，本报告不构成法律意见。

## 推荐的最小可行路径

### 产品 MVP：跳转式“陪你听”

1. AI 在一次明确的用户请求或对话节点提出 1 首歌或 1 个歌单；不自动开始播放。
2. 展示曲名、艺人、来源为网易云音乐和“去网易云播放”按钮；使用官方可点击链接跳转。
3. 用户返回 Murmur 后，可主动点“就听这首”或粘贴/分享歌曲链接；Murmur 只把这次选择作为当前交流的临时上下文。
4. 默认不把曲目、播放时间、情绪推断写入服务端记忆；若未来要形成“这首歌与这段回忆”的关系，必须单独显示并由用户确认。它是交流素材，不自动成为日记成稿。

成功指标：跳转成功率、返回后继续交流率、用户主动确认歌曲的比例；不以持续监听时长、通知读取覆盖率或抓取完整歌单为成功指标。

### 内部研究原型：官方 CLI

在开发者 Mac 上用个人开放平台 + `ncm-cli` 验证三件事：用户是否真的需要语音/文字控制播放、AI 在什么时候说话不会打扰音乐、当前曲目是否能自然触发回忆交流。原型不接 Murmur 生产服务，不导入现有用户数据。

### 完整集成前的硬闸门

只有以下材料齐全，才进入移动端 OAuth/SDK/音频流设计：

- 网易云音乐书面确认 Murmur 的主体可按厂商接入，并明确允许 iOS/Android 使用的 API 组和 SDK；
- 明确搜索、推荐、歌单、OAuth、当前播放/最近播放、播放 URL、一起听各自的权限、限额、地域与会员规则；
- 提供沙箱/测试 App ID、SDK 包和许可、上线/播放数据验收标准；
- 明确音频是否可在 Murmur 内播放、是否允许 AI 语音与音乐混音/打断、是否需要展示品牌与会员提示；
- 明确用户撤销授权、token 删除、账号删除、听歌数据上报和数据跨境/共享条款。

## 仍需真机与商务验证

- iOS/Android 上歌曲、歌单、专辑 HTTPS 链接是否稳定唤起网易云音乐，未安装时如何回落；返回 Murmur 的流程是否顺畅。
- `orpheus://` 在当前 iOS/Android 网易云版本上的白名单、参数与回跳行为；Android 对应 scheme/intent 是否公开支持。
- `ncm-cli state` 对 mpv 与 macOS 网易云客户端分别返回哪些字段、切歌延迟、退出与重登行为、会员歌曲覆盖率。
- Android 网易云是否持续发布标准 `MediaSession`、metadata 是否包含曲名/艺人/封面、控制命令是否被接受；锁屏、蓝牙、后台、双播放器和主流 ROM 情况。
- 官方“一起听”API 是否允许 Murmur 这种 AI 陪伴场景、是否必须两个真实网易云账号、房间同步是否包含音频/进度还是只做数据记录。
- OAuth 唤端所需 schema 是否能为 Murmur 配置；回调域名、审核、撤销授权和 token 轮换在沙箱中是否可闭环。
- SDK 是否支持当前 Murmur 的 iOS/Android 最低版本、Swift/Kotlin 架构、后台音频、包体与 Store 审核；是否与现有 SSE/身份系统隔离。
- 音频版权：普通会员、SVIP、数字专辑、云盘、灰歌、地区限制、试听、音质降级的真实返回；失败提示必须来自官方规则。

## 对 Murmur 现有边界的影响

- 首版不需要修改现有 `POST /v1/moments`、SSE、设备身份或服务端记忆 wire 契约；歌曲链接可作为用户明确提交的普通文本素材处理。
- 若进入 OAuth，新增独立的“外部音乐授权”凭证域；令牌只存服务端加密存储，与 Murmur 邀请身份分离，可独立撤销和删除。
- 若保存听歌上下文，应将“用户选择的歌曲”“平台上报的播放事实”“AI 推断的情绪/回忆”分成三种数据；AI 推断不得冒充用户事实。
- 若进入播放器，需新增音频焦点、来电/耳机中断、后台、播放上报、版权错误和账户切换状态机，不能塞进当前聊天作业生命周期。

## 证据限制

- 核验日期为 2026-08-30；网易云开放平台个人能力于 2026 年出现，变化可能很快，进入实现前需再次读取 FAQ 与控制台。
- 部分开放平台目录包含厂商、IOT、Web、移动端、定制和个人文档；公开可见不等于默认授权。
- 本轮未登录开发者控制台、未申请 App ID、未执行 CLI 登录，也未在真机调用接口；所有“可调用”结论必须以实际获批凭证和验收为准。
