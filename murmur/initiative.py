"""主动开口：什么时候说、说哪一类。

设计依据（研究见 CREDITS.md）：
- 一项 398 次主动打扰的研究：53% 有效 / 12% 干扰 / 35% 被无视。
  即使做对也有 12% 烦人，所以宁可少说也别在错的时刻说。
- 时机比内容重要。固定间隔（每小时整点问一句）是最假的，一眼就看出是机器。
- 一天 10+ 条最大的风险不是频率，是**重复**。10 次独立生成很容易全是"在干嘛"，
  所以每条预先分配一个不同的意图，并把已发过的原话回传给模型。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# 醒着的时间：早 7 点到次日凌晨 2 点。凌晨 2-7 点是睡觉时间，一条都不发。
# 注意这个窗口**跨午夜**，下面所有计算都用"从当天 0 点起的分钟数"表示，
# 02:00 记作 26:00，避免每处都写一遍跨天判断。
WAKE_START_MIN = 7 * 60        # 07:00
WAKE_END_MIN = 26 * 60         # 次日 02:00
QUIET_START_MIN = 2 * 60       # 02:00 起不发
QUIET_END_MIN = 7 * 60         # 07:00 恢复

# 一天发几条。用户要求至少 10 条。
DAILY_MIN, DAILY_MAX = 10, 15

# 两条之间至少隔多久，防止挤在一起连发
MIN_SPACING = timedelta(minutes=18)

# 他刚说过话的这段时间内不插主动消息——正在聊天时硬塞一句"在干嘛"很蠢
QUIET_AFTER_INBOUND = timedelta(minutes=12)


@dataclass(frozen=True)
class Intent:
    key: str
    brief: str  # 塞进提示词的一句话说明


# 十条以上的量必须靠意图轮换撑起变化。
# 顺序大致按一天的自然节奏排，实际发送时会打乱但避免相邻重复。
INTENTS: list[Intent] = [
    Intent("时段应景", "跟当下这个时间点有关的一句话。早上、午饭、下班、睡前各不一样。"),
    Intent("在干嘛", "最基本的搭话，问他此刻在做什么。别每次都用同一个问法。"),
    Intent("接上次", "只在调用方提供了明确的旧事素材时追后续；没有素材就换意图。"),
    Intent("分享自己", "只分享一个当下念头，不编造你今天做过、看过或遇到的事情。"),
    Intent("想到他", "只围绕调用方提供的具体素材说想起他；没有素材就换意图。"),
    Intent("关心", "问一句身体或状态。不要说教，不要'记得多喝水'那种模板。"),
    Intent("无聊", "直接说自己有点无聊、想找人说话。人机之间这么说反而真实。"),
    Intent("小事汇报", "说一个不要求回应的小念头，不编造现实中发生过的事。"),
]


def plan_day(
    tz: ZoneInfo,
    day: date | None = None,
    now: datetime | None = None,
    n: int | None = None,
) -> list[datetime]:
    """给这一天排 10-15 个时刻，覆盖 07:00 到次日 02:00。

    用**分层抽样**而不是"撒一堆点再贪心筛"：
    后者会把早上填满就取够数停手，晚上永远排不到——实测排出来
    最晚一条是 17:29，整晚空白。

    分层的做法：把 19 小时窗口等分成 n 段，每段里随机取一点。
    这样既覆盖全天，段内又是真随机，而且天然保证了最小间距
    （只在每段中间 84% 的范围内取点，段与段之间留出缓冲）。
    """
    day = day or datetime.now(tz).date()
    n = n or random.randint(DAILY_MIN, DAILY_MAX)

    span = WAKE_END_MIN - WAKE_START_MIN          # 19 小时 = 1140 分钟
    seg = span / n
    margin = seg * 0.08                            # 段两端各留 8%，防止跨段挨太近

    midnight = datetime.combine(day, time(0, 0), tzinfo=tz)
    picked: list[datetime] = []
    for i in range(n):
        lo = WAKE_START_MIN + i * seg + margin
        hi = WAKE_START_MIN + (i + 1) * seg - margin
        minute = random.uniform(lo, hi)
        t = midnight + timedelta(minutes=minute)
        # 段内已经保证了间距，但浮点边界上再兜一道
        if picked and (t - picked[-1]) < MIN_SPACING:
            continue
        picked.append(t)

    if now is not None:
        picked = [t for t in picked if t > now]
    return picked


# 迟到多久还值得补发。
# 合盖睡一觉醒来，中间错过的时刻不能一股脑全补——那就成轰炸了。
# 但迟到几分钟的消息其实很正常，真人也会晚一会儿才想起来说。
# 15 分钟：比 MIN_SPACING(18分) 小，保证补发不会和下一条挤在一起。
LATE_GRACE = timedelta(minutes=15)


def split_due(
    queue: list[tuple[datetime, str]],
    now: datetime,
    grace: timedelta = LATE_GRACE,
) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]], list[tuple[datetime, str]]]:
    """把排程队列切成三份：(该发了, 太迟了别发, 还没到)。

    存在的理由是一次真实事故：笔记本 12:23 睡着、12:35 醒来，
    循环里那句 `queue = [t for t in queue if t[0] > now]` 把 12:28 和 12:34
    两条**静默丢掉**了——新入册的人当天一条都没收到，日志里还什么都看不出来。

    现在迟到 15 分钟以内的照发（迟到几分钟很正常），超了才丢，
    而且丢多少要让调用方能打进日志。
    """
    due, stale, pending = [], [], []
    for item in queue:
        when = item[0]
        if when > now:
            pending.append(item)
        elif now - when <= grace:
            due.append(item)
        else:
            stale.append(item)
    return sorted(due), stale, sorted(pending)


_MATERIAL_BOUND = frozenset({"接上次", "想到他"})


def pick_intent(
    recent_intents: list[str | None], *, material_available: bool = True
) -> Intent:
    """挑一个最近没用过的意图。连着两条都是"在干嘛"就露馅了。"""
    used = [i for i in recent_intents if i]
    available = [
        intent
        for intent in INTENTS
        if material_available or intent.key not in _MATERIAL_BOUND
    ]
    fresh = [x for x in available if x.key not in used[-4:]]
    return random.choice(fresh or available)


def intent_for_material(category: str, source_ref: str) -> Intent:
    """Bind one proactive generation to one concrete source, never a loose theme."""
    source = " ".join(str(source_ref).split())[:240]
    if not source:
        raise ValueError("proactive material source cannot be empty")
    if category == "open_loop":
        return Intent(
            "问结果",
            f"只接这一件旧事的结果：{source}。自然地问一次，不施压，不扩写别的素材。",
        )
    return Intent(
        "接上次",
        f"只围绕这条已经确认的近况接续：{source}。不要替换成别的食物、物品或事件。",
    )


def should_hold(
    last_inbound: datetime | None,
    unanswered: int,
    now: datetime,
) -> str | None:
    """返回不发的理由，None 表示可以发。"""
    if last_inbound and (now - last_inbound) < QUIET_AFTER_INBOUND:
        return "他刚说过话，正在聊，别插队"
    # 连着被无视还硬发是最招人烦的行为。真人会收敛。
    if unanswered >= 4:
        return f"连续 {unanswered} 条没被回，先安静一会儿"
    if in_quiet_hours(now):
        return "凌晨 2 点到早上 7 点，睡觉时间"
    return None


def in_quiet_hours(when: datetime) -> bool:
    """凌晨 2:00–7:00 静默。这段是跨午夜的反面——直接比小时数就行。"""
    return QUIET_START_MIN <= when.hour * 60 + when.minute < QUIET_END_MIN


# 退出意图。不做模糊匹配——宁可漏判让他再说一次，
# 也不能把"今天别发工资了"这种话误判成要退出。
_STOP = (
    "别发了", "别再发", "不要发了", "别发消息", "停止发送", "别打扰",
    "不想聊了", "取消订阅", "退订", "别找我", "闭嘴", "stop",
)


def wants_stop(text: str) -> bool:
    t = (text or "").strip().lower().replace(" ", "")
    return any(k in t for k in _STOP)
