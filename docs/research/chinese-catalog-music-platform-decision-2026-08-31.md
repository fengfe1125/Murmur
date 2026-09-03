# Murmur 中文曲库音乐平台最终选型

> 状态：产品与技术决策调研｜适用：Murmur 中文曲库平台选型｜核验：2026-08-31｜依据：平台官方开发文档、产品页、条款、代码仓库与企业资料；不代表平台授权或生产验收
>
> 核验日期：2026-08-31（Asia/Shanghai）
>
> 目标：正式音乐账号授权、读取用户音乐资产、Murmur 与用户在聊天中双向发送结构化歌曲卡、iOS/Android App 内完整播放，并具备足够的中文/华语曲库
>
> 证据口径：仅采用平台官方开发文档、官方产品页、官方条款、官方代码仓库与官方企业资料；公开能力与商务合作能力分开标记

## 1. 最终结论

Audius 中文歌覆盖不足后，候选路线已经收敛为三条：

1. **Apple MusicKit：当前唯一可以由普通开发团队自行启动、并最接近全部要求的正式方案。**它有正式音乐授权、catalog、用户资料库、收藏/歌单/最近播放、iOS 与 Android App 内整曲播放，也有中国大陆服务和大量官方华语内容。
2. **QQ 音乐 / TME：中文曲库与中国大陆用户匹配度最高，但只能作为企业商务合作路线。**正式能力形状可以覆盖登录、个人资产、搜索、流媒体与播控；Murmur 仍需取得 iOS/Android SDK、AI 聊天场景和商业播放的书面合同，不能用非官方接口替代。
3. **KKBOX Partner/B2B：港澳台及海外华语市场的第二商务候选。**公开 Open API 只能可靠完成搜索、元数据和歌曲卡；完整用户账号、用户资料库、DRM 播放器与 AI 商业用途必须由 KKCompany 通过 Partner 合同提供。

因此建议不是继续寻找隐藏的免费接口，而是：

> **现在用 Apple MusicKit 做两周 PoC；并行向 TME/QQ 音乐询价。如果 Apple 中文样本命中率达标，就以 Apple MusicKit 上线；若命中率不足或必须覆盖非 Apple 用户，再决定是否承担 TME 企业接入成本。**

## 2. 严格需求对比

| 平台/路线 | 中文曲库 | 正式用户授权 | 用户资料库/歌单 | 双向歌曲卡 | iOS/Android App 内完整播放 | 是否可立即启动 | 判断 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Apple MusicKit** | 强；中国大陆有国语、粤语、C-Pop、城市榜与大陆热门榜 | 是，音乐授权与 Murmur 主账号分离 | 是 | 是 | **是，官方双端能力** | **是** | **第一选择，条件 Go** |
| **QQ 音乐 / TME 企业合作** | 很强，最贴近大陆用户 | 能力存在，具体 scope 需合同 | 能力存在，具体 scope 需合同 | 可通过正式 ID/metadata 实现 | 需确认当前 iOS/Android SDK 与授权 | 否，先商务 | **大陆长期最优候选** |
| **KKBOX Partner/B2B** | 强，偏台湾、香港与海外华语 | Partner 能力待确认 | Partner 能力待确认 | 是 | 企业方案有播放器/DRM，但须合同确认 | 否，先商务 | **海外华语第二候选** |
| KKBOX 公开 Open API | 强 | 公开流程主要是 client credentials | 否 | 是 | 否；SDK 是 API 客户端，不是完整播放器 | 可申请 | **只能做卡片，不满足目标** |
| 酷狗曲库开放计划 | 强 | 未公开证明个人账号授权 | 未公开证明 | 可基于曲目 ID 实现 | **官方有 iOS/Android 在线播放 SDK** | 需客户经理 | **只满足播放，账号闭环不足** |
| 网易云音乐厂商合作 | 强 | 需厂商授权 | 需厂商 scope | 可设计 | 需正式 SDK/播放权 | 否，先商务 | **签约后可能可行；目前 No-Go** |
| 咪咕音乐合作 | 强 | 未公开证明 Murmur 所需 OAuth | 未公开证明 | 取决于合同 | 取决于合同 | 否，企业审核/签约 | **备选商务线** |
| JOOX / MOOV / LINE MUSIC | 各自地区曲库有优势 | 无公开完整用户库授权 | 无公开完整能力 | 最多链接/元数据 | 无公开第三方双端完整播放 SDK | 否 | **不进入工程排期** |
| Spotify / YouTube Music | 中文歌不少但区域/目录不同 | 有部分授权能力 | 有部分能力 | 是 | Spotify 移动端是 App Remote；YouTube 只能可见视频 iframe | 可注册部分 API | **不满足 Murmur 内原生音乐播放** |

