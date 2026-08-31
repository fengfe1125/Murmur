# Apple Music / MusicKit 对 Murmur 中文音乐需求的适配性核验

> 状态：调研笔记｜适用：中文曲库音乐平台选型｜核验：2026-08-31｜依据：Apple 官方开发者文档、App Store Review Guidelines、Apple Developer Program License Agreement、Apple 中国官网及 Apple Music 中国区页面；不代表 Apple 已向 Murmur 授权
>
> 核验目标：正式账号授权、读取用户资料库、Murmur 与用户在聊天中双向发送结构化歌曲卡、iOS/Android App 内完整播放、中文歌曲覆盖。

## 一、结论

**结论：有条件可行，建议把 Apple Music 作为 Murmur 中文音乐正式版的第一候选。**

它在公开官方能力中能够同时提供：

- Apple Music 用户授权和订阅状态检查；
- Apple Music catalog 搜索、曲目元数据、用户资料库、收藏、歌单、最近播放和个性化推荐；
- iOS 原生 App 内整曲播放与后台播放；
- Android 官方登录库和原生播放 SDK；
- 中国大陆正式服务、人民币订阅，以及规模显著高于 Audius 的华语/国语/粤语内容。

但它并不是无条件满足 Murmur 的全部设想。必须接受以下边界：

1. **MusicKit 授权不是一个可读取姓名、头像、邮箱的“社交账号登录”。**它证明用户允许访问音乐数据，并提供 Music User Token、资料库与订阅能力；Murmur 自身登录仍需使用现有账号体系，若需要 Apple 身份登录则另接 Sign in with Apple。
2. **用户必须发起 Apple Music stream 播放，并能使用播放、暂停、跳过等标准控制。**最稳妥的交互是 Murmur 发歌曲卡，用户点击卡片上的播放按钮；不应让 LLM 在没有新的明确用户操作时自行开播。
3. **不能要求用户付费或间接变现 Apple Music 访问。**不能把“连接/播放 Apple Music”锁在 Murmur Pro 后，也不能靠广告、索取用户资料等方式变现 MusicKit 访问。
4. **不能传输或分享音乐文件。**聊天里只能发结构化 catalog 引用和与播放关联的元数据，接收端再通过 MusicKit 播放，不能发音频文件、缓存文件或可脱离 MusicKit 使用的播放地址。
5. **中文曲库虽然明显满足产品方向，但不能仅凭官网宣称保证“每一首网易云歌曲都有”。**曲目因版权和 storefront 而异，必须用目标歌单做实际命中率 PoC。

因此，严格定义应当是：

> Murmur 可以选择 Apple Music catalog 中的歌曲并在聊天里发卡；用户也可以从 catalog 或其资料库选择歌曲发给 Murmur；接收端按用户 storefront 重新解析，并在用户点击播放后，通过 MusicKit 在 Murmur 内播放整曲。用户需要有效的 Apple Music 播放资格，歌曲也必须在接收端 storefront 可播放。

这个定义可以实现。若要求“Murmur 在没有用户点击的情况下自主播放一首特定歌曲”“把 Apple Music 播放作为 Murmur 付费卖点”或“把用户 Apple Music 数据拿去训练第三方模型”，则不应视为已获 Apple 官方许可。

## 二、逐项需求判定

