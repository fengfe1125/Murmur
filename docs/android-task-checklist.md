# 安卓适配详细任务清单

> 状态：执行中——**M1 里程碑已完成**（T0.1/T0.2/T1.1/T1.2/T1.7，61 条 JVM 单测 +
> 5 条 Compose UI 测试全绿）。本文档把 `docs/android-adaptation-plan.md` 的
> Phase 1–3 拆成可勾选、可验收的任务。每个任务写清「改哪、对照哪个 iOS 文件、
> 怎么算完成」。
> 服务端（P0–P4）已全部落地，本清单只含客户端与运维工作。
> 总原则不变：**同产品、同约束，iOS 是基准，安卓不做功能加餐。**

## 0. 先修的两个既有问题（进入 Phase 1 前完成）

- [x] **T0.1 SSE 中断被静默吞掉**
  `MurmurSessionModel.kt` `send()`：流在没有 `done` 的情况下断开时，目前被当成
  Complete 并清空输入、删除照片（照片已删，无法重试）。对齐
  `MurmurSessionModel.swift` `run()`：无 `done` 断开抛
  `MurmurFailure("stream_ended", "回应中断了。", retryable = true)`，进入
  `phase = Error`，**不删除照片、不清 note**。
  验收：模拟器把 SSE 在 bubble 后掐断（停掉 app-api），界面显示"回应中断了"+
  「再试一次」，重试用同一个 idempotencyKey 不产生新 moment。
  状态：**已修**（JVM 测试 `streamEndingWithoutDoneIsAnInterruptibleErrorNotCompletion` 盯着这条）。

- [x] **T0.2 死代码接线清单**
  `MurmurApiClient.kt` 已实现但没有任何调用方：`currentProactive` / `acknowledge` /
  `devices` / `removeDevice` / `preferences` / `updatePreferences` / `deleteAccount` /
  `resetLocalIdentity`。本清单 T1.3 / T1.4 负责全部接上，此处只建清单防止遗漏。
  状态：**会话模型已全部接线**（refreshProactive / ack / devices / preferences /
  删除账户 / 重置身份）；设置页 UI 仍属 T1.4。

## Phase 1 — 客户端功能对齐（3–4 周）

### T1.1 会话状态机对齐 iOS（`MurmurSessionModel.swift` 为基准逐条移植）

- [x] T1.1.1 `Submission` + `lastSubmission`：`send()` 快照
  `(note, photo, idempotencyKey, replyToProactiveMomentID)`；`retry()` 只允许
  `phase == Error && failure.retryable && lastSubmission != null`。
- [x] T1.1.2 draft 与 current 分离：`draftPhoto/draftNote`（输入区）与
  `currentPhoto/currentNote/currentMomentID`（在途 moment）两个概念分开；
  `begin(submission, replacingCurrent)` 语义照搬。
- [x] T1.1.3 SSE 续传循环：`events(momentID, lastEventID)` 带 `Last-Event-ID`
  重连，最多 2 次重试、退避 `350ms × n`；`seenEventIDs` 去重；`done` 才 terminal。
- [x] T1.1.4 `done` 状态判定：`wasQuiet && bubbles.isEmpty` → Quiet，否则 Complete；
  `move`/`scene` 存入状态（UI 暂不展示也要留着）。
- [x] T1.1.5 取消语义两档：`cancelCurrentOperation`（保留 current moment，回到
  Ready/Idle）与 `clearCurrent`（清空全部、回 Idle），对照 iOS 同名方法。
- [x] T1.1.6 `requiresDeviceReconnect`：`app_attest_invalid_key` /
  `attestation_key_unknown` 错误码 → `connection = Offline`，不把用户留在
  已连接状态里继续重试。
- [x] T1.1.7 幂等重排矩阵：发送失败→重试→成功/失败/取消/中断，每个分支的
  idempotencyKey 行为与 iOS 相同（对照 `MurmurSessionModelTests.swift` 用例表）。
