# Murmur Android

> 状态：现状记录｜适用：Android 跟进客户端｜核验：2026-08-30｜依据：Phase 4 代码与模拟器验收

Android 与 iOS 共用 App API，客户端已对齐 iOS 三入口体验：聊天（本地转录气泡、
送达勾、失败重发、图片查看）、当年今日（相册扫描 + 照片房间 + 按天存档）、我的
（设置）。服务端零改动。真机 Key Attestation 验收、FCM 端到端与 Play 发布仍待
原 Phase 2/3 运维项（Firebase 项目），未真机验证。

## 构建

本机固定用仓库自带的 JDK 17 与 gradle home（系统默认 JDK 版本过低）；SDK 路径写在被
忽略的 `local.properties`。从仓库根目录执行：

```sh
cd apps/android
JAVA_HOME=$PWD/../../tools/android/jdk17 \
  GRADLE_USER_HOME=$PWD/../../tools/android/gradle-home \
  ./gradlew assembleDebug --console=plain
```

## 测试

两层验收，缺一不可：

```sh
# 1) JVM 单测（纯本地，已挂 CI）
JAVA_HOME=../../tools/android/jdk17 GRADLE_USER_HOME=../../tools/android/gradle-home \
  ./gradlew test --console=plain

# 2) 仪器化测试（必须模拟器实跑；封装了冷启动与等待开机）
../../scripts/dev/emulator-check.sh
```

**「编译通过」不是 UI 测试的验收证据**：仪器化用例合并前必须在模拟器上实跑全绿。
2026-08-30 曾发生 12 条用例只编译验证就当完成、实跑全红的事故（根因已修，见
`docs/android-task-checklist.md` T4.7 过程修复）。

## 调试桩

Debug 构建支持 `--murmur-stub-onthisday*` 系列启动旗标驱动当年今日假数据
（对照 iOS DEBUG stub），供 UI 测试与手动验收；release 构建只见真实 MediaStore。
