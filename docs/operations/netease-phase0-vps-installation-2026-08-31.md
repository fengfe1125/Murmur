# 网易云“一起听”Phase 0 VPS 隔离安装记录

> 状态：现状记录｜适用：当前个人 PoC 隔离环境｜核验：2026-09-01｜依据：用户授权的当前 VPS 隔离测试、本分支工具与远端回执；不构成生产部署授权

## 已安装内容

- 专用系统用户 `murmur-netease-poc`，登录 shell 为 `nologin`。
- 根目录 `/opt/murmur-netease-poc`，生产 `/opt/murmur` 未读取、未修改、未重启。
- `app/`：仓库 `scripts/ops/netease-phase0/` 的实验工具。
- `runtime/node/`：Node.js `22.23.1` Linux x64，经官方 SHA-256 清单验证。
- `vendor/api-enhanced/`：固定提交
  `f5ce55bcb46e29c8e5350ca796fb1cc9d9914acd`，生产依赖按 `pnpm-lock.yaml` 安装且禁用安装脚本。
- `secrets/bot-session.json`：权限 `0600`；目录权限 `0700`。
- `work/run/`：权限 `0700`，只保存 PID、随机回环页面地址和脱敏观察报告。

没有新增 systemd 单元、防火墙规则、反向代理、数据库或生产环境变量。实验 HTTP 服务只监听
`127.0.0.1:18763`，访问需要已有 SSH 权限和随机能力路径。

## 当前运行状态

实验进程由专用账号临时后台运行，标准输出和错误输出不落盘。两小时观察进程每 30 秒读取一次
公开房间状态，234 次检查全部成功。50 次控制报告与观察报告权限均为 `0600`，不含账号、用户、
歌曲、房间或邀请标识。隔离故障测试结束后上游故障开关已恢复关闭，残留房间经启动清理结束。

## 停用与清理

立即停用只需要向专用账号的 Node 进程发送 `SIGTERM`。房间进程会尽力调用远端结束接口，
随后移除 ready/PID 文件。这个动作不涉及 `murmur-app-worker`。

撤销机器人会话时只删除
`/opt/murmur-netease-poc/secrets/bot-session.json`，并在网易云账号安全页面撤销对应会话。
删除整个隔离环境属于另一次破坏性操作，必须重新取得明确授权；不能把 `/opt/murmur` 或其他
宽泛路径作为清理目标。
