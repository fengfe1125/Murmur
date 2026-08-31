# 全球音乐平台完整双向会话集成对比（2026-08-30）

> 状态：产品与技术可行性调研，不代表任何平台已批准 Murmur
> 核验日期：2026-08-30（Asia/Shanghai）
> 证据口径：只引用平台官方开发者文档、官方政策、官方支持页或平台官方 GitHub 组织；公开文档存在不等于生产准入、内容转授权或商店审核通过
> 范围：Apple Music / MusicKit、Audius、Spotify、YouTube Music / YouTube、SoundCloud、Amazon Music、TIDAL、Deezer，并补充 Jamendo、Bandcamp、用户自有音乐与中国平台
> 事实标签：**已证实**为官方资料直接支持；**推断**为基于公开边界的工程判断；**未知**需平台书面答复或真实凭证/真机验证

## 结论先行

若验收要求是“用户授权音乐账号、双方在聊天中发送结构化歌曲卡、点击后在 Murmur iOS/Android 内播放完整曲目”，截至 2026-08-30：

1. **Apple MusicKit 排名第一，是主流商业曲库中最适合进入双端生产型 PoC 的公开路线。** MusicKit 明确允许在 App 内搜索 Apple Music、访问获授权用户的资料库/播放列表、创建播放列表并播放目录内容；同时提供 Swift、Android 和 Web 能力。完整目录播放通常要求用户具备有效 Apple Music 订阅。
2. **Audius 排名第二，是最快完成海外开放目录 MVP 的路线。** 官方 API 提供 OAuth 2.0 + PKCE、搜索、用户资料库/歌单、稳定 track ID 和 stream endpoint；免费计划为每秒 10 次、每月 50 万次。2025 年官方把 API Terms 与 Open Music License 纳入体系，明确面向第三方音乐 App，但仍须实时尊重创作者对每首歌的 API/许可设置并完成法务核验。
3. **SoundCloud 技术匹配度很高，但标准条款下仍不能直接上线商业 Murmur。** 官方 API 有 OAuth 2.1、搜索、likes/playlist、stream URL、自定义播放器与 Widget；但商业、多平台聚合、AI 输入与内容权利限制要求书面特批。
4. **Amazon Music 是能力方向匹配但尚不能公开自助接入的商务候选。** 官方 Web API 预览已有 OAuth、目录、用户资料库、播放及所有订阅层级能力，并公开提及 AI & Voice；然而仍是 closed beta，只限获批开发者并要求认证。
5. **Spotify 不满足“由 Murmur 原生播放器承载音频”。** iOS/Android SDK 是 App Remote，控制已安装的 Spotify App；Web Playback SDK 不是原生移动播放器。Spotify 还禁止商业 streaming integration、音频重叠及把 Spotify Content 输入 AI 模型。
6. **YouTube 只能作为前台可见的视频 iframe 卡片。** 它没有对应 MusicKit 的 YouTube Music第三方目录/原生音频 SDK，且禁止分离音频、隐藏播放器和后台播放。
7. **TIDAL 与 Murmur 目标存在明确条款冲突；Deezer 新个人 API access 已关闭。** 两者都不进入近期路线图。

地区覆盖保留为部署信息，但不作为本次全球方案排名的硬门槛。**建议：** 用 Audius 快速验证完整双向发歌 MVP，用 Apple MusicKit 做海外正式主流目录；SoundCloud先询证商业与 AI 权限，Amazon Music申请 closed beta/partner。本地文件或用户自建音乐库作为不依赖订阅的长期兜底。

## 1. 完整验收口径

### 1.1 用户体验验收

一个平台只有同时满足以下条件，才算“完整双向音乐会话”，不能以网页能打开或能遥控原 App 替代：

1. 用户通过正式 OAuth / 平台授权框架登录；Murmur 不保存用户名、密码或网页会话 Cookie。
2. Murmur 能通过正式 catalog search 查找曲目，并取得平台稳定 ID、标题、艺人、专辑、封面、时长、显式内容标记和可播性。
3. AI 或用户生成的是结构化 `music_track` 消息，而非只有歌曲名称或任意 URL。
4. Murmur 发出的曲目卡可由用户点击，在 Murmur 当前 iOS/Android 界面内开始完整曲目播放。
5. 用户可通过平台搜索、资料库、播放列表或平台分享入口选择曲目，再把同一结构化卡片发给 Murmur。
6. 播放器具备 play/pause/seek/queue、进度、状态/错误回调、版权/订阅提示、音频焦点、中断恢复、后台和锁屏控制。
7. 退出授权、删除账号或删除曲目消息后，token、用户资料库数据和缓存能按平台政策撤销/清理。

### 1.2 统一消息与适配器要求

建议平台无关消息至少包含：

```text
MusicTrackMessage
  platform              apple_music | spotify | youtube | soundcloud | ...
  catalog_id            平台正式 ID；不得用 CDN/临时播放 URL 作为 ID
  storefront_or_market  查询时的国家/地区
  canonical_url         平台落地页
  title / artists / album / artwork / duration
  explicit_rating
  availability_snapshot 发送时的可播性快照，不承诺接收时仍可播
  sender                 user | murmur
  user_initiated_play    必须由用户点击触发
```

曲目卡元数据应按平台要求刷新；播放时必须重新检查当前账号、订阅、地区和版权状态。跨平台“同一首歌”匹配属于 Murmur 自己的推断，不能覆盖平台 ID，也不能把一个平台的播放 URL交给另一个平台。

### 1.3 播放方式术语

| 类型 | 本报告定义 | 是否满足严格 App 内播放 |
| --- | --- | --- |
| Native in-app playback | 平台官方原生 SDK/系统框架在 Murmur 进程或其受支持播放器内输出音频，Murmur获得正式播放状态 | **满足** |
| Web playback | 官方 JS/DRM 播放器运行在浏览器或 WebView | 仅能作为降级；不等同原生，后台/锁屏和商店行为另验 |
| App Remote | Murmur 控制设备上的平台官方 App，音频与系统媒体会话属于官方 App | **不满足严格口径**，但可做远程控制模式 |
| iframe / Widget | 平台托管播放器嵌在可见网页区域 | **不满足严格口径**；通常受尺寸、可见性、广告和后台限制 |
| Deep link | 跳到平台 App/浏览器 | 只满足“去播放”，不满足 Murmur 内播放 |

