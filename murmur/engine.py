"""组装上下文 → 调模型 → 拿回一句话。

走 OpenAI 兼容接口（OpenCode Zen 网关），不是 Anthropic SDK。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from .config import Config
from .memory import Entry, Memory
from .moment import Moment
from .persona import OUTPUT_SCHEMA, SYSTEM
from .photo import Photo

log = logging.getLogger("murmur.engine")

MAX_TOKENS = 900  # 一句话很短，但有些模型会先想一会儿，留点余量


@dataclass
class Reply:
    scene: str
    move: str  # speak | brief | quiet
    say: list[str]  # 一条一条发，每项是一条独立消息

    @property
    def silent(self) -> bool:
        return self.move == "quiet" or not self.say

    @property
    def joined(self) -> str:
        """存进数据库和打日志用。真正发出去要用 say 逐条发。"""
        return " ⏎ ".join(self.say)


def history_turns(recent: list[Entry]) -> list[dict]:
    """把历史记录还原成真正的多轮对话。

    之前是压成一段"最近几次"的摘要塞进 prompt，结果模型会失忆——
    上一句他刚说是他妈打的电话，下一句它就问"刚才那通电话是谁打的"。
    聊天模型要 user/assistant 交替的消息序列才接得住上下文。
    """
    turns: list[dict] = []
    for e in recent:
        # 它主动开口的记录没有"他"那一轮：只有它说的话 + 他可能回的话。
        # 之前这里会把它拼成一条"[图：...]"的 user 消息——等于把它的
        # 主动消息算在了他头上，后面的模型会以为是他发了张图。
        if e.kind == "out":
            if e.said:
                turns.append({"role": "assistant", "content": e.said})
            if e.reply:
                turns.append({"role": "user", "content": e.reply})
            continue
        # 他那一轮：有图就描述图，有话就带上话
        bits = []
        if e.scene and e.scene != "（纯文字）":
            bits.append(f"[图：{e.scene}]")
        if e.note:
            bits.append(e.note)
        if not bits:
            continue
        turns.append({"role": "user", "content": " ".join(bits)})

        # 它那一轮
        if e.move == "quiet" or not e.said:
            turns.append({"role": "assistant", "content": "（这次没说话）"})
        else:
            turns.append({"role": "assistant", "content": e.said})

        # 他之后又回的话，单独算一轮
        if e.reply:
            turns.append({"role": "user", "content": e.reply})
    return turns


def build_context(
    moment: Moment,
    visits: int,
    note: str | None,
    has_photo: bool = True,
) -> str:
    """只描述"这一条"消息。历史走 history_turns()。"""
    lines = [f"此刻：{moment.describe()}"]

    if visits >= 3:
        lines.append(
            f"（系统提示，别点破）他在同一个地方的{moment.bucket}发过 {visits} 次图，"
            "这大概是他的日常场景。"
        )

    if note:
        lines.append(f"他随图说了：{note}" if has_photo else f"他说：{note}")
    if not has_photo:
        lines.append("（这次没有图，只有他这句话。必须回，不要选 quiet。）")

    return "\n".join(lines)


def _as_obj(value) -> dict | None:
    """模型偶尔会把结果包成单元素数组返回，剥掉。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((x for x in value if isinstance(x, dict)), None)
    return None


class BubbleStreamer:
    """从流式 JSON 里边收边抠出 say[] 中已经写完的那几条。

    不这么做的话，第一条气泡要等整个 JSON 生成完——实测 21 秒。
    这样第一条通常 3-5 秒就能发出去，剩下的边生成边发。
    """

    def __init__(self):
        self.buf = ""
        self._start = -1
        self._emitted = 0

    def feed(self, chunk: str) -> list[str]:
        self.buf += chunk
        if self._start < 0:
            m = re.search(r'"say"\s*:\s*\[', self.buf)
            if not m:
                return []
            self._start = m.end()

        s, i, done = self.buf, self._start, []
        while i < len(s):
            ch = s[i]
            if ch == '"':
                j, esc = i + 1, False
                while j < len(s):
                    if esc:
                        esc = False
                    elif s[j] == "\\":
                        esc = True
                    elif s[j] == '"':
                        break
                    j += 1
                if j >= len(s):
                    break  # 这条还没写完，等下一块
                try:
                    done.append(json.loads(s[i : j + 1]))
                except json.JSONDecodeError:
                    pass
                i = j + 1
            elif ch == "]":
                break
            else:
                i += 1

        fresh = done[self._emitted :]
        self._emitted = len(done)
        return [x for x in fresh if x.strip()]


