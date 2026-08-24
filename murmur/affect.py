"""Tiny, relationship-local affect state.

This deliberately models tone, not attachment pressure.  Only an explicit
conversation event can move the axes; the passage of time merely returns them
toward neutral, so silence can never make the companion resentful or anxious.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AffectState:
    valence: float
    arousal: float
    updated_at: datetime


_POSITIVE = (
    "开心", "高兴", "太好了", "过了", "成功", "顺利", "拿到", "喜欢", "期待",
    "放心", "好多了", "搞定", "赢了", "值得", "庆祝",
)
_NEGATIVE = (
    "难受", "伤心", "失败", "没过", "崩了", "生气", "委屈", "害怕", "焦虑",
    "失望", "后悔", "糟糕", "痛苦", "哭", "撑不住", "分手", "去世",
)
_HIGH_AROUSAL = (
    "太", "特别", "真的", "终于", "居然", "突然", "气死", "吓死", "激动", "紧张",
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("affect timestamps must include a timezone")
    return value.astimezone(UTC)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def decay(state: AffectState, now: datetime) -> AffectState:
    """Return the wall-clock-decayed state without inventing a silence event."""
    current = _aware(now)
    previous = _aware(state.updated_at)
    hours = max(0.0, (current - previous).total_seconds() / 3600)
    # Tone can linger through a conversation; activation settles more quickly.
    valence = state.valence * math.pow(0.5, hours / 8.0)
    arousal = state.arousal * math.pow(0.5, hours / 3.0)
    return AffectState(valence, arousal, current)


def event_delta(text: str | None) -> tuple[float, float]:
    """Map explicit wording to a conservative delta; ordinary updates are neutral."""
    compact = re.sub(r"\s+", "", text or "")
    positive = sum(token in compact for token in _POSITIVE)
    negative = sum(token in compact for token in _NEGATIVE)
    if not positive and not negative:
        return 0.0, 0.0
    direction = 1.0 if positive > negative else -1.0
    strength = min(0.55, 0.22 + 0.08 * abs(positive - negative))
    activated = min(0.5, 0.2 + 0.06 * sum(token in compact for token in _HIGH_AROUSAL))
    return direction * strength, activated


def has_negative_affect(text: str | None) -> bool:
    """Whether explicit wording calls for a gentler nearby reply instruction."""
    compact = re.sub(r"\s+", "", text or "")
    return any(token in compact for token in _NEGATIVE)


def apply_message(state: AffectState, text: str | None, now: datetime) -> AffectState:
    settled = decay(state, now)
    valence_delta, arousal_delta = event_delta(text)
    if valence_delta == 0.0 and arousal_delta == 0.0:
        return settled
    return AffectState(
        _clamp(settled.valence + valence_delta, -1.0, 1.0),
        _clamp(settled.arousal + arousal_delta, 0.0, 1.0),
        settled.updated_at,
    )


def prompt_context(state: AffectState) -> str:
    """Translate axes into one nearby instruction instead of exposing numbers."""
    if state.valence <= -0.45 and state.arousal >= 0.55:
        mood = "有些低落而且绷着"
    elif state.valence <= -0.45:
        mood = "有些低落，语气要放轻"
    elif state.valence >= 0.45 and state.arousal >= 0.55:
        mood = "真心替他高兴，语气可以亮一点"
    elif state.valence >= 0.45:
        mood = "心情偏暖，但不要夸张庆祝"
    elif state.arousal >= 0.55:
        mood = "注意力被这件事提起来了，回应要具体"
    else:
        return ""
    return f"此刻的语气底色：{mood}。这是表达方式，不要把情绪压力推给他。"
