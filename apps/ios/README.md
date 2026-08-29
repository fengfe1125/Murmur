# Murmur iOS workspace

> 状态：现行规范｜适用：iOS 工程布局｜核验：2026-08-28｜依据：PR #13、#14 与目录迁移提交

`MurmurApp/` 保存现有 Swift 源码、资源、配置示例与单元测试；`MurmurApp.xcodeproj/` 保存同级 Xcode 工程。
二者一起迁移，工程内部相对引用不变。请从仓库根目录按 [客户端开发说明](MurmurApp/README.md) 构建和验收。

本地 `.local.xcconfig` 随工程保全但不进 Git；目录调整不修改 App ID、签名、UI 或本机历史存储。
