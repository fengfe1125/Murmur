# Murmur 安卓平台适配计划

> 状态：已评审通过，按阶段执行中。本文档与 `design.md` 并列——design.md 锁定视觉与交互，
> 本文档锁定安卓端的技术映射与实施顺序。iOS 端是产品基准，安卓端做"同产品、同约束"的实现，
> 不做功能加餐。

## 1. 背景与目标

Murmur 当前由四部分组成：

| 部分 | 现状 | 安卓适配影响 |
|---|---|---|
| `murmur/` Python 服务端 | App HTTPS API + SSE + 邀请制 + App Attest + APNs | 需增加安卓认证与推送通道 |
| `MurmurApp/` iOS 客户端 | SwiftUI，iOS 18+，正式产品入口 | 是本次适配的对照基准 |
| `webui/` 只读看板 | 单 HTML，无构建 | 无需改动 |
| Telegram/钉钉/微信/QQ 测试 Bot | 隔离测试通道 | 无需改动 |

目标：

1. 发布**安卓正式客户端**，产品边界与 iOS 完全一致：发一张图（可带一句文字），收到当前
   moment 的一句回应；**界面一次只展示当前 moment，不提供聊天记录、会话列表或本地留存**。
2. 服务端改造对 iOS 现有客户端零破坏（过渡期双平台共存）。
3. 本机安卓模拟器作为 Phase 0–2 的开发测试环境（本文档编写时已安装完毕，见 §12）。

## 2. 技术选型

| 维度 | iOS 现状 | 安卓选择 | 理由 |
|---|---|---|---|
| UI | SwiftUI | **Jetpack Compose**（Kotlin） | 声明式、状态驱动，与 SwiftUI 心智模型最接近；自定义 token 平移成本最低 |
| 并发 | Swift Concurrency（actor / AsyncStream） | Kotlin Coroutines + Flow | SSE 流 → Flow；actor 串行闸门 → Mutex |
| 网络 | URLSession（ephemeral，双超时 45s/300s） | OkHttp + okhttp-sse（自备 SSE 分帧解析，见 §3） | 需要与 iOS 一致的缓存禁用、超时与断线续传语义 |
| 摘要/签名 | CryptoKit（SHA-256 / P-256） | `java.security`（MessageDigest / Signature） | 请求摘要与设备密钥签名 |
| 身份存储 | Keychain（AfterFirstUnlockThisDeviceOnly） | Android Keystore（硬件 P-256）+ EncryptedSharedPreferences（security-crypto） | 设备绑定身份 + pending key |
| 设备认证 | App Attest | **Play Integrity API（主）+ Key Attestation（无 GMS 降级）** | 见 §4 |
| 推送 | APNs（HTTP/2 + ES256 JWT） | **FCM HTTP v1（OAuth2 服务账号）** | 见 §5 |
| 照片解码 | ImageIO（HEIC 原生） | ImageDecoder / BitmapFactory + `androidx.exifinterface` | HEIF 解码需 API 28+ |
| 选图 | PhotosPicker | Photo Picker（`ActivityResultContracts.PickVisualMedia`） | 免存储权限，系统界面 |
| 隐私清单 | `PrivacyInfo.xcprivacy` | 权限清单 + Play Data safety 表单 | 见 §7 |

构建基线：

- 语言 Kotlin，Gradle Kotlin DSL + AGP + version catalog，单一 `app` 模块起步。
- `minSdk 28`（Android 9：HEIF 原生解码、通知渠道成熟）；`targetSdk 35`；`compileSdk 36`
  （当前 okhttp 5.4 / Compose BOM 2026.06 起要求按 API 36 编译；compileSdk 只是编译期
  API 级别，不改变运行时行为门槛）。AGP 9 起 Kotlin 内置，不再单独引入 kotlin-android 插件。
