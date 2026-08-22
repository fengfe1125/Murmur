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
from .persona import OUTPUT_SCHEMA, READING_SYSTEM, SYSTEM
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
        # 模型偶尔把历史里的 ' ⏎ ' 拼接符模仿回单条气泡，流式路径同样拆掉。
        return [
            piece.strip()
            for x in fresh
            for piece in x.split("⏎")
            if piece.strip()
        ]


def _as_bubbles(value) -> list[str]:
    """say 应该是数组。但模型偶尔会给一整个字符串——
    那就按换行拆开，别把 '\\n' 当字面量发出去。"""
    if isinstance(value, str):
        parts = [ln.strip() for ln in value.splitlines()]
    elif isinstance(value, list):
        parts = [str(x).strip() for x in value]
    else:
        return []
    # 喂历史时多条气泡用 ' ⏎ ' 拼接，模型有时会把这个符号原样模仿回
    # 单条气泡里——按 ⏎ 再拆一次，别让它出现在他看到的文字里。
    out: list[str] = []
    for p in parts:
        for piece in p.split("⏎"):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out[:3]


def _parse_json_obj(text: str) -> dict | None:
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
                    return _as_obj(json.loads(text[start : i + 1]))
                except json.JSONDecodeError:
                    pass
                break
    return None


