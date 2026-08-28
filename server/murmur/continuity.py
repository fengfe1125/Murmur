"""Post-delivery extraction for explicit future commitments and outcomes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING

from .config import Config

if TYPE_CHECKING:
    from openai import OpenAI

    from .memory import Memory

log = logging.getLogger("murmur.continuity")

_SYSTEM = """\
从用户这条消息中提取将来需要回访、或正在等待结果的明确事项。
只收用户亲口说的，不猜测。普通近况、愿望和没有后续节点的事实不要收。
kind 只能是 event、task、ongoing。能确定日期就输出带时区的 ISO 8601；
只确定某一天可输出 YYYY-MM-DD；完全不能确定则 due_at 为 null。
只返回 JSON：{"loops":[{"title":"短标题","kind":"event","due_at":null}]}
"""

_FUTURE_MARKERS = (
    "明天", "后天", "下周", "下个月", "月底", "周一", "周二", "周三", "周四",
    "周五", "周六", "周日", "星期", "面试", "考试", "复诊", "预约", "出差", "旅行",
    "搬家", "入职", "离职", "截止", "等结果", "等通知", "等回复", "到货", "快递",
    "准备", "计划", "打算", "过几天", "之后", "到时候", "待会", "晚点",
)
_KINDS = frozenset({"event", "task", "ongoing"})


@dataclass(frozen=True)
class OpenLoopCandidate:
    title: str
    kind: str
    due_at: datetime | None


def _first_object(text: str) -> dict | None:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M
    )
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _due_at(value: object, now: datetime) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("due_at must be an ISO string")
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed_date = date.fromisoformat(raw)
            # A date-only promise should become due during that person's day, not
            # at UTC midnight (which can be the previous local evening).
            parsed = datetime.combine(parsed_date, time(12), tzinfo=now.tzinfo)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("due_at is not ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(UTC)


def parse_open_loop_candidates(raw: str, now: datetime) -> list[OpenLoopCandidate]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    data = _first_object(raw)
    items = data.get("loops") if data is not None else None
    if not isinstance(items, list):
        return []
    out: list[OpenLoopCandidate] = []
    for item in items[:4]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:160]
        kind = str(item.get("kind") or "task").strip().lower()
        if not title or kind not in _KINDS:
            continue
        try:
            due_at = _due_at(item.get("due_at"), now)
        except ValueError:
            # A made-up date is worse than missing a loop; the source message is
            # still present and dossier compaction can surface it for review.
            continue
        out.append(OpenLoopCandidate(title, kind, due_at))
    return out


def _looks_future_bearing(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(marker in compact for marker in _FUTURE_MARKERS)


def refresh_open_loops(
    cfg: Config,
    memory: Memory,
    chat_id: int,
    source_entry_id: int,
    text: str | None,
    *,
    now: datetime | None = None,
    client: OpenAI | None = None,
) -> int:
    """Resolve explicit outcomes, then asynchronously-safe extract new loops.

    The caller invokes this after the reply is terminal, so extraction latency
    never delays the user's bubbles.  All writes are idempotent in ``Memory``.
    """
    if not cfg.open_loops or not (spoken := (text or "").strip()):
        return 0
    now = now or datetime.now(cfg.tz)
    memory.resolve_open_loops_from_text(chat_id, spoken, now)
    if not _looks_future_bearing(spoken):
        return 0

    if client is None:
        from .engine import _client

        client = _client(cfg)
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": f"此刻：{now.isoformat(timespec='minutes')}\n用户说：{spoken}",
        },
    ]
    kwargs: dict = {}
    if cfg.json_prefix:
        messages.append({"role": "assistant", "content": "{", "prefix": True})
    else:
        kwargs["response_format"] = {"type": "json_object"}
    model = cfg.memory_model or cfg.model
    response = client.chat.completions.create(
        model=model,
        max_tokens=500,
        messages=messages,
        **kwargs,
    )
    choice = response.choices[0]
    raw = choice.message.content or ""
    candidates = parse_open_loop_candidates(raw, now)
    if not candidates and cfg.json_prefix and raw.lstrip().startswith(('"', "“")):
        candidates = parse_open_loop_candidates("{" + raw, now)
    if not candidates:
        category = "empty" if not raw else "no_valid_loops"
        log.info(
            "open_loop_extract model=%s finish_reason=%s length=%d category=%s",
            model,
            getattr(choice, "finish_reason", None) or "unknown",
            len(raw),
            category,
        )
        return 0
    for candidate in candidates:
        memory.upsert_open_loop(
            chat_id=chat_id,
            title=candidate.title,
            kind=candidate.kind,
            due_at=candidate.due_at,
            source_entry_id=source_entry_id,
            now=now,
        )
    log.info("open_loop_extract model=%s category=saved count=%d", model, len(candidates))
    return len(candidates)
