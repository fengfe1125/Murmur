# Murmur iOS App

这是 Murmur 的正式移动端入口，支持 iPhone 和 iPad、最低 iOS 18；不支持 Mac Catalyst。
App 使用邀请制注册、App Attest 请求验证、真实照片/文字上传、SSE 流式回应和 APNs
主动消息，不包含本地假回复或会话列表。

## 产品边界

- 界面一次只保留当前 moment；新的发送会替换上一次，冷启动不会恢复内容。
- Murmur 服务端仍保留私有记忆，用来延续人格和上下文；App 不提供历史查询接口。
- 照片预览在后台通过 ImageIO 下采样。原图作为临时上传文件，服务器处理结束后删除；
  长期只保留去 EXIF 的压缩预览、文字记忆和匿名地点指纹，不保留原始 GPS 坐标。
- App 完成、安静结束、取消或不可重试失败后立即删除本机临时原图；冷启动还会清理上次崩溃
  遗留的 Murmur 临时文件。原图与 multipart 文件均使用完整文件保护。
- App 可能瞬时处理照片 EXIF 中的精确位置，因此已在 `PrivacyInfo.xcprivacy` 如实声明。
- 每位邀请用户最多绑定 3 台设备，设置页可查看并撤销设备。
- APNs token 只有在系统通知权限已允许时才同步；未决定或拒绝时会向服务端写入 `null`。
- 首次注册响应丢失时使用 `/v1/enrollments/recover` 恢复已绑定身份，不会重复消耗邀请码或
  对同一个 key 再次 attestation。App Attest key 失效时可在设置中安全重置本机绑定。

## 本地开发

1. 打开 `MurmurApp.xcodeproj`，选择 `Murmur` shared scheme。
2. 将 `Config/Debug.local.xcconfig.example` 复制为
   `Config/Debug.local.xcconfig`，填写本机 API 地址和仅用于开发的 token。
3. 运行 iOS 模拟器。开发 token 绕过只会在 `DEBUG + Simulator` 中编译；Debug 真机仍强制
   使用 App Attest。

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
取消、无历史、主动消息回复关联、注册响应丢失恢复、APNs 授权策略、设备撤销、安全请求
串行化、临时文件清理和大图下采样。UI 测试覆盖发送与冷启动为空、横屏、深色、
Accessibility XXXL、44pt 点击区域，并在 iPad target 上断言 regular-width 双栏。

## Apple 账号阻塞项

项目已包含 App Attest 与 APNs entitlements、推送注册和通知打开流程，但完整真机验证仍需：

- Apple Developer Team 与已注册的 `com.sakura.Murmur` App ID；
- App Attest capability；
- APNs token signing key 与服务端 provider 配置；
- 真机签名、TestFlight 和生产/开发 APNs 环境验收。

在这些条件满足前，只能完成模拟器开发和无签名构建，不能宣称 App Attest/APNs 真机链路已验收。
