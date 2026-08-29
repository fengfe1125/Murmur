# 仓库地图与路径迁移

> 状态：现行规范｜适用：当前迁移分支的源码、构建与运维｜核验：2026-08-28｜依据：PR #15、#16 与目录迁移提交；未宣称部署

本分支已采用目标布局；旧路径列仅作历史对照。包内部和客户端工程内部不重构。
逐文件映射见 [path-mapping.tsv](path-mapping.tsv)，原有测试清单见 [test-baseline.txt](test-baseline.txt)。

| 旧路径 | 目标路径 | 职责 |
|---|---|---|
| `MurmurApp/`、`MurmurApp.xcodeproj/` | `apps/ios/` 下同名目录 | iOS 主体验 |
| `android/` | `apps/android/` | 跟进客户端 |
| `murmur/`、`tests/`、`pyproject.toml` | `server/` 下同名路径 | 共享 Python 包、测试、依赖 |
| `deploy/` | `infra/deploy/` | 更新器、systemd 与代理示例 |
| `scripts/setup-murmur.sh`、`scripts/dev-app.sh` | `scripts/dev/` 下同名脚本 | 本地开发 |
| VPS 迁移、GitHub 连接、Windows 面板、环境清洗脚本 | `scripts/ops/` | 运维工具 |
| `run.sh` | `scripts/ops/run-test-bots.sh` | 本机测试 Bot 监督器 |
| 测试 runner 与回复质量评估脚本 | `scripts/check/` | 验收与评估 |
| `brand/` | `assets/brand/` | 品牌素材 |
| 旧设计、适配计划、调研与交接 | `docs/archive/` | 历史依据，不是当前待办 |

根目录保留 README、AGENTS、术语、来源记录、GitHub 配置、环境示例与忽略规则。
当前代码位置以 Git 文件列表为准，历史路径表不代表两份代码同时维护。

## 源码不等于运行数据

根目录 `.env`、`.venv`、数据库、照片、dossier、日志和私钥不纳入源码迁移。
客户端的被忽略本地配置与工程一起保全，保持内容不变；不会生成替代凭据。
VPS 的 sparse-checkout 必须包含全量服务端测试及它们读取的部署和脚本资源。

## 验收

迁移必须提供逐路径映射和迁移前测试清单，保证没有漏掉原有测试。
Python 包名和 CLI、客户端标识、API/SSE、数据格式不变；只有安装与构建路径改变。