- ABI：`arm64-v8a`（真机）+ `x86_64`（模拟器）。
- 最低系统版本对标：iOS 最低 iOS 18（当前世代），Android 侧不追同样的激进度——minSdk 28
  已覆盖 2024 年以后在售的几乎所有设备，且不需要为 HEIC 引入转码库。

## 3. 客户端架构映射（文件级）

`MurmurApp/` → 安卓工程 `android/`，逐文件对照：

| iOS 文件 | 安卓对应 | 说明 |
|---|---|---|
| `MurmurApp.swift` | `MainActivity.kt` + `MurmurApp.kt`（Application） | 入口与全局装配 |
| `MurmurEnvironment.swift` | `MurmurEnvironment.kt` + BuildConfig | Debug/Release 双环境；本地配置走 `local.properties`（对应 `*.local.xcconfig`） |
| `MurmurModels.swift` | `MurmurModels.kt` | DTO + `MurmurFailure` 错误模型 |
| `MurmurAPI.swift` | `MurmurApiClient.kt` | 见下方要点 |
| `MurmurSecurity.swift` | `MurmurAuthenticator.kt`（接口）+ PlayIntegrity / Development 两个实现 | §4 |
| `MurmurSessionModel.swift` | `MurmurSessionModel.kt` | 状态机同构移植，测试矩阵直接对照 |
| `MurmurChatView.swift` | `MurmurChatScreen.kt` | 单栏/双栏断点布局 |
| `MurmurTranscriptView.swift` | `MomentBubble.kt` 等子组件 | 当前 moment 展示 |
| `PhotoLoader.swift` | `PhotoLoader.kt` | 后台下采样（1024/1568 分档） |
| `PrivacyInfo.xcprivacy` | `AndroidManifest.xml` + Data safety | §7 |

网络层必须逐条保留的 iOS 行为：

1. **无缓存**：禁用 OkHttp Cache，所有请求 `Cache-Control: no-cache`、`reloadIgnoringLocalAndRemoteCacheData` 语义。
2. **双超时**：连接/请求 45s，SSE 读取 300s，互不拖累。
3. **SSE 手写分帧解析**：okhttp-sse 的流式接口会吞掉终止事件的空行（iOS 端 `consumeEvents`
   注释里踩过的同一个坑）。照搬 iOS 的"按字节读、`\n` 分行、空行结束事件、支持
   `Last-Event-ID` 断线续传、忽略 `:` 注释行"的解析器，包一层 Flow。
4. **multipart 上传**：先落临时文件（`cacheDir`），计算 SHA-256 摘要后随请求头发送，
   上传完成或失败立即删除；临时文件全部放应用私有目录，冷启动清理残留。
5. **串行闸门**：`ProtectedRequestGate` actor → Kotlin `Mutex` 实现的同一语义
   （认证类请求同一时刻只跑一个），并发测试直接对照 `MurmurAPI` 的既有用例。
6. **错误信封**：`MurmurFailure(code, message, retryable)` 完全对齐，UI 只消费这三个字段。

## 4. 设备认证：App Attest → Play Integrity（核心差异点）

> **落地状态（2026-08，以代码为准）**：服务端改造已按 `deploy/android-server-plan.md`
> 完成（P0–P4，commit `beed935`/`b42ab3c`/`da9cf75`/`89994e5`），与本节原计划的
> 三处关键出入：
>
> 1. **主认证方案是 Key Attestation，不是 Play Integrity。** `AndroidKeyAttestor`
>    （`murmur/app_attest_android.py`）验证 Google 硬件认证根签发的 X.509 证书链 +
>    KeyDescription 扩展（OID 1.3.6.1.4.1.11129.2.1.17），production 模式 fail
>    closed（TEE/StrongBox + verifiedBoot Verified + 签名证书白名单缺一不可）。
>    Play Integrity 经评审**暂缓**（Key Attestation + 邀请码已够，且绑定 GMS 对国内
>    设备是负担），接口留作后续可选。
> 2. **enroll 不分派字段，而是显式 `platform`。** 请求体新增 `platform`
>    （`"ios" | "android"`，缺省 `ios` 以兼容已发布 iOS 客户端）；每请求认证的平台
>    取自注册时存的 key，**不取自请求**（防降级）。
> 3. **schema 是 `platform` 列 + `apns_token` 改名 `push_token`**，不是
>    `push_platform`（见 §5.1 已同步修正）。
>
> 客户端要对上的精确 wire 契约见 `deploy/android-server-plan.md` 末尾的契约表。

