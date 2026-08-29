# 部署 Murmur 正式 App 服务

> 状态：现行规范｜适用：授权运维｜核验：2026-08-29｜依据：本次日志所有权修复 PR；生产状态另行验收。

正式生产入口只有 iOS App。Telegram、钉钉、微信和 QQ 的代码继续保留用于回归测试，
但凭据、SQLite、日志和 systemd 环境都必须与生产 App 隔离。任何脚本都不会因为发现
平台凭据而自动启用 Bot。

## 主机与端口

- `murmur-app-api`：仅监听 `127.0.0.1:8766`，由 Caddy 暴露 HTTPS。
- `murmur-app-worker`：领取 SQLite 中的 moment/推送作业，不监听公网端口。
- `murmur-web`：管理员面板，仅监听 `127.0.0.1:8765`，通过 SSH 隧道查看；会写余额快照，授权后可经 SSH 创建邀请码。
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
  .venv/bin/pip install -e ./server
  scripts/dev/setup-murmur.sh
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
sudo cp /opt/murmur/infra/deploy/*.service /etc/systemd/system/
sudo cp /opt/murmur/infra/deploy/murmur-logrotate /etc/logrotate.d/murmur
sudo systemctl daemon-reload
sudo systemctl enable --now murmur-app-worker murmur-app-api murmur-web
sudo systemctl --no-pager --full status \
  murmur-app-worker murmur-app-api murmur-web
```

把 [`Caddyfile.example`](../../infra/deploy/Caddyfile.example) 中的 `app.example.com` 替换成正式域名，
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
  /opt/murmur/infra/deploy/test-bots.env.example \
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
- 真机验收文字、照片、SSE 首泡、主动推送、重放拒绝、第四台设备拒绝和无服务端聊天历史查询；本机聊天与照片房间存档按现有规则保留。

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

目录重组使用分阶段桥接流程，见 [布局切换](layout-transition.md)。本文命令针对新布局；代码合并、桥接安装和生产更新分别记录，不能相互代替。

`murmur-update` 只接受 `main` 的快进提交。更新前使用 Python 的 SQLite 在线备份 API，
分别对 `MURMUR_DB`、`MURMUR_APP_DB`、`MURMUR_APP_MEMORY_DB` 解析出的唯一数据库做快照。
配置解析使用 python-dotenv，并与 CLI 一致地保留显式环境变量优先级；App 配置缺失或为空时回落到基础数据库。
快照在 `backups/<配置名>-<旧sha>-<时间戳>.db`，包含已提交 WAL 内容；新目录 0700、文件 0600。
备份失败直接中止，不会用快照自动覆盖运行中的数据库。
随后按实际布局选择安装目标与统一测试 runner。成功后只重启管理员已 enable 的服务；
若已知服务正在运行但未 enable，预检直接拒绝，由管理员先处理其状态。不会自动启用服务或 Bot。

仓库里的 App 代码（`apps/ios/MurmurApp/`、`apps/android/` 等）不会保留在 VPS：桥接更新器在切换 HEAD 前展开
旧、新服务端路径；旧布局成功后保留 `murmur deploy tests scripts`，新布局保留 `server infra scripts`，
均保留 cone 模式所需根目录文件。`scripts/` 不能少——`test_channel_gate` 和 `test_env_sanitizer`
会读它，缺了更新流程的测试阶段会失败回滚。若某台 VPS 早年是全量检出，第一次跑到这
一步时会把 App 目录从磁盘清掉（前提是它们与 Git 一致；有本地改动会拒绝更新并列出路径）。
未忽略的未跟踪文件会挡住更新前的干净检查；已忽略的 `backups/`、`botpy.log*` 和运行数据原地保留。

更新不会把服务单元强推到布局不同的主机上。单元文件里写死了 env 文件和日志目录，
仓库里的四个 Bot 单元指向隔离布局（`.env.test-bots`、`/opt/murmur/test/logs`）：
更新只会安装本机已经具备这些路径的单元，其余原样保留并在结尾列出，例如

```text
Units left in place, repo copy needs paths this host lacks:
  murmur-qq.service (this host has no /opt/murmur/.env.test-bots, …)
```

要采用隔离布局就按上面「测试 Bot 隔离」建好路径，下次更新会自动装上对应单元；
在此之前旧单元继续读 `/opt/murmur/.env` 和 `/opt/murmur/logs/`，Bot 不会被改瘫。
若任何安装、测试、服务配置或重启失败，更新器恢复旧 SHA、原 sparse/full 检出、旧安装目标、
安装前的更新器、服务单元和 logrotate，并重启所有已尝试重启的服务，包括此前成功的服务。
回滚成功仍返回非零，表示更新被拒绝。若回滚本身失败，报告 `ROLLBACK INCOMPLETE`，
保留 `.git/murmur-update-txn.*` 的旧版本与文件快照清单及 `.git/murmur-update.lock`；下一次更新会拒绝运行。
管理员应先检查输出路径、旧 revision、安装目标和服务状态，再按清单手动恢复；未核验前不要删除锁和恢复材料。
更新器自我替换使用同目录原子 rename，不在运行时截断自身脚本。

```bash
sudo systemctl start murmur-update
sudo systemctl status murmur-update --no-pager
```

首次关联私有 GitHub 仓库可使用：

```bash
scripts/ops/link-vps-to-github.sh --repo OWNER/REPO \
  --gcloud INSTANCE --zone ZONE --project PROJECT
```

`scripts/ops/migrate-to-vps.sh` 只有在输入 `CUTOVER` 后才替换远端目录和传输生产状态；它只
自动启用 App API、Worker 和看板，并显式保持四个平台 Bot 为 disabled。测试 Bot 的
`.env.test-bots` 不随生产迁移传输。

源码包只来自干净的当前 Git 提交，允许 `server/`、`infra/`、`scripts/` 和指定仓库级文件，
不包含移动端、品牌素材、文档目录或未跟踪内容。打包前拒绝被跟踪的私钥、运行数据、符号链接和隐藏索引改动；
验收打包必须在提交后进行，不使用目录拷贝或排除列表猜测哪些文件可上传。

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

服务单元不再让 systemd 以 root 直接打开日志；`scripts/ops/run_logged_service.py`
在 `User=murmur` 生效后以追加模式打开 `0600` 普通文件，并拒绝符号链接和管道等
非普通文件。这样管理员面板与 `su murmur murmur` 的 logrotate 规则使用同一权限边界。

从旧单元首次更新到这套日志入口前，若实查 App API/Worker 日志仍为 `root:root`，
需另行取得生产授权后先原地修正所有权；`chown` 不截断内容，运行中的文件描述符继续有效：

```bash
sudo chown murmur:murmur \
  /opt/murmur/logs/murmur_app_api.log \
  /opt/murmur/logs/murmur_app_worker.log
sudo chmod 0600 \
  /opt/murmur/logs/murmur_app_api.log \
  /opt/murmur/logs/murmur_app_worker.log
sudo -u murmur test -r /opt/murmur/logs/murmur_app_api.log
sudo -u murmur test -w /opt/murmur/logs/murmur_app_worker.log
```

不要把提权 `chown` 放进服务的 `ExecStartPre`：日志目录由应用用户持有，root 跟随其中
路径会扩大符号链接攻击面。日志目录缺失时，更新器通过 unit 的精确
`ReadWritePaths` 预检失败，不安装一个启动后才报错的单元。

## 模型降级备案

- 主模型（`MURMUR_MODEL`）抛网关错误（503/连接失败等）或吐不出 JSON 时，
  Worker 自动换 `MURMUR_FALLBACK_MODEL`（默认 `deepseek-v4-flash`）重试一次；
  降级调用不带 `response_format`（deepseek 系不支持 json_schema）。
- `MURMUR_JSON_SCHEMA=0` 可让主模型也走「不带 response_format」模式——
  glm / deepseek 系传 json_schema 会 400 或把 token 全烧进思考，关掉后靠
  SYSTEM 提示词约束输出，实测 glm-5.3 合规。qwen/kimi 系保持 1。
- 带图消息直接走 `MURMUR_IMAGE_MODEL`（默认 `mimo-v2.5`，多模态）；它同样
  不支持 json_schema。mimo 也挂掉时退回 `MURMUR_FALLBACK_MODEL`，**不带图**
  纯文本重试——EXIF/时间/文字仍会进上下文，回复质量下降但不会断。
- 选主模型时避开与降级模型同一厂商线路（deepseek 主 + deepseek 降级没有
  隔离意义）；glm-5.3 主 + deepseek-v4-flash 降级是 2026-08-17 Kimi 上游
  中断后的生产组合。
- 每一次降级都会在 `murmur_app_worker.log` 打一条
  `模型 <name> 失败（<错误类型>），尝试降级`；巡检日志看到成片出现就说明
  主模型上游出问题了（2026-08-16 Qwen、2026-08-17 Kimi 两次上游中断即前例）。
- 模型偶尔无视 json_schema 直接吐短句气泡（kimi-k2.6 在旧网关上
  实测约 1/3 概率把思考写进正文）——内容是对的就不会丢：无花括号且
  ≤150 字、每行 ≤60 字的输出会按行收下当气泡（日志记
  `模型没按 JSON 返回，抢救出 N 条气泡`），只有更长或带花括号的垃圾
  才会交给降级模型。
- 换模型只改 `.env` 三项然后重启 `murmur-app-worker murmur-app-api`，
  代码默认值已覆盖，不写这三行也按上面默认行为跑。

- `.env`、Apple 密钥、SQLite、日志、dossier、照片预览和测试环境文件都不得进 Git。
- `/opt/murmur/murmur.db` 使用 WAL；备份用
  `sqlite3 murmur.db ".backup backup.db"`，不要直接复制单个数据库文件。
- Caddy 只代理 8766；8765 不开放公网。服务日志不得打印图片、正文、邀请码、assertion
  或 APNs token。
- App API/Worker 使用 `UMask=0077`、`ProtectSystem=strict` 和
  `ReadWritePaths=/opt/murmur`；测试 Bot 只能写 `/opt/murmur/test`。
