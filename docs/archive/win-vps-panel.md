# 在 Windows 上使用 Murmur VPS 面板

> 状态：归档｜适用：历史追溯｜归档核验：2026-08-28｜依据：重组前文件快照。
> 以下保留当时描述、路径和判断，不代表当前能力、部署状态或新开发要求。现行说明见 [文档导航](../README.md)。

目标：在 Win 电脑上双击一个图标，就能看 VPS 状态、创建邀请码。
和 Mac 上是同一套代码（`murmur web` 的 `/vps` 页），通过本机的 gcloud/ssh
凭据连 VPS，VPS 上没有任何新端口。

## 一次性安装（约 15 分钟）

### 1. 装三个软件

- **Python 3.12+**：https://www.python.org/downloads/ —— 安装时**务必勾选
  "Add python.exe to PATH"**
- **Git for Windows**：https://git-scm.com/download/win —— 一路下一步
- **Google Cloud SDK**：https://cloud.google.com/sdk/docs/install ——
  安装向导最后让它自己跑 `gcloud init` 也行

装完**新开一个** PowerShell / CMD（让 PATH 生效）。

### 2. 登录 Google 并打通 SSH

```bat
gcloud auth login
gcloud config set project project-6af82f13-3757-4375-824
gcloud compute ssh instance-20260516-162140 --zone us-west1-b --command "echo ok"
```

最后这条会做三件事：在 `%USERPROFILE%\.ssh\` 生成 `google_compute_engine`
密钥对、把公钥写进 VPS、验证能连上。看到 `ok` 就是通了。

注意：

- VPS 上会用你的 **Windows 用户名**新建账号（自动带 sudo，所以面板创建
  邀请码需要的 `sudo -u murmur` 不受影响）。如果 Windows 用户名含中文等
  非 ASCII 字符，gcloud 会改用 Google 账号名（如 `fye816368`）作为 Linux
  用户名——下面 ssh config 里的 `User` 要写这个名字。
- Windows 上 `gcloud compute ssh` 用的是自带 plink（要 .ppk 密钥），
  面板不用它：面板只调 `gcloud compute instances describe` / 
  `start-iap-tunnel` 这类纯 API 命令，加密连接一律走系统 OpenSSH。

### 3. 网络：IAP 隧道 + ssh 别名

从国内直连 VPS 的 22 端口，TCP 能连上但 SSH 握手会被掐（报
"Connection timed out during banner exchange"）。所以面板不直连，改走
**IAP 隧道**（流量走 HTTPS 到 Google，再由 Google 内网转给实例，防火墙
规则 `allow-ingress-from-iap` 已经放行）。一次性配置两样东西：

`%USERPROFILE%\.ssh\config` 里加一个别名（IP 变了也不用改这里）：

```
Host murmur-vps
  HostName 127.0.0.1
  Port 2222
  User fye816368
  IdentityFile ~/.ssh/google_compute_engine
  IdentitiesOnly yes
```

再让面板用这个别名（写入用户环境变量，新开窗口生效）：

```bat
setx MURMUR_VPS_SSH "fye816368@murmur-vps"
```

隧道本身不用手动维护：`start-vps-panel.bat` 每次会自动起一个最小化的
隧道窗口（`gcloud compute start-iap-tunnel … 127.0.0.1:2222`），检测到
已在跑就复用。

### 4. 拉代码、装依赖

仓库是私有的，先配 GitHub 凭据（二选一）：

```bat
rem 方式 A：装 GitHub CLI 后一键登录（推荐）
winget install GitHub.cli
gh auth login

rem 方式 B：GitHub 网页 Settings → Developer settings → Personal access token，
rem 生成一个勾了 repo 的 token，clone 时当密码用
```

然后：

```bat
git clone https://github.com/fengfe1125/Murmur.git
cd Murmur
python -m venv .venv
.venv\Scripts\pip install -e .
```

## 日常使用

双击 `scripts\start-vps-panel.bat`：

- 自动起 IAP 隧道（一个最小化的黑窗口，**别关**，关了面板就报连接超时）
- 自动开浏览器到 http://127.0.0.1:8765/vps
- 面板自己的黑窗口留着就是运行中，关掉（或 Ctrl-C）就停

想放桌面：右键 `start-vps-panel.bat` → 发送到 → 桌面快捷方式。

面板上有：四个服务 + Caddy 的运行状态、负载/内存/磁盘、服务日志尾巴、
未使用邀请码列表、App 用户列表，以及创建邀请码的表单（备注、有效天数、
是否不限次数）。明文邀请码只在创建那一下显示，数据库只存哈希。

## 以后更新代码

```bat
cd Murmur
git pull
.venv\Scripts\pip install -e .
```

## 连不上时的排查

- 面板状态区报"连接超时"：先看隧道窗口还在不在（任务栏里最小化的
  `murmur-iap-tunnel`）。不在就手动跑一遍
  `gcloud compute start-iap-tunnel instance-20260516-162140 22 --local-host-port=127.0.0.1:2222 --zone=us-west1-b`，
  然后在 CMD 里 `ssh fye816368@murmur-vps "echo ok"` 验证，通了再开面板。
- 报"本机没有 ssh"：Win10/11 自带 OpenSSH；如果没有，设置 → 应用 →
  可选功能 → 添加"OpenSSH 客户端"。
- 报鉴权失败：`gcloud auth login` 的账号要和 Mac 上是同一个
  （`fye816368@gmail.com`），否则没有这台实例的权限。
- 邀请码列表报 `splitlines` 之类奇怪错误：老版本在 Windows 上用 GBK 解码
  远端中文输出会炸，`git pull` 到含 UTF-8 修复的版本即可。
- 改 `start-vps-panel.bat` 时**必须保存为 CRLF 行尾、不带 BOM**（LF 行尾
  的 bat 会被 cmd 解析得面目全非）。建议不动它，要动用支持选行尾的编辑器。
- 面板主页（`/`，本机看板）在 Win 上会显示空状态或报错——正常，
  那页读的是本机数据库，Win 上没有数据。只用 `/vps` 页。
