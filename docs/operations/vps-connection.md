# VPS 连接与权限边界

> 状态：现行规范｜适用：已授权的运维人员｜核验：2026-09-03｜依据：2026-09-02 新 VPS 迁移及公网验收

生产域名是 `https://claude.sakuramu.edu.kg`，DNS 指向 `193.106.250.61`。服务器为 Ubuntu 24.04，Murmur 位于 `/opt/murmur`；公网只由 Caddy 暴露，API、Worker 和管理面板使用既有 systemd unit。

## 当前连接

本机 `~/.ssh/config` 的私有配置应等价于：

```sh
Host murmur-new-vps
  HostName 193.106.250.61
  User root
  IdentityFile ~/.ssh/murmur_new_vps
  IdentitiesOnly yes
```

首选连接命令：

```sh
ssh -o BatchMode=yes murmur-new-vps
```

服务器已关闭密码与键盘交互认证，root 仅允许公钥登录。仓库只记录公钥连接位置，不保存密码、私钥内容或其他生产凭据。主机密钥变化时先通过供应商控制台核验指纹，不跳过 SSH 主机验证。

旧 Google Cloud 主机、旧直连密钥和 IAP 流程均已退役；不再从历史文档、终端记录或脚本默认值恢复旧连接。只读检查可查看服务状态和当前提交；日志、用户列表仍可能敏感，展示前脱敏。

## 操作边界

更新代码、重启服务、修改配置、创建邀请码、删除数据都属于生产变更，逐次取得授权。
“查看面板”不隐含创建邀请码；“合并 PR”不隐含部署。
不得为解决连接问题开放新公网管理端口、放松 SSH 验证或把密钥写入文档。

Windows 面板使用说明见 [面板操作](windows-panel.md)。布局迁移见 [切换流程](layout-transition.md)。
