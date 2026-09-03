from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from dotenv import load_dotenv
from openai import OpenAIError

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
    if not args.dry_run:
        cfg.require_test_bot("平台主动消息")
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
            # 先记后发：发完才记的话，中间进程一死，这条主动消息就不在
            # 记忆里，去重失效，用户可能收到两条不一样的。
            mem.record(chat_id=key, thread=label, shot_at=None, bucket=moment.bucket,
                       weekday=moment.weekday, spot=None, scene=reply.scene,
                       move=reply.move, said=reply.joined, note=None,
                       kind="out", intent=intent.key)
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
            print("  已记入记忆并发送")
    return 0


def cmd_bot(args) -> int:
    Config.load().require_test_bot("Telegram Bot")
    from .bot import run

    run()
    return 0


def cmd_dingtalk(args) -> int:
    Config.load().require_test_bot("钉钉 Bot")
    from .dingtalk import run

    run()
    return 0


def cmd_wechat(args) -> int:
    Config.load().require_test_bot("微信 Bot")
    from .wechat import run

    run()
    return 0


def cmd_qq(args) -> int:
    Config.load().require_test_bot("QQ Bot")
    from .qq import run

    run()
    return 0


def cmd_web(args) -> int:
    """看板 + VPS 面板。VPS 页通过本机 SSH 别名查状态、建邀请码。"""
    from . import vps_panel
    from .web import run

    vps = vps_panel.VpsConfig.resolve(
        ssh_target=args.vps_ssh,
    )
    print(f"VPS 面板连接：{vps.describe()}")
    run(host=args.host, port=args.port, open_browser=not args.no_open, vps=vps)
    return 0


def cmd_app_api(args) -> int:
    """Run the loopback-only HTTPS upstream for the first-party App."""
    from .app_api import run

    run(host=args.host, port=args.port)
    return 0


def cmd_app_worker(_args) -> int:
    """Run the durable moment and proactive-message worker."""
    from .app_worker import run

    run()
    return 0


def _app_store():
    """Open the administrative App store without weakening startup checks.

    Invite creation only needs the database path.  The public API and worker
    still call ``AppSettings.validate()`` and therefore fail closed when a
    production App Attest or HTTPS setting is missing.
    """
    from .app_settings import AppSettings
    from .app_store import AppStore

    cfg = Config.load()
    settings = AppSettings.from_env(cfg)
    return AppStore(settings.db_path)


def cmd_app_invite(args) -> int:
    """Print an invitation for a new App user: seven-day single-use by default."""
    from datetime import timedelta

    with _app_store() as store:
        code = store.create_invite(
            kind="user",
            alias=args.alias,
            ttl=timedelta(days=args.days),
            max_uses=None if args.reusable else args.max_uses,
            permanent=args.permanent,
        )
    print(code)
    if args.permanent or args.reusable:
        print(
            "注意：这个邀请码长期有效，每次兑换都会新建一个用户。"
            "泄露等于把注册入口交出去，用 `app-invite-revoke <id>` 可以随时停用。",
            file=sys.stderr,
        )
    return 0


def cmd_app_device_code(args) -> int:
    """Print one 30-minute, single-use code for an existing user's device."""
    from datetime import timedelta

    with _app_store() as store:
        code = store.create_invite(
            kind="device", user_id=args.user_id, ttl=timedelta(minutes=30)
        )
    print(code)
    return 0


def cmd_app_users(_args) -> int:
    """List active App identities without exposing message or device secrets."""
    with _app_store() as store:
        users = store.active_users()
    if not users:
        print("（还没有 App 用户）")
        return 0
    for user in users:
        print(user["id"])
    return 0


def cmd_app_invites(_args) -> int:
    """List unused invite/device codes without revealing the plaintext code."""
    with _app_store() as store:
        invites = store.list_invites()
    if not invites:
        print("（没有未使用的邀请码）")
        return 0
    for invite in invites:
        cap = "∞" if invite["max_uses"] is None else invite["max_uses"]
        expiry = "永不过期" if invite["expires_at"].startswith("9999-") else invite["expires_at"]
        print(
            f"{invite['id']}  {invite['kind']}  {invite['alias'] or '-'}  "
            f"{expiry}  用量 {invite['use_count']}/{cap}"
        )
    return 0