## 3. 第一选择：Apple MusicKit

### 3.1 为什么它能满足核心需求

[MusicKit 官方总览](https://developer.apple.com/musickit/)明确支持：

- 用户授权 Apple Music；
- 搜索 Apple Music catalog；
- 访问用户音乐资料库、收藏、歌单、最近播放和推荐；
- 创建或修改歌单；
- 在 Apple 平台、Android 和 Web 使用 API；
- iOS 在 App 内播放；
- Android 用户登录后直接在第三方 App 内播放，并支持后台与锁屏。

[Apple Music API](https://developer.apple.com/documentation/applemusicapi/)提供正式歌曲 ID、歌名、艺人、专辑、封面、时长、ISRC、分享 URL 与可播放参数。聊天消息因此可以保存正式 catalog 引用，而不是临时 CDN URL。

推荐的消息模型：

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
  "canonicalUrl": "..."
}
```

Music User Token、开发者私钥、HLS 地址、DRM 信息、缓存文件和音频文件不得进入聊天消息或模型上下文。

### 3.2 中文曲库证据

Apple 的官方资料足以证明它不是 Audius 那种少量中文独立音乐目录：

- [Apple Music 中国大陆产品页](https://www.apple.com.cn/apple-music/)称中国大陆服务包含数千万首歌曲，支持 iOS、Android 和其他设备；
- [Apple Music 国语流行](https://music.apple.com/cn/curator/apple-music-%E5%9B%BD%E8%AF%AD%E6%B5%81%E8%A1%8C/1019400042)包含新歌、网络热播、国语经典、年代歌单和主流华语艺人；
- [Apple Music C-Pop](https://music.apple.com/cn/curator/apple-music-c-pop/1479949880)提供独立的华语内容分类；
- [每周热门 100 首：中国大陆](https://music.apple.com/cn/playlist/%E6%AF%8F%E5%91%A8%E7%83%AD%E9%97%A8-100-%E9%A6%96-%E4%B8%AD%E5%9B%BD%E5%A4%A7%E9%99%86/pl.939cf56e73c44970b81fd9648f859223)当前包含周杰伦、孙燕姿、王力宏、方大同、梁静茹、陶喆、陈奕迅、林俊杰等；
- [Apple Music 中国区排行榜](https://music.apple.com/cn/new/top-charts)还有北京、上海、成都、广州、武汉等城市榜；
- [Apple 媒体服务可用性](https://support.apple.com/zh-cn/118205)明确列出中国大陆 Apple Music。

这些证据可以证明“方向正确”，但不能证明它与 QQ 音乐或网易云逐首相同。最终选型必须对 Murmur 真实用户的目标歌单做命中率测试。

### 3.3 三个硬边界

[App Store Review Guidelines 4.5.2](https://developer.apple.com/app-store/review/guidelines/)对 MusicKit 有明确限制：

1. **用户必须发起播放。**最稳妥的交互是 Murmur 发歌曲卡，用户点击播放；不能在 AI 消息生成完毕后无操作自动开播。
2. **不能变现 Apple Music 访问。**连接、播放和基本控制不能锁在 Murmur Pro 后，也不能用广告或索取额外用户资料间接变现。
3. **不能分享音乐文件。**聊天可以分享 catalog 引用和与播放相关的元数据，不能传输、下载、转码或导出 Apple Music 音频。

Apple 还指出，“在特定时刻播放某首特定歌曲”等深度同步可能需要权利人额外许可。因此第一版应当是“AI 推荐并发卡，用户点击播放”，而不是让 Murmur 在剧情节点强制开播或把歌曲混入可分享的音视频作品。

### 3.4 登录数据的准确含义

MusicKit 授权能给 Murmur：

- Music User Token；
- storefront；
- 订阅和可播放能力；
- 经用户同意的资料库、收藏、歌单、最近播放与推荐。

它不应被理解为能读取 Apple Music 用户姓名、邮箱和头像的社交登录。Murmur 主账号继续使用自己的账号体系；“连接 Apple Music”是独立的音乐权限。

### 3.5 双向发歌流程

Murmur 发歌：

1. 用户说“给我放一首适合下雨天的周杰伦”；
2. 模型只生成查询意图；
3. 客户端或受控服务调用 Apple catalog search；
4. 确定性代码生成正式歌曲卡；
5. 用户点击卡片；
6. MusicKit 重新检查订阅、storefront 与可播放状态，然后在 Murmur 内播放。

用户发歌：

1. 用户从 Apple Music catalog 或获授权资料库选择歌曲；
2. library song 先转换成 catalog 关联；
3. 聊天发送 catalog ID、storefront、ISRC 和必要元数据；
4. 接收端按自己的 storefront 重新解析后播放。

个人导入且没有 Apple catalog 对应项的歌曲不能跨用户播放，只能作为不可播放的文字引用。

## 4. 第二选择：QQ 音乐 / TME 企业合作

从中文曲库和大陆用户已有账号看，QQ 音乐可能比 Apple Music 更匹配。腾讯音乐[官方公司资料](https://www.tencentmusic.com/zh-cn/about-us.html)称其拥有 QQ 音乐、酷狗、酷我和全民 K 歌，并拥有海量的中国音乐内容曲库。

[QQ 音乐开放平台](https://y.qq.com/music_developer/)展示的能力范围包含移动应用、网站/小程序与社交场景，能力形状涉及登录授权、OpenAPI、音乐流媒体、个人资产和播放控制。腾讯云面向合作设备的[QQ 音乐服务文档](https://cloud.tencent.com/document/product/1081/67456)也能证明正式合作体系中存在登录授权、会员权益、歌曲搜索、个人歌单、最近播放和音乐播放能力。

但这些材料不能自动证明 Murmur 已获得普通移动 App 的全部权限。进入 PoC 前必须由 TME 书面确认：

1. Murmur 能否使用用户现有 QQ 音乐账号授权；
2. scope 是否包含红心、收藏、私人歌单、最近播放和推荐；
3. 是否交付当前 iOS 与 Android SDK，而不是只支持车机、IoT、Android 或 HarmonyOS；
4. 是否允许在 Murmur 聊天中保存正式曲目 ID 和最小 metadata；
5. 是否允许 AI 生成搜索条件、推荐理由与歌曲卡；
6. 用户点击歌曲卡后能否在 Murmur 内完整播放；
7. 会员权益、DRM、缓存、地区、下架、版税和播放事件如何处理；
8. Murmur 订阅模式是否构成对音乐能力的商业化；
9. 能否提供 App Store/Android 商店所需的内容授权证明与审核账号；
10. token、用户音乐资产与删除/撤销的合同和隐私要求。

只有以上十项获得明确答案，QQ 音乐才从“商务候选”变成“条件 Go”。在此之前不能使用 Cookie、网页内部接口、逆向 API 或第三方播放地址代替正式授权。

## 5. 第三选择：KKBOX Partner/B2B

KKBOX 适合台湾、香港及海外华语用户，但必须区分两种产品：

- [KKBOX Open API Swift](https://github.com/KKBOX/OpenAPI-Swift)、[Android](https://github.com/KKBOX/OpenAPI-Android)和 [JavaScript](https://github.com/KKBOX/OpenAPI-JavaScript) SDK 主要用于调用公开 API，适合搜索、曲目元数据和歌曲卡；它们不是完整 DRM 播放器。
- KKCompany 企业级方案的官方材料包含会员、曲库、播放器、DRM、支付、AI 推荐、版税报告和版权协商，具备形成完整产品的组件，但需要 Partner 合同。

因此 KKBOX 只有在对方提供以下材料后才进入 PoC：

- 现有 KKBOX 账号 OAuth；
- 用户收藏、私人歌单和历史 scope；
- iOS/Android 完整播放 SDK 或 DRM 集成文档；
- 后台、锁屏与订阅资格处理；
- AI 聊天、推荐、metadata 保存与 Murmur 商业模式的书面许可；
- 授权区域、费率、最低承诺与版税上报规则。

若只能提供 Open API、Widget、30 秒试听或跳转 KKBOX App，就不满足 Murmur 的要求。

## 6. 为什么酷狗、网易云和网页嵌套还不够

### 6.1 酷狗

[酷狗曲库开放计划](https://www.kugou.com/musiclibrary_openplan/?from=listenweb)明确提供千万级正版曲库、iOS/Android 无界面在线播放 SDK，并按调用次数收费。这证明它能解决“中文歌 + App 内播放”。

但公开页面没有证明它能同时完成用户已有酷狗账号授权、私人收藏、歌单和历史读取。因此它适合做“由 Murmur 搜歌并播放的授权曲库”，不一定能满足“用户带着原账号资料库登录”。若产品愿意放弃用户酷狗账号资产，酷狗可作为第四条商务询价线。

### 6.2 网易云音乐

网易云厂商平台可能具备完整能力形状，但个人开发者公开入口不能提供 Murmur 所需的移动 OAuth、用户资料库 scope 和双端完整播放 SDK。[官方个人开发者 FAQ](https://developer.music.163.com/st/developer/document?docId=3b75ab8e475d41ca93d91ebd4dfd383f)与[厂商接入说明](https://developer.music.163.com/st/developer/document?docId=4d1bd372f8b444ddb6bbde7197bea19f)应分开理解。

因此网易云仍是“签约后可能可行”，不是可以立即实现的公开方案。

### 6.3 网页嵌套

网页 Widget 或 WebView 可以显示内容，但不能稳定提供：

- 正式移动账号 token 生命周期；
- 用户资料库 scope；
- 原生后台和锁屏控制；
- 完整播放状态与错误回调；
- 可承诺的 DRM、会员、地区和下架处理；
- App Store 所需的第三方流媒体授权证明。

所以网页嵌套只能作为展示或跳转降级，不能承担“陪你听”的核心播放器。

## 7. 推荐的执行顺序

### Stage 0：两周 Apple Music PoC

准备 300–500 首中文目标样本，至少包括：

- 100 首最近两年大陆热门歌；
- 100 首 2000–2020 年国语/粤语经典；
- 50 首独立、小众和网络音乐；
- 50 首真实用户当前常听或红心歌曲；
- 必要时加入现场版、OST、游戏音乐和翻唱版本。

PoC 验收：

1. 中国大陆 Apple ID 在 iPhone 完成 MusicKit 授权；
2. 样本搜索命中、正确版本和 `playParams` 可播放率达到产品阈值；
3. 用户与 Murmur 都能发送相同的结构化歌曲卡；
4. 用户点击后在 Murmur 内整曲播放；
5. 后台、锁屏、蓝牙、耳机、来电中断与恢复通过；
6. Android 在中国常见设备和分发环境完成登录、播放与后台控制；
7. 非会员、会员过期、曲目下架、跨 storefront 与私人导入歌曲有明确降级；
8. Apple Music 基本能力不受 Murmur 付费墙限制。

### Stage 1：并行商务询证

- 向 TME/QQ 音乐提交中文产品说明和上述十项问题；
- 若目标含台湾、香港和海外华语用户，同时联系 KKCompany Partner；
- 若愿意放弃“读取原音乐账号资产”，询价酷狗曲库播放 SDK。

### Stage 2：选型闸门

- **Apple 中文样本达标：**直接以 Apple MusicKit 作为第一期 provider；架构保留多 provider 接口。
- **Apple 样本不足，但 TME 给出完整双端与 AI 许可：**以 QQ 音乐作为大陆 provider，Apple Music 作为海外/Apple 用户 provider。
- **两者都未满足：**只上线外部分享链接卡片，不承诺 Murmur 内完整播放；不要转向逆向接口。

## 8. 对产品文案的约束

可以说：

- “Murmur 给你推荐了一首歌”；
- “点击在 Murmur 中播放”；
- “连接你的 Apple Music”；
- “把这首歌发给 Murmur”。

首版不要说：

- “Murmur 会在任何时候自动给你放歌”；
- “支持所有中文歌曲”；
- “同步你的全部音乐人格”；
- “不需要音乐会员也能播放会员歌曲”；
- “可以下载或把完整歌曲发进聊天”。

## 9. 决策

**当前推荐：选择 Apple MusicKit 进入两周 PoC。**

理由不是它一定拥有最多中文歌曲，而是它是当前唯一同时满足以下条件且可由 Murmur 自行开始验证的平台：

- 官方用户授权；
- 用户资料库与歌单；
- 正式 catalog 与稳定歌曲 ID；
- 双向结构化歌曲卡；
- iOS/Android App 内完整播放；
- 中国大陆服务和明确的华语内容证据；
- 可公开取得的开发文档和审核路径。

如果产品要求中文覆盖尽可能接近 QQ 音乐/网易云，则最终形态很可能不是单平台，而是：

> **Apple MusicKit 作为可立即落地的正式 provider，QQ 音乐/TME 作为拿到商务许可后的中国大陆 provider，KKBOX 作为港澳台或海外华语的可选 Partner provider。**

这套多 provider 设计比继续押注任何非官方 API 更可控，也更符合 Murmur 的账号、聊天和数据生命周期边界。

## 10. 配套调研

- [Apple Music / MusicKit 中文曲库适配性核验](apple-music-chinese-catalog-fit-2026-08-31.md)
- [亚洲华语音乐平台接入调研](asian-chinese-music-platform-options-2026-08-31.md)
- [全球音乐平台完整双向会话集成对比](music-platform-integration-comparison-2026-08-30.md)
- [网易云音乐非官方播放与网页嵌入可行性](netease-unofficial-playback-and-web-embed-feasibility-2026-08-30.md)
