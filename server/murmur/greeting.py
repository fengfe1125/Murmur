"""第一次遇到一个人时说的话。

两边（Telegram / 钉钉）共用同一份，改这里就够了。
一条一条发，跟它平时说话的节奏一致——挤成一大段就变成公告了。
"""

from __future__ import annotations

INTRO: list[str] = [
    "嗨，我是 Murmur",
]

JOINED = " ⏎ ".join(INTRO)
