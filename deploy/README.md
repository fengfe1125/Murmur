# 部署到云主机

## 先选对地方（这一步最关键）

三个外部依赖，所在位置不一样：

| 依赖 | 位置 | 国内主机 | 境外主机 |
|---|---|---|---|
| Telegram API | 境外 | ❌ 被墙，必须挂代理 | ✅ 直连 |
| OpenCode 网关 | 境外 | ❌ 需要代理 | ✅ 直连 |
| 钉钉 API | 国内 | ✅ 最快 | ✅ 能用，多几十毫秒 |

**结论：买境外的**，香港 / 新加坡 / 东京都行。三个依赖里两个必须境外直连，
钉钉从境外访问只是慢一点点，完全不影响体感。

买国内主机的话得给 Telegram 和 OpenCode 挂代理——就是你现在笔记本上的情况，
而那个代理正是今天所有断线的根源。别重复这个坑。

## 配置

1 核 1G 足够。这服务是纯 I/O：图片处理 23ms，其余时间都在等模型返回。
磁盘几 G 就行（SQLite + 日志）。

## 步骤

```bash
# —— 服务器上 ——
sudo useradd -r -m -d /opt/murmur -s /bin/bash murmur
sudo apt update && sudo apt install -y python3-venv git

# 传代码（本地执行）
# *.db* 把 murmur.db 连同 -wal/-shm 一起排除：WAL 里是最近的聊天记录
rsync -av --exclude .venv --exclude .git --exclude '*.db*' --exclude logs \
  ~/Murmur/ user@你的服务器:/tmp/murmur/
```

```bash
# —— 服务器上 ——
sudo mv /tmp/murmur/* /opt/murmur/ && sudo chown -R murmur:murmur /opt/murmur
sudo install -d -o murmur -g murmur -m 0750 /opt/murmur/logs
sudo -u murmur bash -c '
  cd /opt/murmur
  python3 -m venv .venv
  .venv/bin/pip install -e .
'

# 不传本机 .env：在服务器上重新输入密钥，旧电脑不必把凭据发到网络上。
sudo -u murmur -H bash -c '
  cd /opt/murmur
  ./scripts/setup-murmur.sh
'
# 向导会用隐藏输入写入 /opt/murmur/.env，且不会启动机器人。

# 装服务
sudo cp /opt/murmur/deploy/*.service /etc/systemd/system/
sudo cp /opt/murmur/deploy/murmur-logrotate /etc/logrotate.d/murmur
sudo systemctl daemon-reload
sudo systemctl enable --now murmur-web

# 只开启已在向导中配置的平台；先确认其状态，再进行切换（示例）。
# sudo systemctl enable --now murmur-telegram
# sudo systemctl enable --now murmur-dingtalk
# sudo systemctl enable --now murmur-wechat
# sudo systemctl enable --now murmur-qq
```

### Google Cloud 浏览器 SSH 上传

浏览器 SSH 的“上传文件”不能传目录。把项目压缩包上传到登录用户的家目录后，在服务器上执行：

```bash
sudo install -d -o murmur -g murmur -m 0750 /opt/murmur
sudo tar -xzf ~/murmur-vps-deploy-YYYYMMDD.tar.gz \
  -C /opt/murmur --strip-components=1 --no-same-owner
sudo chown -R murmur:murmur /opt/murmur
sudo install -d -o murmur -g murmur -m 0750 /opt/murmur/logs
sudo -u murmur -H bash -c 'cd /opt/murmur && ./scripts/setup-murmur.sh'
```

向导只收集模型和平台凭据、安装 Python 依赖与写入 `.env`；不会启动通道。确认
`murmur-web` 正常后，再按一个平台一个平台地从旧电脑切换，避免同一 Token 被两个进程
同时长轮询而重复回复或漏消息。

微信不在服务器上扫码。先在本地完成官方 OpenClaw 登录，再把凭据传过去：

```bash
# 本地执行；服务文件会把 OPENCLAW_STATE_DIR 指到这个位置
ssh user@你的服务器 'sudo install -d -o murmur -g murmur -m 0700 \
  /opt/murmur/openclaw/openclaw-weixin/accounts'
scp ~/.openclaw/openclaw-weixin/accounts/*.json user@你的服务器:/tmp/
ssh user@你的服务器 'sudo mv /tmp/*.json \
  /opt/murmur/openclaw/openclaw-weixin/accounts/ && \
  sudo chown murmur:murmur /opt/murmur/openclaw/openclaw-weixin/accounts/*.json && \
  sudo chmod 600 /opt/murmur/openclaw/openclaw-weixin/accounts/*.json'
```

## 日常操作

```bash
systemctl status murmur-telegram murmur-dingtalk murmur-web  # 看状态
tail -f /opt/murmur/logs/murmur_bot.log                       # 跟 Telegram 日志
tail -n 200 /opt/murmur/logs/murmur_dingtalk.log              # 查钉钉历史
sudo systemctl restart murmur-dingtalk             # 改完 persona.py 后重启
```

### 从 GitHub 更新（推荐）

VPS 首次关联私有 GitHub 仓库时，需要为它配置只读 Deploy Key。关联完成后，更新只要：

```bash
sudo systemctl start murmur-update
sudo systemctl status murmur-update --no-pager
```

更新器只接受 `main` 的快进提交：拉取代码后会重新安装依赖，并运行所有 `tests/test_*.py`。
测试通过才会重启已经启用的平台服务；失败会把代码还原到上一个提交，正在运行的机器人
不会被重启。`.env`、SQLite 数据库、日志、档案与照片均被 Git 忽略，更新不会覆盖它们。

改人格只需要重启，不用重装：

```bash
sudo -u murmur vim /opt/murmur/murmur/persona.py
sudo systemctl restart murmur-telegram murmur-dingtalk
```

## 要注意的

**`.env` 不进 git，也不进 rsync。** 里面是活的 API key。
`chmod 600` + `ProtectSystem=strict` 已经在 service 文件里配好了。

**日志与看板。** 四个 bot 和看板都把日志写进 `/opt/murmur/logs/`，这样
`murmur web` 可以展示脱敏后的日志尾巴和 24 小时重启次数。部署包同时安装
`logrotate`，每天轮转、保留 14 天。看板本身以 `murmur-web.service` 常驻，
默认只监听服务器的 `127.0.0.1:8765`。

从本机看远程看板，优先用 SSH 隧道，不开放 8765：

```bash
ssh -N -L 8765:127.0.0.1:8765 user@你的服务器
# 然后在本机浏览器打开 http://127.0.0.1:8765
```

**数据库。** `/opt/murmur/murmur.db` 是全部记忆。已开 WAL 模式，
两个进程同时写没问题（实测并发 120 次写入零冲突）。
想备份就 `sqlite3 murmur.db ".backup bak.db"`，别直接 cp（WAL 下会拿到不一致的快照）。

**要不要把本地的记忆带过去。** 想接着用就把 `murmur.db` 一起传；
想重新开始就别传，服务会自己建。注意 `thread` 键包含平台和会话 id，
换机器不影响，同一个人还是同一条上下文。

**代理相关。** 境外主机上不需要任何代理配置，
[`dingtalk.py`](../murmur/dingtalk.py) 里那段绕过代理的逻辑会自动空转，无害。

## Docker（可选）

不建议为这个服务上 Docker——两个长驻进程 + 一个 SQLite 文件，
systemd 更轻、日志更顺手、改人格重启更快。
真要容器化的话注意把 `murmur.db` 挂成 volume，别放进镜像层。