- **验收**：T1.7 状态机测试全绿；手动在模拟器上演练 断网重发 / 取消 / quiet /
  连续两次发送。
  状态：**已落地**（`MurmurSessionModel.kt` 全量重写；24 条 JVM 测试对照 iOS 矩阵移植，
  含断流重试、幂等键复用、SSE 续传、取消、主动消息 ack、30 连发无残留）。

### T1.2 PhotoLoader.kt（新增，对齐 `PhotoLoader.swift`）

- [x] T1.2.1 大小与空文件守卫：≤25 MB（`image_too_large`）、非空（`empty_image`）、
  读不出（`photo_unreadable`），错误文案与 iOS 一致。
- [x] T1.2.2 预览降采样：`BitmapFactory.Options.inSampleSize` 二段采样或
  `ImageDecoder.setTargetSize`，预览 ≤1800px；替换
  `MurmurSessionModel.stagePhoto()` 里裸 `decodeFile`（当前全尺寸解码，大图 OOM）。
- [x] T1.2.3 EXIF 方向：`androidx.exifinterface` 读 Orientation，预览转正；
  坐标照 iOS 逻辑**不出网**（服务端 `photo.py` 负责最终剥离）。
- [x] T1.2.4 文件名清洗：`[A-Za-z0-9_-]` 白名单 + 80 字符上限 + 合法扩展名，
  对照 `PhotoLoader.safeFilename`。
- [x] T1.2.5 临时文件生命周期统一：photo 副本与 multipart 体全部 `cacheDir`、
  用后即删；冷启动清理收敛到 loader（现在散在 `MurmurApp.onCreate` 与
  `settleMoment()`）。
- **验收**：48MP 手机照片选图内存峰值可控；>25MB 被拒；HEIC 在 API 28+ 真机
  可读可发。
  状态：**已落地**（`PhotoLoader.kt`：ImageDecoder ≤1800px、25MB/空文件守卫、
  文件名清洗、managed 副本生命周期；`PhotoPoliciesTest` 覆盖纯策略；EXIF 转正由
  ImageDecoder API 28+ 自动处理，真机 HEIC 验证待 Phase 1 验收）。

### T1.3 主动消息接线（API 已就绪，只差 session 与 UI）

- [ ] T1.3.1 bootstrap 成功后拉一次 `currentProactive()`：有内容则替换当前
  moment（`refreshProactive(expectedMomentID = null)` 语义），并 `ack`。
- [ ] T1.3.2 回复主动消息：note 提交时带 `replyToProactiveMomentID`，先
  `acknowledge(momentID, reply)` 再 `createMoment`（对照 iOS `run()` 开头）。
- [ ] T1.3.3 通知深链入口：`intent extra moment_id` → `refreshProactive`；
  不匹配 `expectedMomentID` 时静默忽略。
- **验收**：worker 造一条 proactive，模拟器冷启动展示 + ack；带回复发送走
  ack 先行。

### T1.4 设置页（顶栏 trailing 入口，对照 iOS Settings 全部功能）

- [ ] T1.4.1 设置入口 + 面板（design.md 顶层 chrome：词标 | 连接状态 + 设置）。
- [ ] T1.4.2 设备列表：展示 `devices()`、移除他人设备、移除当前设备后回
  `NeedsEnrollment` 并 `clearCurrent()`。
- [ ] T1.4.3 偏好：`dailyFrequency` / `quietStart` / `quietEnd` 读写 +
  `settingsMessage`（"已保存"/失败原因），`preferencesLoaded` 守卫。
- [ ] T1.4.4 删除账户与重置本地身份：都回 `NeedsEnrollment`，错误走
  `recordSettingsFailure`，`requiresDeviceReconnect` 时转 Offline。
- **验收**：设置全流程在模拟器开发通道可走通；错误提示不泄漏技术细节。

### T1.5 通知权限策略（对齐 iOS `notificationPromptRequested`）

