"""长期记忆文件：每条 thread 一份，磁盘上一个 markdown。

为什么是文件而不是数据库表：
- 你可以直接打开看它记住了什么，也可以手改（记错了就删掉那行）
- 模型看到的就是文件原文，不需要我们把结构再翻译一遍给它

结构借了 Letta (MemGPT) 的 memory block（label + value + 字数上限），
分区借了 kirabot 的 JSON 分块（life_events / current_topics / hard_facts）。
"整理"这一步单独跑，不占对话回路——这是 Letta 的 sleep-time agent 思路：
用户在等回复的时候不该为整理记忆付延迟。

参考见 CREDITS.md。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

from .config import Config
from .memory import Entry, Memory

log = logging.getLogger("murmur.dossier")

# 每块的字数上限。超了就得删旧的——上限是硬约束，
# 不设的话文件会一路长到吃掉整个上下文。
BLOCKS: dict[str, tuple[str, int]] = {
    "他是谁": (
        "长期不变的事实：作息、工作、住哪一类、在意的人（写名字和关系）、"
        "口味偏好、身体状况。只记确认过的，别记推测。",
        1200,
    ),
    "正在发生": (
        "这段时间在进行的事：在找工作、猫生病了、下周有面试、在减肥。"
        "带上时间。结束了就删掉。",
        900,
    ),
    "怎么跟他说话": (
        "哪种说法他接了、哪种他没理、他反感什么。"
        "从他的回复里学到的，是这份文件里最值钱的部分。",
        700,
    ),
}

# 攒够多少条新记录就整理一次。kirabot 用 15，这里用 12。
REFRESH_EVERY = 12


@dataclass
class Dossier:
    thread: str
    path: Path
    blocks: dict[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    covered_upto: int = 0  # 已经整理到哪条 entry id

    # ---------- 磁盘 ----------

    @classmethod
    def load(cls, root: Path, thread: str) -> Dossier:
        path = root / f"{_safe(thread)}.md"
        d = cls(thread=thread, path=path)
        if not path.exists():
            return d
        text = path.read_text(encoding="utf-8")
        # 头部的 HTML 注释里存元数据，人看不见但机器读得到
        if m := re.search(r"<!--\s*murmur\s+(\{.*?\})\s*-->", text, re.S):
            try:
                meta = json.loads(m.group(1))
                d.updated_at = meta.get("updated_at")
                d.covered_upto = int(meta.get("covered_upto", 0))
            except (json.JSONDecodeError, ValueError):
                pass
        for name in BLOCKS:
            if mm := re.search(
                rf"^##\s*{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)",
                text, re.S | re.M,
            ):
                d.blocks[name] = mm.group(1).strip()
        return d

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        meta = json.dumps(
            {"updated_at": self.updated_at, "covered_upto": self.covered_upto},
            ensure_ascii=False,
        )
        parts = [
            f"<!-- murmur {meta} -->",
            f"# {self.thread}",
            "",
            "<!-- 这个文件是它对你的长期记忆。可以直接改：记错了就删掉那行。 -->",
            "",
        ]
        for name, (_hint, _limit) in BLOCKS.items():
            body = (self.blocks.get(name) or "").strip() or "（还不知道）"
            parts += [f"## {name}", "", body, ""]
        self.path.write_text("\n".join(parts), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # ---------- 给提示词用 ----------

    def as_prompt(self) -> str:
        """塞进 system 之后、对话之前。空的就返回空串，别浪费 token。"""
        filled = {
            k: v for k, v in self.blocks.items()
            if v and v.strip() and v.strip() != "（还不知道）"
        }
        if not filled:
            return ""
        lines = ["你对他的长期记忆（不要复述这些，只是知道）："]
        for name, body in filled.items():
            lines.append(f"【{name}】{body}")
        return "\n".join(lines)

    @property
    def is_empty(self) -> bool:
        return not self.as_prompt()


def _safe(thread: str) -> str:
    """thread 标签里有 / 和 base64 的 +=，不能直接当文件名。"""
    return re.sub(r"[^0-9A-Za-z_.-]", "_", thread)[:120]


# ---------- 整理（离线跑，不在对话回路里） ----------

_REWRITE = """\
你在维护一份关于某个人的长期记忆文件。

