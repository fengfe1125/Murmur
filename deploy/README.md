# 部署 Murmur 正式 App 服务

正式生产入口只有 iOS App。Telegram、钉钉、微信和 QQ 的代码继续保留用于回归测试，
但凭据、SQLite、日志和 systemd 环境都必须与生产 App 隔离。任何脚本都不会因为发现
平台凭据而自动启用 Bot。

## 主机与端口

- `murmur-app-api`：仅监听 `127.0.0.1:8766`，由 Caddy 暴露 HTTPS。
- `murmur-app-worker`：领取 SQLite 中的 moment/推送作业，不监听公网端口。
- `murmur-web`：只读看板，仅监听 `127.0.0.1:8765`，通过 SSH 隧道查看。
- Caddy：唯一公网入口，监听 80/443，TLS 终止后反代 App API。

1 核 1G 可用于邀请制初期。主机必须能直连模型网关和 Apple APNs；App Attest 与 APNs
真机联调还需要 Apple Developer 账号、注册的 App ID 和 APNs 密钥。

## 初次安装

```bash
sudo useradd -r -m -d /opt/murmur -s /bin/bash murmur
sudo apt update
sudo apt install -y python3-venv git caddy
sudo install -d -o murmur -g murmur -m 0750 /opt/murmur/logs
```

把代码放到 `/opt/murmur` 后安装依赖并生成生产 `.env`：

```bash
sudo chown -R murmur:murmur /opt/murmur
sudo -u murmur -H bash -c '
  cd /opt/murmur
  python3 -m venv .venv
  .venv/bin/pip install -e .
  ./scripts/setup-murmur.sh
'
sudo chmod 600 /opt/murmur/.env
```

生产 `.env` 必须至少保持以下门禁；验收完成前使用 `transition`：

```dotenv
MURMUR_CHANNEL_MODE=transition
MURMUR_ENABLE_TEST_BOTS=0
MURMUR_AUTO_ENROLL=0
```

### 暂无 Apple Developer 账号时

正式 App Attest/APNs 不能假装完成。仅为模拟器和后端联调，可在隔离环境显式设置：

```dotenv
MURMUR_APP_ATTEST_MODE=development
MURMUR_APP_ALLOW_DEVELOPMENT=1
MURMUR_APP_DEVELOPMENT_TOKEN=<openssl rand -hex 32 的输出>
MURMUR_APP_BASE_URL=http://127.0.0.1:8766
```

开发 token 至少 24 字符，只放 `.env`，不得写入代码、仓库或 Release App。生产切换时必须
同时改为 `MURMUR_APP_ATTEST_MODE=production`、`MURMUR_APP_ALLOW_DEVELOPMENT=0`、清空
开发 token，并配置 HTTPS、Team ID、App ID 与 APNs 密钥；服务会拒绝夹带开发绕过的
production 配置。

生产 APNs `.p8` 不跟代码或状态包迁移。先在服务器上单独安装，路径放在
`/opt/murmur` 之外，避免发布目录切换时被移走：

```bash
sudo install -d -o root -g murmur -m 0750 /etc/murmur
sudo install -o murmur -g murmur -m 0600 AuthKey_KEYID.p8 \
  /etc/murmur/AuthKey_KEYID.p8
# .env: MURMUR_APP_APNS_KEY_PATH=/etc/murmur/AuthKey_KEYID.p8
```

安装并启动正式服务：

```bash
sudo cp /opt/murmur/deploy/*.service /etc/systemd/system/
sudo cp /opt/murmur/deploy/murmur-logrotate /etc/logrotate.d/murmur
sudo systemctl daemon-reload
sudo systemctl enable --now murmur-app-worker murmur-app-api murmur-web
sudo systemctl --no-pager --full status \
  murmur-app-worker murmur-app-api murmur-web
```

把 [`Caddyfile.example`](Caddyfile.example) 中的 `app.example.com` 替换成正式域名，
将站点块合并进 `/etc/caddy/Caddyfile`，再验证并重载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