### 4.1 现状（不改动 iOS 逻辑的前提下抽象）

服务端 `murmur/app_auth.py`：

- **注册**：challenge → 客户端 App Attest attestation（苹果根证书链 + nonce + rpIdHash +
  credential ID 校验）→ 服务端存储 SPKI 与 counter。
- **每请求**：challenge + method + path + body_digest → `generateAssertion`（P-256 签名，
  counter 单调递增）→ 服务端验签并 `advance_counter`。
- challenge 是一次性的（`consume_challenge`），本身提供重放防护。

### 4.2 安卓主方案：Play Integrity + 设备密钥（已暂缓，保留备查）

> **此方案未实施。** 服务端实际落地的是 §4.3 的 Key Attestation（见 §4 开头）。
> 以下流程仅在未来重启 Play Integrity 时作参考。

协议形状保持不变（challenge → 证明物 → 服务端验），只替换证明物的来源：

**注册流程（enrollment）**

1. 客户端请求 `/v1/auth/challenges`（同 iOS）。
2. 客户端生成**硬件 P-256 密钥对**（Android Keystore，`setUserAuthenticationRequired(false)`，
   设备级而非用户级，对齐 App Attest key 的语义），用私钥对 challenge 签名。
3. 客户端调用 Play Integrity SDK 取 integrity token（`setNonce(challenge哈希)`）。
4. 提交 `{challenge_id, invite_code, key_id, public_key_spki, signature, integrity_token,
   device_name, environment}`。
5. 服务端调用 Google API `decodeIntegrityToken`：校验 `requestDetails.nonce`、
   `appIntegrity.packageName`、`deviceIntegrity.meetsDeviceIntegrity`，通过后验签并存储 SPKI。

**每请求流程**

与 iOS 完全一致：challenge + method + path + body_digest → Keystore 私钥签名 →
服务端用已存公钥验签。Keystore 不提供 App Attest 式的 counter，重放防护依赖一次性
challenge（challenge 表已有），风险等价；如需更强防护可在 challenge 上加短 TTL。

**开发路径（本机模拟器即走这条）**

模拟器上 Play Integrity 只返回虚拟完整性，不能作为设备证明。复用服务端已有的
`MURMUR_APP_ALLOW_DEVELOPMENT` + `X-Murmur-Development-Token` 通道：客户端 **Debug 构建**
自动使用 DevelopmentAuthenticator（随机 `dev-` key + 令牌头），Release 强制 Play Integrity
——与 iOS 的 `DEBUG + Simulator` 逻辑严格对齐，Phase 0 的全部模拟器联调都依赖它。

### 4.3 降级方案：Key Attestation（无 GMS 设备）

> **落地状态**：此方案已升级为主线并**在服务端实现完毕**（`AndroidKeyAttestor`，
> commit `da9cf75`）。原计划"Phase 2 只做 Play Integrity"作废——Play Integrity
> 暂缓（见 §4 开头）。客户端 Release 认证器要按 `deploy/android-server-plan.md`
> 末尾契约表实现：Keystore 生成 P-256 密钥时带 challenge 请求 attestation
> 证书链，注册报文 `attestation` 字段装 **SEQUENCE OF OCTET STRING**（leaf 在前，
> 每项 `Certificate.getEncoded()`），`key_id = base64url(sha256(SPKI))` 去 padding。