- [ ] T1.5.1 首次完整收到回复（非 quiet）后请求一次 `POST_NOTIFICATIONS`
  （API 33+ 运行时权限），只请求一次，拒绝后不再打扰。
- [ ] T1.5.2 权限三态记录：未决定 / 允许 / 拒绝；Phase 2 的 FCM token 上传
  严格按此三态（未决定、拒绝 → 传 `null`）。
- **验收**：权限弹窗时机与 iOS 一致；拒绝后重启不复发。

### T1.6 双栏布局与 UI 细节（对照 `MurmurChatView.swift` + design.md）

- [ ] T1.6.1 `WindowWidthSizeClass`：<600dp 单栏工作台；≥600dp 不对称双栏
  （图/输入 leading，回应 trailing）。
- [ ] T1.6.2 顶栏两端对齐（Murmur 词标 | 连接状态 + 设置）；底部一条安静状态
  线；无 tab 栏。
- [ ] T1.6.3 状态显式化：focus/error/disabled/loading/success/cancel/retry/
  pressed 不靠颜色单通道表达；quiet 是合法状态不是错误。
- [ ] T1.6.4 深色主题、XXXL 字体（跟随 `fontScale`）、横屏逐屏检查。
- **验收**：T1.7.4 的 Compose UI 测试全绿 + 模拟器人工过一遍 design.md 约束。

### T1.7 测试矩阵（`src/test` + `src/androidTest` 从零建立）

- [x] T1.7.1 状态机全矩阵（对照 `MurmurSessionModelTests.swift` 的用例表逐条移植）。
  状态：`MurmurSessionModelTest.kt` 24 条（冷启动、双发闸门、幂等键复用、SSE 续传
  `Last-Event-ID`、终端失败重试、幂等冲突不可重试、替换式 moment、超时可重试、
  慢上传长超时、取消保留 current、移除当前设备回注册、attestation 失效显式重连、
  主动消息 ack、成功即删原图、偏好加载、状态机走查、photo 取消丢弃、SSE 顺序、
  30 连发零残留、推送三态纯函数）。
- [x] T1.7.2 SSE 分帧解析：空行终止、`:` 注释行、`Last-Event-ID` 续传、乱序
  事件、流中断恢复。状态：`SseEventDecoderTest.kt`（8 条，纯 JVM）+
  `OkHttpMurmurApiClientTest.kt` 走真实 socket 的 SSE 用例（注释行、空行终止、
  EOF flush、`Last-Event-ID` 头、KEEP_OPEN 流期间闸门已释放）。
- [x] T1.7.3 multipart 组装与 SHA-256 摘要、Mutex 串行闸门并发、临时文件
  用后即删与冷启动清理。状态：`MultipartWriterTest.kt`（字节级布局、摘要=整文件
  sha256）+ `OkHttpMurmurApiClientTest.kt`（mock server 实测：无缓存头、错误信封、
  摘要等于服务端收到的字节、两并发 createMoment 串行不交错）+ 会话测试里的
  临时文件清理断言。顺带把 `createMoment` 的 `bodyFile.readBytes()` 整包进内存
  改成了边写边摘要。
- [x] T1.7.4 Compose UI 测试：冷启动为空、发送后替换当前 moment、横屏、
  深色、XXXL 字体、≥48dp 点击区、≥600dp 双栏断言（对照 `MurmurUITests.swift`）。
  状态：`MurmurChatScreenTest.kt` 5 条（注册面板、空工作台、发送按钮禁用、
  深色渲染、2.4x 字体不裁切）在 `emulator-5554` 上跑；横屏/双栏断言随 T1.6
  布局落地后补。
- [x] T1.7.5 把 `gradlew test` / `connectedAndroidTest` 挂进现有脚本习惯
  （README「测试」一节补 Android 命令）。
- **验收**：`./gradlew test` 与模拟器 `connectedAndroidTest` 全绿，无需真机。
  状态：**61 条 JVM 单测全绿；Compose UI 测试 5/5 在 `murmur_api35` 模拟器通过**。