def _extract_json(text: str) -> dict:
    """有些模型会包代码块、或把 <think> 写进正文。都剥掉再解析。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    if (obj := _parse_json_obj(text)) is not None:
        return obj
    # deepseek 系偶尔用全角引号当 JSON 定界符。只在正常解析失败后才
    # 试这一招：替换完还是非法就照旧抛，不会把能解析的文本弄坏。
    if "“" in text or "”" in text:
        if (obj := _parse_json_obj(text.replace("“", '"').replace("”", '"'))) is not None:
            return obj
    raise ValueError(f"模型没有返回可解析的 JSON：{text[:200]}")


# ---- 当年今日的读图（reading） -----------------------------------------------

READING_TIMEOUT = 90.0  # mimo 读一张图要想 20-30 秒，他盯着屏幕等，但等得来
READING_MAX_TOKENS = 2500  # mimo-v2.5 的思考先烧掉约 1100，500/900 档实测全部截断
ANGLES_MAX_CHARS = 14  # 硬约束是 ≤12 个汉字，字符数留两格兜底
GUESS_MAX_CHARS = 40  # 硬约束是 ≤30 个汉字，同上

# emoji 与其包装字符（含 ⏎ 所在的 Misc Technical 段——那是历史拼接符，
# 绝不能出现在他看到的文字里）。
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\uFE0F\u20E3]"
)


@dataclass
class PhotoReading:
    """服务端看完一张照片之后交出来的那一屏。

    guess 是猜他想说什么（他看得到，是这间房里的第一句话），
    angles 是三个递到他手边的话头，scene 是存档用的客观画面描述。
    """

    guess: str
    angles: list[str]
    scene: str


def _clean_angles(value) -> list[str]:
    """逐条硬校验入口角度，不合格的丢掉——剩 1 条也照发，剩 0 条就不发。"""
    if isinstance(value, dict):  # 模型偶尔多包一层 {"angles": [...]}
        value = value.get("angles")
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        angle = item.strip()
        if (
            not angle
            or len(angle) > ANGLES_MAX_CHARS
            or "\n" in angle
            or "\r" in angle
            or _EMOJI.search(angle)
            or angle in out
        ):
            continue
        out.append(angle)
    return out[:3]


def _clean_guess(value) -> str:
    """一句话，一行，不带 emoji。太长就当模型没答上来。"""
    if not isinstance(value, str):
        return ""
    guess = " ".join(value.split())
    if not guess or len(guess) > GUESS_MAX_CHARS or _EMOJI.search(guess):
        return ""
    return guess


def _salvage_reading(text: str) -> PhotoReading | None:
    """JSON 被 max_tokens 截断时的抢救。

    prompt 要求按 guess → angles → scene 的顺序写，截断点几乎总在后面两截，
    而 guess 已经完整落地——把它（和写全了的话头）捞出来交付，比整屏退回
    普通回复强。只有带收尾引号的完整串才算数：写到一半的不要。
    """

    def unescape(raw: str) -> str:
        try:
            return json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            return ""

    m = re.search(r'"guess"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    guess = _clean_guess(unescape(m.group(1))) if m else ""
    if not guess:
        return None
    angles: list[str] = []
    # [^\]]* 截在数组收尾或文本末尾：后面的 scene 不许混进话头里
    if am := re.search(r'"angles"\s*:\s*\[([^\]]*)', text):
        angles = _clean_angles(
            [unescape(s) for s in re.findall(r'"((?:[^"\\]|\\.)*)"', am.group(1))]
        )
    log.warning("读图 JSON 被截断，抢救出 guess 和 %d 条话头", len(angles))
    return PhotoReading(guess=guess, angles=angles, scene="")


def _parse_reading(text: str) -> PhotoReading:
    """把模型输出解析成一屏读图。

    guess 是这一屏的主语——它空了就没有可交付的东西，抛 ValueError 让
    调用方降级；angles 和 scene 缺了都还能发。JSON 被 max_tokens 截断时
    先抢救已经写全的部分，抢不出来才抛。
    """
    try:
        obj = _extract_json(text)
    except ValueError:
        if (salvaged := _salvage_reading(text)) is not None:
            return salvaged
        raise
    guess = _clean_guess(obj.get("guess"))
    if not guess:
        raise ValueError(f"读图：没有可用的 guess：{text[:200]}")
    scene = obj.get("scene")
    return PhotoReading(
        guess=guess,
        angles=_clean_angles(obj.get("angles")),
        scene=" ".join(scene.split()) if isinstance(scene, str) else "",
    )


def read_photo(
    moment: Moment, photo: Photo, cfg: Config, *, dossier: str | None = None
) -> PhotoReading:
    """当年今日推过来一张旧照片，他还没说话：先看图，猜他想说什么。

    必须真的看得见图——这一屏的全部价值就是「上游读懂了这张照片」，
    所以只走 cfg.image_model，没有纯文字的降级档可言。任何失败都抛出去，
    由调用方退回普通回复（respond 有自己的降级链）。
    """
    model = (cfg.image_model or "").strip()
    if not model:
        raise ValueError("读图：没有配置 MURMUR_IMAGE_MODEL")
    content = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{photo.media_type};base64,{photo.image_b64}"
            },
        },
        {"type": "text", "text": f"此刻：{moment.describe()}"},
    ]
    raw = (
        _client(cfg)
        .chat.completions.create(
            model=model,
            max_tokens=READING_MAX_TOKENS,
            timeout=READING_TIMEOUT,
            messages=[
                {"role": "system", "content": READING_SYSTEM},
                # 长期记忆是背景知识，不是这一轮的输入。同 _respond_once。
                *([{"role": "system", "content": dossier}] if dossier else []),
                {"role": "user", "content": content},
            ],
        )
        .choices[0].message.content
        or ""
    )
    return _parse_reading(raw)


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


def _fallback_attempts(
    cfg: Config, *, primary: str, use_schema: bool = True,
) -> list[tuple[str, bool]]:
    """(模型, 是否带 json_schema) 的尝试序列。

    降级模型一律不带 json_schema——deepseek-v4-flash / mimo-v2.5 在
    OpenCode 网关上不支持 response_format，靠 SYSTEM 提示词里的
    「只返回 JSON」约束输出，_extract_json 负责剥代码块。主模型是否
    带由 MURMUR_JSON_SCHEMA 决定（glm / deepseek 系都不支持）。
    """
    attempts = [(primary, use_schema)]
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
    for model, use_schema in _fallback_attempts(
        cfg, primary=cfg.model, use_schema=cfg.json_schema
    ):
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
        attempts = [(cfg.model, cfg.json_schema)]
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
                json_prefix=cfg.json_prefix,
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
    json_prefix: bool = False,
) -> Reply:
    messages = [
        {"role": "system", "content": SYSTEM},
        # 长期记忆放在 system 之后、对话之前：它是背景知识，
        # 不是这一轮的输入，混进 user turn 会被当成他刚说的话。
        *([{"role": "system", "content": dossier}] if dossier else []),
        *history,
        {"role": "user", "content": content},
    ]
    if json_prefix and not use_schema:
        # deepseek 直连实测不吃 SYSTEM 里的「只返回 JSON」（十次有九次
        # 直接回聊天正文），但吃 beta 端点的 assistant prefix：把回复的
        # 第一个字符钉死成 "{"，它只能接着把 JSON 写完。
        messages.append({"role": "assistant", "content": "{", "prefix": True})
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=messages,
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
    if json_prefix and not use_schema:
        raw = "{" + raw
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
        return Reply(scene="（输出被截断）", move="speak", say=_as_bubbles(salvaged))

    move = data.get("move", "quiet")
    if move not in ("speak", "brief", "quiet"):
        move = "brief"

    say = _as_bubbles(data.get("say"))

    # 纯文字消息不许沉默。提示词里写了，这里再兜一道——
    # 模型偶尔还是会选 quiet，而"跟它说话没反应"是最劝退的体验。
    if photo is None and (move == "quiet" or not say):
        move, say = "brief", (say or ["嗯"])

    return Reply(scene=str(data.get("scene", "")), move=move, say=say)