部分国内 ROM / 无 Google 服务的设备没有 Play Integrity。降级路径用 Android Keystore 自带的
**Key Attestation**：生成密钥时要求 attestation 证书链（由 Google 硬件根签发），服务端验证
证书链、`verifiedBootState`（锁定）、包名扩展。

### 4.4 服务端改动（app_auth.py / app_settings.py）—— 已落地，以此为准

- `AppAuthenticator` 改为 **attestor 注册表**（`platform -> DeviceAttestor`）：
  `AppleAppAttestVerifier`（`platform = "ios"`）+ `AndroidKeyAttestor`
  （`platform = "android"`）。`enroll()` 按请求体的 `platform` 选 attestor；
  `authenticate()` 按**注册时存的 key 的 platform** 选，客户端不可声明。
- Android 注册证明物 = Key Attestation 证书链（非 Play Integrity token）；
  每请求 assertion = 裸 ECDSA/SHA-256 签名，签 `client_data_hash`
  （`sha256(challenge ‖ METHOD ‖ path ‖ sha256(body))`），counter 恒不推进
  （重放防护由一次性 challenge 承担）。
- 配置项（`MURMUR_APP_ANDROID_*` / `MURMUR_APP_FCM_*` / Google 根证书路径 /
  吊销列表策略）见 `deploy/android-server-plan.md` §4；production +
  `android_enabled` 时 `validate()` 强制要求 Android 与 FCM 配置齐全。

## 5. 推送：APNs → FCM

### 5.1 服务端（app_push.py / app_store.py / app_api.py）—— 已落地，以此为准

- **schema 迁移（已完成）**：`app_devices` 与 `app_attest_keys` 各加 `platform`
  列（`'ios' | 'android'`，存量行 `DEFAULT 'ios'` 回填），`apns_token` **改名**
  `push_token`（`UNIQUE` 保留）。注意是 `platform` 不是原计划的 `push_platform`，
  取值是平台名而非通道名。
- **Provider 抽象（已完成）**：`PushProvider` 协议 + scheduler 按 `platform`
  路由；`PushResult.permanent` 由各 provider 自填（FCM 的 404 `UNREGISTERED`
  与 APNs 的 410 统一成"判死"信号）。缺 provider 的平台走 retry 不判死。
  `FCMProvider`（`murmur/app_push_fcm.py`，commit `b42ab3c`）：
  - 服务账号 RS256 JWT 换 OAuth2 access token（未引入 google-auth，按
    `expires_in` 提前 60 秒续期）；
  - `POST https://fcm.googleapis.com/v1/projects/{project}/messages:send`；
  - payload：`notification.body` 预览（与 APNs 一样只放 180 字预览，不放对话），
    `android.priority=HIGH`、`collapse_key=moment_id`（对齐 `apns-collapse-id`）；
  - 失效 token：`UNREGISTERED` / `INVALID_ARGUMENT` → `permanent` →
    `on_invalid_token` + `mark_push_dead`，复用现有失效清理链路。
- **app_api.py `PUT /v1/device`（已完成）**：接受 `push_token`（保留 `apns_token`
  作旧别名）；token 校验按**注册时存的 key 的 platform** 分流——iOS 仍 hex
  32–256，Android `[A-Za-z0-9_:.-]` 64–512（FCM token 不是 hex）。
- **app_settings.py（已完成）**：`MURMUR_APP_ANDROID_ENABLED` /
  `MURMUR_APP_ANDROID_PACKAGE` / `MURMUR_APP_ANDROID_SIGNING_DIGESTS` /
  `MURMUR_APP_FCM_PROJECT_ID` / `MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH` 等，
  详见 `deploy/android-server-plan.md` §4。
- `ProactiveScheduler` / `deliver_pending` 状态机不动：队列按 device 行分发，
  provider 按 `platform` 选择。

### 5.2 客户端

- `FirebaseMessagingService` + `google-services.json`；token 刷新与更新逻辑对齐 iOS 的
  APNs token 同步策略。
