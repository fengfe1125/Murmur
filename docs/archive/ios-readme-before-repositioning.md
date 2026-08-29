# Murmur iOS App

> 状态：归档｜适用：历史追溯｜归档核验：2026-08-28｜依据：重组前文件快照。
> 以下保留当时描述、路径和判断，不代表当前能力、部署状态或新开发要求。现行说明见 [文档导航](../README.md)。

这是 Murmur 的正式移动端入口，支持 iPhone 和 iPad、最低 iOS 18；不支持 Mac Catalyst。
App 使用邀请制注册、App Attest 请求验证、真实照片/文字上传、SSE 流式回应和 APNs
主动消息，不包含本地假回复或会话列表。

## 产品边界

- 底部三个入口：聊天、当年今日、我的。设置在「我的」里，不再是对话上方的圆盘。
- 界面一次只保留当前 moment；新的发送会替换上一次，冷启动不会恢复内容。
- Murmur 服务端仍保留私有记忆，用来延续人格和上下文；App 不提供历史查询接口。
- 照片预览在后台通过 ImageIO 下采样。原图作为临时上传文件，服务器处理结束后删除；
  长期只保留去 EXIF 的压缩预览、文字记忆和匿名地点指纹，不保留原始 GPS 坐标。
- App 完成、安静结束、取消或不可重试失败后立即删除本机临时原图；冷启动还会清理上次崩溃
  遗留的 Murmur 临时文件。原图与 multipart 文件均使用完整文件保护。
- App 可能瞬时处理照片 EXIF 中的精确位置，因此已在 `PrivacyInfo.xcprivacy` 如实声明。
- 当年今日是两层。第一层是这个 tab：上面一个日历，凡是那天开过照片房间的日子
  都带一个加深的实心圆（今天是描边圈，两者互不混淆）；下面是进浏览器的入口，
  点进去还是原来那套——粒子、上滑发给 Murmur、下滑换一张。第二层是点日历上的
  圆圈，看那天和它聊过哪几张照片、说了什么。
- 房间写进的是**当年今日自己的存档**（`MurmurArchive`，`Murmur/archive/`），
  和聊天记录完全隔开：聊天里看不到房间说过的任何一句。存档按「说这话的那天」
  归档，日历就是它的读法。两份历史两个开关，设置里分别清空。
  没落地的那一句不写——房间把话退回输入框，存档里也不留它；发到一半离开房间，
  那一条按发送失败记，而不是永远转圈。
- 日历和日期不跟随系统语言。App 全部文案是硬编码中文，跟着 locale 走的日历会
  是英文手机上唯一的英文。周一起始，见 `Calendar.murmur`。
- 每位邀请用户最多绑定 3 台设备，设置页可查看并撤销设备。
- APNs token 只有在系统通知权限已允许时才同步；未决定或拒绝时会向服务端写入 `null`。
- 首次注册响应丢失时使用 `/v1/enrollments/recover` 恢复已绑定身份，不会重复消耗邀请码或
  对同一个 key 再次 attestation。App Attest key 失效时可在设置中安全重置本机绑定。

## 本地开发

1. 打开 `MurmurApp.xcodeproj`，选择 `Murmur` shared scheme。
2. 将 `Config/Debug.local.xcconfig.example` 复制为
   `Config/Debug.local.xcconfig`，填写本机 API 地址和仅用于开发的 token。
3. 运行 iOS 模拟器，或直接跑真机。开发 token 绕过编进**所有 Debug 构建**，真机也算
   （见 `MurmurEnvironment.makeAPIClient()`）：没有付费团队的 Team ID 就没法验证
   App Attest，真机连注册都做不到。带没带 token 决定用哪条路——Debug 里读得到
   `MURMUR_DEV_BYPASS_TOKEN` 或 Info.plist 的 `MurmurDevelopmentToken` 就走开发令牌，
   读不到才走 App Attest。真机的 token 只能从 Info.plist 走（`SIMCTL_CHILD_` 环境变量
   到不了真机），所以 `Config/Debug.local.xcconfig` 要同时给地址和令牌。

`*.local.xcconfig` 已被仓库忽略。不得把开发 token、APNs 私钥或 App Store Connect
凭据提交到仓库。

Release 使用 `Config/Release.local.xcconfig` 提供 HTTPS API 地址；Release 构建不包含开发
绕过代码，非 HTTPS 地址会被拒绝。

## 构建与测试

```sh
xcodebuild -project MurmurApp.xcodeproj -scheme Murmur \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/MurmurDerivedData CODE_SIGNING_ALLOWED=NO build

xcodebuild -project MurmurApp.xcodeproj -scheme Murmur \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  -derivedDataPath /tmp/MurmurSimDerivedData test
```

单元测试覆盖状态机、并发发送闸门、同 key 幂等重排、SSE 断线续传、独立长上传超时、
取消、无历史、失败行重启后仍能重发（幂等键存在转录里，重发的是同一个 moment）、
发送失败只标记失败的那一条、照片房间写进存档而不是聊天（含没落地的那句不留痕、
再试一次不写第二张照片、发到一半离开记成失败）、存档按当天归档（含跨零点的那两
分钟）、日数是照片数而不是行数、主动消息回复关联、注册响应丢失恢复、APNs 授权
策略、设备撤销、安全请求串行化、临时文件清理和大图下采样。
UI 测试覆盖发送与冷启动为空、发送后键盘不收起（可以连着写下一句）、发送失败的红色
感叹号与轻点重发、草稿带照片时输入框仍贴住键盘（照片卡片浮在对话底部，不进入底部
inset 的安全区记账）、房间里说的话在离开房间后仍留在对话里、横屏、深色、
Accessibility XXXL、44pt 点击区域，并在 iPad target 上断言 regular-width 双栏。

当年今日与照片房间的 UI 测试靠 `--murmur-stub-onthisday*` 系列打桩相册：`-ask` /
`-denied` / `-limited` 是三种权限门，`-empty` 是"今天空白但相册有照片"（回落到随机
相册照片），`-barren` 是"相册里什么都没有"。`--murmur-stub-reading-fails` 让读图上传
失败，用来驱动房间里的「再试一次」。

发送失败的呈现分三处，互不重叠：已发出的一条失败了，标记落在它自己的气泡旁（`resend-moment`
按钮，可重发时才是按钮，点击先弹「重新发送这一条？」确认，确认按钮 `confirm-resend`）；草稿
本身出问题（照片读不出来）由输入框上方一行说明；连接与设备身份问题仍走顶部状态胶囊与重新
连接页。`--murmur-fail-first-send` 让 UI 测试的假 API 只让第一次发送失败，用来驱动这条路径。

## Apple 账号阻塞项

项目已包含 App Attest 与 APNs entitlements、推送注册和通知打开流程，但完整真机验证仍需：

- Apple Developer Team 与已注册的 `com.sakura.Murmur` App ID；
- App Attest capability；
- APNs token signing key 与服务端 provider 配置；
- 真机签名、TestFlight 和生产/开发 APNs 环境验收。

在这些条件满足前，只能完成模拟器开发和无签名构建，不能宣称 App Attest/APNs 真机链路已验收。