| Murmur 严格需求 | 判定 | Apple 官方能力与限制 |
| --- | --- | --- |
| 正式音乐账号授权 | **满足音乐授权；不等于用户身份资料登录** | iOS 使用 `MusicAuthorization`；Android 官方 Authentication library 会提示用户登录 Apple Music 并取得访问 token。MusicKit 官方页说明 Android 可让用户登录 Apple Music 账号并直接在 App 播放。 |
| 检查会员/可播放状态 | **满足** | iOS `MusicSubscription` 暴露 `canPlayCatalogContent`、`hasCloudLibraryEnabled`、`canBecomeSubscriber`；非会员可展示订阅试用。 |
| 读取用户音乐资料库 | **满足** | Apple Music API 可访问个人 iCloud Music Library；MusicKit 有 `MusicLibraryRequest`，API 还支持资料库、歌单、收藏、最近播放和推荐。须取得明确授权。 |
| Murmur 搜歌并发结构化歌曲卡 | **满足** | Apple Music catalog 支持歌曲、专辑、艺人、歌单等搜索；歌曲资源包含 ID、歌名、艺人、专辑、时长、封面、ISRC、分享 URL、`playParams` 和预览。 |
| 用户从资料库选歌发给 Murmur | **满足，但要先转成可分享的 catalog 引用** | 资料库歌曲有到 catalog 的关联（when known）。仅存在于个人导入资料库、没有 catalog 对应项的歌曲，不应承诺另一端可播放。 |
| iOS App 内完整播放 | **满足** | `ApplicationMusicPlayer` 是专供当前 App 使用的播放器，不改变 Music App 状态；开启 background audio mode 后可在 App 进入后台时继续播放。 |
| Android App 内完整播放 | **满足公开技术能力，仍需真机 PoC** | Apple 官方说明 MusicKit for Android 可将原生 Apple Music 功能集成进 Android App，登录后直接从 App 播放；SDK 提供 play、pause、skip、queue 等控制。公开 Android API 文档较旧，因此必须在中国大陆常见机型和分发渠道实测。 |
| 中文/华语曲库 | **方向满足；需样本验证** | Apple 中国官网称中国大陆 Apple Music 提供“数千万首好歌”；中国区页面有国语流行、粤语流行、C-Pop、周杰伦代表作、中国大陆热门榜等官方内容。 |
| AI 聊天中推荐歌曲 | **满足低风险形态** | 让模型输出搜索意图，后端调用 catalog，返回确定歌曲卡；不要让模型杜撰曲目 ID。用户点击后播放。 |
| AI 自主立即开播 | **高风险/不建议** | Apple 要求用户发起 stream。Apple 还把“在特定时刻播放特定歌曲”列为可能需要权利人额外许可的复杂集成示例。 |
| Murmur 收费 | **Murmur 本体可以有商业模式，但 MusicKit 访问不能被直接或间接变现** | Apple 明确禁止要求付费或间接变现 Apple Music 服务访问，包括 IAP、广告、索取用户信息等。必须让 Apple Music 连接/播放本身不成为付费解锁项。 |

## 三、推荐的双向歌曲卡实现

### 3.1 卡片最小数据

建议聊天消息只保存：

```json
{
  "type": "music_track",
  "provider": "apple_music",
  "sourceStorefront": "cn",
  "catalogSongId": "...",
  "isrc": "...",
  "name": "...",
  "artistName": "...",
  "albumName": "...",
  "durationInMillis": 0,
  "artworkTemplateUrl": "...",
  "appleMusicUrl": "..."
}
```

不要保存：Music User Token、开发者私钥、HLS/音频地址、下载缓存、可脱离 MusicKit 使用的音乐文件。