## 2. 全球平台证据矩阵

### 2.1 核心能力

| 平台 | 正式用户授权 | Catalog / 稳定标识 | Library / playlist | 移动端完整播放方式 | 订阅/准入 | 中国大陆 | 严格目标结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Apple MusicKit** | `MusicAuthorization`、Music User Token | Catalog Search；Apple Music resource ID，需带 storefront 语境 | 读取资料库、搜索资料库、收藏、最近播放、创建/修改播放列表 | **iOS native MusicKit；Android native MusicKit SDK；Web MusicKit JS** | 目录完整播放需 `canPlayCatalogContent`，通常需有效订阅；Apple Developer 配置 MusicKit | **官方可用** | **条件 Go，排名 1** |
| **Audius** | OAuth 2.0 Authorization Code + PKCE；支持移动 custom scheme | Track ID、canonical URL、track/user/playlist search | 收藏、repost、用户 tracks、library 与 playlists | 官方 stream endpoint + Murmur 原生播放器 | 免费计划可自助申请；每首歌须服从 OML/创作者设置 | 非排名硬门槛 | **Go 做海外 MVP，排名 2** |
| **Spotify** | OAuth 2.0 + scopes | Web API search；Spotify URI/ID，market relinking | saved library、private/collaborative playlists、queue/recent | iOS/Android **App Remote** 控制 Spotify App；Web Playback SDK 是 Web DRM 播放 | Web Playback/Premium；Dev Mode 5 用户，扩展模式仅组织申请；商业 streaming 被禁止 | **官方列表无中国大陆** | **不满足 native；远程/网页降级** |
| **YouTube / YouTube Music** | Google OAuth 2.0 | Data API search；video ID，不是标准录音 track ID | YouTube playlists 可读写；没有等价的 YouTube Music library API | 官方 IFrame Player，前台可见视频；无公开原生音乐播放 SDK | 普通嵌入不要求 Premium；配额/审核；不可后台或 audio-only | **YouTube Music 官方列表无中国大陆** | **仅视频卡，No-Go 作为音乐播放器** |
| **SoundCloud** | OAuth 2.1 + PKCE；client credentials 可读公开资源 | Track URN/ID、permalink、搜索 playable tracks | likes、playlists、recently played、用户 tracks | 官方 Widget iframe，或 API transcoding/stream URL + 自建 native player | 开发者需 Artist Pro；商业/聚合/AI 受严格限制 | **未找到官方大陆服务承诺** | **技术可行，条款 No-Go，需逐案许可** |
| **Amazon Music** | Login with Amazon / OAuth 2.0 | Closed-beta catalog/search、Amazon Music IDs | closed-beta library/user/views | Web Playback API 预览；未找到面向普通开发者的公开 iOS/Android播放 SDK | **closed beta，仅已批准开发者，发布前认证** | **未证实大陆可用/获批** | **商务候选，当前 No-Go** |
| **TIDAL** | OAuth 2.1 + PKCE/scopes | Open API catalog IDs/search | user scopes/playlist 能力 | Web/iOS/Android SDK；官方 Player module / embeds | 默认只允许非商业；Production Mode 需审核 | 官方列表仅香港，不含大陆 | **条款明确冲突，No-Go** |
| **Deezer** | 旧 OAuth/API 体系 | 旧 API 有 track/search | 旧 SDK 样例有 favorites/playlists | 旧 iOS/Android/JS SDK 样例 | 新个人 API access 已关闭；维护与生产准入不明 | 未证实 | **No-Go / 暂缓** |

### 2.2 播放、后台、锁屏与商业边界

| 平台 | 谁真正输出音频 | 后台/锁屏 | 商业与 AI/聊天风险 |
| --- | --- | --- | --- |
| Apple MusicKit iOS | Murmur 内的 MusicKit player | 原生能力可集成；仍需配置 Audio Session、后台模式、Now Playing 并真机验收 | 用户必须发起播放且有标准控制；不得收费或用广告间接变现 Apple Music access；资料库数据不得用于识别/广告；“特定时刻播放特定歌曲”等深度同步可能需额外权利 |
| Apple MusicKit Android | Murmur 内的 MusicKit Android SDK | 需要按 SDK/Android MediaSession 实测；公开主页只承诺直接 App 内播放 | 同 Apple Music 服务与品牌/元数据边界；中国发行渠道和 SDK 交付需真机核验 |
| Audius stream | Murmur 的 AVFoundation / Media3 player | 由 Murmur 完整实现和真机验证 | 只播放当前 API 仍允许访问的曲目；不得把“API 可播”误解为允许下载、改编或超出该曲目许可的使用 |
| Spotify App Remote | Spotify App | Spotify App 负责 audio focus、来电、锁屏、缓存；Murmur 前后台时应断开/重连 Remote | 不是 Murmur 内原生播放；Spotify App 必须安装。不得把 Spotify Content 输入 AI；不得混音/重叠其他音频；商业 streaming integration 禁止 |
| Spotify Web Playback | WebView/browser 中的 Spotify Web player | iOS 需用户手势；移动 WebView 后台与锁屏不能作为稳定承诺 | Premium、DRM、encrypted-media/autoplay；仍受商业 streaming 和 AI 禁令 |
| YouTube IFrame | 可见的 YouTube iframe | 政策明确禁止窗口关闭/最小化后的后台播放 | 不得隐藏/改造播放器、分离音频、屏蔽广告；必须显示标题/缩略图/品牌并保持标准体验 |
| SoundCloud Widget | SoundCloud iframe | 取决于 WebView，不应承诺系统级后台 | 商业嵌第三方 User Content、替代点播服务、多服务聚合和 AI 输入均被限制 |
| SoundCloud stream URL | Murmur 自建 native player | 技术上由 Murmur 实现 | stream URL 是受授权的播放入口，不得下载/持久化；Murmur 商业 AI 场景仍须 SoundCloud 与权利人许可 |
| Amazon Web Playback | Amazon 控制的 Web API queue/playback | closed-beta 规范与认证决定；公开预览不足以承诺原生后台 | Amazon Music 保持队列与订阅层级控制；只可在批准与认证后上线 |
| TIDAL Player | 官方 Web/iOS/Android Player module | 原生 SDK 形态可支持，具体锁屏需 PoC | 标准条款禁止商业、messaging app、AI、语音控制与跨服务聚合；必须先获明确书面豁免/合作 |

