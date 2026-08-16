# 在 Windows 上使用 Murmur VPS 面板

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

注意：VPS 上会用你的 **Windows 用户名**新建一个账号（自动带 sudo，
所以面板创建邀请码需要的 `sudo -u murmur` 不受影响）。以后面板走的是直连
SSH，不再每次经过 gcloud，所以只需这第一次。

### 3. 拉代码、装依赖

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

- 自动开浏览器到 http://127.0.0.1:8765/vps
- 黑窗口留着就是运行中，关掉（或 Ctrl-C）就停

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

- 面板状态区报"连接超时"：先在 CMD 里手动跑一遍第 2 步最后那条
  `gcloud compute ssh ... echo ok`，通了再开面板。
- 报"本机没有 ssh"：Win10/11 自带 OpenSSH；如果没有，设置 → 应用 →
  可选功能 → 添加"OpenSSH 客户端"。
- 报鉴权失败：`gcloud auth login` 的账号要和 Mac 上是同一个
  （`fye816368@gmail.com`），否则没有这台实例的权限。
- 面板主页（`/`，本机看板）在 Win 上会显示空状态或报错——正常，
  那页读的是本机数据库，Win 上没有数据。只用 `/vps` 页。
