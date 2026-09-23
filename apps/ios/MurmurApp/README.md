# Murmur iOS

> 状态：现状记录｜适用：iPhone/iPad 主客户端｜核验：2026-08-28｜依据：PR #13、PR #14；未宣称本轮真机验收

iOS 是体验主线，最低 iOS 18，不支持 Mac Catalyst。当前入口仍是聊天、当年今日、我的。
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

从仓库根目录打开 `apps/ios/MurmurApp.xcodeproj`，使用共享 scheme `Murmur`。
按 `Config/Debug.local.xcconfig.example` 配置 Debug 地址和显式开发 token；原件、密钥均不提交。
Release 从本地配置读取 HTTPS 地址，不包含开发绕过；不要因移动文件重置 bundle ID、签名或客户端存储。

```sh
xcodebuild -project apps/ios/MurmurApp.xcodeproj -scheme Murmur \
  -destination 'generic/platform=iOS Simulator' build
xcodebuild test -project apps/ios/MurmurApp.xcodeproj -scheme Murmur \
  -destination 'platform=iOS Simulator,name=iPhone 17' -only-testing:MurmurTests
```

当年今日的 Metal shader 需要对应 Xcode Metal toolchain。测试与构建产物不提交。
现有单元测试覆盖身份、SSE、失败恢复、临时文件、聊天持久化和独立存档。
App Attest/APNs 真机、签名与生产环境必须另行验收，模拟器和无签名编译不替代它们。


## 原生聊天与旧照浏览

2026-09-23 客户端改为系统四标签与导航容器；聊天日期分页使用原目录内的 SQLite，首次读取校验并迁移 JSON。分页只限制内存窗口，不删除窗口外的本机记录，清空聊天和照片存档仍分别操作。没有新增跨设备同步。

当年今日按照片日期横向浏览，明确点击“聊聊这张”才创建房间。日期分页、输入栏和截图检查见 [界面验收记录](../../../docs/validation/native-chat-photo-refactor-2026-09-23.md)。每日回顾客户端以 `/v1/reviews/config` 的能力结果开放；服务端不存在该路由或未开启时不开放回顾，本 PR 不包含每日回顾服务端实现或部署。
