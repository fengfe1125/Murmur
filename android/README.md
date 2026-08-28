# Murmur Android

> 状态：现状记录｜适用：Android 跟进客户端｜核验：2026-08-28｜依据：PR #14 与当前 Android 源码

Android 与 iOS 共用 App API，当前是 Debug 开发骨架，不是已验收的正式客户端。
现有功能包括开发身份、文字/照片上传和回应显示；Release 设备认证仍明确拒绝未实现路径。
后端支持 Android Key Attestation 和 FCM，不代表客户端正式链路已经接通。

## 构建

需要 JDK 21 与 Android SDK。SDK 路径、开发配置写在被忽略的 `local.properties`，不能提交 token。
从仓库根目录执行：

```sh
cd android
./gradlew assembleDebug --no-daemon
```

CI 只证明 Debug APK 可编译。体验对齐、正式认证/推送、真机验收是后续任务。
历史技术拆解见 [Android 适配归档](../docs/archive/android-adaptation-plan.md)，其中进度与旧路径不代表当前状态。