下面给你三样东西：
1. 现有的记忆文件（可能是空的）
2. 自上次整理以来的新对话记录
3. 每个分区该记什么、字数上限

你的任务是**重写**这三个分区。规则：

- **只记他明说过的事，不要补充任何他没说的属性。**
  他说"我妈问我什么时候结婚" → 可以记"母亲会催婚"，
  不能记"和母亲关系紧张"（这是推测）。
  尤其**不要凭名字或语气猜性别、年龄、婚恋状况、宠物性别**——
  他没说就是不知道，写上去就是错的，而且错了很难被发现。
  实测这里最容易出错：模型会给"沈默"补一个"男"，给"年糕"补一个"公猫"。
  一个字都不要补。
- **合并而不是堆叠。** 同一件事有新进展就改写那一条，别又加一行。
- **过期的删掉。** "下周面试"过了两周就该删，或者改成结果。
- **每个分区不超过给定字数。** 超了就砍掉最不重要的。
- 用短句，一行一件事，前面加 `- `。
- 【怎么跟他说话】这一块最重要：从他回了什么、没回什么里总结。
  比如"问'为什么'他会回避，改成陈述句他会自己说"。
- 什么都没学到的分区，写"（还不知道）"。

只返回 JSON：{"他是谁": "...", "正在发生": "...", "怎么跟他说话": "..."}
"""


def _fmt_entries(entries: list[Entry]) -> str:
    out = []
    for e in entries:
        when = " ".join(x for x in (e.weekday, e.bucket) if x)
        who = "它主动" if e.kind == "out" else "他"
        bits = [f"[{when}] {who}"]
        if e.note:
            bits.append(f"说「{e.note}」")
        if e.scene and e.scene not in ("（纯文字）", ""):
            bits.append(f"发了图：{e.scene}")
        if e.said:
            bits.append(f"→ 它回「{e.said}」")
        elif e.move == "quiet":
            bits.append("→ 它没说话")
        if e.reply:
            bits.append(f"→ 他回「{e.reply}」")
        out.append(" ".join(bits))
    return "\n".join(out)


def refresh(
    cfg: Config, mem: Memory, chat_id: int, thread: str, root: Path | None = None,
    force: bool = False,
) -> Dossier | None:
    """把新记录消化进记忆文件。攒够 REFRESH_EVERY 条才动，force=True 强制。

    返回更新后的 Dossier，没动就返回 None。
    """
    root = root or (cfg.db_path.parent / "dossiers")
    d = Dossier.load(root, thread)

    rows = mem.conn.execute(
        "SELECT * FROM entries WHERE chat_id = ? AND id > ? ORDER BY id",
        (chat_id, d.covered_upto),
    ).fetchall()
    if not rows:
        return None
    if len(rows) < REFRESH_EVERY and not force:
        return None

    from .memory import _row_to_entry
    entries = [_row_to_entry(r) for r in rows]
    newest_id = rows[-1]["id"]

    spec = "\n".join(f"- 【{k}】{h}（上限 {n} 字）" for k, (h, n) in BLOCKS.items())
    current = d.as_prompt() or "（还是空的，这是第一次整理）"

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=120.0)
    # 整理记忆是"重写三块摘要"，不需要主模型的对话能力。
    # 默认跟随主模型，.env 里配 MURMUR_MEMORY_MODEL 可以换成更便宜的。
    resp = client.chat.completions.create(
        model=cfg.memory_model or cfg.model,
        max_tokens=1800,
        messages=[
            {"role": "system", "content": _REWRITE},
            {"role": "user", "content": (
                f"分区说明：\n{spec}\n\n"
                f"现有记忆：\n{current}\n\n"
                f"新的对话记录（{len(entries)} 条）：\n{_fmt_entries(entries)}"
            )},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.M)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("整理记忆失败，模型没返回 JSON（响应长度=%d）", len(raw))
        return None

    for name, (_, limit) in BLOCKS.items():
        val = str(data.get(name, "")).strip()
        d.blocks[name] = val[:limit] if val else "（还不知道）"
    d.covered_upto = newest_id
    d.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    d.save()
    log.info("记忆文件已更新 %s（消化 %d 条）", d.path.name, len(entries))
    return d