def _as_bubbles(value) -> list[str]:
    """say 应该是数组。但模型偶尔会给一整个字符串——
    那就按换行拆开，别把 '\\n' 当字面量发出去。"""
    if isinstance(value, str):
        parts = [ln.strip() for ln in value.splitlines()]
    elif isinstance(value, list):
        parts = [str(x).strip() for x in value]
    else:
        return []
    # 太长的一条再兜一道：模型有时不分段，塞一大坨进来
    out: list[str] = []
    for p in parts:
        if p:
            out.append(p)
    return out[:3]


def _extract_json(text: str) -> dict:
    """有些模型会包代码块、或把 <think> 写进正文。都剥掉再解析。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    try:
        if (obj := _as_obj(json.loads(text))) is not None:
            return obj
    except json.JSONDecodeError:
        pass
    # 兜底：抓第一个花括号平衡的片段
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                try:
                    if (obj := _as_obj(json.loads(text[start : i + 1]))) is not None:
                        return obj
                except json.JSONDecodeError:
                    pass
                break
    raise ValueError(f"模型没有返回可解析的 JSON：{text[:200]}")


def _too_similar(text: str, previous: list[str]) -> bool:
    """一天十几条，光靠提示词说"别重复"挡不住——实测会一字不差地重发。
    这里用代码兜一道：整句相同、或开头一样、或用词高度重合都算重复。"""
    def norm(s: str) -> str:
        return re.sub(r"[\s，。、！？~…'\"]", "", s)

    a = norm(text)
    if not a:
        return True
    for p in previous:
        b = norm(p)
        if not b:
            continue
        if a == b:
            return True
        if len(a) >= 6 and len(b) >= 6 and a[:6] == b[:6]:
            return True
        # 字符集重合度：短句用这个判近义比编辑距离稳
        overlap = len(set(a) & set(b)) / max(len(set(a)), len(set(b)))
        if overlap > 0.72:
            return True
    return False


def _response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "reply", "strict": True, "schema": OUTPUT_SCHEMA},
    }


def _fallback_attempts(cfg: Config, *, primary: str) -> list[tuple[str, bool]]:
    """(模型, 是否带 json_schema) 的尝试序列。

    降级模型一律不带 json_schema——deepseek-v4-flash / mimo-v2.5 在
    OpenCode 网关上不支持 response_format，靠 SYSTEM 提示词里的
    「只返回 JSON」约束输出，_extract_json 负责剥代码块。
    """
    attempts = [(primary, True)]
    fallback = (cfg.fallback_model or "").strip()
    if fallback and fallback != primary:
        attempts.append((fallback, False))
    return attempts


def initiate(
    moment: Moment,
    mem: Memory,
    cfg: Config,
    intent,
    *,
    chat_id: int = 0,
    _retry: bool = True,
    dossier: str | None = None,
) -> Reply:
    """主动开口。没有人发消息给你，是你自己想说一句。

    关键在于把"最近主动说过什么"回传给模型——一天十几条，
    不给它看历史它必然重复。
    """
    said_before = mem.recent_outbound(chat_id, limit=8)
    lines = [
        f"此刻：{moment.describe()}",
        "",
        "**这次是你主动开口**，没有人发消息给你。",
        f"这次的意图是「{intent.key}」：{intent.brief}",
    ]
    if said_before:
        lines += ["", "你最近主动说过这些（绝对不要重复，说法也要换）："]
        lines += [
            f"- [{e.intent or '?'}] {e.said}" + (f"  → 他回「{e.reply}」" if e.reply else "  → 他没回")
            for e in said_before
        ]
    else:
        lines += ["", "（你还没主动说过话，这是第一次）"]

    messages = [
        {"role": "system", "content": SYSTEM},
        *([{"role": "system", "content": dossier}] if dossier else []),
        *history_turns(mem.recent(chat_id, limit=6)),
        {"role": "user", "content": "\n".join(lines)},
    ]

    last_error: Exception | None = None
    for model, use_schema in _fallback_attempts(cfg, primary=cfg.model):
        try:
            reply = _initiate_once(
                cfg, model, use_schema, messages,
                moment=moment, mem=mem, intent=intent, chat_id=chat_id,
                said_before=said_before, dossier=dossier, _retry=_retry,
            )
            if last_error is not None:
                log.info("主动消息：降级模型 %s 接住了", model)
            return reply
        except (OpenAIError, ValueError) as error:
            log.warning(
                "主动消息：模型 %s 失败（%s），尝试降级",
                model, type(error).__name__,
            )
            last_error = error
    raise last_error  # attempts 至少有一个，跑不到这里才怪


def _initiate_once(
    cfg: Config,
    model: str,
    use_schema: bool,
    messages: list[dict],
    *,
    moment: Moment,
    mem: Memory,
    intent,
    chat_id: int,
    said_before,
    dossier: str | None,
    _retry: bool,
) -> Reply:
    kwargs = dict(model=model, max_tokens=MAX_TOKENS, messages=messages)
    if use_schema:
        kwargs["response_format"] = _response_format()
    resp = _client(cfg).chat.completions.create(**kwargs)
    raw = resp.choices[0].message.content or ""
    try:
        data = _extract_json(raw)
    except ValueError:
        salvaged = BubbleStreamer().feed(raw)[:2]
        if not salvaged and "{" not in raw:
            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            if (
                1 <= len(lines) <= 3
                and len(raw) <= 150
                and all(len(x) <= 60 for x in lines)
            ):
                salvaged = lines[:2]
        if not salvaged:
            raise
        return Reply(scene="（主动·输出被截断）", move="speak", say=salvaged)

    move = data.get("move", "quiet")
    if move not in ("speak", "brief", "quiet"):
        move = "brief"
    # 主动开口最多两条。三条太扑，像有事求人。
    say = _as_bubbles(data.get("say"))[:2]

    # 代码层去重：重了就重来一次，还重就这次不发。
    prev = [e.said for e in said_before if e.said]
    if say and _too_similar(" ".join(say), prev):
        if _retry:
            log.info("主动消息和之前太像，重试一次")
            return initiate(moment, mem, cfg, intent, chat_id=chat_id,
                        _retry=False, dossier=dossier)
        log.info("主动消息重复，跳过这次")
        return Reply(scene="（跳过：和之前重复）", move="quiet", say=[])

    return Reply(scene=str(data.get("scene", "")), move=move, say=say)


_client_cache: dict[tuple[str, str], OpenAI] = {}
_client_lock = threading.Lock()


def _client(cfg: Config) -> OpenAI:
    if not cfg.api_key:
        raise RuntimeError(
            "没有 OPENCODE_API_KEY。把 .env.example 复制成 .env 填进去，"
            "或者先用 --dry-run 看拼出来的 prompt。"
        )
    # 每次回复都新建 client 的话，底层 httpx 连接池也一起重建，
    # 每张图多付一次 TCP+TLS 握手。OpenAI SDK 的 client 是线程安全的，
    # 按 (key, base_url) 缓存复用即可。
    key = (cfg.api_key, cfg.base_url)
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    with _client_lock:
        if key not in _client_cache:
            _client_cache[key] = OpenAI(
                api_key=cfg.api_key, base_url=cfg.base_url, timeout=90.0
            )
    return _client_cache[key]


def respond(
    moment: Moment,
    mem: Memory,
    cfg: Config,
    *,
    photo: Photo | None = None,
    note: str | None = None,
    chat_id: int = 0,
    on_bubble=None,
    dossier: str | None = None,
) -> Reply:
    """photo 为 None 时是纯文字消息——照样要回。

    传了 on_bubble 就走流式：每写完一条气泡立刻回调，第一条不用等全部生成完。

    降级备案：带图消息走 MURMUR_IMAGE_MODEL（mimo-v2.5，能看图）；
    任何模型抛网关错误或吐不出 JSON 时，自动换 MURMUR_FALLBACK_MODEL
    （deepseek-v4-flash）再试一次——降级模型不支持 json_schema、
    也不看图，所以降级调用去掉 response_format，带图消息降级时退回
    纯文本描述（EXIF/时间仍在上下文里）。
    """
    context = build_context(
        moment,
        mem.spot_visits(chat_id, moment.spot, moment.bucket),
        note,
        has_photo=photo is not None,
    )
    history = history_turns(mem.recent(chat_id, limit=8))

    image_block: dict | None = None
    if photo is not None:
        image_block = {
            "type": "image_url",
            "image_url": {
                "url": f"data:{photo.media_type};base64,{photo.image_b64}"
            },
        }

    client = _client(cfg)
    if image_block is not None and (cfg.image_model or "").strip():
        # 带图消息：mimo 系多模态模型（不支持 json_schema，靠提示词约束）
        attempts = [(cfg.image_model, False)]
    else:
        attempts = [(cfg.model, True)]
    fallback = (cfg.fallback_model or "").strip()
    if fallback and fallback != attempts[0][0]:
        attempts.append((fallback, False))

    last_error: Exception | None = None
    for model, use_schema in attempts:
        include_image = image_block is not None and model == attempts[0][0]
        content: list[dict] = []
        if include_image:
            content.append(image_block)
        content.append({"type": "text", "text": context})
        try:
            reply = _respond_once(
                model=model,
                use_schema=use_schema,
                content=content,
                history=history,
                dossier=dossier,
                client=client,
                on_bubble=on_bubble,
                photo=photo,
            )
            if last_error is not None:
                log.info("降级模型 %s 接住了回复", model)
            return reply
        except (OpenAIError, ValueError) as error:
            log.warning(
                "模型 %s 失败（%s），尝试降级", model, type(error).__name__,
            )
            last_error = error
    raise last_error  # attempts 至少有一个，跑不到这里才怪


def _respond_once(
    *,
    model: str,
    use_schema: bool,
    content: list[dict],
    history: list[dict],
    dossier: str | None,
    client: OpenAI,
    on_bubble,
    photo: Photo | None,
) -> Reply:
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM},
            # 长期记忆放在 system 之后、对话之前：它是背景知识，
            # 不是这一轮的输入，混进 user turn 会被当成他刚说的话。
            *([{"role": "system", "content": dossier}] if dossier else []),
            *history,
            {"role": "user", "content": content},
        ],
    )
    if use_schema:
        kwargs["response_format"] = _response_format()

    if on_bubble is None:
        raw = client.chat.completions.create(**kwargs).choices[0].message.content or ""
    else:
        streamer = BubbleStreamer()
        raw = ""
        for chunk in client.chat.completions.create(stream=True, **kwargs):
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if not piece:
                continue
            raw += piece
            for bubble in streamer.feed(piece):
                on_bubble(bubble)
    try:
        data = _extract_json(raw)
    except ValueError:
        # JSON 截断了（模型话太多撑爆 max_tokens）。别整个崩掉——
        # 把已经写完整的那几条气泡捞出来照样能用。
        salvaged = BubbleStreamer().feed(raw)[:3]
        if not salvaged and "{" not in raw:
            # 有些模型（kimi-k2.6 在 OpenCode 网关上有过）会无视
            # json_schema，直接吐纯文本气泡——内容是对的，别丢掉。
            # 只收"像回复"的短文本：气泡每条 ≤60 字、至多 3 条，
            # 太长或带思考痕迹的（超过 150 字）仍交给降级模型。
            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            if (
                1 <= len(lines) <= 3
                and len(raw) <= 150
                and all(len(x) <= 60 for x in lines)
            ):
                salvaged = lines[:3]
        if not salvaged:
            raise
        log.warning("模型没按 JSON 返回，抢救出 %d 条气泡", len(salvaged))
        return Reply(scene="（输出被截断）", move="speak", say=salvaged)

    move = data.get("move", "quiet")
    if move not in ("speak", "brief", "quiet"):
        move = "brief"

    say = _as_bubbles(data.get("say"))

    # 纯文字消息不许沉默。提示词里写了，这里再兜一道——
    # 模型偶尔还是会选 quiet，而"跟它说话没反应"是最劝退的体验。
    if photo is None and (move == "quiet" or not say):
        move, say = "brief", (say or ["嗯"])

    return Reply(scene=str(data.get("scene", "")), move=move, say=say)
