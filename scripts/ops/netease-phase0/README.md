# 网易云“一起听”Phase 0 隔离工具

> 状态：实验工具｜适用：单用户、专用可丢弃账号、隔离 VPS｜核验：2026-08-31｜依据：本分支 Phase 0 实现与固定参考提交；不代表网易云已授权

这个工具仅用于验证非官方“一起听”协议是否仍可用。它只监听 `127.0.0.1`，必须通过 SSH
隧道访问；不会获取或代理音频，不会把 Cookie 返回浏览器或写入日志。

固定参考实现：`NeteaseCloudMusicApiEnhanced/api-enhanced` 提交
`f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd`（MIT）。参考源码和依赖应安装在独立目录，不作为
Murmur 正式运行依赖。

隔离约束：

- 运行用户为 `murmur-netease-poc`，目录为 `/opt/murmur-netease-poc`。
- 会话只保存在 `/opt/murmur-netease-poc/secrets/bot-session.json`，目录 `0700`、文件 `0600`。
- 不读取 `/opt/murmur`、正式 `.env`、数据库、日志、systemd 配置或更新器状态。
- 不开放公网端口，不安装 systemd 服务，不在测试账号上绕过验证码或风控。
- 任何验证码、异常登录或封禁提示都立即停止实验并撤销会话。

启动命令（由隔离用户执行）：

```sh
/opt/murmur-netease-poc/runtime/node/bin/node \
  /opt/murmur-netease-poc/app/server.js \
  --api-root /opt/murmur-netease-poc/vendor/api-enhanced \
  --state-dir /opt/murmur-netease-poc/secrets \
  --runtime-dir /opt/murmur-netease-poc/work/run \
  --host 127.0.0.1 \
  --port 18763
```

运行环境应设置 `DOTENV_CONFIG_QUIET=true`，避免第三方依赖输出无关启动信息。指定
`--runtime-dir` 后，PID 和随机本地页面地址分别写入权限 `0600` 的 `phase0.pid` 与
`phase0-ready.txt`，进程退出时自动移除。使用 SSH 将本机 `18763` 转发到 VPS
`127.0.0.1:18763`，再打开该 URL。二维码由服务端生成；扫码成功后页面只显示“登录成功”。

本地单元测试：

```sh
node --test scripts/ops/netease-phase0/phase0.test.js
```

`inspect-status.js` 只用于 Phase 0 协议字段研究。它自动遮蔽 Cookie、token、账号、用户和
房间标识，只保留播放状态、歌曲、进度、序号等白名单值；不得把原始上游响应写入日志。

`check-playable.js` 只返回候选歌曲的公开元数据与可播放布尔值。播放器 URL 不跨越第三方
协议层，不写入标准输出、日志、磁盘、浏览器或 Murmur 数据。

`soak.js` 通过回环页面每 30 秒检查一次房间，只记录成功/失败计数和时间。报告不含歌曲、
房间、用户或账号标识，默认运行两小时，结束后不会自动关闭房间。