## Phase 2 — 生产链路（2–3 周，可与 Phase 1 并行启动）

### T2.1 Release 认证器（Key Attestation，契约见 `deploy/android-server-plan.md` 末尾）

- [ ] T2.1.1 Keystore 生成 P-256：purpose SIGN、digest SHA-256、不可导入、
  `setAttestationChallenge(client_data_hash)`、`setUserAuthenticationRequired(false)`。
- [ ] T2.1.2 注册证明物：`KeyStore.getCertificateChain()` 编码为
  **SEQUENCE OF OCTET STRING**（leaf 在前）放 `attestation` 字段。
- [ ] T2.1.3 `key_id = base64url(sha256(SPKI))` 去 padding，与服务端不变量一致。
- [ ] T2.1.4 每请求 assertion：裸 ECDSA/SHA-256 签
  `sha256(challenge ‖ METHOD ‖ path ‖ sha256(body))`（client_data_hash）。
- [ ] T2.1.5 `MurmurEnvironment.kt` release 分支换成
  `KeyAttestationAuthenticator`，删掉 `integrity_unsupported` 占位；
  debug 仍走 Development。
- [ ] T2.1.6 失败路径清理：`invalid_attestation` / `invite_invalid` /
  `app_attest_invalid_key` 时丢弃 pending key（已有骨架，补 release 实现）。
- [ ] T2.1.7 客户端侧纯函数单测：chain 编码、key_id、client_data_hash 字节序
  （服务端 21 条测试是服务端视角，客户端要有自己的）。
- **验收**：锁定 bootloader 的真机 release 构建注册成功；模拟器（非硬件密钥）
  被服务端 fail closed 拒绝，报错信息可读。

### T2.2 FCM 推送

- [ ] T2.2.1 `google-services.json` + firebase-messaging 依赖 + 默认通知渠道
  （API 33+ 渠道必需）。
- [ ] T2.2.2 `FirebaseMessagingService`：`onNewToken` 与启动时 token 变化 →
  `updateDevice(pushToken = …)`；按 T1.5.2 三态决定传 token 还是 `null`。
- [ ] T2.2.3 通知 payload：只认 `data.moment_id`，点击 → T1.3.3 深链拉取；
  通知预览文案与服务端 `FCMProvider` 的 180 字约束一致（客户端不改服务端）。
- [ ] T2.2.4 通知权限变化监听：允许→补传 token，拒绝→传 `null`。
- [ ] T2.2.5 模拟器（google_apis 镜像自带 Play services）FCM token 获取 +
  服务端 `deliver_pending` 真实下发验收。
- **验收**：模拟器收到主动消息推送，点击进入 App 展示对应 proactive moment。

### T2.3 VPS 生产配置

- [ ] T2.3.1 Firebase 项目建立 + FCM 服务账号（`.gitignore` 已挡
  `*service-account*.json`，注意别放错位置）。
- [ ] T2.3.2 确定 release 签名证书 SHA-256 → `MURMUR_APP_ANDROID_SIGNING_DIGESTS`
  （Play App Signing 的话用 Play 上传证书的值，先确认清楚）。
- [ ] T2.3.3 VPS 写入：`MURMUR_APP_ANDROID_ENABLED=1`、`MURMUR_APP_ANDROID_PACKAGE`、
  `MURMUR_APP_FCM_PROJECT_ID`、`MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH`、
  `MURMUR_APP_ATTEST_GOOGLE_ROOT_CA`（Google Hardware Attestation Root CA 路径）。
- [ ] T2.3.4 `validate()` 自检通过（production + android_enabled 时两项配置
  缺一即拒启）；重启 app-api / app-worker。
- [ ] T2.3.5 失效 token 链路测试：上报一个假 FCM token → 服务端收到
  `UNREGISTERED` → `mark_push_dead` 不再重投。
- [ ] T2.3.6 部署文档同步：`deploy/README.md` 补 Android 配置段。
- **验收**：VPS 上 iOS 零回归 + 安卓真机注册/推送全链路通。

