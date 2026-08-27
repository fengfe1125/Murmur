# Murmur 回复质量 · P0 收口与 P1 交接

> 2026-08-24 本地实现状态。本文只描述仓库中可核验的代码和测试；
> 未把本地通过写成生产已验收，也不记录服务器地址、账号或对话正文。

## 当前结论

P0 的代码阻断项已经收口，可以进入灰度前检查；**尚未完成生产验收**。
真实 DeepSeek 端点上的成功率、24–48 小时 dossier 持续推进和用户可见
失败率，都必须在部署后用无正文指标验证。

> 2026-08-27 更新：无正文指标已落地——`murmur/counters.py` 把本文列的
> 分类口径（回复 full/salvaged/quiet、app_moment retryable_failure、
> dossier full/partial/no_usable_blocks）按天写进 `daily_counters` 表，
> 看板总览页有「回复质量 · 近 14 天」卡片。生产验收直接读这张表即可。
> 另：降级链支持第二家网关（`MURMUR_FALLBACK_BASE_URL` /
> `MURMUR_FALLBACK_API_KEY`），不配则维持同网关行为。

两轮模型都失败时，App 仍返回可重试失败并保留客户端重发能力。这里的
P0 承诺是“零静默丢失”，不是伪造成功回复，也不是承诺上游永不失败。

## P0 已实现

### Dossier 完整性

- prefix 模式先解析模型原文；只有典型 continuation 确实缺少 `{` 时才补。
- 完整对象必须同时包含“他是谁 / 正在发生 / 怎么跟他说话”三个字符串分区。
- 只返回 1–2 个合法分区时按 partial 合并：保留缺失分区，且不推进
  `covered_upto`，原记录会在下一轮继续参与整理。
- 废话包裹完整 JSON、全角标点、截断 JSON 和无关对象都有回归测试。

### 隐私诊断和生产计数

- engine、dossier、开环提取和 worker 日志只写结构化元数据：模型、尝试、
  `finish_reason`、响应长度、分类和数量。
- 不写模型正文、用户消息、dossier 内容或异常消息。
- 四个渠道（bot / wechat / qq / dingtalk）的“整理记忆失败”日志只记异常
  类型：`OpenAIError` 的消息可能带网关响应正文。
- 可聚合分类包括：回复 `full_output / salvaged_output / quiet_output /
  retryable_failure`，dossier `full_saved / partial_saved / no_usable_blocks`，
  以及 `reply_guard` 的 emoji、无效残骸和重复命中数。

### 统一出站保护

- 正常 JSON、裸数组、文本 salvage、流式气泡和主动消息统一经过
  `_guard_bubbles()`。
- guard 清理 emoji、历史拼接符、JSON 残骸、空内容和同批重复。
- guard 把所有内容清空时进入重试/失败链；不会用“嗯”之类罐头伪装成功。
- 流式已交付过气泡时，成功路径和 salvage 路径都用 `say = emitted` 对账：
  Reply 只描述真正发出去的那几条。salvage 从 raw 重捞会把被
  `previous_exact` 拦下的重复气泡一起捞回来，而调用方会照 `reply.say`
  补发一遍。
- 带图回复明确选择 `quiet` 仍是合法无输出动作。
- 纯文字回复说了话却标成 `quiet` 时强制改判为 `brief`：`reply.silent` 会让
  所有渠道直接不发，"发消息没反应"属于静默丢失。空 `say` 不受这条影响，
  仍然交回重试/失败链。

### 近端指令和发送前去重

- `MURMUR_REPLY_DIRECTIVES=1` 时，在最近历史之后插入一条短动态指令：
  先接具体内容、1–3 条气泡、最多一个问句、避开近期开头。
- 普通回复会与最近 8 条助手回复比较；流式第一条完整气泡尚未发送时可以
  中断并重试一次，已经发送后绝不撤回。
- 相似度否决只发生在还剩尝试次数时。`respond` 的最后一次尝试宁可发一条
  重一点的回复，也不把 `_TooSimilar` 抛给调用方（用户会看到
  "（出错了：_TooSimilar）"）。
- 主动消息去重**不受本开关控制**，默认一直生效：它早于近端指令存在，
  放到开关后面等于默认允许连着两条"在干嘛"。

## P1 已实现（默认关闭，独立开关）

四项功能都使用 Python + SQLite 自动迁移，不改变 App HTTP/SSE 协议，
现有 iOS 客户端无需同步升级。

### 1. 开环记忆

`open_loops` 持久化字段为：`chat_id`、`title`、`kind`、`due_at`、
`status`、`source_entry_id`、`followup_count`、`last_followup_at`、
`created_at`、`resolved_at`。

