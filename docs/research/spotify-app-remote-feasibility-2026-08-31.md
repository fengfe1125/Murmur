# Murmur 使用 Spotify App Remote 遥控放歌可行性（2026-08-31）

> 状态：产品、技术与政策可行性调研｜适用：Murmur Spotify App Remote 方案选型｜核验：2026-08-31｜依据：Spotify 官方文档、条款、政策、SDK 与支持页；不代表平台授权或生产验收
> 核验日期：2026-08-31（Asia/Shanghai）
> 证据口径：Spotify 官方开发者文档、Developer Terms、Developer Policy、官方 SDK/GitHub 与官方支持页
> 目标口径：音频始终由设备上的 Spotify 官方 App 播放；Murmur 只负责登录授权、搜索/选歌、聊天歌曲卡和遥控
> 事实标签：**已证实**为官方资料直接支持；**推断**为基于公开条款和仓库现状的工程判断；**未知**需 Spotify 书面确认或真机验证

## 结论先行

把目标改成“遥控 Spotify App 放歌”后，**技术上可行，而且比在 Murmur 内承载 Spotify 音频简单很多；但它只适合当前邀请制、非商业、最多 5 名 Premium 测试用户的 PoC，不具备直接扩成公开商业产品的条件。**

原因分成两层：

1. **技术条件成立。** Spotify iOS/Android App Remote 可以连接同一设备上的 Spotify App，按 Spotify URI 发起播放，执行 play/pause/seek/skip 等命令并订阅 PlayerState。音频焦点、锁屏、来电、联网、缓存和后台播放都由 Spotify App 负责。
2. **默认政策不允许正式商业化。** Spotify [Developer Terms](https://developer.spotify.com/terms#section-ii-definitions)明确把“控制后台 Spotify application”也定义为 Streaming；[Developer Policy](https://developer.spotify.com/policy#iv-streaming-and-commercial-use)禁止 Streaming SDA 的售卖、订阅、应用内付费、广告或赞助。
3. **Murmur 的 AI 数据流不能原样复用。** Spotify Content 的定义包含音频、曲名/艺人等 metadata、封面、歌单和用户数据；政策禁止把这些内容输入 AI/ML。当前 Murmur 会把 Audius 歌曲和播放状态注入模型上下文，Spotify 模式必须彻底隔离。
4. **规模准入很窄。** Development Mode 当前每个 App 最多 5 个授权用户，App owner 必须 Premium；Extended Quota 只接受组织申请，并要求已上线服务、至少 25 万 MAU、覆盖关键市场等条件。
5. **不能做语音遥控。** Spotify 明确禁止 voice-enabled SDA 或 voice assistant 控制 Spotify。文字聊天提出请求、生成一张卡片、再由用户点击播放，是风险最低的形态；语音说“播放/暂停/下一首”不可进入 PoC。

最终评级：

| 场景 | 评级 | 说明 |
| --- | --- | --- |
| 1–5 名 allowlist 用户、全部 Premium、按钮点击、非商业 | **条件 Go** | 适合真机产品验证 |
| Murmur 发 Spotify 卡，用户点击后遥控官方 App | **条件 Go** | 模型不得看到 Spotify 返回的卡片内容 |
| 用户从 Spotify 搜索/资料库选歌发给 Murmur | **技术 Go，AI 语义理解 No-Go** | 卡片可传递和显示，但不能把 metadata 交给模型理解 |
| 免费 Spotify 用户指定播放某一首 | **No-Go** | on-demand 需要 Premium；公开产品应按政策保守处理 |
| Murmur 语音控制播放/暂停/下一首 | **明确 No-Go** | Developer Policy 直接禁止 |
| Spotify 与 Audius 混合曲库/并列推荐 | **高风险 / 先询证** | 多音乐服务聚合和品牌展示存在明确限制 |
| 公开用户规模 | **当前 No-Go** | Dev Mode 5 人；Murmur 不满足 Extended Quota 的 25 万 MAU 门槛 |
| 付费 Murmur 中包含 Spotify 遥控 | **默认 No-Go** | 遥控也属于 Streaming SDA，需单独书面协议 |

## 1. 用户目标逐项判断

### 1.1 有 Spotify 登录数据并保持登录

**可行。**

- App Remote 使用 `app-remote-control` scope，让 Murmur 获得控制本机 Spotify App 的许可：[Scopes](https://developer.spotify.com/documentation/web-api/concepts/scopes)。
- 若还要搜索、读取用户收藏和私有歌单，需要独立的 Web API OAuth。移动端官方推荐 Authorization Code with PKCE，不在 App 中保存 client secret：[Authorization](https://developer.spotify.com/documentation/web-api/concepts/authorization)。
- access token 当前有效期为 1 小时；refresh token 当前生命周期为 6 个月，之后必须重新授权：[Refreshing tokens](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens)。
- 建议 access/refresh token 只存在设备 Keychain/Keystore，并绑定当前 Murmur 身份；不能进入聊天消息、日志、模型输入或播放状态接口。
- 用户必须能在 Murmur 内“断开 Spotify”。Spotify 条款要求账号断开后停止访问，并在 5 天内删除持有的 Spotify Personal Data：[Developer Terms](https://developer.spotify.com/terms)。

需要区分两类授权：

| 授权 | 用途 | 是否足够完成完整目标 |
| --- | --- | --- |
| App Remote 内建授权 | 控制本机 Spotify App | 只能遥控，不足以读取完整搜索/资料库 |
| Web API PKCE | 搜索、profile、library、private playlists | 负责选歌与登录数据，需要额外 scopes |

建议最小 scopes：

```text
app-remote-control
user-read-private
user-library-read
playlist-read-private
```

没有保存、修改歌单的产品需求时，不申请 `user-library-modify` 或 `playlist-modify-*`。

### 1.2 Murmur 在聊天里向用户发歌

**技术可行，产品文案和模型链路必须受限。**

低风险流程：

1. 模型只读用户自己写的聊天文本，产出普通搜索 query 或精确歌名；这一阶段没有任何 Spotify API 返回内容。
2. Murmur 的确定性代码调用 Spotify Search，取得 Track ID、Spotify URI、标题、艺人、封面和 canonical URL。
3. 确定性代码选择结果并生成歌曲卡；不能再把搜索候选或最终 metadata 回传给模型。
4. 模型可以预先生成不点名的递歌文案，例如“这首或许适合现在”；卡片由模型回复完成后附加。
5. 用户点击卡片后才连接 App Remote 并发送 `play(spotify:track:...)`。

当前 Audius 实现会先选出歌曲，再把“标题—艺人”注入模型，让 Murmur 说一句与歌曲呼应的话。Spotify 下不能这样做，因为 Spotify [Developer Policy](https://developer.spotify.com/policy#iii-some-prohibited-applications)禁止把 Spotify Content 输入 AI/ML。

### 1.3 用户向 Murmur 发歌

**卡片传递可行；让 AI 理解这张 Spotify 卡片默认不可行。**

用户可在 Murmur 的 Spotify picker 中：

- 搜索 Spotify catalog；
- 浏览 Liked Songs；
- 浏览有权访问的歌单；
- 选择歌曲并发送结构化卡片。

聊天消息可保存稳定引用：

```text
provider = spotify
track_id = Spotify Track ID
canonical_url = https://open.spotify.com/track/...
spotify_uri = spotify:track:...
title / artists / artwork / duration / explicit = metadata snapshot
```

但有三个限制：

1. Spotify metadata、封面、歌单和用户数据都属于 Spotify Content，不能进入模型 prompt、历史摘要、dossier 或长期记忆。
2. 卡片 fallback 文本不能把自动取得的标题、艺人和 URL拼进会传给模型的 `note`；旧客户端的文字降级应改成“分享了一首 Spotify 歌曲”，或者把显示 fallback 与模型输入拆开。
3. Spotify 条款不允许无限期存储 Spotify Content。聊天卡需要刷新和删除策略；断开账号后，历史卡片应降级为失效占位或删除 Spotify metadata，而不是永久保留封面与标题。

这意味着 Murmur 可以收到卡片、显示卡片、让用户点击播放，但模型不能说“我也喜欢这首《X》”或基于该卡片继续分析。若该语义能力是硬要求，必须先取得 Spotify 书面许可。

### 1.4 在 App 里控制播放

**可行，但音频和系统媒体会话属于 Spotify App，不属于 Murmur。**

[Spotify iOS SDK](https://developer.spotify.com/documentation/ios)与[Android SDK](https://developer.spotify.com/documentation/android)支持：

- 按 Spotify URI 发起曲目、专辑或歌单播放；
- play/pause、seek、上一首/下一首、shuffle/repeat 等命令；
- 订阅 PlayerState，获得当前歌曲、播放/暂停状态和进度；
- Spotify App 负责音频焦点、锁屏、来电、缓存、地区 relinking 和后台播放。

Murmur 可显示“Spotify 正在播放”的迷你控制条，但必须遵守[品牌与设计规范](https://developer.spotify.com/documentation/design)：

- 明确显示 Spotify logo/icon；
- metadata 和封面必须回链 Spotify；
- 不裁剪封面、不在封面上叠文字或 logo；
- 提供“在 Spotify 打开 / Play on Spotify”；
- Spotify 建议伴侣 App 只提供 play/pause，若增加其他按钮必须正确处理 Free/Premium 能力差异。

### 1.5 后台、锁屏与 App 切换

**播放体验较好，但 Murmur Remote 连接不是常驻连接。**

- Android 官方要求 Activity `onStart` 连接、`onStop` 断开；不要让 Murmur 在后台长期维持 Remote。Spotify App 自己继续播放：[Android lifecycle](https://developer.spotify.com/documentation/android/tutorials/application-lifecycle)。
- iOS 官方建议 Murmur inactive 时断开、active 时重连。Remote 断开且音乐暂停时，恢复可能需要 app switch 到 Spotify：[iOS lifecycle](https://developer.spotify.com/documentation/ios/concepts/application-lifecycle)。
- 因此 Murmur 的迷你播放器在回到前台后要显示 `reconnecting`，重新订阅 PlayerState，不能假设离开前状态仍有效。
- Spotify 未安装、未登录、未授权、用户非 Premium、URI 不可播、地区下架和 Remote 断线都必须有独立错误态。

## 2. 技术架构建议

### 2.1 数据流

```text
用户/Murmur 自有文本
        │
        ▼
LLM 只输出搜索 query 或普通递歌文案
        │  （此处没有 Spotify Content）
        ▼
确定性 Spotify Catalog Adapter ── Web API Search / Library
        │
        ├── TrackV1(spotify) → 聊天卡片与必要存储
        │                       └─ 不进入 LLM/记忆/dossier
        ▼
用户点击卡片
        │
        ▼
Spotify App Remote ── play(spotify URI)
        │
        ▼
设备上的 Spotify App 输出音频并拥有系统媒体会话
```

### 2.2 客户端模块

建议把当前单一 Audius 实现拆成：

```text
MusicAccountAdapter
  ├─ AudiusAccountAdapter
  └─ SpotifyAccountAdapter (PKCE + App Remote permission)

MusicCatalogAdapter
  ├─ AudiusCatalogAdapter
  └─ SpotifyCatalogAdapter (Search / Library / Playlist)

MusicPlaybackAdapter
  ├─ AudiusNativePlaybackAdapter (AVPlayer / Media3)
  └─ SpotifyRemotePlaybackAdapter (App Remote)
```

统一状态不要假装两种播放器相同：

```text
idle
needs_install
needs_login
needs_premium
connecting
connected
playing
paused
reconnecting
external_app_required
unavailable
failed
```

### 2.3 Token 与数据边界

- PKCE verifier、access token、refresh token：设备安全存储。
- client secret：只可在服务端；如果客户端已用 PKCE，不需要把 secret 下发。
- 服务端聊天 wire：只接受有界 TrackV1；不得接受 token、Spotify session、临时图片二进制或音频 URL。
- 服务端如需为 Murmur 选歌，可用自己的 Client Credentials 查公共 catalog；模型只提供 query，结果由代码处理。
- Spotify 卡片、library、playlist、PlayerState：不得进入第三方模型、历史摘要、dossier、推荐画像或统计分析。
- Spotify 播放状态不调用现有 `PUT /v1/music/playback-state`，除非 Spotify 书面确认；首版只在本地驱动 UI。

## 3. 与 Murmur 当前实现的差异

仓库当前已有 Audius 音乐纵切，但不能把 provider 名称改成 `spotify` 就结束。

### 3.1 可复用

- `MusicTrackAttachmentV1` 已包含 provider、track ID、标题、艺人、封面、官方链接、时长和 explicit，消息形状可扩展。
- 既有 `bubble.music_track` SSE 附加字段与文字 fallback 兼容机制可复用。
- iOS 已有歌曲 picker、聊天卡、mini player、full player 和播放状态机的界面组织。
- 服务端已有有界 JSON、未知字段拒绝、发送前 catalog 重验和 feature gate。

### 3.2 必须修改

| 当前实现 | Spotify 所需变化 |
| --- | --- |
| `server/murmur/app_music.py` 只允许 `provider == audius` | TrackV1 validator 改为 provider adapter；Spotify ID/URL 单独校验 |
| 服务端 catalog 是 Audius API key | 新增 Spotify Client Credentials catalog client，处理 token/429/market |
| `music_prompt_line` 把歌曲信息送进 LLM | Spotify provider 必须返回空，不进入 prompt/history |
| `playback_prompt_line` 告诉模型正在听什么 | Spotify provider 禁用服务端播放报告与模型上下文 |
| 用户歌曲 fallback 含标题、艺人、URL | Spotify fallback 与模型输入分离，不能把自动 metadata 作为 note |
| iOS `MusicModule` 固定 `AudiusClient` | 引入 Account/Catalog/Playback protocols 与 provider factory |
| iOS `MusicPlaybackController` 驱动 AVPlayer、Audio Session、Now Playing | Spotify 使用独立 Remote controller；系统媒体会话归 Spotify App |
| iOS picker 类型和文案含 Audius | provider-neutral playlist/account models 与 Spotify 品牌规则 |
| Android 尚无音乐模块 | Android 需从聊天卡、picker、OAuth、App Remote 状态机开始实现 |

### 3.3 当前最严重的政策冲突

1. `app_worker.py` 会把本轮选中的歌曲标题/艺人加入 `context_extra`。
2. `engine.py` 会把历史歌曲卡重新加入多轮 LLM history。
3. `playback_prompt_line` 会把正在播放或暂停的歌曲加入当前模型上下文。

这三条对 Audius 是当前设计，对 Spotify 都必须禁用。否则即使 App Remote 控制本身正常，产品仍不符合 Spotify AI/ML 条款。

## 4. Premium、地区与设备前提

### 4.1 Premium

[iOS Getting Started](https://developer.spotify.com/documentation/ios/getting-started)说明 Free 用户只支持 shuffle，Premium 才支持 on-demand；现行 [Developer Policy](https://developer.spotify.com/policy#iv-streaming-and-commercial-use)更直接规定通过 Spotify Platform 的音乐 Streaming 只提供给 Premium。

因此 Murmur 的严格验收必须写成：

> 发指定歌曲卡并点击播放，只对 Spotify Premium 用户开放。

不要为 Free 用户承诺“点哪首播哪首”。Free 可显示卡片和“在 Spotify 打开”，但遥控点播按钮应禁用或降级。

### 4.2 Spotify App 与真机

- iOS/Android 都要求安装并登录 Spotify App。
- iOS 必须用真机验证，模拟器不能构成 App Remote 播放验收。
- Android 需要 package name、签名 fingerprint 与 redirect URI 正确注册。
- iOS 需要 Bundle ID、redirect URI、URL scheme/universal link 配置。
- Spotify 不在中国大陆提供正式消费服务，因此该能力只面向 Spotify 支持市场，不通过代理或跨区账号规避。

## 5. 政策硬边界

### 5.1 遥控仍是 Streaming

Spotify [Developer Terms](https://developer.spotify.com/terms#section-ii-definitions)的定义明确包含：使用 Spotify Platform 控制后台 Spotify application。由此得到：

- 音频不经过 Murmur，不等于 Murmur 是 Non-Streaming SDA；
- App Remote 路线仍受 Premium 和 Streaming 商业限制；
- 不能用“只是一个遥控器”作为商业合规依据。

### 5.2 商业化

默认禁止：

- 售卖包含 Spotify 遥控的 App；
- 订阅付费解锁 Spotify 遥控；
- 在 Streaming SDA 内发起应用内付费或电商；
- 在 Streaming SDA 内销售广告、赞助或推广。

Murmur 当前是邀请制小范围使用，且现行定位不做公开增长和商业化，因此 PoC 可以停留在非商业实验范围。未来只要 Murmur 付费产品仍包含 Spotify Streaming 功能，就必须先取得 Spotify 单独书面协议，不能靠 Extended Quota 代替。

### 5.3 AI 与语音

- 不能把 Spotify API 返回的歌曲、封面、歌单、library、recently played 或 PlayerState 输入模型。
- 不能用这些数据训练、微调、RAG、生成推荐画像或派生收听指标。
- 可以让模型处理用户自己键入的普通文本并输出搜索 query；这是基于条款的保守推断，Spotify 没有公开确认这种架构。
- 不能让语音助手执行播放、暂停、切歌、选歌。Murmur 将来若增加语音输入，Spotify 控制入口必须完全禁用。
- 播放过程中不能让 Murmur 音频与 Spotify 音频 segue、mix、remix 或 overlap；AI 语音应禁止，不能只做 ducking。

### 5.4 多音乐平台并存

Developer Policy 禁止把产品与另一服务的 streams/content 集成；品牌规范也要求 Spotify content 不与类似服务内容并排展示。Murmur 已有 Audius 实现，因此：

- 不做“Spotify + Audius”混合搜索、统一推荐或同一列表并排展示；
- 第一轮用独立 `spotify-only` 开发配置或测试 build；
- 即使正式版用账号级 provider 二选一，也应先向 Spotify 书面确认该 SDA 同时支持另一音乐 provider 是否允许；
- 未获确认前，不把 Spotify 当成 Audius 的第二个可切换 tab。

## 6. Development Mode 与正式准入

[Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)当前规则：

- 新 App 从 Development Mode 开始；
- App owner 必须有 Spotify Premium；
- 每个 App 最多 5 个 authenticated/allowlisted 用户；
- 用户可能完成登录但未在 allowlist，API 会返回 403；
- 2026 年 7 月后每个开发者账号最多 25 个 Client IDs，但所有 Dev Mode App 共用账号级 quota；这没有改变每个 App 的 5 人限制。

Extended Quota 当前要求：

- 只接受已注册组织，而不是个人；
- 使用公司邮箱申请；
- 已上线并运营中的服务；
- 至少 25 万 MAU；
- 覆盖 Spotify 关键市场；
- 商业可行并遵守全部条款；
- 审核可能需要最多六周。

Murmur 当前是邀请制小范围产品，明显不满足 25 万 MAU，因此近期不能把 Spotify 正式用户扩张建立在 Extended Quota 上。即使未来获批，Extended Quota 只解决用户数和调用额度，不解除商业、AI、语音和多服务政策。

## 7. 建议 PoC

### 7.1 范围

只做 iOS、Spotify-only、2–5 名 allowlist Premium 用户：

1. 注册 Spotify Developer App，配置 Bundle ID、redirect URI 和 iOS SDK。
2. 固定 Spotify Track URI，验证授权、app switch、连接和指定曲目播放。
3. 验证 PlayerState、play/pause、seek 和打开 Spotify。
4. 接 Web API PKCE，验证 Search、Liked Songs 与 private playlists。
5. 扩展 TrackV1 为 `provider=spotify`，展示符合品牌规范的聊天卡。
6. 用户发卡和 Murmur 发卡都走确定性卡片路径，模型看不到 Spotify metadata。
7. Murmur 退后台时断 Remote；回前台重连并恢复 UI，但不自动恢复音频。
8. 不接服务端播放状态、不接长期记忆、不接语音、不接 Audius 混合选择。

### 7.2 验收矩阵

| 场景 | 预期 |
| --- | --- |
| Spotify 未安装 | 显示“安装 Spotify”，不崩溃 |
| 已安装但未登录 | 跳 Spotify 登录，返回后重新连接 |
| 未授权 Murmur | 展示授权界面，只请求最小 scopes |
| Premium 用户点击曲目卡 | Spotify App 开始指定曲目，Murmur显示同步状态 |
| Free 用户点击曲目卡 | 不承诺指定播放；显示 Premium/在 Spotify 打开降级 |
| Spotify 已被系统杀死 | 允许 app switch 唤醒并返回；失败有重试 |
| Murmur 切后台 | Remote 断开，Spotify 继续播放 |
| Murmur 回前台 | 重连、重新订阅 PlayerState，不重复发 play |
| 暂停后 Remote 已断 | 显示“在 Spotify 继续”或明确 app switch |
| 曲目地区不可用 | 显示不可用；不替换成未经用户确认的另一首 |
| token 过期 | access token 刷新；refresh token 过期则重新授权 |
| 用户断开 Spotify | 清 token；5 天内删除 Spotify Personal Data |
| 模型请求/历史抓包 | 不出现 Spotify title、artist、URI、封面、library、PlayerState |
| Spotify 和 Audius UI | Spotify-only PoC 中不存在混合入口 |
| VoiceOver/动态字体 | 卡片、授权、错误态和控制可访问 |

### 7.3 终止条件

出现任一项就停止从 PoC 进入产品化：

- 无法做到 Spotify Content 与模型 prompt/历史/记忆的结构性隔离；
- 产品必须让 AI 读取用户发来的 Spotify 歌曲才能成立；
- 产品必须支持语音点歌或语音控制；
- 必须同时展示/聚合 Audius 与 Spotify；
- 必须向超过 5 名用户开放，但没有 Extended Quota/合作许可；
- Murmur 将开始收费、广告或赞助，但没有 Spotify 单独书面协议；
- 无法接受 Premium、Spotify 已安装和支持市场作为硬前提；
- 历史歌曲卡必须永久保存且不能按 Spotify 数据政策降级/删除。

## 8. 工期粗估

这是基于当前仓库现状的工程估算，不包含 Spotify 审核或商务时间。

| 工作 | iOS | Android |
| --- | --- | --- |
| 固定 URI App Remote spike | 3–5 天 | 3–5 天 |
| PKCE、token、Search/Library/Playlist | 1–2 周 | 1–2 周 |
| provider-neutral wire 与 server catalog | 1–2 周共享 | 共享 |
| Spotify card/picker/Remote player UI | 1.5–3 周 | 3–5 周 |
| AI 隔离、删除策略、品牌与错误态 | 1–2 周共享 | 共享 |
| 真机矩阵和回归 | 1–2 周 | 1–2 周 |

当前 iOS 已有完整 Audius 音乐 UI，重构后可复用；Android 只有聊天/照片骨架，没有音乐模块。因此建议：

- **iOS Spotify-only PoC：约 4–7 周；**
- **iOS 受限内测质量：约 7–10 周；**
- **Android 对齐：额外约 5–8 周。**

这些时间只能交付 5 人非商业测试能力，不能消除正式准入和政策阻断。

## 9. 产品建议

### 推荐做

- 把 Spotify 定义成“在 Spotify 播放”的陪伴卡，而不是 Murmur 内置播放器。
- 仅对 Premium、Spotify 已安装、支持市场用户显示遥控能力。
- 首轮只做文字交互 + 用户点击；不自动播放。
- 用 Spotify-only build 验证“AI 递一张歌卡、用户点开后继续聊天”的体验价值。
- 在投入 Android 前，先完成 iOS 真机 spike 和 Spotify 的书面政策询证。

### 不推荐做

- 不把 Spotify 当成 Audius 的普通第二 provider 直接接入现有代码。
- 不让模型看到 Spotify 返回的任何 metadata、播放状态或用户资料库。
- 不做“你说一句，Murmur 立即自动播放”的语音助手体验。
- 不做多平台统一曲库、跨平台匹配或 Spotify/Audius 混排。
- 不在没有书面协议时把 Spotify 遥控放入付费版、广告版或公开增长路线。

## 10. 最终判断

如果产品决策是：

> Murmur 发一张 Spotify 卡；用户主动点击；Spotify 官方 App 在后台播放；Murmur只显示符合品牌规则的遥控状态。

那么 **5 人以内的非商业 Premium PoC 现在可以做。**

如果产品决策还要求任一项：

- Murmur 理解用户发来的 Spotify 歌曲并围绕它聊天；
- 使用语音点歌/暂停/切歌；
- 与 Audius 等音乐服务混合推荐；
- 面向公众规模开放；
- Murmur 付费、广告或赞助产品包含 Spotify 遥控；

那么 **按 Spotify 当前公开政策不能直接做正式版，必须先取得单独书面许可。**

所以推荐决策是：**技术 PoC Go；产品化 Conditional No-Go。** 先用 3–5 天固定 URI spike 验证 App Remote 真机体验，再决定是否投入完整 iOS 纵切；不要在政策询证前承诺公开上线。

## 11. 主要一手来源

所有链接最后核验：2026-08-31。

- [Spotify Developer Terms](https://developer.spotify.com/terms)
- [Spotify Developer Policy](https://developer.spotify.com/policy)
- [Spotify Design & Branding Guidelines](https://developer.spotify.com/documentation/design)
- [iOS SDK](https://developer.spotify.com/documentation/ios)
- [iOS Getting Started](https://developer.spotify.com/documentation/ios/getting-started)
- [iOS Application Lifecycle](https://developer.spotify.com/documentation/ios/concepts/application-lifecycle)
- [Spotify iOS SDK official repository](https://github.com/spotify/ios-sdk)
- [Android SDK](https://developer.spotify.com/documentation/android)
- [Android Getting Started](https://developer.spotify.com/documentation/android/tutorials/getting-started)
- [Android Application Lifecycle](https://developer.spotify.com/documentation/android/tutorials/application-lifecycle)
- [Spotify Android SDK official repository](https://github.com/spotify/android-sdk)
- [Web API Authorization](https://developer.spotify.com/documentation/web-api/concepts/authorization)
- [Authorization Code with PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Refreshing tokens](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens)
- [Scopes](https://developer.spotify.com/documentation/web-api/concepts/scopes)
- [Search for Item](https://developer.spotify.com/documentation/web-api/reference/search)
- [Get User's Saved Tracks](https://developer.spotify.com/documentation/web-api/reference/get-users-saved-tracks)
- [Playlists](https://developer.spotify.com/documentation/web-api/concepts/playlists)
- [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
- [July 2026 Development Mode quota update](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates)
- [Spotify on other apps / Remove access](https://support.spotify.com/ee-en/article/spotify-on-other-apps/)

## 12. 证据限制

- 本轮没有创建 Spotify Developer App、接受条款、登录真实 Spotify 账号、申请 allowlist、下载 SDK 或进行真机播放；代码存在与官方文档支持不等于已获得 Murmur 的生产许可。
- Spotify 政策和配额可随时变化；进入实现、TestFlight、商店提交和发版前必须重新核验。
- “AI 只处理用户自有文本、不读取 Spotify Content”是保守工程推断，不是 Spotify 书面批准。
- “provider 二选一”是否足以避开多服务集成限制仍未知；Spotify-only PoC 最安全，正式多 provider 必须询证。
- 商业、AI、语音、多服务和长期聊天卡存储均应取得律师与 Spotify 的书面意见；本报告不是法律意见。