### T2.4 评审项（不写代码，出结论）

- [ ] T2.4.1 Play Integrity 是否重启立项（绑定 GMS 的国内成本 vs 防重签名收益）。
- [ ] T2.4.2 国内厂商推送通道（华为/小米/OPPO/vivo）是否立项，依据 Phase 3
  国内真机 FCM 实测数据。

## Phase 3 — 真机验收与发布（2–4 周，依赖 Phase 2 全绿）

- [ ] T3.1 Google Play Console 注册（$25）、内部测试轨道、App Signing 方案
  与 T2.3.2 的签名摘要核对一致。
- [ ] T3.2 真机矩阵：Key Attestation 硬件判定（至少 2 台不同 OEM）、FCM 到达
  率（海外 / 国内网络分别记录）、Doze 与厂商后台策略下的到达时延。
- [ ] T3.3 邀请现有 iOS 用户交叉试用两周，对照 iOS 反馈问题清单。
- [ ] T3.4 Data safety 表单与隐私清单（`PrivacyInfo.xcprivacy` 等价内容）；
  应用内隐私承诺（不本地留存、坐标不出网）逐条自查。
- [ ] T3.5 依据 T3.2 数据拍板 T2.4 两个评审项；更新 `docs/android-adaptation-plan.md`
  的「落地状态」与 README 正式入口说明。
- **验收**：内测两周真实使用完成；推送到达率与 iOS 同量级或差距已记录原因。

## 并行轨道：Windows 开发机工程修复（不阻塞 Phase 1，但早做）

服务端测试目前在这台 Windows 机器上 20 个文件挂了 15 个，其中结构性问题是
`murmur/app_lock.py` 无条件 `import fcntl`（→ `murmur app-api` / `app-worker`
在 Windows 根本起不来，4 个测试文件直接挂）。修好才有本地基线：

- [ ] W1 `app_lock.py` 平台分支：Windows 用 `msvcrt.locking` 或退化实现，
  POSIX 保持 `fcntl.flock`；行为语义（跨进程互斥）在 Linux 上不变。
- [ ] W2 测试脚本输出 UTF-8：脚本内 `sys.stdout.reconfigure(encoding="utf-8")`
  或文档统一要求 `PYTHONUTF8=1`（✓ 字符在 GBK 控制台炸了一堆脚本）。
- [ ] W3 `test_env_sanitizer.py` 的 0o600 断言加平台分支（Windows 无此语义）。
- [ ] W4 `test_roster.py` / `test_wechat_flow.py` 关闭 SQLite 连接后再退
  TemporaryDirectory（Windows 文件锁导致清理失败，Linux 上无感）。
- [ ] W5 README「测试」一节补 Windows 跑法（`py -3` + 环境变量），并注明
  `app-api/app-worker` 的生产目标平台是 Linux。
- **验收**：Windows 上 20 个测试文件全绿；Linux 上依旧全绿。

## 依赖与顺序

```
T0.1 ──► T1.1 ──► T1.3（依赖 current-moment 语义）
                └► T1.7.1/7.2/7.3（随 1.1 同步写）
T1.2（独立，可与 T1.1 并行）
T1.4 / T1.5 / T1.6（互相独立，可并行；都依赖 T1.1 的状态机）
T2.1（契约固定，可与 Phase 1 并行；真机验收要等真机到手）
T2.2（代码可与 Phase 1 并行；端到端要 T2.3 的 Firebase 项目）
T2.3 ──► Phase 3 全部
W1–W5（独立轨道，随时做）
```

里程碑建议：**M1 = T0.1+T0.2+T1.1+T1.2+T1.7**（状态机与照片管线收敛，测试基线
成立）✅ 已完成 → **M2 = T1.3–T1.6 全部**（功能对齐完成，模拟器开发模式全流程）→
**M3 = T2.1+T2.2+T2.3**（真机双链路 + VPS 生产配置）→ **M4 = Phase 3**（发布）。