- **通知权限策略对齐 iOS**：只有用户授权（`POST_NOTIFICATIONS`，API 33+ 运行时权限）
  才上传 token，未决定/拒绝传 `null`。
- Android 13+ 通知渠道；点通知进入 App 拉取 proactive moment（同 iOS 深链行为）。

### 5.3 风险与决策

**FCM 在国内网络下可能不可达。** 服务器在海外 VPS，海外/有 GMS 的设备不受影响；
国内真机验收时若确认不可达，再评估厂商通道（华为 HMS / 小米 / OPPO / vivo，均需各自
开发者账号与 SDK）。厂商通道是**后续可选工作**，不进主线排期；主动消息在 App 内已有
`/v1/proactive/current` 拉取路径可兜底。

## 6. 设计 token 映射（design.md → Compose）

design.md 是锁定的视觉系统。安卓端用 `MurmurTheme.kt` 集中定义，禁止各组件自造颜色/间距。

颜色（取值直接采自 `MurmurChatView.swift` 的 UIColor 定义，浅/深两套）：

| token | 浅色 | 深色 | Compose |
|---|---|---|---|
| paper | (0.96, 0.95, 0.91) | (0.08, 0.08, 0.07) | `Color(0.96f, 0.95f, 0.91f)` / 深色分支 |
| raisedPaper | (0.99, 0.98, 0.95) | (0.12, 0.12, 0.10) | 同上 |
| ink | (0.12, 0.12, 0.10) | (0.92, 0.91, 0.86) | 同上 |
| secondaryInk | (0.38, 0.38, 0.33) | (0.62, 0.62, 0.56) | 同上 |
| rule | (0.81, 0.80, 0.73) | (0.24, 0.24, 0.20) | 同上 |
| olive | (0.32, 0.37, 0.18) | (0.66, 0.70, 0.47) | 同上（强调/焦点，占屏 ≤5%） |
| coral | (0.76, 0.27, 0.20) | (0.93, 0.53, 0.43) | 同上（信号/错误） |
| outgoingBubble | (0.42, 0.48, 0.25) | (0.26, 0.33, 0.18) | 同上 |

字型：标题/词标用 `FontFamily.Serif`（映射 New York，仅 bold 做层级）；正文用系统
`FontFamily.Default`；数字用等宽数字特性（`fontFeatureSettings = "tnum"`）。全部 `sp`
单位、跟随系统 `fontScale`（Dynamic Type 对应项），不做固定文本框、不设小于 caption 的字号。

其余约束平移：

- 4pt 间距阶梯（4/8/12/16/24/32/40/64dp）。
- 点击区域 ≥48dp（design.md 的 44/50pt 在 Android 按 Material 基准取 48dp，主控件 56dp）。
- 单层表面，不嵌套描边卡片；圆角 12–18dp。
- 动效只有透明度渐变与按压反馈；系统"移除动画"开启时全部动效收敛为 ≤150ms 透明度。
- 焦点/错误/禁用/加载/成功/取消/重试/按压状态显式，不靠颜色单通道表达。
- 文案语气同 iOS：中文优先、短动词（"选一张照片"/"发送此刻"/"再试一次"），"安静"是合法
  状态不是错误。

布局：design.md 的 compact/regular 映射为 Compose `WindowWidthSizeClass`——紧凑宽度单栏
工作台，常规宽度（≥600dp）不对称双栏（图/输入在 leading，回应在 trailing），顶栏两端对齐
（Murmur 词标 | 连接状态 + 设置），底部一条安静状态线，无 tab 栏。

## 7. 隐私与权限

与 iOS 的产品承诺逐条对齐：

- **不本地留存**：照片原图、multipart 临时文件、note、回应全部只进应用私有 `cacheDir`，
  用后即删，冷启动清理残留；不申请 `WRITE_EXTERNAL_STORAGE` / `MANAGE_EXTERNAL_STORAGE`；
  不用 Room/DataStore 存任何用户内容（身份凭证走 Keystore/EncryptedSharedPreferences，
  与 Keychain 的 `ThisDeviceOnly` 语义一致）。