def cmd_app_invite_revoke(args) -> int:
    """Revoke an unused invite or device code by id."""
    with _app_store() as store:
        if not store.revoke_invite(args.invite_id):
            raise ValueError("没有找到可撤销的邀请码")
    print("已撤销")
    return 0


def main(argv: list[str] | None = None) -> int:
    # 配置文件按命令选，不能让隔离测试 Bot 从生产 .env 补齐缺失凭据。
    # systemd 已通过 EnvironmentFile 注入；override=False 保留显式环境值。
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    command = raw_argv[0] if raw_argv else ""
    env_file = Path(".env.test-bots" if command in {
        "bot", "dingtalk", "wechat", "qq", "poke"
    } else ".env")
    if env_file.is_file():
        load_dotenv(env_file, override=False)

    p = argparse.ArgumentParser(prog="murmur", description="发一张图，它回你一句。")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reply", help="对一张图作出回应")
    r.add_argument("photo")
    r.add_argument("--note", help="随图附的一句话")
    r.add_argument("--model", help="临时换个模型试试，比如 deepseek-v4-pro / kimi-k2.6")
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

    b = sub.add_parser("bot", help="启动 Telegram 测试 bot（需显式开启测试通道）")
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

    dt = sub.add_parser("dingtalk", help="启动钉钉测试 bot（需显式开启测试通道）")
    dt.set_defaults(func=cmd_dingtalk)

    wx = sub.add_parser("wechat", help="启动微信测试 bot（需显式开启测试通道）")
    wx.set_defaults(func=cmd_wechat)

    qq = sub.add_parser("qq", help="启动 QQ 测试 bot（需显式开启测试通道）")
    qq.set_defaults(func=cmd_qq)

    wb = sub.add_parser("web", help="打开看板：状态、额度、每个人的聊天窗口和记忆")
    # 默认只绑本机：面板上有聊天原文和记忆文件，不该因为连了个 wifi 就暴露出去
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    wb.add_argument("--vps-ssh", metavar="HOST",
                    help="SSH 主机或别名（默认 MURMUR_VPS_SSH 或 murmur-new-vps）")
    wb.set_defaults(func=cmd_web)

    api = sub.add_parser("app-api", help="启动正式 App API（默认仅监听本机）")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8766)
    api.set_defaults(func=cmd_app_api)

    worker = sub.add_parser("app-worker", help="启动正式 App 的持久化后台 Worker")
    worker.set_defaults(func=cmd_app_worker)

    invite = sub.add_parser("app-invite", help="创建 App 用户邀请码（默认 7 天、一次性）")
    invite.add_argument("--alias", help="可选的管理侧备注，不会发给 App")
    invite.add_argument("--days", type=int, default=7, help="有效天数，默认 7")
    invite.add_argument("--max-uses", type=int, default=1, help="可兑换次数，默认 1")
    invite.add_argument(
        "--reusable", action="store_true", help="不限兑换次数（仍受 --days 限制）"
    )
    invite.add_argument(
        "--permanent", action="store_true",
        help="永不过期；配合 --reusable 即为一直可用的邀请码",
    )
    invite.set_defaults(func=cmd_app_invite)

    device_code = sub.add_parser(
        "app-device-code", help="为既有 App 用户创建 30 分钟有效的新设备码"
    )
    device_code.add_argument("user_id", help="既有 App user_id")
    device_code.set_defaults(func=cmd_app_device_code)

    users = sub.add_parser("app-users", help="列出可签发新设备码的 App user_id")
    users.set_defaults(func=cmd_app_users)

    invites = sub.add_parser("app-invites", help="列出未使用的邀请码和设备码（不含明文）")
    invites.set_defaults(func=cmd_app_invites)

    revoke = sub.add_parser("app-invite-revoke", help="撤销未使用的邀请码或设备码")
    revoke.add_argument("invite_id", help="app-invites 列出的 id")
    revoke.set_defaults(func=cmd_app_invite_revoke)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError, ValueError,
            sqlite3.OperationalError, ZoneInfoNotFoundError, OpenAIError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