Apple 的歌曲资源正式提供曲目 ID、ISRC、元数据、封面、分享 URL 和 `playParams`；`playParams` 存在表示该歌曲可由 Apple Music 订阅播放。来源：[Songs.Attributes](https://developer.apple.com/documentation/applemusicapi/songs/attributes-data.dictionary)。

### 3.2 Murmur 发歌

1. 用户在聊天里提出“放一首周杰伦适合雨天的歌”。
2. LLM 只生成搜索条件或候选名称，不直接生成 Apple Music ID。
3. Murmur 用接收用户的 storefront 调用 catalog search。
4. 展示候选或生成结构化歌曲卡。
5. 用户点击播放按钮。
6. 客户端再次校验授权、`canPlayCatalogContent` 和 `playParams`，然后交给 MusicKit 播放。

这样既能让 Murmur“在聊天里向用户发歌”，也能保留 Apple 要求的用户发起播放动作。

### 3.3 用户发歌

1. 用户从 Apple Music catalog 或已授权的资料库选择歌曲。
2. 如果选择的是 library song，先读取它的 `catalog` relationship；Apple 说明此关联只在已知时存在。
3. 发送 catalog card，不发送 library ID 作为唯一跨用户标识。
4. 接收端根据自己的 storefront 解析可播放版本，再显示和播放。

Apple Music catalog 内容随地区变化，每个 catalog 请求都必须指定 storefront。跨地区时，优先调用 Apple 官方的 equivalent-song endpoint；失败时再用 ISRC 查询并让用户确认多个返回版本。来源：[Storefronts and Localization](https://developer.apple.com/documentation/applemusicapi/storefronts-and-localization)、[Get Equivalent Catalog Songs by ID](https://developer.apple.com/documentation/applemusicapi/get-equivalent-ids-for-the-albums-3ce20)、[Get Multiple Catalog Songs by ISRC](https://developer.apple.com/documentation/applemusicapi/get-multiple-catalog-songs-by-isrc)。

### 3.4 不可跨用户分享的资料库内容

Apple 官方说明个人 iCloud Music Library 可能包含 Apple Music catalog 曲目、iTunes Store 购买内容，以及从光盘或其他 App 导入、并不在 Apple Music catalog 的内容。后一类歌曲即使发送了标题和封面，也不能假设另一位用户可通过 MusicKit 获得同一音源。

产品上应当把它显示成“仅存在于你的资料库，无法作为 Apple Music 歌曲卡发送”，或只发送不可播放的文字引用。来源：[Apple Music API Overview](https://developer.apple.com/documentation/applemusicapi/)。

## 四、中文曲库与中国大陆可用性

### 4.1 官方证据

Apple 的一手材料足以确认“Apple Music 不是只有少量中文独立音乐”：

- [Apple Music 中国大陆产品页](https://www.apple.com.cn/apple-music/)明确提供人民币个人、学生和家庭订阅，称有“数千万首好歌”，并列出 iOS、Android、Windows 等设备支持。
- [Apple Media Services availability](https://support.apple.com/en-us/118205)把 Apple Music 和 Apple Music Classical 列在 China mainland 可用服务中。
- [Apple Music 中国区类别页](https://music.apple.com/cn/search)直接提供“国语流行”“粤语流行”“C-Pop”等类别。
- [Apple Music 中国区排行榜](https://music.apple.com/cn/new/top-charts)包含“热播金曲：国语流行”“A-List：国语流行”“周杰伦代表作”“A-List：粤语流行”等内容。
- [周杰伦代表作](https://music.apple.com/cn/playlist/%E5%91%A8%E6%9D%B0%E4%BC%A6%E4%BB%A3%E8%A1%A8%E4%BD%9C/pl.d467987f72384448b2bebe52c0b212d6)是 Apple Music 中国区官方国语流行歌单，当前页面有 33 首曲目。
- Apple 2024 年与中国移动合作的[官方新闻稿](https://www.apple.com.cn/newsroom/2024/10/apple-music-comes-to-china-mobile-customers/)称中国大陆用户可使用中国大陆 Apple ID 登录，享受数千万首音乐和近万个精选歌单。

### 4.2 不能由官网直接证明的事情

这些材料不能证明 Apple Music 中国区和网易云、QQ 音乐的曲库逐首相同，也不能保证某首歌、某个现场版、翻唱、播客或用户上传音频存在。

因此，正式选型前必须做 catalog 命中率测试，而不是继续比较“总曲目数”。建议准备 300–500 首目标样本：

- 100 首最近两年中国大陆热门歌；
- 100 首 2000–2020 年国语/粤语经典；
- 50 首独立音乐人与小众中文作品；
- 50 首用户当前网易云红心/常听歌曲；
- 若产品需要，再加入现场版、游戏/影视 OST、同人音乐。

验收指标建议：catalog 搜索命中率、正确版本率、`playParams` 可播放率、中国大陆 Apple ID 实际整曲播放成功率分别统计。**中文覆盖是否足够是产品样本问题，不是 SDK 能力问题。**

## 五、登录、授权与数据边界

### 5.1 iOS

MusicKit for Swift 使用 `MusicAuthorization` 请求用户知情同意；必须在 `Info.plist` 提供 `NSAppleMusicUsageDescription`。框架默认自动管理开发者 token 和 Music User Token，并用 `MusicSubscription.current` 检查当前用户是否可以播放 catalog 内容。来源：[MusicKit framework](https://developer.apple.com/documentation/musickit)、[MusicSubscription](https://developer.apple.com/documentation/musickit/musicsubscription)。

### 5.2 Android

MusicKit 官方页说明 Android Authentication library 会提示用户登录 Apple Music，取得用于播放或调用 MusicKit Web API 的 access token；如果设备没有 Apple Music App，还会帮助用户下载后返回 Murmur。Android 的 Music User Token 需要应用自行管理并加入 API 请求头。来源：[MusicKit](https://developer.apple.com/musickit/)、[User Authentication for MusicKit](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)。

Apple 中国大陆产品页还提供 Android 版本并链接到小米和腾讯下载渠道，这证明 Apple Music 消费服务在中国 Android 设备上有官方分发；但它不能替代 Murmur 对 Authentication library 与 Android playback SDK 的真机兼容测试。

### 5.3 Murmur 应保存什么

- Murmur 用户 ID 与“Apple Music 已连接”状态；
- 用户 storefront；
- 必要的授权状态、订阅能力缓存及过期时间；
- 经授权读取并且确实用于音乐体验的最少量 library 数据。

Music User Token 必须保存在 Keychain/Android Keystore 或等价安全存储，不进入聊天正文、日志、分析事件或 LLM prompt。

MusicKit 没有在这些官方页面中承诺向第三方 App 提供用户姓名、头像或邮箱；因此产品不能把“连接 Apple Music”设计成可读取 Apple Music 社交个人资料的登录。Murmur 主账号与音乐授权应当分离。

## 六、播放与交互边界

### 6.1 整曲与后台播放

Apple Developer Program License Agreement 要求：如果 App 提供 MusicKit 播放，必须启用完整歌曲播放，用户必须发起播放，并可使用播放、暂停、跳过等标准控制。来源：[Apple Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/)。

iOS `ApplicationMusicPlayer` 专为 App 自身播放，且 Apple 明确说明：App 配置 background audio mode 后，进入后台仍继续播放当前歌曲。来源：[ApplicationMusicPlayer](https://developer.apple.com/documentation/musickit/applicationmusicplayer)。

Android 官方 `MediaPlayerController` 提供 play、pause、stop、上一首、下一首和队列操作，说明 App 可构建自己的播放控制界面。来源：[MediaPlayerController](https://developer.apple.com/musickit/android/com/apple/android/music/playback/controller/MediaPlayerController.html)。

### 6.2 “Murmur 给我放歌”的安全解释

建议把产品语义定义为：

- 用户请求 Murmur 推荐或放一首歌；
- Murmur 发出可播放歌曲卡，并把播放按钮置于明确位置；
- 用户点击播放；
- 随后正常的暂停、继续、下一首、队列播放由用户控制。

不要默认把“消息生成完成”当作播放动作。Apple 没有在公开材料中明确说明一句自然语言/语音命令是否一定构成 MusicKit 所要求的 user initiation；首版用显式点击最稳妥。

如果未来要让歌曲和 Murmur 对话、故事节点、视频画面或情绪脚本精确同步，必须单独评估权利。Apple Review Guidelines 明确指出，MusicKit API 不能替代复杂集成所需的授权，并把“在某个特定时刻播放某首特定歌曲”列为可能需要联系权利人的示例。来源：[App Review Guidelines 3.1.1](https://developer.apple.com/app-store/review/guidelines/)。

## 七、AI、隐私与商业限制

### 7.1 Apple 没有公开的 MusicKit “禁止 AI”条款，但不代表可任意使用数据

本次核验到的 Apple 官方材料没有像 Spotify/TIDAL 那样直接写出“不得用于 AI”的 MusicKit 禁令。可行的低风险方案是：

- 将用户自然语言转换为 catalog 搜索请求；
- 将少量必要的曲目元数据提供给模型生成推荐理由；
- 由 API 而不是模型确定正式 catalog ID；
- 不把音频、Music User Token、完整资料库或可识别的长期听歌画像送入模型；
- 明确禁止模型供应商用 Apple Music 用户数据训练通用模型。

这是基于 Apple 数据规则得出的保守工程建议，不是 Apple 对某种 LLM 架构的预先批准。

App Review Guidelines 要求 App 明确披露访问 Apple Music 用户数据；收集的数据不得共享给第三方，除非用于支持或改善 App 体验；不得用于识别用户/设备或定向广告。若 Murmur 使用外部模型供应商，应将其限定为代表 Murmur 处理数据的服务商，做数据最小化、禁训练、保留期和删除约束，并在上线前让法务/Apple Review 对具体数据流确认。来源：[App Review Guidelines 3.1.1](https://developer.apple.com/app-store/review/guidelines/)。

### 7.2 商业模式硬限制

Apple 明确禁止要求付费或间接变现 Apple Music 服务访问，例子包括 IAP、广告和索取用户信息；同时禁止把 Apple Music 访问作为内建 Apple 服务能力进行变现。来源：[App Review Guidelines 3.1.1、4.10](https://developer.apple.com/app-store/review/guidelines/)及[Apple Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/)。

对 Murmur 的直接影响：

- Apple Music 连接、歌曲卡播放、基本播放控制不应只给 Murmur 付费用户；
- 不应在 Apple Music 播放器周围放广告，或以连接 Apple Music 为条件索取额外营销资料；
- Murmur 可以对其独立的 AI/陪伴能力收费，但产品、价格页和代码权限必须能证明收费不是为了获得 Apple Music 访问；
- 最好在 App Review Notes 里解释 Murmur 订阅与 MusicKit 免费集成的边界。

### 7.3 内容限制

禁止下载、上传、修改或允许用户分享 MusicKit 音乐文件；MusicKit 内容只能按 MusicKit API/JS 渲染。封面和音乐文本只能与播放或歌单管理关联，未经额外许可不得用于营销广告。来源：[Apple Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/)。

因此聊天歌曲卡可以是播放入口，但不要将封面做成 Murmur 广告素材，也不要把 Apple Music 音频混入 Murmur 导出的日记视频、语音消息或社交分享文件。

## 八、建议的 PoC 与 Go/No-Go 标准

### 8.1 两周技术 PoC

只做垂直切片：

1. 中国大陆 Apple ID 在 iPhone 授权 MusicKit；
2. 搜索 50 首中文样本并显示歌曲卡；
3. 用户点击后用 `ApplicationMusicPlayer` 整曲播放；
4. 锁屏/后台播放、暂停、跳过；
5. 读取资料库歌曲并转换为 catalog card；
6. 同一张卡在另一账号/storefront 上通过 equivalent/ISRC 重新解析；
7. Android 在中国常见机型完成 Apple Music 登录、搜索、整曲播放和后台行为；
8. 非会员、会员过期、曲目下架、无 catalog 对应 library song 的降级体验。

### 8.2 Go 条件

- iOS 和 Android 均能在 Murmur 内稳定完成用户授权后的整曲播放；
- 目标中文样本的搜索命中和正确版本率达到产品设定阈值；
- 中国大陆 Apple ID 与目标 Android 分发环境可稳定登录；
- Apple Music 连接/播放不被 Murmur 付费墙阻挡；
- LLM 数据流不包含 token、音频、完整资料库或可识别长期画像；
- 歌曲卡始终由 catalog API 确认，播放始终由用户明确发起。

### 8.3 No-Go 条件

- Murmur 的商业模式必须把 Apple Music 播放作为 Pro 独占权益；
- 产品坚持要求 LLM 自动在任意时刻强制开播或把音乐与内容精确同步，却不计划取得额外权利；
- Android 中国目标设备无法稳定完成官方登录和 App 内播放；
- 核心用户的中文歌曲样本命中率不足；
- 产品必须读取 Apple Music 用户姓名/邮箱/头像作为账号数据；
- 产品必须把 Apple Music 音频导出、混音或作为聊天附件传输。

## 九、最终判断

在 Audius 中文曲库不足的前提下，**Apple Music 是当前公开官方方案里最接近 Murmur 完整要求的选择**。它不是网页嵌套或遥控外部 App，而是有 iOS/Android 原生播放能力、正式用户授权、用户资料库和中国大陆华语 catalog 的完整平台。

最适合 Murmur 的上线形态是：

> Murmur 主账号 + 独立 Apple Music 授权；双向聊天歌曲卡只携带 catalog 引用；接收端按 storefront 解析；用户点击后在 Murmur 内整曲播放；MusicKit 基础访问不放入 Murmur 付费墙；AI 只处理最少量音乐元数据，不接触 token、音频与完整资料库。

这个版本可以进入 PoC。真正剩余的不确定性不是“SDK 有没有”，而是三项需要实测/确认的产品条件：**目标中文歌单命中率、中国 Android 真机稳定性、Murmur 商业模式与 MusicKit 不得变现条款能否兼容。**

## 十、Apple 官方来源索引

- [MusicKit 总览：Apple platforms、Web、Android、catalog、library、playback](https://developer.apple.com/musickit/)
- [MusicKit for Swift](https://developer.apple.com/documentation/musickit)
- [ApplicationMusicPlayer 与后台播放](https://developer.apple.com/documentation/musickit/applicationmusicplayer)
- [MusicSubscription](https://developer.apple.com/documentation/musickit/musicsubscription)
- [Apple Music API](https://developer.apple.com/documentation/applemusicapi/)
- [User Authentication for MusicKit](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)
- [Storefronts and Localization](https://developer.apple.com/documentation/applemusicapi/storefronts-and-localization)
- [Songs.Attributes](https://developer.apple.com/documentation/applemusicapi/songs/attributes-data.dictionary)
- [Library song 到 catalog 的关系](https://developer.apple.com/documentation/applemusicapi/librarysongs/relationships-data.dictionary)
- [跨 storefront 等价歌曲查询](https://developer.apple.com/documentation/applemusicapi/get-equivalent-ids-for-the-albums-3ce20)
- [按 ISRC 查询歌曲](https://developer.apple.com/documentation/applemusicapi/get-multiple-catalog-songs-by-isrc)
- [Android MediaPlayerController](https://developer.apple.com/musickit/android/com/apple/android/music/playback/controller/MediaPlayerController.html)
- [App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/)
- [Apple Music 中国大陆产品页](https://www.apple.com.cn/apple-music/)
- [Apple Media Services availability](https://support.apple.com/en-us/118205)
- [Apple Music 中国区类别](https://music.apple.com/cn/search)
- [Apple Music 中国区排行榜](https://music.apple.com/cn/new/top-charts)
- [Apple 与中国移动合作公告](https://www.apple.com.cn/newsroom/2024/10/apple-music-comes-to-china-mobile-customers/)

