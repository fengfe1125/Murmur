"""合盖睡觉之后，错过的主动消息该怎么办。

来自一次真实事故：2026-08-13 笔记本 12:23 睡着、12:35 醒来，
排在 12:28 和 12:34 的两条主动消息被**静默丢弃**——
新入册的毛杰当天一条都没收到，日志里还什么都看不出来。

原因是循环里那句 `queue = [t for t in queue if t[0] > now]`：
醒来时这些时刻全成了"过去"，直接被滤掉。

现在的规矩：迟到 15 分钟以内照发，超了才丢，而且丢了要打日志。

    python tests/test_late.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.initiative import LATE_GRACE, MIN_SPACING, split_due  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


def at(hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(2026, 8, 13, int(h), int(m), tzinfo=TZ)


print("\n── 复现那次事故 " + "─" * 41)
# 11:47 排的队；12:23 睡着，12:35 醒来
queue = [(at(t), "毛杰") for t in
         ("12:28", "13:08", "15:19", "16:41")] + [(at("12:34"), "你")]
due, stale, pending = split_due(sorted(queue), at("12:35"))

check("12:28 迟到 7 分钟 → 补发",
      [u for _, u in due] == ["毛杰", "你"], f"实际 {[(f'{t:%H:%M}', u) for t, u in due]}")
check("12:34 迟到 1 分钟 → 补发", any(t == at("12:34") for t, _ in due))
check("没有被静默丢弃的", stale == [])
check("后面的还排着", [f"{t:%H:%M}" for t, _ in pending] == ["13:08", "15:19", "16:41"])

print("\n── 睡久了就别补了 " + "─" * 39)
# 午休睡两小时
queue = [(at(t), "他") for t in ("12:28", "12:50", "13:30", "15:19")]
due, stale, pending = split_due(queue, at("14:20"))
check("14:20 醒来，12:28/12:50/13:30 全都太迟",
      [f"{t:%H:%M}" for t, _ in stale] == ["12:28", "12:50", "13:30"],
      f"实际 {[f'{t:%H:%M}' for t, _ in stale]}")
check("一条都不补（不然醒来被灌三条）", due == [])
check("但会被调用方打进日志（stale 非空就是信号）", len(stale) == 3)
check("下午的还留着", [f"{t:%H:%M}" for t, _ in pending] == ["15:19"])

print("\n── 边界 " + "─" * 49)
now = at("12:35")
exactly = now - LATE_GRACE
due, stale, _ = split_due([(exactly, "x")], now)
check("正好卡在 15 分钟上 → 还算数（宁可发也别丢）", len(due) == 1 and not stale)

due, stale, _ = split_due([(exactly - timedelta(seconds=1), "x")], now)
check("超出 1 秒 → 丢弃", len(stale) == 1 and not due)

due, stale, pending = split_due([(now, "x")], now)
check("正好是此刻 → 算到期", len(due) == 1)

due, stale, pending = split_due([(now + timedelta(seconds=1), "x")], now)
check("差 1 秒 → 还没到", len(pending) == 1 and not due)

check("空队列不炸", split_due([], now) == ([], [], []))

print("\n── 补发不会和下一条挤在一起 " + "─" * 30)
# 宽限期必须小于最小间距，否则补发的那条会紧贴着下一条
check(f"LATE_GRACE({LATE_GRACE}) < MIN_SPACING({MIN_SPACING})",
      LATE_GRACE < MIN_SPACING,
      "宽限期比最小间距还大的话，补发会和下一条撞在一起")

print("\n── 顺序 " + "─" * 49)
q = [(at("13:00"), "c"), (at("12:30"), "a"), (at("12:32"), "b")]
due, _, pending = split_due(q, at("12:35"))
check("到期的按时间排（先该发的先发）", [u for _, u in due] == ["a", "b"])
check("待发的也按时间排", [u for _, u in pending] == ["c"])

print("\n── 跨午夜（醒着的窗口到次日 2 点） " + "─" * 24)
q = [(datetime(2026, 8, 14, 1, 9, tzinfo=TZ), "他")]
due, stale, pending = split_due(q, datetime(2026, 8, 14, 1, 15, tzinfo=TZ))
check("凌晨 1:09 排的，1:15 醒来 → 补发", len(due) == 1)

print("\n── Telegram 那边的宽限期对齐了 " + "─" * 27)
try:
    from telegram.ext import JobQueue

    default = JobQueue().scheduler._job_defaults.get("misfire_grace_time")
    check(f"APScheduler 默认只有 {default} 秒（所以必须显式覆盖）", default == 1)

    import inspect

    from murmur import bot

    src = inspect.getsource(bot._schedule_day)
    check("bot.py 里显式传了 misfire_grace_time",
          "misfire_grace_time" in src and "LATE_GRACE" in src)
except ImportError as e:
    print(f"  （跳过 Telegram 检查：{e}）")

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