- **照片位置**：Photo Picker 返回的 Uri 通常已由系统剥离位置；客户端仍沿用 iOS 的
  "仅瞬时读取 EXIF 用于拍板，坐标不出网"逻辑，服务端继续负责最终剥离（`photo.py`）。
- **权限最小集**：`POST_NOTIFICATIONS`（运行时，API 33+）、`INTERNET`。相册选择用
  Photo Picker（无需存储权限）；相机拍摄若后续加，走系统相机 intent。
- **网络安全**：Release 构建强制 HTTPS + `networkSecurityConfig` 禁明文；OkHttp 无缓存、
  无 cookie 持久化；API 地址仅来自 BuildConfig/local.properties，与 iOS 的 xcconfig 同策略。
- **Data safety 表单**：按 `PrivacyInfo.xcprivacy` 的等价内容填写（照片内容、设备标识
  用于认证、通知 token 等），与 iOS 声明保持一致。

## 8. 服务端改造清单汇总 —— 全部已落地（2026-08）

| 文件 | 改动（实际，commit 见 deploy/android-server-plan.md） |
|---|---|
| `murmur/app_store.py` | `app_devices` / `app_attest_keys` 加 `platform` 列（存量回填 `ios`）；`apns_token` 改名 `push_token`；push 查询带出 `platform` |
| `murmur/app_auth.py` | `DeviceAttestor` Protocol + attestor 注册表；`enroll()` 按请求体 `platform` 分派；`authenticate()` 按存库 key 的 platform 选 attestor |
| `murmur/app_attest_android.py` | `AndroidKeyAttestor`：Google 硬件根证书链 + KeyDescription 扩展解析；production fail closed；吊销列表 1 小时缓存 |
| `murmur/app_push.py` | `PushProvider` 协议化；`PushResult.permanent`；scheduler 按平台路由，缺 provider 走 retry |
| `murmur/app_push_fcm.py` | `FCMProvider`：RS256 自签换 OAuth2（无 google-auth）；HTTP v1 发送；失效 token 判死 |
| `murmur/app_api.py` | `/v1/enrollments` 收 `platform`（缺省 `ios`）；`/v1/device` 收 `push_token`（`apns_token` 别名保留），token 校验按存库平台分流 |
| `murmur/app_settings.py` | `MURMUR_APP_ANDROID_*` / `MURMUR_APP_FCM_*` / Google 根证书 / 吊销策略配置与校验 |
| `tests/` | FCM provider（14 条）、Android attestor（21 条合成证书链）、schema 迁移、平台 wire 契约（7 条）——全部通过，iOS 零回归 |

**兼容性红线**：全部改动必须保证现有 iOS 客户端（含旧版 App）在迁移后行为不变；
过渡期内 `apns_token` 旧字段继续生效。

## 9. 分阶段实施计划

### Phase 0 — 环境与骨架（1–2 周）

- [x] 本机安卓模拟器安装（API 35 google_apis x86_64，WHPX 加速）——**已完成**，见 §12
- [x] Gradle 工程骨架 + 空 Compose 界面 + 设计 token 主题——**已完成**（`android/`，
  Gradle 9.7 + AGP 9.3.1，模拟器安装启动验证通过，注册界面渲染正确）
- [x] DevelopmentAuthenticator + `MurmurApiClient`（challenge/enroll/moments/SSE）——
  **已完成**（字节级对齐 app_auth.py 开发通道与 MurmurAPI.swift 行为）
- [x] 端到端跑通：模拟器上邀请码注册 → 选图 → 发 moment → SSE 收流式回应——
  **已完成**（2026-08-16，文本 moment 对 VPS 生产后端全链路验收通过：
  开发通道注册 → multipart 上传 → SSE 三条流式气泡 → done 后输入清空；
  照片 moment 待网关视觉恢复后补验）
