from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .config import Config
from .engine import build_context, respond
from .memory import Memory
from .moment import Moment
from .photo import load as load_photo


def cmd_reply(args) -> int:
    cfg = Config.load()
    if args.model:
        cfg = replace(cfg, model=args.model)
    photo = load_photo(args.photo)
    moment = Moment.of(photo, cfg.tz)

    with Memory(cfg.db_path) as mem:
        if args.dry_run:
            ctx = build_context(
                moment,
                mem.spot_visits(0, moment.spot, moment.bucket),
                args.note,
            )
            print("── 拼给模型的上下文 " + "─" * 40)
            print(ctx)
            print("─" * 60)
            print(f"（图已编码 {len(photo.image_b64) // 1024} KB base64，未发送）")
            return 0

        reply = respond(moment, mem, cfg, photo=photo, note=args.note)

        if not args.no_save:
            mem.record(
                shot_at=photo.shot_at,
                bucket=moment.bucket,
                weekday=moment.weekday,
                spot=moment.spot,
                scene=reply.scene,
                move=reply.move,
                said=reply.joined,
                note=args.note,
            )

    if reply.silent:
        print("（没说话）")
    else:
        for i, bubble in enumerate(reply.say):
            print(f"  {'▸' if i == 0 else '▹'} {bubble}")
    if args.verbose:
        print(f"\n[{cfg.model} · {reply.move}] {reply.scene}", file=sys.stderr)
    return 0


def cmd_inspect(args) -> int:
    cfg = Config.load()
    photo = load_photo(args.photo)
    moment = Moment.of(photo, cfg.tz)

    print(f"文件      {photo.path}")
    print(f"拍摄时间  {photo.shot_at or '（无，会用收到的时间兜底）'}")
    print(f"相机      {photo.camera or '（无）'}")
    print(f"定位指纹  {moment.spot or '（无）'}")
    print(f"此刻      {moment.describe()}")
    return 0


def cmd_log(args) -> int:
    cfg = Config.load()
    with Memory(cfg.db_path) as mem:
        entries = mem.recent(args.chat, args.n)
    if not entries:
        print("（还没有记录）")
        return 0
    for e in entries:
        when = " ".join(x for x in (e.weekday, e.bucket) if x) or "?"
        print(f"{when}  {e.scene or ''}")
        print(f"    [{e.move}] {e.said or '（没说话）'}")
        if e.reply:
            print(f"    他回：{e.reply}")
    return 0


def cmd_poke(args) -> int:
    """手动触发一条主动消息，用来验证链路和看效果。
    绕过时间窗和冷却——这是测试入口，不是正常路径。"""
    import asyncio

    from .engine import initiate
    from .initiative import INTENTS, pick_intent
    from .memory import thread_key

    cfg = Config.load()
    if args.model:
        cfg = replace(cfg, model=args.model)

    if args.to == "tg":
        if not args.chat:
            print("错误：--chat 要给 Telegram chat id", file=sys.stderr)
            return 1
        platform, conv, sender = "tg", str(args.chat), str(args.chat)
    else:
        if not args.user:
            print("错误：--user 要给钉钉 userId", file=sys.stderr)
            return 1
        platform, conv, sender = "dt", args.conv or "oto", args.user

    key, label = thread_key(platform, conv, sender)
    with Memory(cfg.db_path) as mem:
        intent = next((i for i in INTENTS if i.key == args.intent), None) if args.intent \
                 else pick_intent([e.intent for e in mem.recent_outbound(key)])
        moment = Moment.text_only(cfg.tz)
        from .dossier import Dossier
        _d = Dossier.load(cfg.db_path.parent / "dossiers", label)
        reply = initiate(moment, mem, cfg, intent, chat_id=key,
                         dossier=None if _d.is_empty else _d.as_prompt())

        print(f"意图：{intent.key}   thread={label}")
        if reply.silent:
            print("（模型选择不发——大概和最近说过的太像了）")
            return 0
        for b in reply.say:
            print("  ▹", b)

        if not args.dry_run:
            if args.to == "tg":
                from telegram import Bot
                bot = Bot(cfg.telegram_token)
                async def send():
                    for i, t in enumerate(reply.say):
                        if i:
                            await asyncio.sleep(2.0)
                        await bot.send_message(int(args.chat), t)
                asyncio.run(send())
            else:
                from .dingtalk import send_oto
                res = send_oto(cfg, [args.user], reply.say)
                bad = res.get("invalidStaffIdList") or []
                print("  钉钉已投递" + (f"（无效 userId: {bad}）" if bad else ""))
            mem.record(chat_id=key, thread=label, shot_at=None, bucket=moment.bucket,
                       weekday=moment.weekday, spot=None, scene=reply.scene,
                       move=reply.move, said=reply.joined, note=None,
                       kind="out", intent=intent.key)
            print("  已发送并记入记忆")
    return 0