DNS 必须先指向 VPS，80/443 必须可达。API 会流式读取请求并精确执行 26MiB 总 body
上限（其中图片最大 25MiB）；Caddy 示例不使用仅 2.10+ 才有的实验性
`request_body` 指令，避免系统仓库的旧版 Caddy 拒绝加载配置。SSE 使用
`flush_interval -1`，避免第一条 bubble 被代理缓冲。不要把邀请码、assertion、APNs token
或消息正文放进 URL 查询参数。示例刻意不启用 Caddy access log，避免结构化请求日志把
认证 header 写盘；运维只使用 App API 自己脱敏后的指标和 service log。

上传防护的生产基线记录在 `.env.example`：全局最多同时读取 2 个 body，总时限 180 秒、
连续 15 秒无数据即中止，10 秒宽限后最低 16KiB/s；图片解码上限 80MP，小 JSON 上限
256KiB，限流身份表最多 4096 项。部署时不要用更宽松的旧值覆盖这些默认值。

## 邀请用户与新增设备

管理员只通过服务器 CLI 创建一次性代码；代码仅在当前终端输出一次，数据库只保存哈希：

```bash
# 新用户：7 天有效，可选备注只供管理员辨认
sudo -u murmur -H bash -c \
  'cd /opt/murmur && .venv/bin/dotenv -f .env run -- .venv/bin/murmur app-invite --alias "邀请备注"'

# 先列出 user_id，再为该用户签发 30 分钟有效的新设备码
sudo -u murmur -H bash -c \
  'cd /opt/murmur && .venv/bin/dotenv -f .env run -- .venv/bin/murmur app-users'
sudo -u murmur -H bash -c \
  'cd /opt/murmur && .venv/bin/dotenv -f .env run -- .venv/bin/murmur app-device-code USER_ID'
```

每位用户最多 3 台有效设备；第四台会由 API 拒绝。不要把邀请码或设备码写入工单、日志、
命令历史以外的长期文件，使用后立即丢弃终端输出。

## 测试 Bot 隔离

测试 Bot 不读取生产 `/opt/murmur/.env`。它们只读取
`/opt/murmur/.env.test-bots`，并只写 `/opt/murmur/test/`：

```bash
sudo install -d -o murmur -g murmur -m 0750 /opt/murmur/test/logs
sudo install -o murmur -g murmur -m 0600 \
  /opt/murmur/deploy/test-bots.env.example \
  /opt/murmur/.env.test-bots
sudoedit /opt/murmur/.env.test-bots
```

测试文件必须显式包含：

```dotenv
MURMUR_CHANNEL_MODE=transition
MURMUR_ENABLE_TEST_BOTS=1
MURMUR_AUTO_ENROLL=0
MURMUR_DB=/opt/murmur/test/murmur.db
MURMUR_LOGDIR=/opt/murmur/test/logs
```

只在测试主机上、只为需要的单个平台手动启动，例如：

```bash
sudo systemctl enable --now murmur-telegram
```

平台凭据和模型密钥存在不等于授权启动。缺少 `.env.test-bots`、测试开关未开启、
或当前运行环境已是 `app_only` 时，CLI 会失败关闭。不要让测试 Bot 指向生产数据库；不要在
生产 App 用户和测试平台账号之间复用 thread。

## 分阶段切换

### 1. transition 验收

- 正式 `.env` 使用 `MURMUR_CHANNEL_MODE=transition` 和
  `MURMUR_ENABLE_TEST_BOTS=0`。
- App API/Worker 常驻；旧平台如确需对照，只在独立测试环境显式开启。
- 真机验收文字、照片、SSE 首泡、主动推送、重放拒绝、第四台设备拒绝和无聊天记录。

### 2. App-only 切换

只有真机验收完成后才执行：

```bash
sudoedit /opt/murmur/.env
# MURMUR_CHANNEL_MODE=app_only
# MURMUR_ENABLE_TEST_BOTS=0
# MURMUR_AUTO_ENROLL=0
sudo systemctl restart murmur-app-worker murmur-app-api
sudo systemctl disable --now \
  murmur-telegram murmur-dingtalk murmur-wechat murmur-qq
```

