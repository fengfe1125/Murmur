# Murmur 后续开发意见与功能提案

> 调研来源（2026-08-20 预览）：
> [sanjaynela/liquid-glass-ios-system](https://github.com/sanjaynela/liquid-glass-ios-system)、
> [amosgyamfi/open-swiftui-animations](https://github.com/amosgyamfi/open-swiftui-animations)、
> [Dimillian/IceCubesApp](https://github.com/Dimillian/IceCubesApp)、
> [GetStream/purposeful-ios-animations](https://github.com/GetStream/purposeful-ios-animations)、
> 同赛道产品 [念念](https://nn.xclsf.top/)（v0.1.0，前端 JS 与 API 路由已逐项分析）。

## 参考速览

| 参考 | 是什么 | 对 Murmur 的价值 |
|---|---|---|
| liquid-glass-ios-system | 纯 SwiftUI 玻璃设计系统复刻（GlassKit） | 借工程手法：集中风格参数、单修饰符分层、降级第一公民；视觉上恰是 Murmur 的反面教材 |
| open-swiftui-animations | 75 个 SwiftUI 动效示例（iOS 17+ 现代 API） | 借状态机写法（PhaseAnimator 等）；反面：审美装饰化、全库 0 处 Reduce Motion |
| IceCubesApp | 4 万行 / 13 包的成熟 SwiftUI App | 借轻量设计哲学：@Observable 注入、Endpoint 枚举、Theme 收敛——按 Murmur 体量砍到只剩骨架 |
| purposeful-ios-animations | GetStream 动效原则合集 | 借"动效为谁服务"的原则与 ReduceMotionSpring 金标准 |
| 念念 nn.xclsf.top | AI 记忆日记：星图 / 手卷 / 日记改稿 / 搜索 / 语音 | 同赛道的产品功能地图：哪些 Murmur 缺、哪些可以克制地学 |

---

# 一、10 个开发意见

## 1. 动效参数收口成 `MurmurMotion` 规格层

现状：`spring(response:0.32, dampingFraction:0.86/0.78/0.82)`、`easeInOut 0.18/0.22/0.26`、
`easeOut 0.12` 散落在 `MurmurChatView.swift`、`MurmurTranscriptView.swift` 各处。
open-swiftui-animations 的教训：75 个文件没有一个 `accessibilityReduceMotion`；
liquid-glass 的 README 宣称尊重 reduceMotion，实际只放了一个没接线的演示开关。
purposeful-ios 的金标准是 `ReduceMotionSpring.swift`：系统环境 + 三元切换。

做法：新建 `MurmurApp/MurmurMotion.swift`——

- 命名 token：`MurmurMotion.reveal`（回复浮现，0.15s easeOut，位移 0–6pt）、
  `MurmurMotion.press`（spring 0.15 / damping 0.9）、`MurmurMotion.phaseChange`
  （0.3s easeInOut）、`MurmurMotion.reduced`（≤150ms、纯 opacity）。
- 一个 `.murmurMotion(\.reveal)` 扩展，内部读 `accessibilityReduceMotion` 自动坍缩。
- Android 侧同步一张参数表（Compose `spring(dampingRatio, stiffness)` ↔ SwiftUI
  `spring(response, dampingFraction)` 等价换算）写进 `docs/android-adaptation-plan.md`，
  兑现"同产品、同约束"。

收益：未来动效只引用 token 不许散写，Reduce Motion 永远默认正确。

## 2. 动效改成状态机：让 `MurmurPhase` 直接驱动动画

open-swiftui-animations 最值得带走的是现代 API 的干净写法——用 `PhaseAnimator` /
`KeyframeAnimator` 把动画定义成状态枚举，而不是 `withAnimation` + 魔法数字。
Murmur 已有现成状态机 `MurmurPhase`（idle/preparingPhoto/ready/uploading/responding/
quiet/complete/error）。

做法：每个 phase 配一张动画定义表（进入/驻留/离开），`.animation(value: model.phase)`
驱动：

- `uploading`：草稿照片 opacity 1→0.4 + scale 0.96，easeInOut 0.28s（"一笔轻轻翻过"）；
- `responding`：现有三点 TypingIndicator 的 Timer 换成 `phaseAnimator([false,true])` +
  easeInOut 0.6 autoreverses 呼吸；
- `quiet`：收束到纯文字，easeInOut 0.4，不残留任何循环动画；
- `complete`：最后一条气泡落地即止，零庆祝。

收益："动效只表达阶段变化"从口头规范变成类型约束，每个状态可单测。

## 3. 回复到达做逐字级联浮现（克制参数版）

purposeful-ios #4 与 open-swiftui #6 的共识：让"戳心的话"被读出，而不是整块砸下。
Murmur 已有 `MurmurBubblePacing` 管气泡间节奏（0.075s/字），缺气泡内的逐字节奏。

做法：每条 say 气泡内文字逐字 opacity 浮现——easeOut 0.18s + 每字 25ms stagger，
总时长封顶 0.6s；用状态计数（`phaseAnimator`）驱动，不要 60 个 Timer；末端细光标
（墨色 2pt，opacity 呼吸）只在浮现期间存在。

参数纪律（两个动效库的共同反面教训）：damping 拉到 15–20 不反弹、不循环、
不用 hueRotation、blur ≤ 4、一次到位；Reduce Motion 下整段单次淡入；
XXXL 动态字体下按字数决定是否自动降级为整段淡入。

## 4. `EditorialSurface`：表面样式收口成一个修饰符

liquid-glass 的 `glassSurface()` 四层结构 + `GlassStyle` EnvironmentKey 是纯工程手法，
与玻璃无关：底衬 → 1pt 描边 → 受控高光 → `compositingGroup().shadow()`。
Murmur 现在浮盘、输入框 pill、气泡、设置 sheet 各自散写 background + stroke + shadow，
`shadow(color: ink.opacity(0.08), radius: 6, y: 2)` 同款值至少出现三处。

做法：建 `MurmurTheme.editorialSurface(cornerRadius:raised:interactive:)` 修饰符——
raisedPaper 底衬 + rule 1pt 描边 + 统一软影（ink 8%，y:2）+ compositingGroup；
强度参数（描边/阴影透明度）走 EnvironmentKey 可局部覆盖。所有"纸面"一个入口，
调一次全 App 生效，"accent ≤ 5%"这类约束也变成可审计的代码而非文字。

## 5. 系统降级是第一公民：Reduce Transparency / 深色 / 高对比三线审查

liquid-glass 唯一值得抄的写法是 GlassSurface 读 `accessibilityReduceTransparency`
换实色底衬；IceCubes 的 `ThemeApplier` 统一 `.tint()` / `.preferredColorScheme()`。
Murmur 的 iOS 26 glass 浮盘、raisedPaper、顶部掩码渐变在"降低透明度 / 增强对比"下
都需要一条实色路径。

做法：

- 审计所有 `glassEffect` / material / opacity 用法，补 Reduce Transparency 实色替代；
- `MurmurTheme` 已是 trait 双色，按 IceCubes 模式升级为 `@Observable Theme` +
  `applyTheme` 修饰符，为"墨水模式 / OLED 纯黑"留缝，收敛散落的 UIColor 构造；
- 深色对比度（outgoingBubble 的 4.5:1 注释说明你们在意）写成 UI 测试矩阵：
  深色 × Reduce Transparency × 增强对比。

注意：liquid-glass 自己在 reduceMotion 上是假的（演示开关），不要学。

## 6. 触觉收口成语义表

IceCubes 用 HapticManager 单例集中触发点；purposeful-ios 给了语义：
`.selection`（选图）/ `.impact(.medium)`（发送）/ `.success`（送达）/ `.error`（失败）。

做法：建 `MurmurHaptics` 一个文件——`select() / send() / delivered() / failed()`，
iOS 17+ 走 `.sensoryFeedback(...)`，旧版本 fallback `UIImpactFeedbackGenerator`；
每个调用点语义命名。触觉与视觉同帧触发（发送瞬间 = impact + 照片吸入动画）；
同语义 500ms 内限频，防止连发时触觉轰炸。

## 7. 工程组织：轻量分层 + Endpoint 枚举 + 类型化环境注入

IceCubes 的核心教训一句话：别学它的 13 包，学它的轻量哲学。Murmur 现在 13 个文件
平铺，`MurmurAPI.swift` 已 621 行，`MurmurChatView.swift` 已 1354 行（Theme + 7 个
View 挤在一个文件）。

做法（不拆包、不引框架、不引 TCA）：

- 目录分层：`Models/ Network/ Services/ Theme/ Features/ App/`；
- `MurmurAPI` 按 Endpoint 协议 + enum 收敛：`MomentAPI.create / events / proactive /
  preferences`，客户端一个泛型 `request(endpoint:)`；
- 注入：@Observable 服务由 `@main` 的 `@State` 持有，`.environment()` 类型注入，
  聚合成一个 `.withAppDependencies()` 修饰符；
- 副作用：零散 `onAppear` 换成 `.task(id:)`（加载 moment / 握手 / checkProactive）；
- 抄 IceCubes 的 AGENTS.md 思路：写一个简短 `AGENTS.md` 锁架构约定
  （"新视图不许写 ViewModel、颜色只许走 MurmurTheme"）——design.md 的工程版。

## 8. 性能预算与动效禁区清单

两个动效库共同的工程短板：liquid-glass 的 MorphingBlob 两个 420×520 blob 逐帧
`TimelineView(.animation)` + blur 38–52 + plusLighter，常驻 GPU 负担；open-swiftui
通篇 `repeatForever` 且无任何性能手段。把反面教材写成禁区：

- 禁：repeatForever 常驻、`TimelineView(.animation)` 逐帧重绘、blur > 8、
  大面积离屏渲染、每帧重算 Path；
- 允许：phaseAnimator 低频状态推进、opacity / 微小 scale、一次到位；
- 逐字浮现用状态计数驱动，不用延迟 Timer 堆；上传进度环随真实 Progress 更新。

把清单写进 `design.md` 的 Motion 一节；DEBUG 下给关键路径（键盘弹出、气泡落地）
加帧时间断言（>30ms 打日志）。

## 9. 无障碍与动效的自动化测试进 CI

Murmur 的测试文化是亮点（自包含脚本 + UI 测试覆盖 XXXL/横屏/深色）。补三件事：

- Reduce Motion UI 测试：启动参数打开 reduceMotion，断言气泡入场零位移、语义仍可达；
- VoiceOver 朗读节奏：1–3 条气泡提供"整组朗读 / 逐条朗读"选项，typing indicator
  朗读时 hidden，`MurmurBubblePacing` 在 VoiceOver / Reduce Motion 下自动 instant
  （现在 instant 只给测试用，需接入环境判断）；
- 动效参数单测：`MurmurMotion.reduced` 必须 ≤150ms 且 opacity-only（直接对齐 design.md 数字）。

## 10. 设置与偏好工程：Form + Section + @Bindable + 推送配额可视化

IceCubes 的 SettingsTab：`Form + Section` 由 `@ViewBuilder` 分节拼、偏好集中
`@Observable UserPreferences`（内部 `@AppStorage`）、视图 `@Bindable` 直绑不写
ViewModel、权限被拒跳 `UIApplication.openSettingsURLString`、推送开关单独一 View
并真实调用订阅接口。Murmur 的 `MurmurSettingsView` 已是 Form + Section，差距在：

- preferences 散在 `MurmurSessionModel`（dailyFrequency / quietStart / quietEnd
  手动 Binding 转换）→ 抽成 `@Observable MurmurPreferencesService`，`@Bindable` 直绑，
  时间格式转换收进服务；
- "连接推送"整链按钮已经做得很好，再补一行**今日已推 / 剩余配额**（如"今天它主动
  开口 1/3"），让"每天最多 3 条"从抽象数字变成可见状态；
- 相机、相册权限被拒时与通知一样提供"前往系统设置"入口。

---

# 二、10 个新增功能提案

## 1. 游客体验模式：先发一张，再谈邀请

念念在登录墙后留了"游客只能体验一张照片"的钩子（配图形验证码防刷）。Murmur 更该有：
产品一次就一个 moment，天生适合"不注册先体验一张"。新用户打开 App → 无邀请码也可
发一张照片 → 收到它那一句 → 之后才出现邀请墙。

- 防刷：按设备匿名指纹限额（现有 App Attest / 指纹基建），配验证码或限速；
- 冷启动传播的决定性功能：让"发一张图，它回一句戳中的话"在 30 秒内被亲身体验。

## 2. 语音备注：按住说话

念念有 `/api/asr` 和"切换为语音输入 / 打字输入"。Murmur 的场景是边走边拍：打字是
摩擦，说话是自然。

- 优先本地 Speech framework 转写（音频不出设备，只传文本——与"原图不上传"哲学一致）；
- 网络差时退回录音直传 + 服务端便宜模型转写；
- 交互：输入框加一个"按住说话"mic disc，松开入草稿可编辑，草稿模型不变。

## 3. 多图连发合并（完成 README 里"做了一半"的那条）

念念的 `/api/image-uploads/{authorize,heic,promote,complete}` 是完整多图管道；
Murmur README 已承认"连发的多张图只把最新那张给模型"。

- 客户端 `PhotosPicker` 多选（上限 3–4 张），草稿排小队列；
- 服务端 job 把同一静默期的几张图一起进上下文，压缩分档逐张套用；
- 模型一次接住（"你连发三张，第三张是不是……"才是朋友的接法）；
- 成本注意：多图 = 多份 token，与提案 4 的输入侧分级配套。

## 4. 输入侧成本分级（完成 README 里剩下的那半）

输出侧已分级（记忆整理走便宜模型、图片分档、降级链）。补输入侧：先用便宜模型
（MURMUR_IMAGE_MODEL 级别）对图+文字打"值不值得深聊"的分，值得才上主模型出文案，
不值得直接 brief/quiet 短回应。念念的 `/api/config/beta-group` 提示他们也在做
功能/模型分级。预期：随手拍 / 无信息量图省下大部分主模型调用。

## 5. 「留一页」：scene 存档的可选留存与手动改稿

念念最有产品力的细节是"改稿 · 手动修订这页日记"——AI 写的日记允许人改。Murmur 的
scene 存档（客观画面描述）目前用户不可见。

- 一次 moment 完成后，极安静地出现"留一页?"小动作（一行字，不弹窗不打扰）；
- 留存后成为一页：照片预览 + 它的回复 + scene 改写的一两行；可手动修订正文/标题；
- 纪律：默认不提示、不自动留存、主界面永不出现"过去"，只存在于"翻页"入口（提案 6）。

## 6. 翻页回看：手卷式的一次一页

念念的核心体验是星图 + 手卷（"滚轮/拖动翻阅 · 悬停端详 · 点击展开手卷"）。Murmur
不做列表式历史，但可以做"翻页"：独立、主动进入的视图，一次只呈现一页（某一天的
留存页），上下滑动翻页，无时间轴无列表；进入时默认停在随机一页或"今天"。

- 参考念念手卷交互（按住端详、抬手展开），但用 Murmur 的纸张语言：纸白、serif、无玻璃；
- 纪律：主界面零入口残留，只在设置里一个安静的"翻翻看"。

## 7. 隐私星图：只有光点，没有地名

念念的星图是它的标志，但展示的是回忆卡片。Murmur 有更好的原料：匿名地点指纹
（110m 格子，只数次数，坐标不出网、地名从不生成）。

- 本机渲染一张星图：每个"常去格子"一个橄榄色光点，大小 = 去过次数；
- 点开只显示"这里来过 N 次"，无地图、无坐标、无地名；
- 服务端只下发匿名计数（现状即可），渲染完全本机——比念念的星图更隐私。

## 8. 每日开场白：从"等它找你"到"它也会开口"

念念有 `/api/guest/opening` 和"今天过得怎么样"的开场问题；Murmur 的 proactive
scheduler（30s 循环、意图字段、每天最多 3 条、08:30–22:30）已经就位，只差开场意图。

- 固定开场：早 8:30 让 persona.py 的"主动开口"一节自己挑开场词（已有"看时段"约束）；
- 记忆触发：同一匿名地点第 N 次出现 → "又是这个点"；dossier 里的事到了日子 → 轻轻问一句；
- 天气 / 节气本地感知（只用日期季节，不说地名）；
- 全部遵守"一条就够，最多两条，一半的时候只是说一句自己的事"。

## 9. 问它一件事：记忆问答入口

念念有"搜索记忆 · 对话 · 日记"全文搜索。Murmur 的记忆在服务端（dossiers/*.md +
SQLite），用户自己反而看不见。

- 输入框以问句开头（"我上周是不是说过要搬家?"）→ 走记忆检索路径（本地 SQLite +
  dossier 检索，不调主模型或走 MURMUR_MEMORY_MODEL）→ 回答"记得"并引用片段；
- 与聊天严格区分：显式检索，不是人格闲聊；回复样式区分（如橄榄色引用块）；
- 这是"私有记忆留在服务端"承诺的正面兑现：记忆是为你服务的，你可以问它。

## 10. 邀请朋友与公测分发

念念有完整账户体系 + 公测群二维码 + beta-group；Murmur 是邀请制，服务端已有
`app-invite` CLI，缺产品化：

- 设置页"邀请朋友"→ 自助生成一次性邀请码（App API 暴露受限 invite 接口，
  每用户每月配额，明文码只在生成瞬间显示——与 VPS 面板现有逻辑一致）；
- TestFlight 公测是当前 Apple 账号阻塞项，排进发布计划；
- 邀请卡文案用产品自己的声音（"它最近挺想认识你"），不走"拉新奖励"（与设计语言冲突）。

### 候补池

- 字幕模式（念念：点按隐去文字，只看照片 + 大字 serif 回复）；
- 读出它的回复（TTS，AVSpeechSynthesizer 中文，配合逐字浮现）；
- 氛围调节（念念 → Murmur"人格旋钮"：话多话少 / 主动频率 / 语气浓度，persona 参数化）；
- 连续陪伴状态行（极安静地显示"第 N 天"，纯状态行，无打卡压力）；
- 锁屏 Widget / 灵动岛（显示"它刚说的一句"）。

---

# 优先级建议

1. **先做**：开发意见 1–3（动效债一次清完）；功能提案 1（游客模式）+ 3/4（README 已列）。
2. **再做**：开发意见 4/5/7（表面收口 / 降级 / 分层）；功能提案 8/9（开场白 / 记忆问答）。
3. **后做**：开发意见 10；功能提案 5/6/7（留存 / 翻页 / 星图——三者互相依赖，建议一起规划）。

调研素材：仓库浅克隆在 `/tmp/liquid-glass`、`/tmp/open-swiftui-animations`、
`/tmp/icecubes`、`/tmp/purposeful-ios`；念念前端 chunk 与 API 路由提取在 `/tmp/nn_chunks`。

---

# 三、10 个功能提案（第二轮 · 2026-08-20 深读后重写）

第一轮（上面第二节）是把念念的功能地图对着 Murmur 抄了一遍。这一轮重新读了四个 iOS
仓库的**工程结构**（不只是 README）和念念前端的全部中文串（313 条），并逐条对着
Murmur 现有代码验证，结论有三处和第一轮/DeepSeek 的说法不一致，先更正：

**更正 1：「每日开场白」基本已经做完了，不该占一个提案位。**
`murmur/app_push.py:273` 的 `ProactiveScheduler` 已经在调 `initiative.pick_intent()`，
按最近 8 条已发意图去重轮换，`initiative.py` 里 8 个意图第一条就是「时段应景」。
缺的只是**意图表里加几条**（开场、匿名地点第 N 次、dossier 里的日子）和 persona 的
几行提示词——那是调参，不是新功能。

**更正 2：「翻页回看」已经被真实代码超过了。**
`MurmurApp/MurmurTranscript.swift` 有 `MurmurTranscriptStore`（本机 600 条、照片存本机），
`MurmurTranscriptView.swift:364` 有 `DaySeparator`。App 早就不是"只显示当前 moment"了
（README 那句话已过期）。手卷式"一次一页"再做一遍是重复建设；真正缺的是**端详**
（提案 8），不是列表。

**更正 3：念念的「氛围调节」不是人格旋钮。**
展开看，下面挂的是 `明亮度 / 星星大小 / 暗角范围 / 散开幅度 / 松散程度 / 扬起高度 /
摆正速度 / 涟漪速度 / 立体感`——是星图的视觉参数实验室。真正的人格旋钮叫
`响应强度`，只有一个。把"人格参数化"当成念念的既有功能来抄，是抄错了对象。

**另外，念念自己也承认「移动端小程序开发中~」——它没有原生 App。**
Murmur 最大的、且正在浪费的优势，就是它是一个真的 iOS App：系统分享、快捷指令、
可回复通知、Widget、本机语音识别，这五样念念一样都做不了。第一轮完全没利用这一点，
所以这一轮前三个提案都在这里。

## 1. 不打开 App 也能发一张：分享扩展 + 快捷指令

**证据。** IceCubes 主 App 之外挂了五个扩展 target：`IceCubesShareExtension`、
`IceCubesActionExtension`、`IceCubesAppIntents`、`IceCubesAppWidgetsExtension`、
`IceCubesNotifications`。其中 `AppShortcuts.swift` 注册了 5 个 `AppShortcut`，
`InlinePostImageIntent` 是**后台发图、完全不打开 App**。
Murmur 的 `MurmurApp.xcodeproj` 只有三个 target：`Murmur / MurmurTests / MurmurUITests`，
一个扩展都没有。

**做什么。** 把"发一张给它"变成系统动作，而不是一个必须先打开的 App：

- **分享扩展**：相册里选中一张 → 分享 → Murmur。极小的一张纸片界面（照片缩略图 +
  一行可选备注 + 送出），送出即关闭，回复走推送。
- **App Intents**：`SendMomentIntent(image:note:)`，后台执行。挂到快捷指令、
  Siri（"发给它"）、**操作按钮**（Action Button 一按拍一张直接发）和控制中心控件。

**落地点。** 新增两个 target + App Group。难点只有一个：`MurmurSecurity.swift` 的
App Attest assertion 要在扩展进程里能签——建议扩展**不直接调 API**，只把照片和备注
落进 App Group 容器的草稿队列，由主 App 的 `BGProcessingTask` 或下次前台启动补发
（正好复用提案 7 的发件箱）。这样密钥材料不出主 App，扩展权限最小。

**纪律。** 扩展界面里不出现它的回复——一句戳中的话不该在分享面板里闪一下就没了。
送出后只有一行"它收到了"。

**代价。** 中。两个 target、一次 App Group 迁移、一轮 Attest 边界评审
（`docs/` 里应同步 murmur-privacy 的判断）。

## 2. 通知就是对话面：可回复、带图、能被"专注模式"放行

**证据。** 现在的推送是 `murmur/app_push.py:177`：
`{"aps": {"alert": {"body": text}, "sound": "default"}}`，`apns-push-type: alert`，
只加了 `apns-collapse-id`。没有 `mutable-content`、没有 `category`、没有 `thread-id`、
没有 `interruption-level`。iOS 侧 `MurmurApp.swift:167` 有 `UNUserNotificationCenterDelegate`，
但**没有注册任何 `UNNotificationCategory`**。
IceCubes 为此专门做了一个 `IceCubesNotifications` NSE，并在里面 donate `INSendMessageIntent`。

**做什么。** 它主动开口是这个产品的一半，而这一半现在长得像验证码短信。

- **可回复通知**：注册一个带 `UNTextInputNotificationAction` 的 category，
  锁屏上直接回一句就走 `/v1/moments`（note-only）。它说一句、你回一句，全程不进 App
  ——这才是"一个会主动找你的朋友"该有的体积。
- **通信通知**（NSE + `INSendMessageIntent`）：让它的推送带头像、成组、
  可以被用户加进**专注模式的允许列表**。对一个"每天最多 3 条"的产品，
  能被放行比能被发出更重要。
- **富推送**：`mutable-content: 1` + NSE，把 `app_moments.preview_path` 的缩略图
  贴进通知（**仅它主动开口时**，附上它"看到"的那张——目前 proactive 无图，
  这条留给未来）。`thread-id` 按用户，`interruption-level: passive` 给 brief。

**落地点。** `murmur/app_push.py` payload 加字段；新 `MurmurNotifications` NSE target；
`MurmurApp.swift` 注册 category 与 `didReceive` 分支。

**纪律。** 回复框的占位符不写"回复"，写它自己的话；`quiet` 永远不推送
（现在就是这样，别破）。

**代价。** 中低。服务端改动很小，收益极大。

## 3. 一按"戳中了"：一次点击的人格调优信号

**证据。** purposeful-ios 的 `AugmentFeeling` 一节（Twitter Like / Messenger Reactions）
的论点是：**让人表达感受，成本要低到一次点击**。
Murmur 的 README 自己写着"`entries.reply` 里存着你每次的回应，那是最好的调优依据"
——但拿到这个依据的前提是用户愿意打字。绝大多数时候他不会。

**做什么。** 长按它的气泡 → 两个极安静的选项：**「戳中了」/「不像它」**。
一次点击，无动画庆祝，气泡角上留一个 2pt 的橄榄色小点（"不像它"是灰点）。

- 写进 `entries` 的一个新列（或 `app_moments.feedback`），与 `reply` 同级；
- 进 dossier 的"语气偏好"分区：连续三次"不像它"就在下一轮上下文里附一句
  "他最近觉得你说话不太对味，收着点"；
- 看板（`murmur/web.py`）每人一行"戳中率"——这是 persona.py 唯一可量化的指标。

**落地点。** `MurmurTranscriptView.swift` 的 `MessageRow` 加 contextMenu；
`/v1/moments/{id}/feedback`（PATCH，幂等）；`murmur/dossier.py` 加一句摘要规则。

**纪律。** 不做表情包、不做多档评分、不做"再生成一次"。两个选项，因为第三个选项
会让人开始思考——而这个产品的全部功夫都在让人不思考。

**代价。** 低。一天的活，长期回报最高的一条。

## 4. 记忆的三件事：告诉它 / 问它 / 按住让它忘掉

**证据。** 念念有 `/api/long-term-memories` 和一整套用户直写记忆的界面：
"告诉小念一件事"、"你亲手写下的 · 不来自某次聊天"、"它只会记住你亲口说的，
不会替你猜测"、"一件事最多 300 个字"；删除是**按住不放**：
"按住 · 让它忘掉" → "正在忘掉…"；容量满了它说"忘掉一些，才能记住新的"。
Murmur 这边：记忆在服务端（`dossiers/*.md` 三分区 + SQLite），用户看不见、
改不了、删不掉——只有 `DELETE /v1/account`（全删）。

**做什么。** 第一轮只提了"问它"（检索问答），那是三件事里最不重要的一件。
真正该做的是三件一起：

- **告诉它**：设置页/输入框长按 → "让它记住一件事"（≤200 字），直接写进 dossier
  的"他说的事实"分区，标记 `source: 亲口`——**不经过模型改写**。
- **问它**：以问句开头走检索路径（SQLite + dossier，`MURMUR_MEMORY_MODEL`，
  不上主模型），回答并引用片段，橄榄色引用块，与闲聊严格区分。
- **忘掉它**：每条记忆按住 1.2 秒 → 灰掉 → 移除。这是三件里最重要的一件：
  一个把"私有记忆留在服务端"写进 README 的产品，必须让人能**一条一条地**收回。
  它也顺带解决 App Store 的数据删除审查和"记忆越攒越歪"的实际问题。

**落地点。** `/v1/memories`（GET/POST/DELETE）；`murmur/dossier.py` 加 `source` 与
按条删除；iOS 新 `MurmurMemoryView`（设置页进入，主界面无入口）。

**纪律。** 记忆列表不做搜索框、不做时间轴——它是一张"它记得的事"的清单，不是数据库。
"按住才忘"照抄念念，因为这是我见过对"删除"最好的一次交互：**要用力**。

**代价。** 中。服务端 dossier 结构要动，值得。

## 5. 按住说话：本机转写的语音备注

**证据。** 念念有 `/api/asr`、"切换为语音输入"、"长按说话 (上滑或移出取消)"、
"松手以取消发送"、"没采到声音（麦克风未出声）"——整条链路做得很完整，
但**音频要传到它的服务器**。open-swiftui-animations 有整个
`SlideToCancelAnimations/` 目录（多个变体）可以直接当交互参考。

**做什么。** 输入框右侧一个 mic disc，按住说话，上滑取消，松手转写成草稿文字
（**可编辑再发**，不直接发送）。

- **本机 Speech framework 转写，音频不出设备**——只有转写后的文本进 `note`。
  这和"原图不上传"是同一句承诺的两半，也是相对念念的直接优势；
- 本机识别不可用（语言包缺失/系统版本）时，明确告诉用户"这台设备上转不了"，
  **不偷偷传音频上服务器**。宁可少一个功能，不换一次信任。

**落地点。** `MurmurChatView.swift` 的 `MomentComposer`；新 `SpeechDictation.swift`；
`Info.plist` 加麦克风与语音识别用途说明；`PrivacyInfo.xcprivacy` 同步。

**纪律。** 转写结果落进草稿，永远给一次修改机会——语音识别错字发出去，
是这个产品最尴尬的失败方式。

**代价。** 中低。纯客户端，服务端零改动。

## 6. 一次接住连发的几张（含输入侧分级）

**证据。** README 自己列着这条"做了一半"。硬阻塞点在
`murmur/app_api.py:615`：`form(max_files=1, max_fields=4)`——服务端**一次只收一张**，
不是"只把最新一张给模型"那么简单。念念的
`/api/image-uploads/{authorize,heic,promote,complete}` 是完整四段式多图管道，
但它同时写着"每次只能上传一张图片"——**它也没做完**。

**做什么。**

- 客户端 `PhotosPicker` 多选（上限 3），草稿区排成一小队，可单张移除；
- 服务端放开 `max_files`，同一静默期的几张一起进上下文，
  `photo.py` 的压缩分档逐张套用；
- **配套输入侧分级**（README 另外半条）：多图意味着 token 翻倍，
  所以先用 `MURMUR_IMAGE_MODEL` 级别的便宜模型打一个"值不值得深聊"的分，
  值得才上主模型，不值得直接 `brief`/`quiet`。

**纪律。** 上限 3 张。它是朋友，不是相册收件箱；四张以上的连发不该被"一次接住"，
该被"你今天怎么了"。

**代价。** 中。多图会让单次成本上升，必须和分级同一个 PR 落地。

## 7. 发件箱：弱网自动补发，和"它还没看到"的诚实状态

**证据。** IceCubes 用 SwiftData `@Model Draft` 持久化草稿。
Murmur 刚在 `364a7b3` 加了失败行与重发问句（`SendFailureMark` / `ResendQuestion`），
做对了一半：失败**看得见**了，但补发还得人手点。而这个产品的典型场景是
"边走边拍"——地铁口、电梯里、地下车库，弱网是常态而不是异常。

**做什么。** 把失败的那一张变成一个持久发件箱：

- 草稿（照片 + 备注 + idempotency_key）落盘，App 被杀也在；
- 网络恢复 / `BGProcessingTask` 唤醒时自动补发一次，成功就静静地补上气泡；
- 状态行说人话："等信号"而不是"发送失败"；超过 6 小时才降级成需要人处理的失败。
- 与提案 1 共用同一个 App Group 队列。

**落地点。** `MurmurTranscript.swift` 扩出 outbox；`MurmurSessionModel.swift` 的重发路径；
服务端不用改（`idempotency_key` 与 `UNIQUE(user_id, idempotency_key)` 已经保证幂等）。

**纪律。** 自动补发只补**一次**，且只补 24 小时内的。一张昨天的照片今天突然发出去，
它回的那句会错得莫名其妙。

**代价。** 低。基础设施都在了。

## 8. 端详：点一下，只剩照片和那一句

**证据。** 念念有"字幕模式 · 点按隐去文字"和"完整对话 · 点按切换为字幕"，
以及三种看图方式：`网格 · 一眼纵览全部` / `长廊 · 一次端详一张` / `叠影 · 整叠铺开`。
它在"读"这件事上花的心思，比 Murmur 多。

**做什么。** 在已有 transcript 上加**一个手势**，不加任何入口：
点一下照片 → 进入端详——纸白铺满，只有那张照片和它那一句，serif 大字，
时间收成一行小字，其余全部隐去；再点一下退出。左右滑切上一/下一张有照片的 moment。

- 用 `matchedGeometryEffect` 从气泡里长出来，`MurmurMotion.reveal`（见开发意见 1）；
- Reduce Motion 下改成纯淡入；
- **这就是第一轮"留一页 / 翻页回看"该有的样子**：不新建一个"过去"的房间，
  只是把已经在的东西看清楚。

**落地点。** `MurmurTranscriptView.swift` 的 `MessageRow` + 新 `MomentDetailView`；
纯客户端。

**纪律。** 端详里没有任何按钮——没有分享、没有收藏、没有导出。要退出就点一下。

**代价。** 低。视觉收益最高的一条。

## 9. 私有星图：只有光点，没有地名

**证据。** 星图是念念的招牌（"点击上传 · 把从前收进星图"、"返回星图"、
"隐藏背景星星"），但它的星点背后是一张张回忆卡片。
Murmur 手里有比它更干净的原料：`moment.py:spot_key()` 把 GPS 四舍五入到
小数点后三位（≈110 米）当匿名指纹，`memory.py` 只数"来过几次"，
坐标不出网、地名从不生成。

**做什么。** 一张**完全本机渲染**的图：每个常去的格子一个橄榄色光点，
大小 = 去过的次数，位置用相对布局（不是地图投影，也不叠底图）。
点开只有一行："这里来过 17 次"。没有地图、没有坐标、没有地名、没有连线。

**落地点。** `/v1/places`（只返回 `{fingerprint_hash, count}`，服务端本来就只有这些）；
iOS Canvas 渲染；设置页进入。

**纪律。** 永远不接地图 SDK，永远不反查地名——README 的设计决定 3 说了
"你又在这个地方了"听着像被跟踪，那么一张能被认出是自己家小区的图同理。
光点的相对位置要**故意打乱到只保留疏密关系**，不保留真实方位。

**代价。** 中低。它是这个产品隐私哲学唯一一次可以被"看见"的机会。

## 10. 先体验一张，再谈邀请

**证据。** 念念把游客体验做进了登录墙："游客只能体验一张照片，登录后即可继续上传"，
配 `/api/guest/captcha` + `/api/guest/opening/permit` 防刷，
并且首屏就问"今天过得怎么样"。分发靠"扫码进入公测群聊"。
Murmur 是纯邀请制，服务端有 `app-invite` CLI 和 VPS 面板的建码功能，但 App 里没有。

**做什么。** 两半，一个漏斗：

- **游客 moment**：无邀请码也能发一张 → 收到那一句 → 然后才出现邀请墙。
  防刷用现成的 App Attest（`app_attest_keys` 表已在），一台设备一次，
  游客 moment 不建 dossier、不进主动排程、**24 小时后连同照片一起删干净**；
- **自助邀请**：设置页"邀请朋友" → App API 一个受限接口调 `app-invite`
  （每用户每月 3 个，明文码只在生成那一次返回——和 VPS 面板现有逻辑一致）。
  邀请卡用它自己的声音（"它最近挺想认识你"），不做拉新奖励。

**纪律。** 游客那一张必须是**完整体验**——真模型、真人格、真的可能 `quiet`。
一个为了转化而保证说好话的 demo，会把这个产品最值钱的东西演砸。

**代价。** 中。且有外部依赖：TestFlight 卡在 Apple 账号上，先把服务端和 App 侧做好等位。

---

## 与第一轮 / DeepSeek 那版的对应

| 第一轮 / DeepSeek | 这一轮 | 处置理由 |
|---|---|---|
| 1 游客体验模式 | **10**（上半） | 保留，与自助邀请合成一个漏斗 |
| 2 语音备注 | **5** | 保留，改为本机转写优先并补上"转不了就明说" |
| 3 多图连发 | **6** | 保留，补上真正的阻塞点 `max_files=1` |
| 4 输入侧分级 | **6**（下半） | 并入多图：两者必须同一个 PR，单列会被拆开做 |
| 5 留一页 / 改稿 | **8** | 降级。transcript 已存在，缺的是"看清楚"不是"再存一份" |
| 6 翻页回看 | **8** | 合并。手卷式历史与已发布的 transcript 重复建设 |
| 7 隐私星图 | **9** | 保留，加"打乱方位"这条纪律 |
| 8 每日开场白 | — | **移出功能列表**：`pick_intent` 已在跑，剩下的是 persona 调参 |
| 9 记忆问答 | **4** | 扩大。问答是三件事里最次要的，写入与遗忘更重要 |
| 10 邀请朋友 | **10**（下半） | 保留 |
| —（第一轮没有） | **1** 分享扩展 / 快捷指令 | Murmur 是原生 App、念念不是，这是最大的未用优势 |
| —（第一轮没有） | **2** 可回复通知 | 主动开口是产品的一半，现在的推送长得像验证码短信 |
| —（第一轮没有） | **3** 一按"戳中了" | persona.py 唯一可量化的调优信号，一天的活 |
| —（第一轮没有） | **7** 发件箱 | "边走边拍"场景里弱网是常态；`364a7b3` 只做了一半 |

## 建议顺序

**第一批（各一到两天，互不依赖）：3 戳中了 → 8 端详 → 7 发件箱。**
成本最低、当天就能自己用上，而且 3 会开始积累后面所有 persona 调整的依据。

**第二批（这一轮的重点）：2 可回复通知 → 1 分享扩展/快捷指令。**
两条都在把"这是个原生 App"这件事兑现成体验；2 先做，因为它改动小且能直接检验
主动开口的真实打扰程度。1 顺带把 App Group 打通，7 的发件箱正好挂上去。

**第三批：4 记忆三件事 → 5 语音 → 9 星图。**
4 要动 dossier 结构，排在有一批真实反馈数据之后做更稳。

**第四批（受外部条件约束）：6 多图+分级 → 10 游客与邀请。**
6 的成本模型要先跑一轮真实账单；10 等 Apple 账号。

