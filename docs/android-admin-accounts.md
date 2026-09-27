# 安卓上线所需的账号与管理员概念

> 状态：现状记录｜适用：安卓上线前的外部账号准备｜核验：2026-08-30｜依据：Google Firebase 与 Play Console 的账号要求、Murmur 现行运维口径；不代表任何账号已申请或已获批

Murmur 的安卓适配（`docs/android-adaptation-plan.md`）卡在两类外部账号上。先把
"管理员"这个概念拆清楚：Murmur 本身**没有管理员账号**，需要申请的是 Google 侧
的 Firebase 项目与 Play 开发者账号。

## 1. Murmur 的产品管理员 —— 没有账号，只有 SSH

Murmur 是邀请制，没有管理员登录。管理员能力 = **VPS 的 SSH 权限**：

- 能执行 `sudo -u murmur -H bash -c 'cd /opt/murmur && ... murmur app-invite'`
  的人就是管理员；
- 日常用本机看板 `http://127.0.0.1:8765/vps` 创建邀请码（走
  `fye816368@murmur-vps` + IAP 隧道，见 `docs/win-vps-panel.md`）；
- 不需要为安卓单独获取任何"管理员账号"。

## 2. Firebase 项目（FCM 推送用）—— 免费，普通 Google 账号即可

任何 Google 账号（现有 Gmail 即可）创建 Firebase 项目后就是项目 Owner。
建议为 Murmur 用专用账号，便于以后移交。步骤：

1. 没有专用账号先去 [accounts.google.com](https://accounts.google.com) 注册（免费）；
2. 打开 [console.firebase.google.com](https://console.firebase.google.com) →
   **添加项目**（名字如 `murmur-android`，Google Analytics 可以关）。创建后，
   设置页的**项目编号**即 `.env` 的 `MURMUR_APP_FCM_PROJECT_ID`；
3. 项目设置 → **服务账号** → 生成新的私钥 → 下载 JSON。此文件是
   `MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH`（放 VPS `/etc/murmur/`，0600，
   murmur 可读；**内含 RSA 私钥，与 APNs `.p8` 同级，不进 Git、不发聊天**）；
4. 项目设置 → 您的应用 → 添加 Android 应用（包名 `com.sakura.murmur`）→ 下载
   `google-services.json` → 放进本仓库 `android/app/`（`.gitignore` 已挡；
   Gradle 检测到该文件才会启用 Firebase 配置）。

Firebase 免费额度对 Murmur 量级（每天几十条通知）绰绰有余。

## 3. Google Play 开发者账号（Phase 3 发布用）—— $25 一次性

- [play.google.com/console](https://play.google.com/console) 注册开发者账号，
  **$25 一次性注册费**，个人/公司身份均可，实名与地址验证通常要数天——
  **提前注册，别卡到发布前**；
- **建议与 Firebase 用同一个 Google 账号**：Play App Signing 的签名摘要要配进
  `MURMUR_APP_ANDROID_SIGNING_DIGESTS`，同账号下 Play Console 与 Firebase
  联动最省事（注意：用 Play App Signing 时摘要取**上传证书**的值，不是应用签名
  证书）。

## 4. 三个东西齐了之后的移交清单

1. `google-services.json` → `android/app/`
2. FCM 服务账号 JSON → VPS `/etc/murmur/`，路径写入 `MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH`
3. release 签名证书 SHA-256 → `MURMUR_APP_ANDROID_SIGNING_DIGESTS`
   （keytool 或 Android Studio 签名报告生成）

齐全后即可完成 `docs/android-task-checklist.md` 的 T2.3.1–2.3.5（VPS 配置、
`validate()` 自检、失效 token 链路）与 T2.2.5（FCM 真实下发联调）。

## 5. 附：本机 SSH 密钥权限修复（让开发机直接写 VPS 配置）

Windows OpenSSH 对 `~/.ssh` 文件权限很严格。报 "Bad owner or permissions"
时，在你自己的 PowerShell 里执行：

```powershell
icacls %USERPROFILE%\.ssh\config /inheritance:r /grant:r "%USERNAME%:R"
icacls %USERPROFILE%\.ssh\google_compute_engine /inheritance:r /grant:r "%USERNAME%:R"
```
