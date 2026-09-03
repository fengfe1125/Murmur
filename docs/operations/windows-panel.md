# Windows 管理面板

> 状态：现行规范｜适用：管理员本机｜核验：2026-09-03｜依据：新 VPS 连接迁移；未进行 Windows 实机验收

先安装 Python 和项目依赖，在仓库根目录保留 `.venv`。按 [VPS 连接](vps-connection.md) 配置 `murmur-new-vps` SSH 别名和公钥；不再需要 Google Cloud CLI 或 IAP 隧道。
运行 `scripts/ops/start-vps-panel.bat` 可启动本机面板。启动器默认使用 `murmur-new-vps`，需要临时覆盖时设置 `MURMUR_VPS_SSH`。

面板默认绑定 `127.0.0.1:8765`，不在 VPS 新开管理端口。查状态是读取；创建邀请码是生产写操作，需要授权。
余额快照也会写本机数据库；不要把面板描述成完全只读。

仓库不保存真实 SSH 账号、私钥、访问 token 或新生成的邀请码。检查日志时先脱敏。
