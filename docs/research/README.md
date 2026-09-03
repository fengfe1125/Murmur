# 调研笔记

> 状态：现行规范｜适用：开发者与自动化 agent｜核验：2026-08-31｜依据：本目录既有笔记与 [协作规范](../development/workflow.md)

调研笔记全部放在这里，一份一个文件。它们是**读来的事实和据此得出的判断**，不是承诺。

## 什么进这里

去外部把一件事查清楚之后写下来的东西：平台能力与授权边界、API 与源码审计、选型对比、
可行性结论、竞品与生态盘点。判断可以有，但每条事实要能追回它自己的出处。

## 什么不进这里

| 它是什么 | 放哪 |
|---|---|
| 已接受的规则、职责与边界 | `docs/architecture/`、`docs/development/` |
| 产品现在是什么、要往哪走 | `docs/product/` |
| 怎么做、按什么顺序做、坏了怎么退 | 实施提案进 `docs/product/`，部署与回滚进 `docs/operations/` |
| 不再指导新开发的旧材料 | `docs/archive/` |

分界是**问题的性质**，不是话题：同一件事的「能不能做」在这里，「决定做，这么做」在
`docs/product/`。网易云那份实施提案和它的回滚方案就是这样分开的。

## 写法

文件名 `<主题>-<核验日期>.md`，kebab-case，日期用 `YYYY-MM-DD`。同一主题查第二次不覆盖
旧的，另写一份新日期的——结论变了，变化本身就是要留下的东西。

第三行写状态头：`> 状态：…｜适用：…｜核验：<日期>｜依据：…`。状态里说清楚这是调研，
并且在涉及第三方平台时明确它**不代表对方已授权**——这些笔记会被当成做决定的依据看。

每条事实链到它的一手出处：官方文档、规格、源码行号，而不是转述。查不到就写查不到。

## 现有笔记

| 笔记 | 是什么 |
|---|---|
| [中文曲库音乐平台最终选型](chinese-catalog-music-platform-decision-2026-08-31.md) | 汇总下面几份，给出选型结论 |
| [全球音乐平台集成对比](music-platform-integration-comparison-2026-08-30.md) | 双向会话集成的横向对比 |
| [亚洲华语音乐平台](asian-chinese-music-platform-options-2026-08-31.md) | KKBOX、JOOX 等 |
| [Apple Music 中文曲库适配](apple-music-chinese-catalog-fit-2026-08-31.md) | MusicKit 对中文需求的适配度 |
| [Spotify App Remote](spotify-app-remote-feasibility-2026-08-31.md) | 遥控放歌可行性 |
| [网易云「陪你听」可行性](netease-listening-companion-feasibility-2026-08-30.md) | 产品形态可行性 |
| [网易云集成能力来源](netease-cloud-music-integration-sources.md) | 外部能力盘点 |
| [网易云四项能力可行性](netease-music-exact-scope-feasibility-2026-08-31.md) | 精确到具体能力的分析 |
| [netease-music-mcp「一起听」源码审计](netease-music-mcp-listen-together-audit-2026-08-31.md) | 第三方实现的源码审计 |
| [网易云「一起听」Phase 0 初测](netease-listen-together-phase0-results-2026-08-31.md) | 双账号真机核心链路与首日待办 |
| [网易云「一起听」Phase 0 收尾](netease-listen-together-phase0-results-2026-09-01.md) | 稳定性、延迟与故障收尾结果 |
| [网易云非官方播放与网页嵌入](netease-unofficial-playback-and-web-embed-feasibility-2026-08-30.md) | 非官方路径的边界与风险 |
| [100 个相似 GitHub 仓库](github-landscape-2026-08-29.md) | 功能路线的生态盘点 |
