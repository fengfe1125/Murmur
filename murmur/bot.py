"""Telegram 入口。

发图 → 它看一眼 → 回一句，或者不说话。
"quiet 就真的不发消息" 是这个 bot 的核心行为，不要改成发个省略号。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import time as dtime

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import photo as photo_mod
from . import watchdog
from .config import Config
from .debounce import Debouncer
from .dossier import Dossier, refresh
from .engine import initiate, respond
from .greeting import INTRO, JOINED
from .initiative import LATE_GRACE, pick_intent, plan_day, should_hold, wants_stop
from .memory import Memory, thread_key
from .moment import Moment


def _dossier_root(cfg: Config):
    return cfg.db_path.parent / "dossiers"


async def ensure_greeted(ctx, cfg: Config, mem: Memory, chat_id: int,
                         key: int, label: str) -> bool:
    """新人首次出现：入册 + 发自我介绍 + 建记忆文件。已打过招呼返回 False。"""
    # 入册放在 has_greeted 之前：老用户是在名册这张表出现之前就聊上的，
    # 不补一次的话他们永远不在册，主动消息就断了。
    if mem.enroll("tg", str(chat_id), key, label):
        log.info("新人入册｜chat=%s｜从下一轮起也会收到主动消息", chat_id)
    if mem.has_greeted(key):
        return False
    for i, text in enumerate(INTRO):
        if i:
            await asyncio.sleep(1.8)
        await ctx.bot.send_message(chat_id, text)
    moment = Moment.text_only(cfg.tz)
    mem.record(chat_id=key, thread=label, shot_at=None, bucket=moment.bucket,
               weekday=moment.weekday, spot=None, scene="（自我介绍）", move="speak",
               said=JOINED, note=None, kind="out", intent="自我介绍")
    mem.mark_greeted(key, label)
    try:
        d = Dossier.load(_dossier_root(cfg), label)
        if not d.path.exists():
            d.save()
    except Exception as e:
        log.warning("建记忆文件失败：%s", e)
    log.info("已向新用户 %s 发送自我介绍", label)
    return True


async def _maybe_refresh(cfg: Config, mem: Memory, key: int, label: str) -> None:
    """整理记忆放在回复发出之后，用户等不到这一步——
    这是 Letta sleep-time agent 的思路：别让人为整理付延迟。"""
    try:
        await asyncio.to_thread(refresh, cfg, mem, key, label, _dossier_root(cfg))
    except Exception as e:
        log.warning("整理记忆失败（不影响聊天）：%s: %s", type(e).__name__, e)


def _load_dossier(cfg: Config, label: str) -> str | None:
    d = Dossier.load(_dossier_root(cfg), label)
    return None if d.is_empty else d.as_prompt()


def _thread(update: Update) -> tuple[int, str]:
    """一个人一条上下文。群里也要按人分开——
    只按 chat_id 的话，群里 A 的心事会串进 B 的回复。"""
    return thread_key(
        "tg", str(update.effective_chat.id), str(update.effective_user.id)
    )

log = logging.getLogger("murmur.bot")

# 连发合并：他一口气发几句，等他说完一起回
_deb = Debouncer()

# 一条一条发的节奏。真人打字没那么快——打一句短话大概 2-4 秒，
# 之前设成 0.6~2.4 秒，三条几乎同时弹出来，像机器刷屏。
TYPING_CPS = 4.0            # 每秒"打"几个字
MIN_GAP, MAX_GAP = 1.6, 4.2 # 每条之间至少/至多停多久


async def _send_bubbles(ctx, chat_id: int, msg, bubbles: list[str]) -> None:
    """按人打字的节奏逐条发出去。第一条回复原消息，后面的直接发。"""
    for i, text in enumerate(bubbles):
        if i:
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            gap = min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS))
            await asyncio.sleep(gap)
        if i == 0:
            await msg.reply_text(text)
        else:
            await ctx.bot.send_message(chat_id, text)

# 它上一句话对应的记录 id，按会话存。用户接着回的话会写回这条。
_LAST_ENTRY: dict[int, int] = {}


def _allowed(cfg: Config, chat_id: int) -> bool:
    # 安全默认：关掉自动入册时必须显式命中白名单；空白名单不是“所有人”。
    # /start 仍可告诉测试者自己的 chat id，但不会调用模型或写入记忆。
    # 开了自动入册才一律放行，新人由 ensure_greeted 记进名册。
    # 注意 Telegram 的 bot 链接是公开的，这条和钉钉/微信不一样：
    # 那两边的人来自你的组织和通讯录，这边可能是任何搜到 bot 的人。
    return cfg.auto_enroll or chat_id in cfg.allowed_chat_ids


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "拍到什么随手发我。\n"
        "我不一定每张都接话——没什么好说的时候我就不说了。\n\n"
        f"你的 chat id 是 {chat_id}，"
        "把它填进 .env.test-bots 的 MURMUR_ALLOWED_CHAT_IDS 并重启后才能使用。"
    )


async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _allowed(cfg, update.effective_chat.id):
        return
    mem: Memory = ctx.application.bot_data["mem"]
    entries = mem.recent(_thread(update)[0], limit=10)
    if not entries:
        await update.message.reply_text("还没有记录。")
        return
    lines = []
    for e in entries:
        when = " ".join(x for x in (e.weekday, e.bucket) if x) or "?"
        lines.append(f"{when}｜{e.scene or ''}\n  → {e.said or '（没说话）'}")
    await update.message.reply_text("\n".join(lines))


async def _handle_image(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: bytes
) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    mem: Memory = ctx.application.bot_data["mem"]
    msg = update.message
    chat_id = update.effective_chat.id           # 发消息用的（Telegram 的会话）
    key, label = _thread(update)                  # 存记忆用的（按人隔离）

    await ensure_greeted(ctx, cfg, mem, chat_id, key, label)

    ph = photo_mod.from_bytes(data)
    seq = _deb.arrive(key, text=(msg.caption or "").strip() or None, photo=ph)
    await asyncio.sleep(_deb.window)
    batch = _deb.claim(key, seq)
    if batch is None:
        log.info("合并：%s 还在发，这条不单独回", label)
        return
    ph = batch.photo or ph
    note = batch.note

    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    moment = Moment.of(ph, cfg.tz, received_at=msg.date)

    try:
        # respond 是同步的（OpenAI SDK 同步客户端），丢到线程里别卡住事件循环。
        reply = await asyncio.to_thread(
            respond, moment, mem, cfg, photo=ph, note=note, chat_id=key,
            dossier=_load_dossier(cfg, label)
        )
    except Exception as e:  # 网关超时、额度、模型抽风
        log.exception("respond failed")
        await msg.reply_text(f"（出错了：{type(e).__name__}）")
        return

    entry_id = mem.record(
        chat_id=key,
        thread=label,
        shot_at=ph.shot_at,
        bucket=moment.bucket,
        weekday=moment.weekday,
        spot=moment.spot,
        scene=reply.scene,
        move=reply.move,
        said=reply.joined,
        note=note,
        has_photo=True,
    )
    try:
        photo_mod.save_preview(cfg.db_path.parent, entry_id, ph)
    except Exception as e:
        log.warning("存图失败：%s", e)

    if reply.silent:
        log.info("thread=%s quiet | %s", label, reply.scene)
        return  # 真的不发消息——这是设计，不是 bug

    _LAST_ENTRY[key] = entry_id
    await _send_bubbles(ctx, chat_id, msg, reply.say)
    await _maybe_refresh(cfg, mem, key, label)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _allowed(cfg, update.effective_chat.id):
        return
    # photo[-1] 是最大的那档。Telegram 压缩过，EXIF 没了，
    # 所以时间用消息时间（Moment 会兜底）。
    f = await update.message.photo[-1].get_file()
    await _handle_image(update, ctx, bytes(await f.download_as_bytearray()))


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _allowed(cfg, update.effective_chat.id):
        return
    doc = update.message.document
    if not (doc.mime_type or "").startswith("image/"):
        return
    # 以"文件"方式发的原图会保留 EXIF——拍摄时间和 GPS 都在，效果最好。
    f = await doc.get_file()
    await _handle_image(update, ctx, bytes(await f.download_as_bytearray()))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """纯文字消息。两件事：
    1) 如果它刚说过话，这句就是你的回应，记到那条记录上——调人格全靠它；
    2) 不管有没有上文，都要回。直接跟它说话却没反应是最劝退的体验。"""
    cfg: Config = ctx.application.bot_data["cfg"]
    mem: Memory = ctx.application.bot_data["mem"]
    msg = update.message
    chat_id = update.effective_chat.id
    key, label = _thread(update)
    if not _allowed(cfg, chat_id):
        return

    text = (msg.text or "").strip()
    if not text:
        return

    await ensure_greeted(ctx, cfg, mem, chat_id, key, label)

    if wants_stop(text):
        mem.set_optout(key, label, True)
        log.info("已停止对 %s 的主动消息", label)
        await msg.reply_text("好，那我不主动找你了。想说话随时来。")
        return

    if (entry_id := _LAST_ENTRY.pop(key, None)) is not None:
        mem.add_reply(entry_id, text)

    seq = _deb.arrive(key, text=text)
    await asyncio.sleep(_deb.window)
    batch = _deb.claim(key, seq)
    if batch is None:
        log.info("合并：%s 还在说，这条不单独回", label)
        return
    text = batch.note or text

    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    moment = Moment.text_only(cfg.tz, received_at=msg.date)
    try:
        reply = await asyncio.to_thread(
            respond, moment, mem, cfg, photo=None, note=text, chat_id=key,
            dossier=_load_dossier(cfg, label)
        )
    except Exception as e:
        log.exception("respond(text) failed")
        await msg.reply_text(f"（出错了：{type(e).__name__}）")
        return

    new_id = mem.record(
        chat_id=key,
        thread=label,
        shot_at=None,
        bucket=moment.bucket,
        weekday=moment.weekday,
        spot=None,
        scene=reply.scene or "（纯文字）",
        move=reply.move,
        said=reply.joined,
        note=text,
    )
    _LAST_ENTRY[key] = new_id
    await _send_bubbles(ctx, chat_id, msg, reply.say)
    await _maybe_refresh(cfg, mem, key, label)


async def _fire_initiative(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """一个排好的时刻到了，看要不要主动说句话。"""
    cfg: Config = ctx.application.bot_data["cfg"]
    mem: Memory = ctx.application.bot_data["mem"]
    chat_id, key, label = ctx.job.data
    now = datetime.now(cfg.tz)

    last_in = mem.last_inbound_at(key)
    last_dt = (
        datetime.fromisoformat(last_in).astimezone(cfg.tz) if last_in else None
    )
    if mem.is_opted_out(key):
        return
    try:
        if await ensure_greeted(ctx, cfg, mem, chat_id, key, label):
            return       # 这次只发介绍
    except Exception as e:
        log.error("打招呼失败 %s: %s", type(e).__name__, e)
    hold = should_hold(last_dt, mem.unanswered_outbound(key), now)
    if hold:
        log.info("主动消息跳过（%s）thread=%s", hold, label)
        return

    intent = pick_intent([e.intent for e in mem.recent_outbound(key)])
    moment = Moment.text_only(cfg.tz, received_at=now)
    try:
        reply = await asyncio.to_thread(
            initiate, moment, mem, cfg, intent, chat_id=key,
            dossier=_load_dossier(cfg, label),
        )
    except Exception as e:
        log.error("主动消息失败 %s: %s", type(e).__name__, e)
        return

    if reply.silent:
        log.info("主动消息 quiet（%s）thread=%s", intent.key, label)
        return

    entry_id = mem.record(
        chat_id=key, thread=label, shot_at=None, bucket=moment.bucket,
        weekday=moment.weekday, spot=None, scene=reply.scene, move=reply.move,
        said=reply.joined, note=None, kind="out", intent=intent.key,
    )
    _LAST_ENTRY[key] = entry_id
    log.info("主动发出 [%s] %s", intent.key, reply.joined)
    for i, text in enumerate(reply.say):
        if i:
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS)))
        await ctx.bot.send_message(chat_id, text)


async def _schedule_day(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """每天排一次当天的主动消息时刻。刻意每天重排，节奏才不会固定。"""
    cfg: Config = ctx.application.bot_data["cfg"]
    jq = ctx.application.job_queue
    now = datetime.now(cfg.tz)

    # 名册是活的：今天新来的人今天就排上，不用等重启。
    mem: Memory = ctx.application.bot_data["mem"]
    chat_ids = list(dict.fromkeys(
        sorted(cfg.allowed_chat_ids)
        + [int(r["user_id"]) for r in mem.roster("tg") if r["user_id"].lstrip("-").isdigit()]
    ))
    # 每小时那次补排**只管新人**：已经排过的人再排一遍，等于把当天剩下的
    # 时段又填满一轮 10-15 条，一天下来能翻好几倍。
    # （他为这个骂过一次：一次收到 12 条。）
    newcomers_only = bool(ctx.job and ctx.job.data == "newcomers")

    for chat_id in chat_ids:
        pending = [j for j in jq.jobs()
                   if j.name and j.name.startswith(f"init:{chat_id}:")]
        if newcomers_only and pending:
            continue
        key, label = thread_key("tg", str(chat_id), str(chat_id))
        for job in jq.jobs():
            if job.name and job.name.startswith(f"init:{chat_id}:"):
                job.schedule_removal()
        times = plan_day(cfg.tz, now=now)
        for t in times:
            jq.run_once(
                _fire_initiative, when=t,
                data=(chat_id, key, label), name=f"init:{chat_id}:{t:%H%M}",
                # APScheduler 默认 misfire_grace_time=1 秒：错过一秒就当没这回事，
                # 而且是静默的。合盖睡一觉，这段时间里排的消息会全部消失。
                # 放宽到 15 分钟，和 initiative.LATE_GRACE 对齐——
                # 迟到几分钟照发（真人也会晚一会儿才想起来说），迟太久才算了。
                job_kwargs={"misfire_grace_time": int(LATE_GRACE.total_seconds())},
            )
        log.info(
            "chat=%s 今天排了 %d 条主动消息：%s",
            chat_id, len(times), " ".join(f"{t:%H:%M}" for t in times),
        )


async def _heartbeat(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """给看门狗报平安。这个任务跑在事件循环上——循环卡死它就停，
    看门狗（独立线程）就会发现并重启进程。"""
    watchdog.beat()


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """网络抖动（代理 503、超时）在这台机器上是常态，PTB 会自己重试。
    没有这个处理器的话每次都打一整段 traceback，把真正的错误淹掉。
    可恢复的降成一行，其余的照旧打全。"""
    err = ctx.error
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning("网络抖动（会自动重试）：%s: %s", type(err).__name__, err)
        return
    log.exception("未处理的异常", exc_info=err)


def run() -> None:
    cfg = Config.load()
    cfg.require_test_bot("Telegram")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    if not cfg.telegram_token:
        raise RuntimeError("没有 TELEGRAM_BOT_TOKEN。找 @BotFather 要一个填进 .env.test-bots。")
    if not cfg.api_key:
        raise RuntimeError("没有 OPENCODE_API_KEY。")

    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["mem"] = Memory(cfg.db_path)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    # 主动消息：启动时排一次今天剩下的，之后每天凌晨 5:05 重排。
    # 只给白名单里的人排——没有白名单就不知道该主动找谁。
    jq = app.job_queue
    jq.run_repeating(_heartbeat, interval=60, first=5)
    watchdog.start("Telegram")

    # 不再拿白名单当开关：开了自动入册的话，收件人是从名册里长出来的，
    # 启动时可能一个人都没有，但今天下午就会有。
    if cfg.allowed_chat_ids or cfg.auto_enroll:
        jq.run_once(_schedule_day, when=3)
        jq.run_daily(_schedule_day, time=dtime(5, 5, tzinfo=cfg.tz))
        # 名册里的人是每天 05:05 重排时才会被排上的，
        # 今天刚认识的人不该等到明天——每小时补排一次。
        jq.run_repeating(_schedule_day, interval=3600, first=3600, data="newcomers")
        log.info("主动消息已启用，.env.test-bots 指定的收件人 %s（名册里的人另算）",
                 sorted(cfg.allowed_chat_ids) or "（无）")
    else:
        log.warning("没配白名单且关了自动入册，主动消息不启用（不知道该找谁）")

    log.info("模型 %s @ %s", cfg.model, cfg.base_url)
    if not cfg.allowed_chat_ids and not cfg.auto_enroll:
        log.warning("没配白名单且自动入册已关闭：除 /start 外不会处理任何用户消息。")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
