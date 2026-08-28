# Murmur

> 状态：现行规范｜适用：整个项目｜核验：2026-08-28｜依据：产品重定位决策、PR #14

以照片和讲述为入口的私人回忆与日记应用。AI 帮助你回看、表达和整理，由你决定哪些内容正式留存。

目前仍在邀请制小范围开发：**iOS 是体验主线，Android 逐步跟进**。新的日记成稿和风格选择尚未实现，不能把现有聊天或照片房间存档当成已确认的日记。

## 现在能做什么

- iOS：发送照片和文字、流式回应、失败重试；聊天记录在本机保留，冷启动恢复。
- 当年今日：从本机相册寻找旧照片，进入独立照片房间；房间按天存档，可从旧日期继续聊，与聊天记录分开。
- 共享后端：邀请与设备身份、作业处理、私有记忆、主动消息和分类指标。没有面向客户端的聊天历史查询接口。
- Android：已有 Debug 客户端与编译检查；正式设备认证和推送仍需后续联调。
- 现有固定人格继续运行，但不再是唯一产品承诺。用户可选风格、AI 辅助成稿、用户确认留存均为后续开发。

[当前能力与验证边界](docs/product/current-state.md) · [后续路线](docs/product/roadmap.md) · [术语](CONTEXT.md)

## 开发入口

在仓库根目录执行：

```sh
uv venv
uv pip install -e '.[dev]'
cp .env.example .env
# 填写开发环境配置；不要把生产密钥提交到 Git。
.venv/bin/python scripts/run_tests.py
```

按需运行 `murmur app-worker` 和 `murmur app-api --host 127.0.0.1 --port 8766`。
正式设备验证默认失败关闭；开发 token 只用于明确隔离的 Debug 开发环境，不能进入 Release。

- [iOS 开发](MurmurApp/README.md)
- [Android 开发](android/README.md)
- [服务端与测试](docs/development/server.md)
- [部署与运维](docs/operations/deployment.md)

## 仓库与协作

一条长期主干 `main`，各平台按目录分工，不按长期分支分家。
[仓库地图](docs/development/repository-layout.md) 记录当前路径与目标路径；
[协作规范](docs/development/workflow.md) 规定分支、文档状态及合并验收。

Telegram、钉钉、微信、QQ 仅保留为隔离测试通道，不是正式产品入口。
运维面板属于管理员工具：它会写余额快照，也能通过授权的 SSH 创建邀请码，不应统称“完全只读”。

## 数据边界

聊天记录与照片房间存档保存在设备；服务端也会处理上传、短期事件并保留用于上下文的私有记忆与压缩预览。
因此本项目**不是所有数据只在手机、本地离线推理或端到端加密产品**。
原图可临时上传至服务端处理，完成后清理；传给模型的是压缩图与必要文字上下文。

详见 [数据生命周期](docs/architecture/data-lifecycle.md)。[历史资料](docs/archive/README.md) 不代表当前需求。
参考与来源见 [CREDITS.md](CREDITS.md)。