- 回复交付后提取明确的未来事项；日期非法时不猜，跨时区统一为 UTC。
- 每轮最多注入 4 条，只有明显相关时才接，不逐条盘问。
- 到期事项只允许主动追问一次；明确结果或取消表述才保守关闭。
- 追问先用 `last_followup_at` 建立 15 分钟租约，App moment 落库后才把
  `followup_count` 提交为 1；进程在中间崩溃时会等待租约过期重试，已落库
  的 App moment 则通过 App SQLite 的持久 outbox 在 worker 重启后完成提交；
  恢复不依赖 ACK、24 小时过期、用户偏好或可能被删除的排程 slot。
- 主动消息的 Memory 记录先标为 `pending`，App moment 持久化后才提交；
  recent/dossier/web 只读 `committed`。worker 在每用户操作锁内保留 outbox
  引用并清理其余崩溃孤儿，既不泄露未送达内容，也不让孤儿永久卡住游标。
- event/task/ongoing 分别按 2/7/14 天过期兜底，状态跨进程重启保留。
- App 回执会继续关闭开环；提取失败发生在终态之后，不影响已交付回复。

### 2. 主动消息素材账本

- dossier 的明确“正在发生”条目生成稳定 `material_id/category/source_ref`。
- 发送记录把 `material_id` 写回 `entries`；相同素材默认冷却 14 天。
- 到期开环优先成为 `open_loop:<id>` 素材，并原子占用唯一一次追问机会。
- 回调意图与同一 `source_ref` 绑定；没有可靠素材时不会选择“接上次 / 想到他”。
- 连续 4 条主动消息没有回复时，原有收手机制保持不变。

### 3. 极简情绪状态

- 每个 `chat_id` 独立持久化 `valence + arousal`。
- 只有明确对话事件更新；时间只让状态回归中性，用户沉默不会积累委屈、
  焦躁或联系压力。
- 数值只翻译成一句自然语言近端提示，不直接暴露给模型或日志。
- worker 重放与 App ACK 都有幂等事件序号，不会重复施加同一次变化。

### 4. 分阶段开关

`.env.example` 中新增，默认均为 `0`：

```dotenv
MURMUR_OPEN_LOOPS=0
MURMUR_REPLY_DIRECTIVES=0
MURMUR_PROACTIVE_MATERIALS=0
MURMUR_AFFECT=0
```

建议一次只开启一个，顺序为：开环记忆 → 近端指令/去重 → 素材账本 →
情绪状态。任一指标恶化都可独立关闭。

## 质量语料和测试

- 仓库当前有 **31 个** `test_*.py` 文件。
- `tests/fixtures/reply_quality_cases.json` 提供 30 条合成、无真实用户内容的
  固定质量语料，覆盖接情绪、连续性、能力边界、克制追问、去重和素材一致性。
- `scripts/evaluate_reply_quality.py` 自动检查 emoji、问句数、近期开头、
  素材错配、具体事件和气泡数；“是否真正接住”等六项仍保留人工评分。
- 回归覆盖 prefix/partial/游标、日志隐私、所有出站路径、日期失败、跨时区、
  重复提取、保守关闭、一次追问、过期、重启、素材冷却、情绪幂等、
  account erasure、追问租约崩溃恢复，以及连续性后处理失败不影响已交付消息。

## 灰度与验收顺序

1. 首次部署保持四个 P1 开关关闭，`MURMUR_TEMPERATURE` 和
   `MURMUR_PRESENCE_PENALTY` 也留空；先观察结构可靠性 24 小时。
2. P0 验收：没有缺失分区清空旧记忆；partial 后同批记录仍可处理；
   日志无正文；无静默丢失；dossier 连续 24–48 小时能持续推进。
3. 结构稳定后只设置 `MURMUR_PRESENCE_PENALTY=0.3`，再观察一天。
4. 只有脱敏样本显示回复仍过平且 JSON 成功率未下降，才试
   `MURMUR_TEMPERATURE=1.1`。
5. P1 四项逐项上线，每项至少单独观察一天；用 30 条固定语料做同条件对比。

## 参考边界

- [溪语](https://github.com/xiyuailove/xiyuai) 的开环、素材冷却等机制只作为
  设计参考；本仓以 Python 重写，没有复制其 Node 框架，也不把短公开历史
  当作生产成熟证明。
- [积温](https://github.com/ClaraShafiq/jiwen) 只参考“状态翻译为自然语言提示”
  的思路，没有复制 pride/connection/immersion 全套。
- SillyTavern 的 [Author's Note](https://docs.sillytavern.app/usage/core-concepts/authors-note/)
  只作为近端指令的 A/B 假设，不认定它就是复读根因。
- 可继续参考 [companion-emergence](https://github.com/hanamorix/companion-emergence)
  的后台维护和记忆归档思想，但不引入完整桌面架构。
- 已删除无法由当前仓库核验的“积温约 500 行”和 CREDITS 评价说法。

“像人”的第一验收信号仍是：能在正确时间接上用户之前说过的事；情绪轴
和角色生活复杂度都排在它之后。
