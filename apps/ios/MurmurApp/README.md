# Murmur iOS

> 状态：现状记录｜适用：iPhone/iPad 主客户端｜核验：2026-09-13（导航、构建与测试入口）；其余沿用 2026-08-28｜依据：581b332 工程与脚本静态核对；其余依据 PR #13、PR #14，未运行本轮构建或真机验收

iOS 是体验主线，最低 iOS 18，不支持 Mac Catalyst。当前入口是聊天、一起听、当年今日、我的；一起听按服务端开关和白名单开放。
聊天在本机保留并恢复；照片房间使用独立按天存档，可续聊旧日期。清空两个记录分别操作。
当年今日会读旧照片的拍摄时间与位置（`PHAsset`，不解 EXIF，也不申请定位权限），
地名在本机反解后随上传声明；经纬度只在服务端折成匿名指纹，不落库、不出网做地图查询。
新定位中的日记确认和风格选择尚未实现，见 [当前能力](../../../docs/product/current-state.md)。

音乐（Audius）默认整条关闭，需要两个开关同时打开：本地 xcconfig 配了
`MURMUR_AUDIUS_API_KEY`，且服务端 `GET /v1/music/config` 说这个账号可用。
OAuth 走 `ASWebAuthenticationSession` + PKCE，令牌只存本机 Keychain；播放用
`AVPlayer`，只上报离散状态，不上报进度。真机链路（登录、后台、锁屏、耳机、
弱网）尚未验收，模拟器不替代它。

## 开发

2026-09-21 本地开发版已接入邮箱注册/绑定、账号每日 50 次主动请求额度和主动开启的历史同步。配置、数据边界及迁移见 [邮箱与同步](../../../docs/architecture/email-history-sync.md)。邮箱注册服务端默认关闭，本轮未部署生产；App Attest 与两台真机同步仍需单独验收。

从仓库根目录打开 `apps/ios/MurmurApp.xcodeproj`，使用共享 scheme `Murmur`。
按 `Config/Debug.local.xcconfig.example` 配置 Debug 地址和显式开发 token；原件、密钥均不提交。
Release 从本地配置读取 HTTPS 地址，不包含开发绕过；不要因移动文件重置 bundle ID、签名或客户端存储。

## 构建与测试

以下命令从仓库根目录执行。构建不需要启动后端：

```sh
xcodebuild -project apps/ios/MurmurApp.xcodeproj -scheme Murmur \
  -destination 'generic/platform=iOS Simulator' build
```

测试先用 `xcrun simctl list devices available` 查可用设备，将下例的占位值替换为所选模拟器 UDID。
保持现有 project、scheme 和测试 target；单元测试与 UI 测试分别执行：

```sh
MURMUR_SIMULATOR_UDID='<available-simulator-UDID>'
# 单元测试
xcodebuild test -project apps/ios/MurmurApp.xcodeproj -scheme Murmur \
  -destination "platform=iOS Simulator,id=$MURMUR_SIMULATOR_UDID" -only-testing:MurmurTests
# UI 测试（fake API，无需启动真实后端）
xcodebuild test -project apps/ios/MurmurApp.xcodeproj -scheme Murmur \
  -destination "platform=iOS Simulator,id=$MURMUR_SIMULATOR_UDID" -only-testing:MurmurUITests
```

局部改动可将 `-only-testing:` 收窄至对应 target/class/method。UI 测试结果仅证明所选用例和设备；
共享布局变化还需覆盖相关大字号、深色、横屏和 iPad 场景。

真实后端联调使用 `scripts/dev/dev-app.sh`；它会使用开发配置和数据库、创建邀请码，并保持
API 或 SSH 隧道运行。构建、单元测试和 fake API 界面检查无需启动这个脚本。

界面使用系统 TabView、NavigationStack、Form 与 UICalendarView；照片浏览不再依赖 Metal shader。测试与构建产物不提交。
现有单元测试覆盖身份、SSE、失败恢复、临时文件、聊天持久化和独立存档。
App Attest/APNs 真机、签名与生产环境必须另行验收，模拟器和无签名编译不替代它们。

### 日期聊天与每日回顾验收

2026-09-21 工作区新增 SQLite 迁移、分页日期定位与回顾缓存；后端需显式开启 `MURMUR_DAILY_REVIEWS_ENABLED`。数据位置、删除语义与 Figma 链接见 [日期聊天与每日回顾](../../../docs/architecture/daily-reviews.md)。

Debug UI 测试可组合 `--murmur-ui-testing --murmur-reset-transcript --murmur-stub-diary --murmur-seed-diary` 使用固定虚构内容；`--murmur-ui-test-dark` 和 `--murmur-ui-test-large-type` 分别强制深色与最大辅助字号，不影响 Release。新增的四个 `MurmurUITests` 以 `testDailyReview` 或 `testChatCalendar` 开头。测试不连接真实账号或生产数据库。
