# 分支、worktree 与未提交资料处置清单

> 状态：现状记录｜适用：2026-08-28 仓库整理｜核验：2026-08-28｜依据：main 1205a27；迁移提交 6e46f5a；Git refs、PR 与 worktree 实查

## 当前保留

| 引用 | 去向与理由 |
|---|---|
| `main` | 唯一长期分支；本地已同步到 1205a27。已合并 #14、#15、#16，不代表已部署。 |
| `codex/chore/repo/organize-source-layout` | [PR #17](https://github.com/fengfe1125/Murmur/pull/17)，待桥接更新器线上安装确认后才能合并。 |
| `codex/chore/repo/record-merged-work-cleanup` | 本清理记录独立 PR，基于 #17；不绕过迁移门禁，随后按顺序合入 main。 |
| 本地 `docs/repo/name-branches-by-what-changed`（d6b6cdc） | 命名原则已吸收到现行协作规范；原提交仍独有，保留，不用旧 README 覆盖新定位。 |
| 本地 `fix/reliability-and-metrics`（9e694a9） | 原本地可靠性工作保留；其修复已通过保留远端历史的 #14 合入，原提交不强制删掉。 |
| `stash@{0}`（9d1318eef3927947f84eecb220b61c4f429bb1fc） | 原样保留，没有 apply、pop 或 drop。 |

原本地 0f17b99 与远端 322a0ad 的 tree 相同；以远端为基线补入修复得到 58e077b，再合入 main 得到 87c1faa。
因此 #14 保留了原远端提交，没有 force-push 或重写共同历史。

## 已删除的合并分支引用

以下 10 条旧远端分支和同名本地分支，在删除前均再次确认是 `origin/main` 的祖先；删除的是引用，不是主线提交。

| 分支 | 原 tip | 合并依据 |
|---|---|---|
| `claude/send-failure-marks-and-composer-gap` | 364a7b3 | PR #1 |
| `feat/on-this-day-photo-reading` | c5175c7 | PR #2 |
| `fix/photo-reading-retry` | 3258882 | PR #3 |
| `ci/server-workflow` | af26ff8 | PR #4／#5／#6，最终 tip 在 #6 |
| `fix/memory-salvage-and-sampling` | 51ac823 | PR #7 |
| `fix/reply-directive-photo-context` | 41beeac | PR #8 |
| `feat/persona-warm-companion` | c767ac6 | PR #9 |
| `fix/directive-matches-one-message` | bd142ec | PR #10 |
| `fix/gitignore-backups` | 662a177 | PR #11 |
| `fix/answer-what-he-asks` | d617a8e | PR #12 |

额外清除 8 条已合并本地引用，故本轮共删 18 条本地引用：

| 分支 | 原 tip | 保留位置 |
|---|---|---|
| `claude/amazing-cartwright-64f905` | 9132ab0 | main 祖先；原始 bundle |
| `claude/amazing-chatelet-56dcce` | b682eff | main 祖先；原始 bundle |
| `claude/competent-lovelace-b4d32e` | 4eac4cd | main 祖先；原始 bundle |
| `worktree-android-server-auth-push` | 15a9392 | main 祖先；原始 bundle |
| `feat/ios/continue-an-archived-day` | afa5233 | PR #13；main |
| `codex/fix/repo/close-reliability-work` | 87c1faa | PR #14；main |
| `codex/docs/repo/reframe-private-memories` | d078150 | PR #15；main |
| `codex/ci/deploy/bridge-layout-update` | 56739bf | PR #16；main |

远端 `fix/reliability-and-metrics`、本轮文档分支和桥接分支在各自 PR 合并后自动删除；本地原始 9e694a9 引用仍保留。

## 已移除的旧 worktree

| 仓库相对路径 | 原 HEAD／分支 | 检查与处置 |
|---|---|---|
| `.claude/worktrees/amazing-cartwright-64f905` | detached b682eff | 无未提交/未跟踪工作；HEAD 在 main；移除 |
| `.claude/worktrees/android-server-auth-push` | 15a9392／worktree-android-server-auth-push | 无未提交/未跟踪工作；忽略文件仅 Ruff/Python 缓存；移除 |
| `.claude/worktrees/competent-lovelace-b4d32e` | detached 4eac4cd | 无未提交/未跟踪工作；忽略文件仅 Ruff/Python 缓存；移除 |

提交可从主线或 bundle 恢复为新 worktree；缓存可重建。当前只保留主工作目录，没有清理其中的运行数据。

## 原件与恢复材料

- 本机被忽略目录 `.trash-backup/reorg-20260828.h11qmY/` 保存操作前 refs 清单、两个未跟踪文档原件和经 `git bundle verify` 确认完整的 `git-before-reorg.bundle`。
- `docs/product-plan-2026-08-27.md` 原件在备份中；带核验边界的 [归档副本](../archive/product-plan-2026-08-27.md) 不作为需求。
- `docs/vps-connection.md` 原件在备份中；现行 [连接说明](../operations/vps-connection.md) 已脱敏，原始凭据/主机资料不进入新提交。
- `.env`、移动端本地配置和已有根目录运行数据不因清理而删除；`.venv` 仅更新 editable 路径。

不要为了让分支列表更短而强删保留的独有工作；也不要恢复原始文档后无检查地提交私有信息。
