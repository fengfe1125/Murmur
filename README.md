# Murmur

发一张图，它回你一句戳中的话。

**正式入口是 iOS App。** Telegram、钉钉、微信和 QQ 只保留为隔离测试通道，
不会因为仓库或服务器里存在旧凭据而自动启动。App 底部三个入口：聊天、当年今日、
我的。聊天记录留在本机，冷启动会读回来；当年今日的照片房间写进一份**单独的存档**，
按天归档，日历是它的读法，和聊天互不掺和。Murmur 的私有记忆仍留在服务端，
用于保持人格和上下文——两个模块共用同一份记忆，服务端不存聊天记录，也不提供历史
查询接口。

```
你 ▸ [一张等电梯的照片]
    18:47 周三

它 ▸ 是在回家的路上吗？今天怎么样
```

同一张照片，换个时间：

```
你 ▸ [同一张等电梯的照片]
    23:38 周四

它 ▸ 这个点还在12楼啊，今天又加班到这么晚？
```

```
你 ▸ [同一张等电梯的照片]
    10:05 周日

它 ▸ 周日出门了
```

**不需要你配置任何东西。** 场景（写字楼电梯间）是它从画面里看出来的，
时间来自 EXIF 或消息时间。画面告诉它"是什么"，时间告诉它"意味着什么"。

上面三句都是实跑出来的，不是设计稿。

---

## 跑起来

```bash
uv venv && uv pip install -e .
cp .env.example .env      # 填模型与 App API 配置；不要在这里开启测试 Bot
```

### 交互式初始化（推荐）

不想手动逐项填 `.env`，运行：

```bash
./scripts/setup-murmur.sh
```

向导会检查 Python 3.11+ / `venv`、创建 `.venv` 并安装依赖，然后用隐藏输入配置
DeepSeek（或其他 OpenAI 兼容网关）的 API Key。正式配置写入 `.env`；如果选择旧平台，
它们的测试凭据会单独写入被 Git 忽略的 `.env.test-bots`，数据库和日志写入 `./test/`。
向导不会启动服务或开放端口。

后台分成两个正式进程：

```bash
murmur app-worker
murmur app-api --host 127.0.0.1 --port 8766
```

管理员用 `murmur app-invite` 创建 7 天有效的新用户邀请码；已有用户新增设备时，先用
`murmur app-users` 找到 user ID，再用 `murmur app-device-code USER_ID` 创建 30 分钟有效的
一次性设备码。代码只在终端显示，数据库只保存哈希。

默认 App Attest 模式是 `production`，缺 Team ID、App ID 或 HTTPS 时会拒绝启动。
当前没有 Apple Developer 账号时，按 [`deploy/README.md`](deploy/README.md) 使用显式、
仅限本地的 development token；该绕过不能进入 Release 或生产环境。

各平台要准备的东西：Telegram 的 BotFather Token（首次 `/start` 后补 Chat ID）、钉钉
企业内部应用的 Client ID / Client Secret、QQ 官方机器人的 AppID / AppSecret；个人微信
需要安装 OpenClaw + Node.js 22+，向导会引导扫码，授权凭据保留在 `~/.openclaw`，不进
正式 `.env`。

### 迁移到 VPS（代码、凭据与记忆）

准备替换电脑上正在运行的服务时，使用迁移脚本。它先把代码和依赖在远端测试通过；只有
输入 `CUTOVER` 后，才停止本机 Murmur、制作 SQLite 一致性快照、传输 `.env`、
`murmur.db`、`dossiers/` 与照片预览，并启动 VPS 上的 App API、Worker 和只读看板。
脚本不会根据旧平台凭据 enable 任何 Bot。

Google Cloud Compute Engine：

```bash
./scripts/migrate-to-vps.sh \
  --gcloud instance-20260516-162140 \
  --zone us-west1-b \
  --project project-6af82f13-3757-4375-824
```

普通 SSH 主机则使用 `--ssh user@host`。脚本需要本机已登录对应的 SSH 或 gcloud 账号；
迁移过程不显示任何密钥。若电脑上的 Murmur 已手动停止，加 `--local-stopped` 后仍需输入
`CUTOVER`，才会启动远端正式 App 服务。

