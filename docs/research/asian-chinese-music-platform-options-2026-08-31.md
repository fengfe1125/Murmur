# 亚洲华语音乐平台接入调研：KKBOX、JOOX 及其他候选

> 调研日期：2026-08-31
>
> 范围：Apple Music 之外，寻找同时具备华语曲库、正式账号授权、用户资料库、搜索、结构化歌曲卡、iOS/Android App 内完整播放，并能用于商业 AI 聊天产品的平台。
>
> 证据标准：只采用平台官方开发文档、官方条款、官方公司资料及官方代码仓库。没有公开证据的能力按“未证实”处理。

## 1. 结论

如果排除 Apple Music，**最值得继续推进的是 KKBOX，但必须走 KKCompany 企业级 B2B/Partner 合作，不是直接使用公开 Open API**。

KKBOX 是本轮唯一同时具备以下官方证据的平台：

- 官方明确把自己定位为华语音乐曲库优势平台；
- 有搜索、曲目、专辑、艺人、歌单等公开元数据 API；
- 官方企业方案包含曲库、会员系统、播放器、DRM、支付、版税结算与版权协商；
- 官方明确提供播放器、搜索、AI 推荐等模块化能力，并支持通过 API 集成；
- 已有第三方 App 内播放 KKBOX 完整音乐的商业合作先例。

但是，公开资料还不能证明普通开发者可以自行获得以下能力：

- 用现有 KKBOX 账号完成 OAuth 登录；
-读取用户私人歌单、收藏和完整历史；
- 在 Murmur iOS/Android 内获得受 DRM 保护的完整曲目；
- 把 KKBOX 数据用于 Murmur 的商业 AI 聊天、推荐和个性化上下文。

这些都必须写进合作合同，并由 KKCompany 提供 Partner 级接口、播放授权和技术资料。也就是说，**KKBOX 是“商务上有机会实现”，不是“注册开发者账号后立即可做”**。

其他候选的结论：

- **JOOX**：华语及亚洲曲库有吸引力，但没有面向普通开发者的公开音乐 OAuth、用户库 API 或双端完整播放 SDK；官方条款还禁止未经许可开发互操作插件。只能作为腾讯音乐娱乐的商务合作线索，不能当公开 API 技术方案。
- **MOOV**：香港本地华语曲库强，但没有公开第三方开发平台；公开能力只覆盖 MOOV 自有 App/网站。
- **LINE MUSIC**：公开的 LINE Login 只返回 LINE 身份资料，不是 LINE MUSIC 资料库授权；未发现公开音乐搜索、用户库或完整播放 SDK。
- **Anghami**：技术能力几乎完整，甚至官方称其 API “agent-ready”，但开发者门户尚未公开上线，而且官方主打阿拉伯与国际曲库，没有足够证据证明华语覆盖能满足 Murmur。
- **YouTube Music**：华语内容覆盖可能高，但没有公开 YouTube Music API；YouTube IFrame 政策不允许隐藏播放器、抽离纯音频或把视频当后台音乐源，因此不满足产品要求。

## 2. Murmur 的判定标准

本报告把“可以接入”定义为同时满足以下条件，而不是只返回歌曲搜索结果或分享链接：

1. 用户通过平台正式授权登录，而不是提交账号密码、Cookie 或扫码后抓取网页会话。
2. Murmur 能读取用户被授权的收藏、歌单或历史，以支持“陪你听”的个性化。
3. Murmur 能搜索平台曲库并发送稳定的结构化歌曲卡。
4. 用户也能把平台歌曲作为结构化消息发给 Murmur。
5. iOS 和 Android 都能在 Murmur 内播放完整歌曲，不跳转平台 App，不只播放 30 秒试听。
6. 播放遵循订阅资格、地区、DRM、锁屏/后台控制及版税规则。
7. 商业条款允许聊天、推荐或 AI 场景；至少能通过书面合作获得相应授权。

## 3. 平台对比矩阵

