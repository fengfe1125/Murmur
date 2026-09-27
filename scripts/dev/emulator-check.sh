#!/usr/bin/env bash
# Murmur 安卓模拟器验收：按需冷启动 murmur_api35 → 等开机完成 → 跑仪器化测试。
# 用法：tools/android/emulator-check.sh [gradle 任务与参数，默认 connectedDebugAndroidTest]
# 环境坑记录：AVD 存放在仓库内 tools/android/avd-home，必须显式导出
# ANDROID_AVD_HOME，否则 emulator 找不到 murmur_api35。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export ANDROID_AVD_HOME="$ROOT/tools/android/avd-home"
export JAVA_HOME="$ROOT/tools/android/jdk17"
export GRADLE_USER_HOME="$ROOT/tools/android/gradle-home"
export PATH="$ROOT/tools/android/sdk/platform-tools:$ROOT/tools/android/sdk/emulator:$PATH"

AVD="${MURMUR_AVD:-murmur_api35}"
TASK=("${@:-connectedDebugAndroidTest}")

if ! adb devices 2>/dev/null | grep -qE 'emulator-[0-9]+[[:space:]]+device'; then
  echo "[emulator-check] starting $AVD (cold boot)"
  emulator -avd "$AVD" -no-snapshot-save -no-boot-anim >/tmp/murmur-emulator.log 2>&1 &
  adb wait-for-device
fi

booted=0
for _ in $(seq 1 80); do
  if [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
    booted=1
    break
  fi
  sleep 3
done
if [ "$booted" != "1" ]; then
  echo "[emulator-check] boot timeout, see /tmp/murmur-emulator.log"
  exit 1
fi

cd "$ROOT/apps/android"
./gradlew "${TASK[@]}" --console=plain