- **验收**：模拟器上完成一次真实 moment 收发 ✅；`tests/` 服务端不报错 ✅；iOS 客户端回归通过（服务端零改动）✅

### Phase 1 — 客户端功能对齐（3–4 周）

- [ ] 状态机（发送中/流式/安静/完成/取消/失败重试）与全部 API 端点
- [ ] 照片下采样分档、EXIF、冷启动清理、主动消息（proactive/current + ack）
- [ ] 设备管理、偏好设置、账户删除、邀请注册失败恢复（`/v1/enrollments/recover`）
- [ ] 对照移植 iOS 单元测试矩阵（状态机、幂等重排、SSE 续传、串行闸门、临时文件清理）
- **验收**：模拟器开发模式全流程 + 全测试通过；横屏/深色/XXXL 字体/双栏断点 UI 检查

### Phase 2 — 生产链路（真机认证 + 推送）（2–3 周）

> 原计划的服务端条目（schema 迁移 / FCMProvider / enroll 双平台）**已全部提前落地**
> （见 §8），本阶段剩下的都是客户端与运维工作。

- [ ] 客户端 Release 认证器：Keystore P-256 + Key Attestation 证书链注册
  （wire 契约见 `deploy/android-server-plan.md` 末尾），替换 `MurmurEnvironment`
  里 release 分支的 `integrity_unsupported` 占位
- [ ] 客户端 FirebaseMessagingService + `POST_NOTIFICATIONS` 权限策略 +
  `updateDevice(pushToken=…)` 上传（客户端已发 `push_token` 字段）
- [ ] VPS 部署：`MURMUR_APP_ANDROID_*` / `MURMUR_APP_FCM_*` 配置、Google 根证书
  与 Firebase 服务账号就位、部署文档同步
- [ ] （可选，评审后再定）Play Integrity 是否重启；国内厂商推送通道是否立项
- **验收**：iOS 回归零回归；安卓 Debug（development 通道）与
  Release（Key Attestation + FCM，真机）双链路验收；失效 token 清理链路测试

### Phase 3 — 真机验收与发布（2–4 周）

- [ ] Google Play Console 应用注册、FCM/Firebase 项目、服务账号与 API 启用
- [ ] 真机矩阵：Key Attestation 硬件密钥判定、FCM 到达（海外/国内网络分别记录）、OEM 后台限制
- [ ] 内测渠道发布（internal testing），邀请现有 iOS 用户交叉试用
- [ ] 决定 Play Integrity 增补与国内厂商推送通道是否立项
- **验收**：内测用户完成两周真实使用；推送到达率与 iOS 同量级（或已明确记录差距与原因）

## 10. 测试策略

- **单元测试**（JVM + Robolectric 少量）：状态机全矩阵、SSE 解析器（含空行终止、注释行、
  `Last-Event-ID` 续传、乱序事件）、multipart 与摘要、串行闸门并发、临时文件清理——
  全部对照 iOS 既有用例移植。
- **模拟器集成**（本机 AVD）：development 模式端到端；FCM token 获取与服务端下发
  （google_apis 镜像自带 Play services）；通知权限三态（未决定/允许/拒绝）。
- **UI 测试**（Compose UI test）：冷启动为空、发送后替换、横屏、深色、XXXL 字体、
  48dp 点击区域、≥600dp 双栏断言——对照 `MurmurUITests.swift` 矩阵。
- **真机**：Key Attestation 硬件密钥判定、FCM 到达率、Doze/厂商后台策略。
- **服务端测试**（Python，项目现有风格，已完成）：合成证书链验证 Android attestor、
  mock FCM transport、schema 迁移前后兼容、双平台 wire 契约。

## 11. 风险与阻塞项