| 平台 | 华语曲库证据 | 正式用户授权与资料库 | 公开搜索/歌曲卡 | iOS/Android 内完整播放 | AI/商业聊天 | 最终判断 |
| --- | --- | --- | --- | --- | --- | --- |
| **KKBOX 公开 Open API** | 强 | 公开 SDK 示例主要为 client credentials；私人数据与播放不开放给该流程 | 是 | 否；Swift/Android SDK 是元数据 API 客户端，不是播放器 SDK | 未见公开许可 | 只能做搜索和歌曲卡，不满足完整目标 |
| **KKBOX B2B/Partner** | 强 | 可提供会员模块；现有 KKBOX 账号/用户库仍需逐项确认 | 可 | 官方有播放器、DRM、多设备和完整产品模块 | 官方提供 AI 推荐，但 Murmur 聊天用途需合同确认 | **唯一值得优先商务询证的亚洲方案** |
| **JOOX** | 较强，官方称超过 4,000 万首全球曲目 | 消费者有账号/歌单，但无公开第三方用户库授权 API | 无公开开发者 API | 无公开第三方完整播放 SDK | 未授权互操作被条款禁止 | 只能商务洽谈，不能直接开发 |
| **MOOV** | 强于粤语/香港市场 | 仅 MOOV 自有服务 | 无公开 API | 无公开第三方 SDK | 无公开许可 | 不可作为公开集成方案 |
| **LINE MUSIC** | 台湾/日本本地内容较强 | LINE Login 不是 LINE MUSIC 用户库授权 | 无公开 Music API | 无公开 Music 播放 SDK | 消费者条款限制为私人使用 | 不可行 |
| **Anghami** | 无充分华语优势证据 | OAuth 2.0 PKCE；可读资料库、歌单、历史 | 是 | 有 DRM stream acquisition，Swift/Java 客户端模型 | 官方称 agent-ready | 技术好但曲库方向不匹配，且当前需合作准入 |
| **YouTube / YouTube Music** | 可能较强 | YouTube OAuth 不是公开的 YouTube Music 资料库 API | 只能使用 YouTube 视频数据 | 只能使用可见 IFrame 视频播放器，不能当纯音频后台源 | 条款限制严格 | 不满足 Murmur 的原生音乐播放目标 |

## 4. KKBOX：真正可行的是 B2B/Partner 路线

### 4.1 华语内容匹配度

KKCompany 的官方公司资料称 KKBOX 服务覆盖台湾、日本、香港和新加坡，当前曲库超过 3 亿首。其公开发行资料进一步称 KKBOX 以“最齐全的华语音乐产品”著称，长期取得唱片公司、版权公司与词曲版权协会授权，并经营华语独家内容及 IP。

