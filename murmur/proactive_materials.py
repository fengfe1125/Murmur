"""Source-bound material for proactive messages.

An intent is merely a shape ("follow up", "say something small").  Material is
the concrete fact that justifies it.  Keeping them separate prevents a callback
from being generated with one source and later described as another.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ProactiveMaterial:
    material_id: str
    category: str
    source_ref: str


def _stable_id(category: str, source_ref: str) -> str:
    digest = hashlib.sha256(f"{category}\0{source_ref}".encode()).hexdigest()[:20]
    return f"{category}:{digest}"


def dossier_materials(blocks: dict[str, str]) -> list[ProactiveMaterial]:
    """Turn explicit current-topic bullets into stable material candidates."""
    body = (blocks.get("正在发生") or "").strip()
    if not body or body == "（还不知道）":
        return []
    out: list[ProactiveMaterial] = []
    seen: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if not line or line == "（还不知道）" or line in seen:
            continue
        seen.add(line)
        out.append(ProactiveMaterial(
            _stable_id("current_topic", line), "current_topic", line
        ))
    return out


def select_material(
    candidates: list[ProactiveMaterial],
    cooldowns: dict[str, datetime],
    now: datetime,
) -> ProactiveMaterial | None:
    """Pick the first source whose material-level cooldown has elapsed."""
    for candidate in candidates:
        until = cooldowns.get(candidate.material_id)
        if until is None or until <= now:
            return candidate
    return None