确认 App 正常后，再到各平台后台人工撤销 Bot 凭据。撤销不可自动化，也不属于普通部署或
更新流程。平台代码与离线测试保留；以后若做回归测试，应使用新的测试凭据和隔离数据库。

## 更新与迁移

`murmur-update` 只接受 `main` 的快进提交，安装依赖并运行全部
`tests/test_*.py`。成功后只重启管理员已经 enable 的服务；失败会回滚代码，不会启用任何
新服务，更不会根据平台凭据启用 Bot。

更新不会把服务单元强推到布局不同的主机上。单元文件里写死了 env 文件和日志目录，
仓库里的四个 Bot 单元指向隔离布局（`.env.test-bots`、`/opt/murmur/test/logs`）：
更新只会安装本机已经具备这些路径的单元，其余原样保留并在结尾列出，例如

```text
Units left in place, repo copy needs paths this host lacks:
  murmur-qq.service (this host has no /opt/murmur/.env.test-bots, …)
```

要采用隔离布局就按上面「测试 Bot 隔离」建好路径，下次更新会自动装上对应单元；
在此之前旧单元继续读 `/opt/murmur/.env` 和 `/opt/murmur/logs/`，Bot 不会被改瘫。
重启阶段也不再中途放弃：每个 enable 的服务都会重启，没起来的按名字列出并以非零码退出。

```bash
sudo systemctl start murmur-update
sudo systemctl status murmur-update --no-pager
```

首次关联私有 GitHub 仓库可使用：

```bash
./scripts/link-vps-to-github.sh --repo OWNER/REPO \
  --gcloud INSTANCE --zone ZONE --project PROJECT
```

`scripts/migrate-to-vps.sh` 只有在输入 `CUTOVER` 后才替换远端目录和传输生产状态；它只
自动启用 App API、Worker 和看板，并显式保持四个平台 Bot 为 disabled。测试 Bot 的
`.env.test-bots` 不随生产迁移传输。

迁移脚本是严格的单机切换工具，会在停机前后都失败关闭：

- `MURMUR_DB` / `MURMUR_APP_DB` / `MURMUR_APP_MEMORY_DB` 必须指向同一个
  SQLite，`MURMUR_APP_DATA_ROOT` 必须是它的目录。非默认分库布局不会被猜测迁移。
- 本机 `app-api` / `app-worker` / `web` 和旧 Bot 都必须完全停止，且
  `queued/processing` moment 必须为 0。脚本不携带排队原图，因此队列未排空就拒绝快照。
- 远端 Caddy 必须已经配好 `127.0.0.1:8766` 反代并能通过 `caddy validate`；
  生产 APNs key 必须事先以 `0600` 放到 `.env` 所指的远端绝对路径。
- 迁移包会把数据库路径规范为 `/opt/murmur`、移除生产 `.env` 中的四平台
  凭据，并将 DB、dossier 和预览收紧为只有 `murmur` 用户可读。
- 启动后会等待服务稳定，再经 `.env` 的 HTTPS 域名实际请求 challenge；
  Worker 短暂启动后因密钥错误退出时，切换不会误报成功。

## 运维与数据

```bash
systemctl status murmur-app-api murmur-app-worker murmur-web
tail -f /opt/murmur/logs/murmur_app_api.log
tail -f /opt/murmur/logs/murmur_app_worker.log
ssh -N -L 8765:127.0.0.1:8765 user@server
```

- `.env`、Apple 密钥、SQLite、日志、dossier、照片预览和测试环境文件都不得进 Git。
- `/opt/murmur/murmur.db` 使用 WAL；备份用
  `sqlite3 murmur.db ".backup backup.db"`，不要直接复制单个数据库文件。
- Caddy 只代理 8766；8765 不开放公网。服务日志不得打印图片、正文、邀请码、assertion
  或 APNs token。
- App API/Worker 使用 `UMask=0077`、`ProtectSystem=strict` 和
  `ReadWritePaths=/opt/murmur`；测试 Bot 只能写 `/opt/murmur/test`。