这意味着从“中文歌够不够”看，KKBOX 比 Audius、Anghami、SoundCloud 等开放平台更符合 Murmur。[KKCompany 官方主页](https://www.kkcompany.com/)、[KKCompany 公开发行资料](https://ir.kkcompany.com/files/uploads/%E5%85%AC%E9%96%8B%E8%AA%AA%E6%98%8E%E6%9B%B8/202310_6950_B07_20240118_174448.pdf)

### 4.2 公开 Open API 能做什么

KKBOX 官方 Open API SDK 可获取歌曲、专辑、艺人、歌单、电台等元数据，支持搜索。官方 JavaScript SDK 还可为歌曲生成 KKBOX Web Widget 地址。这足以支持：

- Murmur 根据歌名、歌手搜索；
- 生成包含 KKBOX track ID、标题、歌手、专辑、封面和分享链接的歌曲卡；
- 用户把 KKBOX 链接发给 Murmur 后解析成结构化消息；
- 在没有播放权时跳转 KKBOX 或展示官方 Widget。

官方 Swift 和 Android 仓库虽然名字中包含 iOS/Android SDK，但其职责是调用 Open API、取得元数据，并不是提供 DRM 全曲播放的 MusicKit 类播放器。Android 官方仓库最后公开 release 为 2017 年，Swift 示例基于 client credentials；它们不应被误判为现代双端播放 SDK。[KKBOX OpenAPI Swift](https://github.com/KKBOX/OpenAPI-Swift)、[KKBOX OpenAPI Android](https://github.com/KKBOX/OpenAPI-Android)、[KKBOX JavaScript SDK](https://github.com/KKBOX/OpenAPI-JavaScript)

### 4.3 公开 Open API 为什么不能直接完成目标

KKBOX 官方 Python SDK 文档明确区分：client credentials 只能访问公开专辑、歌单等数据，不能访问用户私人数据，也不能进行媒体播放；播放要求 Premium 会员和不同的用户授权能力。[KKBOX Open/Partner API Python 文档](https://kkbox.github.io/OpenAPI-Python/kkbox_developer_sdk.html)

所以以下方案不可接受：

- 把 OpenAPI Swift/Android SDK 当作完整播放器；
- 抓取用户 Cookie 或直接索要 KKBOX 密码；
- 从网页或私有接口提取真实音频地址；
- 认为 `getWidgetUri()` 自动保证 Murmur 原生后台全曲播放；
- 用 30 秒预览冒充完整播放。

KKBOX 官方对 Podcast 音乐嵌入的说明也展示了版权边界：非付费用户只能听 30 秒，付费用户才能听完整歌曲；在其他平台收听 Podcast 时，嵌入音乐不会随节目音频一起分发。这说明完整播放必须绑定 KKBOX 资格和被批准的播放环境。[KKBOX Podcast 音乐嵌入说明](https://www.kkcompany.com/newsroom/kkbox-to-launch-a-music-insertion-feature-bringing-music-and-podcasts-together/)

### 4.4 B2B/Partner 方案为什么有机会

KKCompany 官方发行资料列出了三档企业音乐串流产品，其中完整集成模块包含：

- 会员系统；
- 播放系统；
- 音乐曲库；
- 支付系统；
- 版税报告系统；
- 权利协商服务。

官方还说明这些模块包括多设备播放器、商业 DRM、AI 歌曲推荐、搜索、歌曲转码、歌单后台和版税结算，并可通过 API 整合。它已有为日本 Smart Pass Music 提供端到端音乐服务的案例。[KKCompany 企业级串流平台说明](https://ir.kkcompany.com/files/uploads/%E5%85%AC%E9%96%8B%E8%AA%AA%E6%98%8E%E6%9B%B8/202310_6950_B07_20240118_174448.pdf)、[KKCompany 模块化音乐串流与 AI 说明](https://www.kkcompany.com/newsroom-en/kkcompany-technologies-joins-microsoft-startup-initiative-and-advances-ai-powered-cloud-streaming-solutions-in-southeast-asia/)

完整曲目在第三方 App 内播放也有官方先例：KKBOX 曾与 AURALiC 合作，在 AURALiC Lightning DS App 内串接 KKBOX 并播放无损音乐。这证明技术和版权模式并非只允许跳回 KKBOX App，但它显然属于商业合作能力，而不是普通 Open API 权限。[KKBOX 与 AURALiC 合作说明](https://www.kkcompany.com/newsroom/kkbox-to-launch-lossless-audio-family-plan/)

### 4.5 对 Murmur 的准确判断

KKBOX B2B/Partner 在技术组件上可以覆盖 Murmur 的大部分目标，但合作前必须把以下项目写入询证清单和合同：

1. 是否允许用户使用**现有 KKBOX 账号**授权 Murmur，而不是创建独立的白标账号。
2. OAuth/Partner scope 是否包含收藏歌曲、私人歌单、关注艺人、播放历史和推荐结果。
3. 是否提供 iOS 与 Android 的生产级播放器 SDK、DRM license 流程或受保护 manifest。
4. 是否支持后台播放、锁屏控制、耳机控制、断线恢复和订阅资格检查。
5. Murmur 聊天中的歌曲卡是否可展示封面、艺人、专辑、歌词片段和试听状态。
6. 用户能否把歌曲卡发给 Murmur；Murmur 能否在聊天中主动推荐并发卡。
7. 是否允许“用户点击 Murmur 推荐后播放”，以及 AI 是否可以触发暂停、继续、切歌。
8. 是否允许将搜索词、曲目 ID、用户显式反馈和有限偏好数据交给 Murmur 的模型服务处理。
9. 哪些音乐元数据禁止作为模型输入、训练数据、长期记忆或跨用户推荐特征。
10. 授权地区是否包含中国大陆、台湾、香港、新加坡，以及跨区账号如何处理。
11. Murmur 收费、会员捆绑、广告或增值服务是否需要额外音乐商业授权。
12. 版税如何按播放事件上报，30 秒、完整播放、跳过、循环等事件如何计数。

其中第 1、2、7、8 项是最大不确定性。KKCompany 提供 AI 推荐模块，并不自动等于允许第三方生成式 AI 聊天访问全部用户音乐数据。

### 4.6 商务入口

KKCompany 官方利益相关者页面给出的企业合作邮箱为 `bd@kkcompany.com`。[KKCompany 官方联系资料](https://ir.kkcompany.com/en/sustainable/stakeholders)

建议提交一页中英文合作说明，避免只询问“有没有 API”。应明确描述：

> Murmur 是一款用户与 AI 伙伴对话的移动 App。双方可以在私聊中发送结构化歌曲卡，用户点击后在 Murmur 内使用自己的音乐订阅播放完整曲目；Murmur 不下载、重分发或训练音乐音频，只在取得用户同意后使用最少量的曲目元数据和偏好信息完成搜索、推荐与对话。

## 5. JOOX：曲库方向对，但没有公开集成通道

JOOX 官方网站称其拥有超过 4,000 万首歌曲，服务覆盖香港、澳门、泰国、马来西亚、印度尼西亚等市场，消费者产品包含歌单、收藏、搜索、卡拉 OK、直播与社交房间。从产品受众看，它可能拥有相当数量的华语及亚洲音乐。[JOOX 官方主页](https://www.joox.com/intl)

但公开开发能力不足：

- 未发现面向第三方 App 的正式 JOOX OAuth、用户资料库 API 或公开开发者控制台；
- 未发现用于第三方 App 内完整音乐播放的 iOS/Android SDK；
- JOOX 的消费者账号和网页搜索不能视为第三方开发授权；
- `cache.api.joox.com` 等站点的存在不等于可公开使用的开发者 API。

官方 Acceptable Use Policy 明确禁止开发与 JOOX 服务互操作的插件、外部组件或连接技术，除非 JOOX 明确允许；同时禁止抓取、镜像和绕过安全特性。消费者协议还把 JOOX 内容许可限定为个人、非商业使用，并规定下载内容只能在 JOOX 内播放。[JOOX Acceptable Use Policy](https://www.joox.com/en_my/app/acceptableusepolicy.html)、[JOOX User Agreement](https://www.joox.com/en/user_agreement.html)

因此 JOOX 的判断是：

- **不能用非官方接口开发**；
- **不能靠 WebView、Cookie 或内部 API 做完整播放器**；
- 只能向 `BD@JOOX.com` 发起商务询证，由腾讯音乐娱乐明确提供合同、API、DRM 和 AI 场景许可。[JOOX 官方联系页面](https://www.joox.com/en/contact_us.html)

在没有拿到书面材料之前，不应把 JOOX 写入工程排期。

## 6. MOOV 与 LINE MUSIC

### 6.1 MOOV

MOOV 官方资料显示其长期经营香港本地华语、粤语音乐市场，自有 iOS/Android App 支持串流、收藏、下载和离线播放。问题在于这些都是 MOOV 自有消费者产品能力；未发现公开第三方开发门户、OAuth 用户库接口或内嵌播放器 SDK。[MOOV 官方服务说明](https://moov.hk/news.jsp?newsId=2)

所以 MOOV 目前只能作为 PCCW 商务合作候选，不能作为公开 API 方案。即使可以用网页分享链接，也无法满足 Murmur 的账号资料库和双端原生全曲播放。

### 6.2 LINE MUSIC

LINE 的公开 OAuth 能返回 LINE 用户 ID、头像、昵称等 LINE 平台资料，但官方文档没有把它描述为 LINE MUSIC 订阅、歌单、收藏或历史授权。LINE 官方公开 OpenAPI 清单也未列出 LINE MUSIC 搜索、资料库或流媒体播放 API。[LINE Login API](https://developers.line.biz/en/reference/line-login/)、[LINE 官方 OpenAPI 清单](https://github.com/line/line-openapi)

LINE MUSIC 消费者条款把内容使用限定为私人使用，并限制商业、公开传输和非预期用途。因此，不能把普通 LINE Login 与 LINE MUSIC 网页播放器拼接成 Murmur 的商业音乐方案。[LINE MUSIC 台湾服务条款](https://terms2.line.me/music_terms_of_service?lang=zh-Hant)

## 7. Anghami：技术上接近满分，但不能解决华语曲库问题

Anghami 在 2026 年公开的官方 SDK 文档非常接近 Murmur 所需能力：

- OAuth 2.0 Authorization Code + PKCE；
- 搜索歌曲、专辑、艺人、歌单；
- 读取用户喜欢、关注、歌单和播放历史；
- `AcquireMusicStream` 返回 manifest、DRM scheme 和 license URL；
- SDK 生成 Swift、Java、TypeScript、Python 等客户端；
- 官方称 API 为 “agent-ready”，提供 OpenAPI、`llms.txt` 和 Agent Discovery。

[Anghami SDK 介绍](https://docs.sdk.anghami.com/introduction)、[Anghami OAuth](https://docs.sdk.anghami.com/usage-auth)、[Anghami 完整曲目 Stream Acquisition](https://docs.sdk.anghami.com/api-reference/streamingservice/acquiremusicstream)

但它有两个决定性问题：

1. 官方介绍强调阿拉伯和国际歌曲，没有足够一手证据证明主流华语覆盖能满足 Murmur；
2. 官方 Developer Portal 仍是 “Coming soon”，目前要求联系 Anghami partnership lead 或 SDK 项目，因此不是完全自助准入。[Anghami Developer Portal](https://docs.sdk.anghami.com/developer-portal)、[Anghami 官方曲库定位](https://www.anghami.com/about)

所以 Anghami 可作为未来中东市场适配器，也可参考其 API 设计，但不应被当作 Audius 的“中文歌替代品”。

## 8. 建议路线

### 8.1 如果现在就需要做出满足要求的产品

仍应优先使用 Apple MusicKit。它是现阶段唯一同时具备主流华语曲库、正式用户授权、用户资料库、搜索和 iOS/Android 官方播放能力，并可由普通开发团队启动接入的方案。

### 8.2 如果必须不用 Apple Music

按以下顺序推进：

1. **联系 KKCompany，询证 KKBOX Partner/B2B。** 只有对方确认现有账号 OAuth、用户库、双端完整播放和 AI 聊天用途后才进入 PoC。
2. **同时保留腾讯音乐娱乐 B2B 线。** 如果核心市场是中国大陆，QQ 音乐/TME 企业授权可能比 JOOX 更匹配；JOOX 主要是东南亚和香港消费者品牌。
3. **架构上做多 Provider。** 海外/港澳台可用 Apple Music 或 KKBOX，未来中国大陆另接 TME；聊天中的 `TrackAttachment` 不绑定单一平台。
4. **不要把 JOOX、MOOV、LINE MUSIC 排入工程实现。** 它们目前只能进入商务询证列表。

### 8.3 KKBOX PoC 的进入与终止条件

只有 KKCompany 提供以下材料后才开始工程 PoC：

- Sandbox Partner client 与正式 OAuth 文档；
- 测试账号和测试订阅；
- iOS/Android 播放 SDK 或 DRM 集成文档；
- 用户库、搜索、曲目和 entitlement API；
- AI/聊天/商业使用书面许可；
- 覆盖地区、费率、最低承诺与版税上报说明。

出现以下任一情况即终止：

- 只能使用公开 Open API 和 Widget；
- 只能跳转 KKBOX App；
- 只能播放 30 秒；
- 只能创建白标新账号，而产品必须读取用户现有 KKBOX 收藏；
- 只支持 iOS 或只支持 Android；
- 禁止聊天推荐、AI 搜索编排或 Murmur 的商业模式；
- 需要取得、转存或代理用户 KKBOX 密码/Cookie。

## 9. 最终推荐

针对“中文歌足够多，并完成 Murmur 双向发歌 + App 内全曲播放”的目标：

- **可自助落地的第一选择：Apple MusicKit。**
- **Apple 之外唯一值得认真推进的华语方案：KKBOX B2B/Partner。**
- **中国大陆补充线：腾讯音乐娱乐/QQ 音乐企业合作。**
- **JOOX、MOOV、LINE MUSIC：目前没有公开能力，不应作为技术方案承诺。**
- **Anghami：技术可行但华语曲库不匹配。**

因此，不建议继续寻找“某个隐藏的免费 API”。真正的选择已经从技术选型转变为商务与版权选型：要么采用 Apple MusicKit，要么与 KKCompany/TME 签署覆盖账号、播放、用户数据和 AI 聊天场景的正式合作。