迁移完成并推送代码到私有 GitHub 仓库后，可用 `scripts/link-vps-to-github.sh` 给 VPS 设置
只读 Deploy Key。以后在 VPS 执行 `sudo systemctl start murmur-update`，就会拉取、测试并重启
已启用的服务，且不会覆盖 `.env` 或聊天记忆。

### 先在命令行试

```bash
murmur reply ~/Photos/IMG_4821.HEIC -v
murmur reply ~/Photos/IMG_4821.HEIC --note "今天被骂了"
murmur reply ~/Photos/IMG_4821.HEIC --model mimo-v2.5   # 换模型对比
murmur reply ~/Photos/IMG_4821.HEIC --dry-run           # 只看 prompt，不花钱
murmur log
```

### 旧平台测试通道

四个平台不是正式产品入口。启动条件必须同时满足：

```dotenv
# .env.test-bots
MURMUR_CHANNEL_MODE=transition
MURMUR_ENABLE_TEST_BOTS=1
MURMUR_AUTO_ENROLL=0
MURMUR_DB=./test/murmur.db
MURMUR_LOGDIR=./test/logs
```

设置为 `MURMUR_CHANNEL_MODE=app_only` 后，即使误留
`MURMUR_ENABLE_TEST_BOTS=1`，所有平台命令也会拒绝启动。配置值拼错同样会失败关闭。

### 测试 Telegram

