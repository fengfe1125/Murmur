# 仓库重组验收记录

> 状态：现状记录｜适用：2026-08-28 重组交付及合并收口｜核验：2026-08-28｜依据：PR #14–#20；初始迁移 6e46f5a、UI 同步 4838dac、最终迁移验收 c4db90d；桥接安装回执

## 已合并

- [PR #14](https://github.com/fengfe1125/Murmur/pull/14)：保留远端提交，补入缺失可靠性修复并合入最新 main，没有强推重写历史。新提交的服务端、iOS、Android 检查通过。
- [PR #15](https://github.com/fengfe1125/Murmur/pull/15)：产品定位、术语、现有能力与未来功能边界、现行文档和历史归档。各端检查通过。
- [PR #16](https://github.com/fengfe1125/Murmur/pull/16)：双布局桥接更新器、统一 CI 与仓库检查。Python 3.11／3.14、iOS 构建/单元测试、Android Debug 及 Repository gate 全部通过。
- [PR #20](https://github.com/fengfe1125/Murmur/pull/20)：修复实机旧版本尚无统一测试 runner 的桥接识别问题，目标版本测试要求不变。29 项隔离更新回退测试及两版 Python CI 通过，合并提交 2366e8c；桥接已安装，应用未升级。

私有仓库保持私有，已设置项目简介、合并后自动删除分支；main 禁止强推和删除，要求 PR 与 `Repository gate`，审批数量为 0，不要求单人自审，也未升级套餐。

## 目录迁移：已合并，应用未部署

[PR #17](https://github.com/fengfe1125/Murmur/pull/17) 的提交 `6e46f5a022c4de00a8fed35dde20576991ba5007` 已通过
[GitHub 全部检查](https://github.com/fengfe1125/Murmur/actions/runs/33138224775)：Python 3.11／3.14、iOS 构建与单元测试、Android Debug、仓库检查和 Repository gate。
最终提交 `c4db90d9e49770e893150128fde2fb1597ffc59c` 在同步 #20 和安装证据后，通过 [全部 PR 检查](https://github.com/fengfe1125/Murmur/actions/runs/33154663234/attempts/2)，并于 2026-08-28 08:35 UTC 合入 main，合并提交 `caeb6c57da21826316366ca3ec7cc94df302d368`。应用未部署。
首轮 iOS 日志已输出 `TEST SUCCEEDED`，但结束请求与工作流收尾重叠导致取消；未把取消记为代码断言失败，同一提交、相同测试重跑后统一门禁通过（iOS 6m5s）。
合并后的 [主线验收](https://github.com/fengfe1125/Murmur/actions/runs/33155973824) 已独立通过：Python 3.11／3.14、iOS 构建与单元测试（5m4s）、Android Debug（5m9s）、仓库检查和统一门禁全部成功，未用 PR 记录替代。

以下是初始迁移提交 `6e46f5a` 的核验记录；之后独立合入的 UI 修复见下节。

- 174 个原有受跟踪文件有逐文件 [旧新路径映射](path-mapping.tsv)；端内组织未重构。
- [原始 34 个测试文件](test-baseline.txt) 全部保留，现共 38 个测试文件。迁移后本机 Python 3.11／3.14 lint 与全量测试分别通过（129.4s／133.8s）。
- 全部有效文档的状态字段和本地链接检查通过；脚本 Bash 语法通过；忽略规则覆盖移动端本地配置、构建输出、密钥和根目录运行数据。
- 更新器 25 项真实临时 Git／假服务演练通过，覆盖旧→新、新→新、测试/安装/启动失败回退、脏工作区拒绝、分库 WAL 备份、私密权限及不完整回退保留证据。
- 源码包 18 项离线 Git 测试通过；提交 6e46f5a 的真实源码包只含 9 个允许根入口，不含移动端、品牌、文档目录或私有运行数据。
- 临时 `server infra scripts` 稀疏检出中，独立 Python 3.11 环境的全部 38 个测试通过（133.2s），验证部署验收所需脚本与资源完整。
- 71 个客户端/品牌非文档文件逐字节一致；API 和人格文件除模块职责说明外 AST 一致。运维面板仅适配重命名后的 Bot 监督脚本进程过滤，并补回归测试；没有改 UI、提示词或业务契约。
- 根目录 `.env`、iOS 本地 xcconfig、Android local.properties 哈希与迁移前一致；根目录 `.venv` 与数据位置不变，仅重装本地 editable 入口到 `./server`。

若迁移 PR 后续更新提交，须重新验收；上述绿色结果只对应明确记录的 SHA，不能外推到未来版本。

## 后续独立修复 #19

[PR #19](https://github.com/fengfe1125/Murmur/pull/19) 的标签栏胶囊横移动画修复已独立合入 main，合并提交为 `135e57f44cd7d4957b91297cb79a8a39bfdfe5e2`。
审阅提交 `8062a0d` 时，代码与需求未发现阻断问题；分支名缺少 `codex/` 前缀属于非阻断规范偏差，没有为此重写提交或重建 PR。
CI 构建、单元测试与统一门禁通过；另在独立 iPhone 17／iOS 26.5 模拟器上验证了以下三项 UI 回归，均通过：

- `testPressingAStopMovesTheSelectionToIt`
- `testTheTabsNotOnScreenAreOutOfReach`
- `testDarkAccessibilityXXXLKeepsPrimaryControlsReachable`

这些自动测试验证选中状态、隐藏页面隔离和大字体可达性，不构成逐帧流畅度测量。
专用模拟器在测试结束后已移除；没有重置原有模拟器或访问生产数据。

修复已通过普通 merge 同步到 #17（`4838dac6e030e0db698a0d9a08f7f360e0874aed`）和 #18，旧、新路径的 Swift 文件哈希一致，未丢失修复。
[同步后的 #17 检查](https://github.com/fengfe1125/Murmur/actions/runs/33152259471) 已对新 SHA 独立验收通过：Python 3.11／3.14、iOS 构建与单元测试、Android Debug、仓库检查和 Repository gate 全部通过，未沿用初始迁移的绿色记录。
同步 #19 时两个 PR 保持草稿；之后的桥接安装经单独授权完成，并非通过客户端修复绕过门禁。

## 待部署，不越过生产门禁

初始迁移阶段没有执行生产操作。后续经单独授权完成 [桥接安装](../operations/bridge-installation-2026-08-28.md)，线上代码仍为 aae2580，业务进程未重启，运行数据与配置未改动。
桥接安装与 #17 合并已完成；新布局应用部署尚未授权、未执行。#18 只交付整理及验收记录，最终提交和合并状态以其 PR 为准。
Android 正式认证/推送、App Attest/APNs 真机与生产健康检查未在本轮宣称验收。

## 保全

开始操作前制作包含所有 Git refs 的 bundle，并保留两份原始未跟踪文档；本机保全目录在被忽略的 `.trash-backup/reorg-20260828.h11qmY/`。
产品调研已归档，VPS 连接说明归入运维文档并脱敏；原件没有被公开提交。旧 stash 原样保留，不自动应用或清空。
初始清理已清除 10 条旧远端分支、18 条已合并本地引用及 3 个旧 worktree，详见 [PR #18](https://github.com/fengfe1125/Murmur/pull/18) 的 [处置清单](branch-disposition.md)。两条独有历史分支与 stash 均保留。
本次收口另移除已合并的 #20 临时工作区及本地引用，安装程序、只读预检和 5 项安装回退测试源码保全在原备份目录的 `bridge-closeout/` 中。
清理 PR 的最终差异仅为文档；统一工作流仍运行仓库检查与门禁，显式跳过无关构建。按 #17 → #18 顺序合并，最终状态及提交以各 PR 为准，不据此执行生产部署。