def cmd_bot(args) -> int:
    from .bot import run

    run()
    return 0


def cmd_dingtalk(args) -> int:
    from .dingtalk import run

    run()
    return 0


def cmd_wechat(args) -> int:
    from .wechat import run

    run()
    return 0


def cmd_qq(args) -> int:
    from .qq import run

    run()
    return 0


def cmd_web(args) -> int:
    """只读看板。不发消息、不改记忆，随时可以开关。"""
    from .web import run

    run(host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="murmur", description="发一张图，它回你一句。")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reply", help="对一张图作出回应")
    r.add_argument("photo")
    r.add_argument("--note", help="随图附的一句话")
    r.add_argument("--model", help="临时换个模型试试，比如 mimo-v2.5 / kimi-k2.6")
    r.add_argument("--dry-run", action="store_true", help="只打印上下文，不调 API")
    r.add_argument("--no-save", action="store_true", help="不写进记忆")
    r.add_argument("-v", "--verbose", action="store_true", help="打印 move 和画面描述")
    r.set_defaults(func=cmd_reply)

    i = sub.add_parser("inspect", help="看一张图的 EXIF 和推断出的此刻")
    i.add_argument("photo")
    i.set_defaults(func=cmd_inspect)

    lg = sub.add_parser("log", help="看最近的记录")
    lg.add_argument("-n", type=int, default=10)
    lg.add_argument("--chat", type=int, default=0, help="Telegram chat id，CLI 是 0")
    lg.set_defaults(func=cmd_log)

    b = sub.add_parser("bot", help="启动 Telegram bot")
    b.set_defaults(func=cmd_bot)

    pk = sub.add_parser("poke", help="手动触发一条主动消息（测试用）")
    pk.add_argument("--to", choices=["tg", "dt"], required=True)
    pk.add_argument("--chat", help="Telegram chat id")
    pk.add_argument("--user", help="钉钉 userId")
    pk.add_argument("--conv", help="钉钉 conversationId，省略则按单聊算")
    pk.add_argument("--intent", help="指定意图，省略则自动轮换")
    pk.add_argument("--model")
    pk.add_argument("--dry-run", action="store_true", help="只打印不发送")
    pk.set_defaults(func=cmd_poke)

    dt = sub.add_parser("dingtalk", help="启动钉钉机器人（Stream 模式，不需要公网 IP）")
    dt.set_defaults(func=cmd_dingtalk)

    wx = sub.add_parser("wechat", help="启动微信机器人（腾讯官方 ClawBot 通道）")
    wx.set_defaults(func=cmd_wechat)

    qq = sub.add_parser("qq", help="启动 QQ 机器人（腾讯官方 Bot API）")
    qq.set_defaults(func=cmd_qq)

    wb = sub.add_parser("web", help="打开看板：状态、额度、每个人的聊天窗口和记忆")
    # 默认只绑本机：面板上有聊天原文和记忆文件，不该因为连了个 wifi 就暴露出去
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    wb.set_defaults(func=cmd_web)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