| 风险 | 影响 | 对策 |
|---|---|---|
| Google Play Console 注册（一次性 $25）与 Google Cloud 服务账号 | Phase 2 推送验收与 Phase 3 发布 | 提前启动账号申请，可与 Phase 0/1 并行；FCM 需要服务账号 |
| Key Attestation 依赖签名证书白名单与 verifiedBoot | 自签/侧载构建无法通过 production 注册 | Debug 走 development 通道；release 签名证书 SHA-256 需配进 `MURMUR_APP_ANDROID_SIGNING_DIGESTS` |
| FCM 国内网络不可达 | 国内真机推送 | 先实现 FCM；Phase 3 按实测决定厂商通道立项（§5.3） |
| 服务端 schema 迁移破坏旧 iOS 客户端 | 现有用户 | 已落地：存量行回填 `ios` + `apns_token` 别名保留 + iOS 全量回归（§8 红线） |
| 无 GMS 设备（部分国内 ROM） | FCM 不可用（Key Attestation 不受影响，不依赖 GMS） | 厂商推送通道按需立项（§5.3） |
| Keystore 无 counter，重放防护弱于 App Attest | 安全性 | 一次性 challenge + 短 TTL（§4.2），Phase 2 安全评审确认 |

## 12. 本机测试环境（已就绪）

安卓模拟器已安装在工作区（不入库，`tools/` 已加 `.gitignore`）：

```
W:\Murmur\tools\android\
├── jdk17\                       # Temurin JDK 17.0.20（Gradle / sdkmanager 用）
└── sdk\
    ├── cmdline-tools\latest\    # sdkmanager / avdmanager
    ├── platform-tools\          # adb
    ├── platforms\android-35\
    ├── emulator\                # 官方模拟器
    └── system-images\android-35\google_apis\x86_64\
```

- 加速：本机 Hyper-V/WHPX 平台已启用（vmcompute 运行中），`emulator -accel-check` 实测
  `WHPX(10.0.26200) is installed and usable`，GPU 走 RTX 4060 + Vulkan。
- AVD：`murmur_api35`（pixel_7 规格，1080×2400@420dpi，2GB RAM，API 35，google_apis
  镜像，已内置 Play services / Play 商店，Play Integrity 与 FCM 联调可用）。**首次启动
  已验证：Android 15 (API 35)、launcher 渲染正常、adb 在线。**
- 本机启动注意（Windows 沙箱/受限环境）：模拟器主进程与 netsimd 通过命名管道通信，
  受限沙箱会阻断导致启动即崩；需以完整权限启动。同时把 HOME 全部重定向进工作区，
  避免向 `%USERPROFILE%\.android` 写文件。常用命令：

```powershell
$sdk = 'W:\Murmur\tools\android\sdk'; $root = 'W:\Murmur\tools\android'
$env:ANDROID_HOME = $sdk
$env:ANDROID_EMULATOR_HOME = "$root\emulator-home"
$env:ANDROID_USER_HOME   = "$root\user-home"
$env:ANDROID_SDK_HOME    = "$root\user-home"
$env:ANDROID_AVD_HOME    = "$root\avd-home"
& "$sdk\emulator\emulator.exe" -avd murmur_api35 -no-boot-anim   # 启动（带窗口）
& "$sdk\platform-tools\adb.exe" devices                          # 验证在线
& "$sdk\platform-tools\adb.exe" install app-debug.apk            # 装包
& "$sdk\platform-tools\adb.exe" reverse tcp:8766 tcp:8766        # 连本机 app-api
& "$sdk\platform-tools\adb.exe" shell getprop sys.boot_completed # 应为 1
```

- 联调注意：模拟器访问宿主机 API 用 `10.0.2.2`（或上面 `adb reverse`）；development
  认证需服务端 `MURMUR_APP_ALLOW_DEVELOPMENT=1` + 有效 `MURMUR_APP_DEVELOPMENT_TOKEN`，
  与 iOS Simulator 开发配置完全共用。
