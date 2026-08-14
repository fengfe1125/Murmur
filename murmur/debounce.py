"""连发合并：他一口气发几句，等他说完再回一次。

为什么需要：
2026-08-13 上午实测，他 90 秒内发了 4 句话，机器人对每句都单独回了
3 条气泡——他收到 12 条。更早那段 6 分钟里往返 9 次，22 条气泡。
那不是聊天，是刷屏。

真人的行为是：看到对方还在打字就等一下，几句话一起读完，回一次。
这里就是模拟那个"等一下"。

用序号而不是定时器：每条消息进来时领一个号，睡够静默期后
看自己是不是最后一个——不是就闭嘴，让最后那条去回。
比起管理一堆 timer，这个实现不会漏也不会重。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

# 等多久算"他说完了"。太短起不到合并作用，太长他会觉得你反应慢。
# 3.5 秒大约是打完一句短消息再打下一句的间隔。
WINDOW = 3.5


@dataclass
class Batch:
    texts: list[str] = field(default_factory=list)
    photo: Any = None          # 最后一张图（连发多张时只看最新那张）
    extra: dict = field(default_factory=dict)

    @property
    def note(self) -> str | None:
        joined = "\n".join(t for t in self.texts if t).strip()
        return joined or None


class Debouncer:
    """按会话合并。用法：

        batch = deb.arrive(key, text=..., photo=...)
        await/sleep(deb.window)
        batch = deb.claim(key)     # 返回 None 表示后面还有消息，这次别回
    """

    def __init__(self, window: float = WINDOW):
        self.window = window
        self._lock = threading.Lock()
        self._batches: dict[Any, Batch] = {}
        self._seq: dict[Any, int] = {}
        self._mine = threading.local()

    def arrive(self, key: Any, *, text: str | None = None, photo=None,
               extra: dict | None = None) -> int:
        """登记一条新到的消息，返回这条的序号。"""
        with self._lock:
            b = self._batches.setdefault(key, Batch())
            if text:
                b.texts.append(text)
            if photo is not None:
                b.photo = photo
            if extra:
                b.extra.update(extra)
            n = self._seq.get(key, 0) + 1
            self._seq[key] = n
            return n

    def is_latest(self, key: Any, seq: int) -> bool:
        with self._lock:
            return self._seq.get(key) == seq

    def take(self, key: Any) -> Batch | None:
        with self._lock:
            return self._batches.pop(key, None)

    def claim(self, key: Any, seq: int) -> Batch | None:
        """静默期过后调用。只有最后一条能拿到批次，其余返回 None。"""
        with self._lock:
            if self._seq.get(key) != seq:
                return None          # 后面还有消息，让它去回
            return self._batches.pop(key, None)
