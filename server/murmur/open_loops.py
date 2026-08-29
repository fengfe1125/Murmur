"""开环记忆的纯领域规则。数据库读写留在 memory.py。"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta

_TEMPORAL = re.compile(
    r"(?:今天|明天|后天|今晚|明早|上午|下午|晚上|本周|下周|这周|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"\d{1,4}[年./-]\d{1,2}(?:[月./-]\d{1,2}日?)?|\d{1,2}[月日点时])"
)
_LEADING_ACTION = re.compile(r"^(?:准备|参加|去|要|做|完成|提交|交|处理|办理)+")
_CANCEL_WORDS = ("取消", "放弃", "不去了", "不做了", "不再")
_RESULT_WORDS = (
    "完成", "做完", "办完", "搞定", "结束", "提交了", "交了",
    "结果", "通过", "没过", "失败", "成功", "录取", "黄了",
)
_BACKSTOP_DAYS = {"event": 2, "task": 7, "ongoing": 14}


def normalize_title(title: str) -> str:
    """生成稳定的去重标题；保留可读内容，只折叠字形和空白。"""
    normalized = unicodedata.normalize("NFKC", str(title))
    normalized = re.sub(r"[\W_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    if not normalized:
        raise ValueError("open loop title 不能为空")
    return normalized


def aware_datetime(value: datetime | str, *, name: str) -> datetime:
    """解析带时区时间并统一到 UTC；拒绝会受服务器时区影响的裸时间。"""
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{name} 必须是带时区的 ISO 时间") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"{name} 必须是 datetime 或 ISO 字符串")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} 必须带时区")
    return parsed.astimezone(UTC)


def iso_utc(value: datetime | str, *, name: str) -> str:
    return aware_datetime(value, name=name).isoformat(timespec="seconds")


def title_core(title: str) -> str:
    """去掉日期和动作前缀，留下关闭时必须再次提到的保守核心词。"""
    core = _TEMPORAL.sub("", normalize_title(title))
    core = _LEADING_ACTION.sub("", core)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", core)


def resolution_status(title: str, text: str) -> str | None:
    """只有“标题核心词 + 明确结果/取消词”同时出现才关闭。"""
    if not str(text).strip():
        return None
    normalized_text = normalize_title(text)
    core = title_core(title)
    if len(core) < 2 or core not in normalized_text:
        return None
    if any(word in normalized_text for word in _CANCEL_WORDS):
        return "cancelled"
    if any(word in normalized_text for word in _RESULT_WORDS):
        return "resolved"
    return None


def backstop_days(kind: str) -> int:
    """事件很快过时，普通任务一周，持续事项两周；未知类型按任务处理。"""
    return _BACKSTOP_DAYS.get(str(kind).strip().casefold(), 7)


def expires_at(
    kind: str, due_at: datetime | str | None, created_at: datetime | str
) -> datetime:
    """有截止时间从截止点兜底，否则从创建时间兜底。"""
    anchor = aware_datetime(due_at, name="due_at") if due_at is not None else aware_datetime(
        created_at, name="created_at"
    )
    return anchor + timedelta(days=backstop_days(kind))