## 3. 平台逐项核验

### 3.1 Apple Music / MusicKit — 条件 Go

#### 已证实能力

- [MusicKit 总览](https://developer.apple.com/musickit/)明确：用户授权后，App/网站可以搜索 Apple Music catalog、访问本地/云端资料库、创建播放列表、把歌曲加入资料库并播放目录中的歌曲；API 覆盖 Apple 平台、Android 和 Web。
- Swift [MusicKit framework](https://developer.apple.com/documentation/musickit)提供 `MusicAuthorization`、`MusicCatalogSearchRequest`、`MusicLibraryRequest`、`MusicLibrarySearchRequest`、`MusicPersonalRecommendationsRequest`、`MusicRecentlyPlayedRequest`，以及 App 内播放支持。
- [Music User Token](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)用于访问订阅者资料库；Apple 平台与 Web 自动管理，Android 需按 Android SDK 流程取得并附带 token。
- [`MusicLibrary`](https://developer.apple.com/documentation/musickit/musiclibrary)可添加资料库项目、创建播放列表、向播放列表添加歌曲，并编辑 App 创建的播放列表。
- [`MusicSubscription`](https://developer.apple.com/documentation/musickit/musicsubscription)通过 `canPlayCatalogContent` 和 `hasCloudLibraryEnabled`区分当前用户是否能播放订阅目录、修改云资料库。
- Apple [媒体服务可用性](https://support.apple.com/zh-cn/118205)明确列出中国大陆 Apple Music。

#### 播放类型

- iOS：**native in-app playback**。MusicKit 提供应用播放器；不是控制另一个音乐 App，也不是 iframe。
- Android：**native in-app playback**。MusicKit 首页明确提供 Android SDK，让用户登录 Apple Music 并在第三方 App 内直接播放。
- Web：MusicKit on the Web 可直接在浏览器播放，属于 **Web playback**，适合 Web 产品而非替代双端原生 SDK。

#### 条款硬边界

Apple [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) 对 MusicKit 有非常具体的约束：

1. 用户必须主动发起 Apple Music stream，并能使用 play/pause/skip 等标准控制；因此 Murmur 可以发卡，但不应由 AI 无操作自动开播。
2. App 不得收费或通过广告、索取用户信息等方式间接变现 Apple Music access。
3. 不得下载、上传或分享 MusicKit 音乐文件。
4. MusicKit 不是更深音乐同步/改编权的替代品；若把特定歌曲与特定时刻、视觉或可分享作品绑定，需另行取得权利人许可。
5. 资料库、收藏等用户数据必须在 purpose string 中披露；收集的数据只能用于支持/改善 App 体验，不能识别用户或定向广告。

#### 对 AI 会话的安全产品形态

- AI 可生成搜索词或推荐理由，Murmur 通过 MusicKit 正式搜索取得卡片；不要让模型自行伪造 Apple Music ID。
- 卡片播放必须由用户点击；AI 语音不要自动与音乐重叠，也不要把歌曲音频、歌词或大规模资料库数据送入模型训练。
- 向第三方模型服务发送 Apple Music 用户资料库或播放历史前，必须核验 Apple 的“supporting or improving app experience”边界、用户同意、数据处理协议和中国数据合规。最低风险方案是只把用户明确选中的标题/艺人作为当前会话文本，原始 token、完整资料库和音频不进入模型。

#### 未知/PoC 项

- Android MusicKit SDK 在中国大陆下载、登录、订阅购买、DRM 和国产 ROM 上的实际表现；官方支持页只证明服务可用，不保证 SDK 的每条链路。
- iOS/Android 对本地音乐、云资料库和 Apple Music catalog ID 的等价关系；消息应保存 storefront 并在接收时重新解析。
- 后台、锁屏、耳机/蓝牙、中断恢复的双端细节，以及 Apple 对 Murmur“AI 推荐后用户点播”场景的最终审核解释。

### 3.2 Spotify — 目录/授权强，原生播放不匹配

#### 已证实能力

- Spotify [Authorization](https://developer.spotify.com/documentation/web-api/concepts/authorization)采用 OAuth 2.0 和 scopes；Authorization Code flow 支持 refresh token。
- [Web API](https://developer.spotify.com/documentation/web-api)提供搜索、稳定 Spotify URI/ID/metadata、saved library、播放列表、recently played、queue 和播放控制。
- [`streaming`](https://developer.spotify.com/documentation/web-api/concepts/scopes) scope 仅用于 Web Playback SDK且用户必须 Premium；`app-remote-control` 用于 iOS/Android App Remote。

#### 播放类型必须准确表述

- [iOS SDK](https://developer.spotify.com/documentation/ios/getting-started)与[Android SDK](https://developer.spotify.com/documentation/android)是 **App Remote**。它们连接并控制 Spotify 官方 App，读取 PlayerState；音频、缓存、来电、audio focus 和锁屏由 Spotify App 处理。Spotify App 必须安装。它不是将 Spotify decoder/player 嵌进 Murmur。
- [Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk)会在浏览器中创建 Spotify Connect device并播放受保护内容，属于 **Web playback**。官方称支持移动 Safari/Chrome，但 iOS 需要用户手势，iframe 需允许 `encrypted-media` 与 `autoplay`；WebView 的后台、锁屏和 App Store 稳定性不能等同 native SDK。

#### 准入和区域

- 2026 年 [Development Mode](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)要求 App owner 为 Premium，最多 5 个授权用户；扩展额度只接受已成立的组织，且要求已上线服务。Spotify 明确说 Dev Mode 不应作为规模化业务基础。
- Spotify 官方[可用国家/地区](https://support.spotify.com/de/article/where-spotify-is-available/)亚洲列表有香港、澳门、台湾等，但没有中国大陆。因此它不适合 Murmur 中国大陆主市场。

#### 商业与 AI 风险

[Spotify Developer Policy](https://developer.spotify.com/policy)及 SDK 入门页的相关限制包括：

- streaming applications 不得商业化；
- 不得让 Spotify 音频与其他音频 segue、mix、remix 或 overlap；这会直接限制 AI 语音与音乐并播；
- 不得用 Spotify Platform 或 Spotify Content 训练 AI/ML，也不得以其他方式把 Spotify Content 输入 AI 模型；
- 卡片显示 metadata/cover art 时必须有 Spotify 归因和回链，播放时必须同时显示相关 metadata 与封面；
- 不得复制 Spotify 核心体验，产品必须有独立价值。

**推断：** 让 LLM读取 Spotify 返回的元数据、资料库或历史来生成推荐，有较高概率落入“ingest Spotify Content into AI model”。即使技术可做，也必须先取得 Spotify 书面解释。可以把 AI 生成的普通自然语言查询发送给 Spotify Search，再由非 AI 逻辑展示结果，但不应把搜索结果、封面、音频或用户库回灌模型。

#### 结论

Spotify 可提供“聊天卡 + 遥控 Spotify App”或“前台 Web 播放器”版本，但不满足严格 native in-app playback；商业 streaming、AI与音频重叠是与产品本身相关的实质阻断，地区则是部署覆盖限制。**不作为第一阶段完整平台。**

### 3.3 YouTube Music / YouTube — 只适合作为视频卡

#### 已证实能力

- [YouTube Data API](https://developers.google.com/youtube/v3/docs)支持 OAuth 2.0、API key、video/playlist/channel JSON resources；私有用户数据和写操作必须 OAuth。
- [`search.list`](https://developers.google.com/youtube/v3/docs/search/list)可搜索视频与播放列表；[`playlists.list`](https://developers.google.com/youtube/v3/docs/playlists/list)及播放列表实现指南支持读取、创建和修改用户播放列表。
- 官方没有公开的“YouTube Music catalog + music library + native audio player”开发者产品。Data API 的核心标识是 video ID；同一录音可能存在官方 MV、Art Track、用户上传、歌词版等多个视频，不能把它当跨平台 canonical recording ID。

#### 播放类型与政策

- [IFrame Player API / parameters](https://developers.google.com/youtube/player_parameters)是正式第三方播放方式，要求播放器 viewport 至少 200×200；这是 **iframe/Web playback**，不是原生音频 SDK。
- [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies)及[合规指南](https://developers.google.com/youtube/terms/developer-policies-guide)禁止：隐藏/替换标准播放器、分离音频、下载、屏蔽广告、移除标题/缩略图、以及在用户当前不可见的页面/窗口中后台播放。
- 因而不能做“小卡片只出声音”、锁屏继续、AI 聊天页切走仍播的音乐播放器。可以做用户点击后在当前界面显示完整、可见、合规尺寸的视频播放器卡片。

#### AI、数据与区域

- API 数据和用户授权数据受缓存、30 日重新验证、删除、隐私和禁止推断/派生数据等限制；不应把播放历史或视频内容批量输入模型。对不确定的 AI 推荐/聊天用例，官方建议申请 API Compliance Audit。
- [YouTube Music 可用地区](https://support.google.com/youtubemusic/answer/6313540?hl=zh-Hans)包括香港、台湾等，但没有中国大陆。

#### 结论

YouTube 能实现“双方发送 YouTube 视频卡 + 用户点击后前台观看”，不能实现 Murmur 目标中的音频型后台/锁屏播放器。若产品接受视频卡，它是独立内容适配器；若必须是“陪你听”，则 **No-Go**。

### 3.4 SoundCloud — API 完整，标准条款不允许本项目形态

#### 已证实能力

- [API Guide](https://developers.soundcloud.com/docs/api/)提供 OAuth 2.1 + PKCE、Authorization Code与Client Credentials；公开资源也必须带 access token。
- [API specification](https://developers.soundcloud.com/docs/api/explorer/)包含 track/playlist/user search、likes、用户 playlists、recently played、URL resolve、transcoding/stream URL 等。
- 官方明确允许两种播放：嵌入 [Widget API](https://developers.soundcloud.com/docs/api/html5-widget)；或取得 stream/transcoding URL 后使用自定义播放器。因此技术上可做 native audio player，但必须持续使用授权、不得保存媒体副本。
- 2026 年官方重新开放自助 API key；开发者账号需 [Artist Pro](https://developers.soundcloud.com/docs/api/register-app)。

#### 条款冲突

[SoundCloud API Terms](https://developers.soundcloud.com/docs/api/terms-of-use)明确：

- SoundCloud 不把 User Content 权利转授给开发者；开发者需自行取得必要许可；
- 未经明确许可，不得构建聚合多个用户内容的替代点播服务，也不得把 SoundCloud 与其他服务的内容做聚合播放体验；
- 不得将 User Content 用于训练/开发 AI，或作为 AI 技术输入；
- 商业使用仅限创作者上传/推广自身内容等列举场景，其他商业使用须 SoundCloud逐案批准；
- 不得抓取、stream-rip、缓存永久副本；必须正确归因 Uploader 与 SoundCloud并回链。

Murmur 是多平台聊天产品，AI 会围绕曲目生成内容，且可能商业化。这三点分别触及“跨服务聚合”“AI 输入”“不可接受商业使用”。即使 API 自助注册成功，也不能据此上线。

#### 结论

作为技术模型，SoundCloud 的 OAuth + 搜索 + stream URL 很接近验收要求；作为 Murmur 产品，标准条款是 **No-Go**。只有 SoundCloud 对“商业聊天卡、AI 只读取用户明确选择的最小元数据、与其他平台并列但不混合播放”的具体方案给出书面许可，才进入 PoC。

### 3.5 Amazon Music — 能力完整但 closed beta

#### 已证实能力

- [Amazon Music Developer Portal](https://developer.amazon.com/docs/music/landing_home.html)列出 Websites & Apps 与 AI & Voice，但明确 Web API 当前为 closed beta。
- [Web API Overview](https://developer.amazon.com/docs/music/API_web_overview.html)说明仅已批准开发者可访问，全部端点需要 Login with Amazon OAuth 2.0 token。
- [User API](https://www.developer.amazon.com/docs/music/API_web_user.html)预览包含用户、订阅层级与 library tracks；Browse/Views覆盖目录、推荐和播放历史语境。
- [Playback Overview](https://www.developer.amazon.com/docs/music/API_playback_overview.html)声称可为 Free、Prime、Unlimited 全订阅层级提供完整音频播放，并由 Amazon 服务维护队列、广告/插播和合规状态。
- [Program Requirements](https://www.developer.amazon.com/docs/music/requ_AM-Program-Requirements.html)要求产品在分发前提交审核认证，未经批准不得向终端用户发布。

#### 不足

- 公开资料是 Web API preview；未找到可供普通开发者下载、等价于 MusicKit 的稳定 iOS/Android native playback SDK。
- closed beta意味着无法在没有 Amazon 邀请/批准时验证真实 scope、DRM、播放事件、锁屏和中国大陆地区。

#### 结论

将 Amazon Music 放在**商务候选第 4 位**：它公开表达了 AI & Voice 与完整播放方向，可能比 Spotify/TIDAL 更愿意接受 Murmur，但当前不能自助实施，且公开播放能力主要是 Web API 预览。先申请 closed-beta/partner 评估，未获准前 **No-Go**。

### 3.6 TIDAL — 技术强，但 messaging + AI 被明确禁止

#### 已证实能力

- [Authorization](https://developer.tidal.com/documentation/api-sdk/api-sdk-authorization)为 OAuth 2.1，提供 client credentials、authorization code + PKCE 和 refresh token，scope 控制用户资源。
- [Reference](https://developer.tidal.com/reference)列出 Web API 以及 Web、iOS Swift、Android Kotlin SDK；官方 Player module 是受支持播放入口。
- [Manage apps](https://developer.tidal.com/documentation/api-sdk/api-sdk-manage-apps)允许创建 App；生产额度需提交 TIDAL 审核。

#### 直接阻断

[TIDAL Developer Terms](https://developer.tidal.com/documentation/guidelines/guidelines-developer-terms)默认只允许非商业应用，并明确禁止在 messaging applications 中使用 TIDAL Content、将 TIDAL Content 与 AI/机器智能技术结合、将不同服务聚合、以及非官方 Player 取得 playback。其[Developer Guidelines](https://developer.tidal.com/documentation/guidelines/guidelines-developer-guidelines)还把 AI、语音控制、跨服务整合、商业/企业使用列为需明确书面批准或禁止的用例。

[官方可用地区](https://support.tidal.com/hc/en-us/articles/202453191-Where-Tidal-is-Available)列出香港但没有中国大陆。

#### 结论

Murmur 正是 messaging application + AI 服务，不能靠常规开发者注册上线。除非 TIDAL 针对本产品书面批准商业、聊天、AI 和曲目卡播放，否则 **No-Go**；不值得先做技术 PoC。

### 3.7 Deezer — 新项目暂不可实施

#### 一手资料状态

- Deezer 官方 GitHub 的 [iOS SDK samples](https://github.com/deezer/iOS-sdk-samples)证明旧体系曾支持 OAuth、搜索、用户资料库和播放，但仓库示例与 SDK年代明显陈旧，不能证明 2026 年生产支持。
- 2025 年 Deezer 官方社区经理说明，由于 API 滥用以及与权利人的协议问题，已关闭个人开发者创建新 API access，重新开放条件与时间未定：[官方社区答复](https://en.deezercommunity.com/features-feedback-44/api-auth-impossible-80857)。

#### 结论

若团队没有现存、仍获 Deezer 批准的 production app credentials，无法开始正式接入。旧 SDK 样例不能替代当前准入和条款。**No-Go / 暂缓。**

## 4. AI 推荐与聊天卡的横向风险

“AI 只是推荐歌”并不自动安全。平台对 Content 的定义通常包含 metadata、封面、播放列表和用户数据，而不仅是音频。

| 风险模式 | Apple Music | Spotify | YouTube | SoundCloud | Amazon | TIDAL |
| --- | --- | --- | --- | --- | --- | --- |
| AI 先生成普通搜索词，再调用平台 Search | 可行性最高；仍需用户发起播放 | 技术可行，但不要把结果回灌模型 | 可行，注意独立价值与配额 | 技术可行，商业场景仍需批准 | closed beta，需申报用例 | AI 用例标准条款禁止 |
| 把搜索结果/资料库/历史交给 LLM 排序或总结 | 需最小化、目的披露和第三方数据边界核验 | **高风险/禁止倾向：不得 ingest Spotify Content into AI** | 高风险；API 数据推断、合并和用户数据政策严格 | **禁止 User Content 作为 AI 输入** | 需 closed-beta 合同确认 | **明确禁止** |
| 把音频或歌词送入模型 | 不应做；另有版权/深度集成权利 | 禁止 | 禁止分离音频，另受版权约束 | 禁止/需全部权利 | 未知，预计需特别合同 | 禁止 |
| AI 自动开始播放 | Apple 要求用户发起 | 移动 App Remote/网页仍需用户操作；不得操纵播放量 | 播放完整性要求用户选择 | API 条款要求用户动作明确发起 | 由 beta 规则决定 | 需书面批准，且 AI 已禁 |
| AI 语音与音乐叠加 | 深度同步可能需额外权利；建议先 pause/duck 并咨询 | **不得 overlap Spotify Content 与其他音频** | 不得修改/分离播放器音视频 | 可能需同步/公开表演等额外许可 | 未知 | 禁止 mix/overlap |

首版的最保守实现是：AI 只生成搜索 query 和推荐解释；平台 API 返回结果后由确定性代码展示；用户点击卡片才播放；播放中 AI 如需说话先暂停，不把音频、歌词、完整资料库或历史传给模型。

Audius 当前官方定位明确欢迎第三方音乐 App，但 Open Music License 是曲目级、可动态变化的许可，不自动授予 Murmur 把音频、完整用户资料库或播放历史交给第三方大模型的权利。因此 Audius 首版也采用相同的最小数据原则：模型只产出搜索词与推荐理由，平台返回结果由确定性代码处理，只有用户明确发送的标题、艺人和 Track ID 进入当前会话上下文。

## 5. 初步排名

### 5.1 按 Murmur 完整目标排名

排名优先考虑：完整功能覆盖、是否可公开申请、能否 native in-app playback、商业/AI 合规；地区只作为部署备注，不参与硬性淘汰。

| 排名 | 平台 | 评级 | 原因 |
| --- | --- | --- | --- |
| 1 | **Apple MusicKit** | **条件 Go / 立即 PoC** | 正式授权、目录、library、playlist、稳定模型层、iOS/Android native playback；条款要求可通过“用户点播 + 不变现音乐 access + 不训练”设计控制 |
| 2 | **Audius** | **Go 做开放目录 MVP / 生产前法务复核** | 自助 OAuth、搜索、library/playlist、官方 stream endpoint 和面向第三方 App 的 OML；无需用户订阅，但主流唱片覆盖较弱 |
| 3 | **SoundCloud** | **技术匹配 / 商业与 AI 需书面特批** | 自助 OAuth、搜索、library/playlist、stream URL + native custom player 使技术闭环完整；但商业、多平台聚合与 AI 条款直接冲突，内容权利也不转授 |
| 4 | **Amazon Music** | **商务候选 / 当前 No-Go** | closed-beta 文档的目录、library、所有订阅层级播放与 AI & Voice方向匹配；没有批准就无法开发/发布，且公开播放形态偏 Web API |
| 5 | **Spotify** | **部分能力 / 严格目标 No-Go** | OAuth与目录成熟；移动 SDK仅 App Remote，Web播放非原生；商业 streaming、AI、音频重叠受阻 |
| 6 | **YouTube** | **视频卡 Go / 音乐播放器 No-Go** | 搜索和 iframe 稳定，但只允许前台可见标准视频体验，不允许 audio-only/后台；不是 YouTube Music第三方 SDK |
| 7 | **TIDAL** | **No-Go** | SDK 很完整，但标准条款点名禁止 messaging app、AI、商业和跨服务聚合 |
| 8 | **Deezer** | **No-Go / 暂缓** | 新个人开发者 API access 关闭，旧移动 SDK证据不足 |

### 5.2 决策建议

- **最快完整 MVP：Audius。** 无需用户订阅即可验证正式登录、双向歌曲卡和 Murmur 原生播放器；上线前再复核每首歌的 API/许可状态。
- **主流目录真实用户 PoC：Apple MusicKit。** 先做 iOS，再做 Android；不等待“统一所有平台”才验证产品价值。
- **抽象接口，不抽象能力承诺。** `CatalogAdapter`、`AccountAdapter`、`PlaybackAdapter` 分开；同一平台可以是 `native`、`remote`、`web` 或 `external` 能力级别。
- **不要用 Spotify 验证 native player。** 它适合验证“聊天卡 + 遥控官方 App”，不是替代 MusicKit。
- **YouTube 单列为视频消息。** 不要在产品文案里称其为 YouTube Music 集成或后台音乐能力。
- **SoundCloud 先拿商业 + AI + 多平台聚合的书面许可。** 许可前只可做隔离技术 spike，不进入产品实现。
- **Amazon 申请 closed beta/partner，TIDAL 不先做技术。** 没有书面许可不投入播放器工程。
- **Deezer 暂停。** 等待官方重新开放开发者准入及更新 SDK。

### 5.3 建议实施节奏与粗估

以下是基于 Murmur 现有双端客户端、聊天 wire、账号与数据生命周期边界的工程粗估，不是平台 SLA；真实周期取决于团队人数、开发者账号审批、法务与商店审核，可并行安排产品/法务询证。

| 阶段 | 交付物 | 粗估 |
| --- | --- | --- |
| 0. 平台无关基础 | `MusicTrackMessage`、provider adapter、曲目卡、权限/撤销、播放器状态机、服务端不落播放 URL/token | 3–5 周 |
| 1A. Audius iOS 纵切 | OAuth PKCE、搜索/资料库选择、双向发卡、AVFoundation 完整播放、后台/锁屏与错误态 | 2–3 周 |
| 1B. Audius Android | 同一协议接 Media3、MediaSession、音频焦点、后台与真机兼容 | 3–4 周 |
| 2A. Apple Music iOS | MusicAuthorization、catalog/library、订阅/地区判断、ApplicationMusicPlayer、审核文案 | 3–4 周 |
| 2B. Apple Music Android | 官方 SDK 登录/播放、MediaSession、storefront/订阅/设备矩阵验证 | 4–6 周 |
| 3. 上线加固 | 数据删除、token 撤销、指标/限流、异常恢复、隐私披露、法务与商店材料 | 3–5 周 |

若优先验证体验，**8–12 周可形成可用的 Audius iOS 海外 MVP**。按顺序覆盖 Audius 与 Apple Music 的 iOS/Android 并完成上线加固，单人粗略为 **18–27 周**；由 iOS、Android 两名工程师在公共协议稳定后并行，才可把目标压到约 **14–20 周**。平台审批应从第 0 周开始，Amazon/SoundCloud 的商务询证不进入上述可控工程工期。

## 6. Apple MusicKit 首轮 PoC 闸门

### 6.1 PoC 范围

1. iOS 使用 `MusicAuthorization` 和自动 token 管理；Android 使用官方 Authentication/SDK，不自行抓 Cookie。
2. 用 catalog search生成曲目卡，保存 `platform + catalog_id + storefront`；发送前/播放前刷新 metadata。
3. 用户资料库仅展示搜索和播放列表选择；不要把完整资料库同步到 Murmur 服务端。
4. 用户点击曲目卡后才播放；标准 play/pause/seek/skip，处理未订阅、无版权、显式内容和地区差异。
5. AI 只看到用户明确发送的最小字段（标题、艺人、平台）；不看到 token、完整资料库、播放历史、封面二进制、歌词或音频。
6. 播放中 AI 输出先暂停/降低播放需单独核验权利；第一版可完全不做语音叠加。

### 6.2 终止条件

- Android SDK 在目标市场、目标商店与目标设备矩阵中无法稳定登录/播放，或依赖该发行环境不可用的系统链路；
- Apple 审核认为“AI 指定曲目 + 会话时机”需要额外同步/改编权，而团队无法取得；
- 商业模式实际向用户收费或展示广告来解锁 Apple Music access；
- 必须把用户资料库/历史批量发送给第三方模型才能实现核心体验；
- 无法在双端提供明确撤销、删号和数据删除闭环。

## 7. 需要向候选平台统一询问的问题

1. 是否明确允许 AI 陪伴/聊天 App 搜索并展示结构化歌曲卡？
2. 是否允许把最小 metadata（ID、标题、艺人、专辑）作为用户主动发送的聊天消息保存？保存多久？
3. 是否允许把用户明确选择的一首歌标题/艺人发送给第三方 LLM 用于生成当前回复？是否需要额外 AI 条款或 DPA？
4. AI 自动推荐、用户点击后点播，与“特定时刻播放特定歌曲”或同步权之间的边界是什么？
5. 是否允许 AI 语音期间 pause、duck 或 overlap 音乐？
6. iOS/Android 的正式播放模块分别是 native、WebView 还是 App Remote？音频归属哪个进程/媒体会话？
7. 后台、锁屏、耳机、蓝牙、来电、离线和跨设备播放有哪些支持/禁止？
8. 目录、library、playlist、recently played各自的 OAuth scope、订阅层级、QPS 和生产配额是什么？
9. 曲目 ID 是否跨 storefront/market 稳定？relocation/relink、下架和版本等价如何处理？
10. 卡片 metadata/封面的显示、归因、回链、缓存刷新和删除规则是什么？
11. 商业 App 能否使用？Murmur 订阅、广告或 AI 付费功能会不会构成对音乐 access 的直接/间接变现？
12. 能否提供可交 App Store / Android 商店的书面授权、测试账号和审核联系人？
13. 中国大陆是否在服务、API、SDK、曲库授权、登录和支付支持范围内？

## 8. 开放目录与用户自有音乐

### 8.1 Audius — 海外开放目录 MVP 首选

[Audius SDK](https://docs.audius.co/sdk/)明确提供曲目、用户、歌单搜索，stream 与 upload，收藏、repost、歌单整理，以及 OAuth 2.0 + PKCE 登录。移动端可以注册 custom scheme；[公开 API](https://api.audius.co/v1)有稳定 Track ID、canonical URL resolve、用户 library、playlists 与 `/tracks/{track_id}/stream`。免费计划当前为每秒 10 次、每月 50 万次请求：[Audius API Plans](https://api.audius.co/plans)。

2025 年官方将 API Terms 与 Open Music License 纳入体系，说明 API 曲目可以进入 music apps、games 等第三方服务，同时创作者可以关闭 API access 或选择具体许可：[官方条款更新说明](https://blog.audius.co/posts/audius-terms-of-service-update)。因此它比 SoundCloud 更适合先验证 Murmur：

- 用户无需购买订阅即可登录、收藏和播放；
- Murmur 可通过正式 search 得到 Track ID，再发送结构化卡片；
- stream endpoint 可交给 AVFoundation / Media3，形成真正的 App 内播放器；
- catalog 以独立音乐和创作者上传为主，不能期待 Apple Music 级别的主流唱片覆盖；
- 每次播放前必须重新检查 access/许可，不离线缓存，不把 OML 理解为下载、改编或无限商业使用许可。

结论：**Go 做海外完整 MVP；生产发布前由法务复核当前 API Terms、OML、Murmur 商业模式及每首歌的动态许可。**

### 8.2 Jamendo — 原型可用，商业版需另签

[Jamendo API](https://developer.jamendo.com/v3.0/docs)有 OAuth、目录搜索、收藏、歌单和直接 audio URL，技术上可以满足双向卡片与原生播放。但其 [API Terms](https://devportal.jamendo.com/api_terms_of_use)把免费 API 使用限定在非商业用途；商业 App 需要单独报价，而且每首曲目仍须服从对应 CC 许可，含 `NC` 的曲目不能用于商业 Murmur。

结论：**适合隔离原型或签约后的独立音乐目录，不是无需审批的商业生产源。**

### 8.3 Bandcamp、Navidrome 与本地文件

- [Bandcamp 官方公告](https://blog.bandcamp.com/2026/07/16/discover-improvements-and-subsonic-implementation/)显示，其 2026 年开放的 Subsonic Beta 允许用户用自己生成的凭据，在兼容客户端播放已购买/收藏的 collection，并管理歌单。它适合作为“连接我的 Bandcamp 收藏”，但不是 Murmur 可任意推荐的全站目录，且仍是 Beta。
- [Navidrome Subsonic API 文档](https://www.navidrome.org/docs/developers/subsonic-api/)显示，Navidrome / OpenSubsonic 可让用户连接自己的音乐服务器，支持搜索、封面、歌词、歌单、转码与 stream。平台软件允许第三方客户端，具体音乐权利由服务器所有者负责。
- 用户本地文件是最稳的兜底：系统文件选择器或媒体库授权、原生播放、不上传音频。它没有在线平台推荐目录，但不会把 Murmur 绑定到第三方服务准入。

这三条路线可与 Apple Music/Audius 并存，为没有订阅或希望使用自有音乐的用户提供完整播放路径。

## 9. 中国平台对照

用户已确认国外平台可接受，因此本表不参与全球主路线排序，只保留未来扩展结论。

| 平台 | 官方能力证据 | 缺口 | 结论 |
| --- | --- | --- | --- |
| QQ 音乐 / TME | [QQ 音乐开放平台](https://y.qq.com/music_developer/)明确覆盖移动应用、网站/小程序、社交行业，提供登录授权、OpenAPI、音乐流媒体、个人资产与播控 | 当前公开规则明确 Android/Harmony，未核验当前 iOS SDK 自助交付；资格、报价、AI聊天及商店材料都需商务确认 | **国内首选商务 PoC；iOS 与合同是硬闸门** |
| 酷狗 | [曲库开放计划](https://www.kugou.com/musiclibrary_openplan/?from=listenweb)提供 Android/iOS 无 UI 在线播放组件、正版曲库、按调用计费 | 公开组件以播放为主；完整账号、搜索和用户曲库只在 B2B 伙伴材料中得到部分证明 | **播放型 A；完整账号型 B** |
| 酷我 | 官方合作产品能证明 B2B 播放和 song ID/metadata 存在 | 未找到当前开放平台、个人申请、双端 SDK 与许可文档 | **暂缓，随 TME 商务询证** |
| 咪咕音乐 | [咪咕开放合作](https://open.migu.cn/)明确企业资料审核、测试、商务谈判、合同后商用 | 公开资料未完整证明 Murmur 手机 App 的 OAuth、账户曲库和双端播放器 | **仅商务询价备选** |
| 华为 Audio Connect | [Audio Connect](https://developer.huawei.com/consumer/cn/audioconnenct/)用于合作方把自有内容分发到华为终端 | 方向是内容接入华为，不是第三方 App 使用华为音乐曲库 | **不作为消费音乐 provider** |
| 网易云音乐 | 正式厂商平台能力形状可能覆盖 OAuth、目录与播放 | Murmur 尚未获得厂商 scope、SDK 与播放权；个人入口不足以交付完整能力 | **商务签约后条件 Go** |

国内平台统一的 Stage 0 条件是：provider 书面授权、iOS/Android SDK 当前可交付、聊天卡片与 AI 选曲许可、地域/会员/缓存/分享规则，以及可交商店的内容权利证明。登录 token 与个人资产不得进入聊天 wire；撤回、注销、查阅、删除和个性化推荐退出必须按平台隐私规则实现。

## 10. 主要一手来源

所有链接最后访问：2026-08-30。

### Apple

- [MusicKit 总览](https://developer.apple.com/musickit/)
- [MusicKit framework](https://developer.apple.com/documentation/musickit)
- [MusicKit user authentication](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)
- [MusicLibrary](https://developer.apple.com/documentation/musickit/musiclibrary)
- [MusicSubscription](https://developer.apple.com/documentation/musickit/musicsubscription)
- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple 媒体服务的提供情况](https://support.apple.com/zh-cn/118205)

### Audius / 开放目录

- [Audius SDK](https://docs.audius.co/sdk/)
- [Log In with Audius](https://docs.audius.co/developers/guides/log-in-with-audius/)
- [Audius REST API](https://api.audius.co/v1)
- [Audius API Plans](https://api.audius.co/plans)
- [API Terms 与 Open Music License 更新说明](https://blog.audius.co/posts/audius-terms-of-service-update)
- [Jamendo API](https://developer.jamendo.com/v3.0/docs)
- [Jamendo API Terms](https://devportal.jamendo.com/api_terms_of_use)
- [Bandcamp Subsonic Beta 官方公告](https://blog.bandcamp.com/2026/07/16/discover-improvements-and-subsonic-implementation/)
- [Navidrome Subsonic API](https://www.navidrome.org/docs/developers/subsonic-api/)

### Spotify

- [Authorization](https://developer.spotify.com/documentation/web-api/concepts/authorization)
- [Web API](https://developer.spotify.com/documentation/web-api)
- [Scopes](https://developer.spotify.com/documentation/web-api/concepts/scopes)
- [iOS App Remote](https://developer.spotify.com/documentation/ios/getting-started)
- [Android App Remote](https://developer.spotify.com/documentation/android)
- [Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk)
- [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
- [Spotify Developer Policy](https://developer.spotify.com/policy)
- [Spotify 可用国家/地区](https://support.spotify.com/de/article/where-spotify-is-available/)

### YouTube

- [YouTube Data API](https://developers.google.com/youtube/v3/docs)
- [Search](https://developers.google.com/youtube/v3/docs/search/list)
- [Playlists](https://developers.google.com/youtube/v3/guides/implementation/playlists)
- [IFrame Player parameters](https://developers.google.com/youtube/player_parameters)
- [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- [Developer Policies guide](https://developers.google.com/youtube/terms/developer-policies-guide)
- [YouTube Music 可用地区](https://support.google.com/youtubemusic/answer/6313540?hl=zh-Hans)

### SoundCloud

- [API Guide](https://developers.soundcloud.com/docs/api/)
- [OpenAPI / API Explorer](https://developers.soundcloud.com/docs/api/explorer/)
- [Widget API](https://developers.soundcloud.com/docs/api/html5-widget)
- [Get an API key](https://developers.soundcloud.com/docs/api/register-app)
- [API Terms of Use](https://developers.soundcloud.com/docs/api/terms-of-use)

### Amazon Music

- [Developer Portal](https://developer.amazon.com/docs/music/landing_home.html)
- [Web API Overview](https://developer.amazon.com/docs/music/API_web_overview.html)
- [User API](https://www.developer.amazon.com/docs/music/API_web_user.html)
- [Playback Overview](https://www.developer.amazon.com/docs/music/API_playback_overview.html)
- [Program Requirements](https://www.developer.amazon.com/docs/music/requ_AM-Program-Requirements.html)

### TIDAL

- [Authorization](https://developer.tidal.com/documentation/api-sdk/api-sdk-authorization)
- [API / SDK Reference](https://developer.tidal.com/reference)
- [Developer Terms](https://developer.tidal.com/documentation/guidelines/guidelines-developer-terms)
- [Developer Guidelines](https://developer.tidal.com/documentation/guidelines/guidelines-developer-guidelines)
- [Available countries](https://support.tidal.com/hc/en-us/articles/202453191-Where-Tidal-is-Available)

### Deezer

- [Official iOS SDK samples](https://github.com/deezer/iOS-sdk-samples)
- [Official community statement on API access](https://en.deezercommunity.com/features-feedback-44/api-auth-impossible-80857)

### 中国平台

- [QQ 音乐开放平台](https://y.qq.com/music_developer/)
- [QQ 音乐统一 SDK 隐私规则](https://privacy.qq.com/document/preview/8c8086a85e344bc8bad50fd3e554ee9e)
- [酷狗曲库开放计划](https://www.kugou.com/musiclibrary_openplan/?from=listenweb)
- [咪咕开放合作](https://open.migu.cn/)
- [华为 Audio Connect](https://developer.huawei.com/consumer/cn/audioconnenct/)

## 11. 证据限制

- 平台开发者政策和产品准入变化很快；进入实现、提交商店和发版前必须重新核验。
- 本轮没有创建任何平台生产 App、登录用户账号、接受开发者条款、调用真实 API、下载闭源 SDK或做真机播放；“技术支持”不等于已获得 Murmur 的生产许可。
- Apple Music 中国大陆可用不自动证明 MusicKit Android 在所有国内发行渠道/ROM 可用；需真机验证。
- Spotify、TIDAL 和 YouTube 的地区列表以官方列举为准；“未列中国大陆”不应通过代理、跨区账号或其他规避方式解决。
- SoundCloud 用户上传内容的权利由上传者控制，SoundCloud 明确不向第三方保证内容已为目标用途清权。
- Amazon Music 的文档处于 preview/closed beta；最终合同、SDK、API和认证结果可能与公开页面不同。
- Deezer 的官方社区声明证明新个人 access 关闭，但没有给出未来时间表；恢复准入后需从头复核 API、SDK、商业与 AI 条款。