1. 找 [@BotFather](https://t.me/BotFather) 发 `/newbot`，拿到 token 填进 `.env.test-bots`
2. `.venv/bin/dotenv -f .env.test-bots run -- .venv/bin/murmur bot`
3. 对 bot 发 `/start`，它会告诉你 chat id
4. 把 chat id 填进 `.env.test-bots` 的 `MURMUR_ALLOWED_CHAT_IDS`，重启

**`MURMUR_AUTO_ENROLL=0` 时，白名单为空会拒绝所有人的消息（只保留
`/start` 用来回显 chat id）；必须先填入白名单才会调用模型。**

bot 支持：

| 你做的 | 它做的 |
|---|---|
| 发图（普通方式） | 用消息时间当此刻。Telegram 会压缩掉 EXIF |
| 发图（选"文件"发原图） | EXIF 保留，拍摄时间和定位都在，效果最好 |
| 发图带文字说明 | 优先回你的话，图当背景 |
| 它说完你回一句 | 记进数据库，是调人格最有用的信号 |
| `/log` | 看最近十条 |

### 测试微信

微信 2026 年 3 月才开放官方 Bot API（腾讯 ClawBot，走 iLink 协议）。
**是扫码授权个人微信，不是逆向网页版**，所以不封号——在这之前，个人微信没有能用的合法路子。

登录借用腾讯官方的 OpenClaw 插件做一次，之后收发由 Murmur 自己接管：

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw channels login --channel openclaw-weixin        # 扫码
openclaw config set plugins.entries.openclaw-weixin.enabled false
openclaw gateway restart
.venv/bin/dotenv -f .env.test-bots run -- .venv/bin/murmur wechat
```

**第三步不能省。** `getUpdates` 是带游标的长轮询，一个微信号只能有一个进程在轮——
不关掉插件的话，OpenClaw 网关和 Murmur 会互相把对方的消息取走。

### 测试 QQ

官方 QQ 机器人是**独立账号**，不是扫码挂个人 QQ。去 [q.qq.com](https://q.qq.com/) 创建一个，把 AppID / AppSecret 填进 `.env.test-bots`：

```bash
# .env.test-bots
QQ_APP_ID=...
QQ_CLIENT_SECRET=...

.venv/bin/dotenv -f .env.test-bots run -- .venv/bin/murmur qq
```

然后打开手机 QQ，在消息列表里找到这个机器人，发一句。第一次会在日志里看到
被拒绝的 openId；把它填进 `.env.test-bots` 的 `QQ_ALLOWED_USERS` 并重启后才会回复。

未过审的机器人只在沙箱里收得到消息，这时加 `QQ_SANDBOX=1`，并在开放平台里把自己加成沙箱用户。

和另外三个平台的差别：

| | Telegram | 钉钉 | 微信 | QQ |
|---|---|---|---|---|
| 收图 | ✅ | ✅ | ✅ | ✅ |
| 群聊 | ✅ | ✅ | ❌ 通道只支持单聊 | ✅ 需 @ |
| "正在输入" | ✅ | ❌ 只能靠停顿 | ✅ | ❌ |
| 发送限速 | 宽松 | 宽松 | **约 7 条 / 5 分钟**（服务端，账号级共享） | 宽松 |
| 主动找你 | ✅ | ✅ | ⚠️ 官方不支持，默认关 | ⚠️ 对方好久没说话时可能被拦 |

最后一行是微信这条路唯一的硬伤：`sendMessage` 要带上收消息时给的 `context_token`，
官方文档写明"回复时原样带回"——这个接口是为被动响应设计的。
主动发只能复用上次存下来的 token，属于官方没承诺的用法。所以 `WECHAT_INITIATIVE` 默认关闭，
**主动消息优先走 Telegram 和钉钉**，微信当"你发我回"的那一半用。

---

## 看板

```bash
murmur web            # 打开 http://127.0.0.1:8765
```

一个只读的网页，随时能看到它现在是什么状态：

- **四个进程**：Telegram / 钉钉 / 微信 / QQ 各自跑没跑、PID、跑了多久、内存、
  24 小时内被看门狗重启过几次、日志尾巴（token 已经擦掉）。看板不会把
  “进程还在”当成“一切正常”：重复进程、24 小时内重启 3 次以上、或日志最后一行
  是错误，都会标成「需要留意」。
  端口那一栏通常是空的——三个 bot 都是主动连出去的（长轮询 / websocket），
  谁都不监听端口，所以顺带把出站连接也列出来，免得看起来像挂了。
- **DeepSeek 余额**：账上还剩多少、其中赠送和充值各多少。
  官方接口只给"此刻还剩多少"，所以每 5 分钟采一个点存进
  `balance_snapshots` 表，「余额变化」那一页画的就是这些点。
  **方向和从前的额度百分比是反的**：曲线往下走是在花钱，跌到 0 才是要出事，
  所以看板上是少了才红。采集挂在常驻的 app-worker 上（`murmur/balance.py`），
  看板单跑时也会自己采一份，同值去重保证两边同时跑不写重复行——
  面板开不开都有数据。
- **每个人一个聊天窗口**：左边**按人**排，不是按会话。
  Murmur 内部的划分单位是 `(平台, 会话, 发送人)`——同一个人的单聊、
  每个群、换了组织的钉钉 id 各算一条，实测一个人能散成 4 条。
  按会话列的话名单上会出现四个一模一样的名字，认不出谁是谁，
  所以窗口按人合并，几条会话的消息按时间穿在一起，**换会话的地方画一条分界线**。
  它主动开口的那几条标着意图，选择沉默的那次也画出来。
  每条下面灰字是它当时"看到的画面"——排查"它为什么这么说"最快的入口。
  左栏可以按平台筛选、搜索昵称 / ID / 最近消息；每个人还会显示今日主动次数与
  连续未回复数。达到机器人本身的 4 条未回复冷却阈值时，标为「已冷却」——
  这是只读提醒，不会从看板改变发消息策略。
- **记忆文档**：那个人的每一份 `dossiers/*.md`——三个分区、各用了多少字、
  上次整理是什么时候、还有几条没消化。
  一个人有好几条会话就有好几份记忆：**它在群里认识的那个"他"和单聊里的
  不是同一份记忆**，窗口合并了，记忆没有。这一点面板上写明了。
- **所在 app / 正在用的模型 / 数据库位置**都在总览页。总览页还有一块
  「回复质量 · 近 14 天」：每次回复 / 主动消息 / 记忆整理的**分类计数**
  （完整输出、截断抢救、选择安静、可重试失败……，见 `murmur/counters.py`）——
  只有分类名和次数，不含任何正文，是回复质量生产验收的数据源。

侧栏的「VPS 面板」（http://127.0.0.1:8765/vps ）是生产 VPS 的管理页：
四个服务 + Caddy 的运行状态、负载 / 内存 / 磁盘、两个服务日志的尾巴，
以及**自己创建邀请码**（别名、有效天数、是否不限次数；明文码只在创建的
这一次响应里出现）。它通过本机的 gcloud / ssh 凭据在 VPS 上执行只读命令和
`app-invite` CLI，**VPS 上没有任何新端口或新接口**。gcloud 实例默认就是
现网那台（`MURMUR_VPS_GCLOUD_INSTANCE` / `MURMUR_VPS_ZONE` / `MURMUR_VPS_PROJECT`
可改），普通 SSH 主机用 `murmur web --vps-ssh user@host`。
`gcloud compute ssh` 每次调用要一分钟上下，所以启动时会用 `instances describe`
解析出外网 IP，之后全部走直连 SSH（`~/.ssh/google_compute_engine`）。
在 Windows 电脑上用同一套面板（双击 `.bat` 打开）：见
[docs/win-vps-panel.md](docs/win-vps-panel.md)。

看板支持手机窄屏：侧栏会收成顶部的联系人区，不会挤掉聊天和总览内容。

只读：这个进程不发消息、不改记忆、不碰 `.env`。唯一写的就是额度快照那张表。
默认只绑 `127.0.0.1`——面板上是聊天原文和记忆文件，不该因为连了个 wifi 就暴露在局域网里。
真要在别的机器上看，`--host 0.0.0.0`，并且**先在 `.env` 里设 `MURMUR_WEB_TOKEN`**：
设了之后所有页面和接口都要求 Header `X-Murmur-Token`（或 URL 加 `?token=`），
URL 里的 token 也会自动带到页面后续 API 和照片请求。没设就一切照旧。日志尾巴从 `MURMUR_LOGDIR`（默认 db 旁边的 `logs/`）读，
和 `run.sh` 写日志用的是同一个目录。

---

## 模型

[DeepSeek](https://platform.deepseek.com) 直连（OpenAI 兼容），
默认 `https://api.deepseek.com/beta`。

> **注意是 `/beta` 不是 `/v1`。** 把回复首字符钉成 `{` 的 assistant prefix
> 只在 beta 端点上有，而这是让 deepseek 老实吐 JSON 的唯一可靠办法——
> 它不吃提示词里的「只返回 JSON」（实测十次有九次直接回聊天正文）。
> `MURMUR_BASE_URL` / `MURMUR_JSON_PREFIX` 是配套的，别只改一个。
> 余额接口不在这个路径下，在 API 根上（`/user/balance`）。

| 配置项 | 默认值 | 为什么 |
|---|---|---|
| `MURMUR_MODEL` | `deepseek-v4-flash` | 中文自然、便宜、不把思考过程写进正文 |
| `MURMUR_IMAGE_MODEL` | `deepseek-v4-flash-vision-exp` | 读图 2 秒级，JSON 纪律好 |
| `MURMUR_JSON_SCHEMA` | `0` | 传 `response_format` 要么 400，要么把 token 全烧进思考 |
| `MURMUR_JSON_PREFIX` | `1` | 配套 `/beta`，靠 prefix 而不是提示词约束 JSON |

换模型改 `.env` 的 `MURMUR_MODEL`，或者 `murmur reply xx.jpg --model xxx`
临时试。换成别家网关（qwen / kimi 系吃 json_schema）时，记得连
`MURMUR_JSON_SCHEMA` / `MURMUR_JSON_PREFIX` 一起调回去。

**模型降级备案**（2026-08-16 网关上游整体 503 之后加的）：

- 主模型抛网关错误或吐不出 JSON → 自动换 `MURMUR_FALLBACK_MODEL` 重试一次，
  降级调用不带 json_schema。**默认空 = 就一家，同一个模型再来一次**——
  线上失败几乎全是一次性的（网关 5xx，或者这一次没按 JSON 写）。
  配了 `MURMUR_FALLBACK_BASE_URL` / `MURMUR_FALLBACK_API_KEY` 后第二次尝试
  会打到第二家——网关整体挂掉时「同一家换个模型」不算降级，建议配上。
  这两个**要么都配、要么都留空**：只配 URL 会拿主网关的 key 去打第二家的
  域名，只配 key 会把第二家的 key 发给主网关，所以配了一半会直接拒绝启动。
- 带图消息走 `MURMUR_IMAGE_MODEL`（多模态）；它也挂掉时退回降级模型、
  **不带图**纯文本重试，消息不会断。
- 每次降级都会在日志打 `模型 <name> 失败（<错误类型>），尝试降级`。

`minimax-m3` 实测会把 `<think>` 标签写进正文，不建议用（解析器能剥掉，但浪费 token）。

两处已经做了成本分级，不用换主模型：

- **记忆整理**走 `MURMUR_MEMORY_MODEL`（默认跟随主模型）。整理是"把新对话合并进
  三块摘要"，调用量大但不需要对话能力，`mimo-v2.5` 完全够。
- **图片分档**：普通照片（竖拍/横拍/方形）长边压到 1024，竖屏截图和 16:9 宽幅
  给 1568——截图小字压太狠会糊成灰条，这块的 token 不能省。

---

## 结构

```
murmur/
  persona.py   ← 产品本身。人格提示词 + 输出结构
  photo.py     EXIF 解析 + 压缩编码（支持 HEIC / bytes 输入，照片/截图分档）
  moment.py    照片 → "此刻"（时段/星期/工作日）+ 匿名地点指纹
  memory.py    SQLite：按 chat 隔离、最近 N 条、同地点同时段来过几次
  engine.py    组装上下文 → 调网关 → 解析（能剥代码块和 <think>）
  app_api.py   正式 App HTTPS API、SSE 与请求校验
  app_worker.py 持久化 moment 作业、Murmur 调用与主动推送
  app_auth.py  邀请、App Attest / assertion 验证边界
  app_store.py App 身份、设备、偏好、作业与短期事件表
  app_push.py  APNs provider 与失效 token 清理
  app_settings.py App API/Worker 的集中安全配置
  bot.py       Telegram 测试通道
  dingtalk.py  钉钉测试通道
  wechat.py    微信测试通道
  qq.py        QQ 测试通道
  web.py       只读看板：进程 / 端口 / 余额曲线 / 每人的聊天窗口和记忆
               （配 MURMUR_WEB_TOKEN 后支持开放访问）
  balance.py   DeepSeek 余额采样与快照（app-worker 与看板共用一个 poller）
  counters.py  无正文的回复质量分类计数（daily_counters 表）
  vps_panel.py VPS 面板后端：经 gcloud/ssh 查服务状态、建邀请码
  webui/       看板的前端，单个 HTML，没有构建步骤
  cli.py       app-api / app-worker / 本地诊断 / 测试 Bot / web
```

**一条主干 `main`，三摊代码靠目录分家，sparse-checkout 保证服务器只拿到自己那份：**

- **服务端（会推到 VPS）**：`murmur/`、`deploy/`、`tests/`、`scripts/` 和根目录
  文件（`pyproject.toml` 等）。VPS 用 cone 模式 sparse-checkout 只拉这四个目录。
  `scripts/` 在列不是顺手带的：`tests/test_channel_gate.py` 和环境清洗测试要读
  它里面的脚本，不给就跑不完更新自带的测试步骤，更新会回滚。
- **iOS（不进 VPS）**：`MurmurApp/`、`MurmurApp.xcodeproj/`。
- **安卓（不进 VPS）**：`android/`。
- `brand/`、`docs/` 同样只留在开发机。

`deploy/murmur-update` 和 `scripts/link-vps-to-github.sh` 都会强制应用这套
sparse 规则，旧全量检出的 VPS 在下次更新时会自动把 App 目录从磁盘清掉。

**分支只有 `main` 一条。** 平台之间靠上面的目录边界区分，不靠分支——服务端契约
是三端共用的，拆成三条长期分支只会让同一个 API 改动要合三次。`murmur-update`
默认就拉 `main`（`MURMUR_GIT_BRANCH` 可以覆盖，但正常情况下不该设）。曾经有过
一条和 `main` 逐字相同的 `vps` 分支，它唯一的作用是让「线上到底跑的哪条」变含糊，
已经删掉。

参考的开源项目见 [CREDITS.md](CREDITS.md)。

---

## 四个设计决定

**1. 回应力度是显式字段，不是自由发挥。**
模型每次返回 `move`: `speak` / `brief` / `quiet`。不这么做的话它永远都会说点什么，
而真朋友不会对你发的每张图都发表看法。**`quiet` 时 bot 就是真的不发消息**——
不发省略号，不发表情，什么都不发。这是设计，不是 bug。

**2. 地点让模型自己看，不让用户填坐标。**
写字楼电梯间、地铁车厢、便利店的灯、自家沙发，这些画面本身就带信息，
比逆地理编码给你的街道名有用得多，而且零配置。

**3. 定位只用来数次数，永远不解析成地名。**
有 GPS 时四舍五入到约 110 米的格子当作匿名指纹，只回答"他在同一个地方来过几次"。
坐标不出网，地名从不生成——提示词里还明确要求它**不要点破**，
"你又在这个地方了"听着像被跟踪。

**4. 提示词里篇幅最大的一节是"千万别这样"。**
同样一张等电梯的图，回"是在回家的路上吗"是暖的；回"看起来你今天很疲惫呢，
要记得爱自己哦 💕"就是会让人立刻卸载的。模型天然倾向于后者。
[`persona.py`](murmur/persona.py) 里"千万别这样"那一节是这个产品的生死线。

---

## 接下来

1. **自己用两周。** 每天发几张真照片，把 `persona.py` 调到自己看了会心一笑。
   `entries.reply` 里存着你每次的回应，那是最好的调优依据。
2. **连发合并（做了一半）。** 文字连发和多图连发都会等到静默期结束只回一次，
   但连发的多张图目前只把最新那张给模型，前面几张被丢掉——
   应该把同一批的几张图一起塞进上下文，让它像朋友一样一次接住。
3. **成本分级（做了一半）。** 记忆整理已走便宜模型、图片已按比例分档；
   剩下的是"先用便宜模型判断这张值不值得深聊，再决定要不要上好模型"。
   注意「三个方向」不在这条线上：它现在是 App 端 当年今日 照片房间的开场
   （`intent=photo_reading` 的 moment，见 `engine.read_photo`），必须真的看见图，
   所以只走 `MURMUR_IMAGE_MODEL`，没有纯文字降级档。
4. **主动性。** App 用户默认每天最多 3 条，窗口 08:30–22:30；测试 Bot 仍走原来的 10–15 条排程。

## 测试

`tests/` 里是自包含脚本，不依赖 pytest，直接用项目 venv 跑：

```bash
.venv/bin/python scripts/run_tests.py                    # 全部，跑完汇总失败名单
.venv/bin/python tests/test_web.py                      # 单个：看板逻辑（脱敏/去重/窗口合并）
.venv/bin/python scripts/run_tests.py --fail-fast       # 第一个失败就停
```

每个脚本结尾打一行 `通过 N，失败 M`，退出码非零就是有失败。
注意 `python -m unittest discover tests` **跑不了**：一半测试是脚本式的，
import 的那一刻就把自己跑完再 `sys.exit()`，discover 会把它们整批报成
import error。逐个当脚本跑是唯一对两种风格都成立的方式。
改 `Config` 加字段时，`tests/_helpers.py` 会提醒补测试默认值，不用手查。
lint 用 ruff（`uv pip install -e '.[dev]'` 后 `ruff check murmur tests scripts`）。

CI 按三摊代码分开：`.github/workflows/server.yml` 在 Python 3.11（pyproject
声明的下限）和 3.14（VPS 实际在跑的）上跑 lint + 全量测试，外加一道
`bash -n`；`ios.yml` 只在 iOS 目录变了才起 Xcode；`android.yml`
同理只在安卓目录变了才编一次 debug 包（安卓还没有单元测试，有了再补进同一 job）。

## 隐私

照片和记录都在本地（`./murmur.db`，已 gitignore）。
每次调用发给模型网关的是**压缩后的图 + 一段文字上下文**——
不含 GPS 坐标、不含地名，只有时间和"在同一个地方来过 N 次"这种计数。
原图不上传、不留存。
