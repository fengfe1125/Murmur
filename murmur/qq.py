"""QQ 入口（腾讯官方 QQ 机器人，WebSocket 网关）。

和钉钉、微信共用同一套 engine / persona / memory，
只把收发换成官方 Bot API。

这是**独立的机器人账号**，不是扫码挂个人 QQ。
对方在 QQ 里找到这个机器人、发消息，Murmur 才收得到。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests

from . import photo as photo_mod
from . import watchdog
from .config import Config
from .debounce import Debouncer
from .dossier import Dossier, refresh
from .engine import respond
from .greeting import INTRO, JOINED
from .memory import Memory, thread_key
from .moment import Moment

log = logging.getLogger("murmur.qq")

_deb = Debouncer(window=5.0)

TYPING_CPS = 4.0
MIN_GAP, MAX_GAP = 1.6, 4.2

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.bot.qq.com"
_QQ_HOSTS = "bots.qq.com,api.bot.qq.com,.bot.qq.com,api.sgroup.qq.com,.qq.com"

_AT = re.compile(r"<@!?\w+>\s*")


def ensure_main_event_loop() -> asyncio.AbstractEventLoop:
    """Return a usable loop for botpy on Python 3.14+.

    Python 3.14 no longer creates a loop implicitly for the main thread, while
    qq-botpy still calls ``asyncio.get_event_loop()`` when opening its gateway.
    """
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            return loop
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def create_gateway_client(client_class, intents, *, sandbox: bool):
    """Create botpy's client only after its legacy event-loop prerequisite exists."""
    ensure_main_event_loop()
    return client_class(
        intents=intents,
        is_sandbox=sandbox,
        # Keep gateway lifecycle logs in systemd's QQ log.  Token-bearing
        # payloads are DEBUG-only, while INFO is necessary to diagnose
        # login / handshake failures on a headless VPS.
        bot_log=True,
        log_level=logging.INFO,
    )


async def beat_forever(interval: float = 60.0) -> None:
    """定期给看门狗报平安。必须跑在网关自己的事件循环上。

    只靠入站消息回调喂心跳的话，量到的是"有没有人说话"而不是"循环还活着"，
    空闲超过 STALE_AFTER 就会被判定卡死：2026-08-14 QQ 上线后就这么每
    5 分 42 秒自杀一次，一天四百多轮，期间一条消息都没收到过。

    挪到独立线程里喂也不行——那样循环真卡死时看门狗照样报平安，检查等于废掉。
    """
    while True:
        await asyncio.sleep(interval)
        watchdog.beat()


def _bypass_proxy_for_qq() -> str:
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    for h in _QQ_HOSTS.split(","):
        if h not in parts:
            parts.append(h)
    value = ",".join(parts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


def strip_mention(text: str | None) -> str:
    if not text:
        return ""
    return _AT.sub("", text).strip()


def oto_thread(user_id: str) -> tuple[int, str]:
    return thread_key("qq", "oto", user_id)


def group_thread(group_id: str, user_id: str) -> tuple[int, str]:
    return thread_key("qq", group_id, user_id)


def _dossier_root(cfg: Config):
    return cfg.db_path.parent / "dossiers"


def _load_dossier(cfg: Config, label: str) -> str | None:
    d = Dossier.load(_dossier_root(cfg), label)
    return None if d.is_empty else d.as_prompt()


class QqHttp:
    """同步 HTTP：发消息、下图。收消息走 botpy 的 WebSocket。"""

    def __init__(self, app_id: str, secret: str):
        self.app_id = app_id
        self.secret = secret
        self._token: str | None = None
        self._until = 0.0
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()
        self._token_lock = threading.Lock()

    def token(self) -> str:
        now = time.time()
        if self._token and now < self._until:
            return self._token
        with self._token_lock:
            # 等锁的工夫别的线程可能已经刷新过了，再查一遍，
            # 不然收发两条线程会各自刷一次 token
            if self._token and now < self._until:
                return self._token
            r = requests.post(
                TOKEN_URL,
                json={"appId": self.app_id, "clientSecret": self.secret},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            tok = data.get("access_token")
            if not tok:
                raise RuntimeError(f"拿不到 QQ access_token：{data}")
            ttl = int(data.get("expires_in") or 7200)
            self._token = tok
            self._until = now + max(60, ttl - 120)
            return tok

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"QQBot {self.token()}",
            "X-Union-Appid": self.app_id,
            "Content-Type": "application/json",
        }

    def _next_seq(self, msg_id: str | None) -> int:
        key = msg_id or "_"
        with self._lock:
            # _seq 以 msg_id 为键只增不减，长驻进程会慢慢涨内存（_seen 有清理，
            # 它没有）。msg_seq 只是回复计数，清掉重来没有副作用。
            if len(self._seq) > 200:
                self._seq.clear()
            n = self._seq.get(key, 0) + 1
            self._seq[key] = n
            return n

    def send_c2c(self, openid: str, text: str, msg_id: str | None = None) -> None:
        body: dict[str, Any] = {
            "content": text,
            "msg_type": 0,
            "msg_seq": self._next_seq(msg_id),
        }
        if msg_id:
            body["msg_id"] = msg_id
        r = requests.post(
            f"{API_BASE}/v2/users/{openid}/messages",
            headers=self._headers(),
            json=body,
            timeout=25,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"QQ 单聊发送失败 {r.status_code}: {r.text[:300]}")

    def send_group(self, group_openid: str, text: str, msg_id: str | None = None) -> None:
        body: dict[str, Any] = {
            "content": text,
            "msg_type": 0,
            "msg_seq": self._next_seq(msg_id),
        }
        if msg_id:
            body["msg_id"] = msg_id
        r = requests.post(
            f"{API_BASE}/v2/groups/{group_openid}/messages",
            headers=self._headers(),
            json=body,
            timeout=25,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"QQ 群聊发送失败 {r.status_code}: {r.text[:300]}")

    def send_bubbles(
        self,
        target: str,
        bubbles: list[str],
        *,
        group: bool = False,
        msg_id: str | None = None,
    ) -> None:
        for i, text in enumerate(bubbles):
            if i:
                time.sleep(min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS)))
            if group:
                self.send_group(target, text, msg_id)
            else:
                self.send_c2c(target, text, msg_id)

    def fetch_url(self, url: str) -> bytes | None:
        if not url:
            return None
        if url.startswith("//"):
            url = "https:" + url
        auth = {
            "Authorization": f"QQBot {self.token()}",
            "X-Union-Appid": self.app_id,
        }
        last_err = None
        for headers in (auth, {}):
            try:
                r = requests.get(url, headers=headers, timeout=30)
                if r.ok and _looks_like_image(r.content):
                    return r.content
                last_err = f"HTTP {r.status_code} {r.headers.get('content-type','')} {len(r.content)}B"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
        log.error("下载 QQ 图片失败：%s url=%s", last_err, url[:160])
        return None


def _looks_like_image(data: bytes) -> bool:
    if not data or len(data) < 12:
        return False
    return (
        data[:3] == b"\xff\xd8\xff"
        or data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:6] in (b"GIF87a", b"GIF89a")
        or data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )


def att_info(a) -> dict:
    if isinstance(a, dict):
        return a
    return {k: getattr(a, k, None) for k in
            ("url", "content_type", "filename", "id", "size")}


def image_url(attachments: list) -> str | None:
    for a in attachments or []:
        if isinstance(a, str):
            continue
        info = att_info(a)
        url = (info.get("url") or "").strip()
        ctype = (info.get("content_type") or "").lower()
        name = (info.get("filename") or "").lower()
        if not url:
            continue
        if ctype.startswith(("video/", "audio/")):
            continue
        if (ctype.startswith("image/") or not ctype
                or any(name.endswith(ext) for ext in
                       (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"))):
            return url
    return None


def parse_ts(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


# 新人连发图+文字时两个处理线程会同时进来，都看到 has_greeted==False，
# 自我介绍就发了两遍。按会话键加锁，第二个进来的会看到已经打过招呼。
_greet_locks: dict[int, threading.Lock] = {}
_greet_locks_guard = threading.Lock()


def ensure_greeted(cfg: Config, mem: Memory, http: QqHttp,
                   uid: str, key: int, label: str) -> bool:
    with _greet_locks_guard:
        lock = _greet_locks.setdefault(key, threading.Lock())
    with lock:
        if mem.has_greeted(key):
            return False
        http.send_bubbles(uid, INTRO)
        m = Moment.text_only(cfg.tz)
        mem.record(chat_id=key, thread=label, shot_at=None, bucket=m.bucket,
                   weekday=m.weekday, spot=None, scene="（自我介绍）", move="speak",
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


class Handler:
    def __init__(self, cfg: Config, mem: Memory, http: QqHttp):
        self.cfg, self.mem, self.http = cfg, mem, http
        self._last_entry: dict[int, int] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._seen: dict[str, float] = {}

    def _lock_for(self, key: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _duplicate(self, msg_id: str | None) -> bool:
        if not msg_id:
            return False
        now = time.time()
        if len(self._seen) > 200:
            self._seen = {k: t for k, t in self._seen.items() if now - t < 600}
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = now
        return False

    def handle_c2c(self, message) -> None:
        uid = message.author.user_openid
        if not uid:
            return
        key, label = oto_thread(uid)
        self._handle(uid, key, label, message, group=False, target=uid)

    def handle_group(self, message) -> None:
        uid = message.author.member_openid
        gid = message.group_openid
        if not uid or not gid:
            return
        key, label = group_thread(gid, uid)
        self._handle(uid, key, label, message, group=True, target=gid)

    def _handle(self, uid: str, key: int, label: str, message,
                *, group: bool, target: str) -> None:
        if uid not in self.cfg.qq_allowed_users:
            if not self.cfg.auto_enroll:
                log.info(
                    "已忽略非白名单用户｜openid=%s｜要放行就把它加进 QQ_ALLOWED_USERS",
                    uid,
                )
                return
            if not group and self.mem.enroll("qq", uid, key, label):
                log.info("新人入册｜openid=%s｜已建上下文和记忆文件", uid)

        received = parse_ts(getattr(message, "timestamp", None))
        text = strip_mention(getattr(message, "content", None)) or None
        atts = getattr(message, "attachments", None) or []
        url = image_url(atts)
        msg_id = getattr(message, "id", None)
        if not text:
            log.info("QQ 无文字｜id=%s attachments=%s",
                     msg_id, [att_info(a) for a in atts])
        if self._duplicate(msg_id):
            return

        if not group:
            try:
                if ensure_greeted(self.cfg, self.mem, self.http, uid, key, label):
                    if text:
                        m = Moment.text_only(self.cfg.tz, received_at=received)
                        self.mem.record(
                            chat_id=key, thread=label, shot_at=None,
                            bucket=m.bucket, weekday=m.weekday, spot=None,
                            scene="", move="speak", said=None, note=text,
                        )
                    return
            except Exception as e:
                log.warning("打招呼失败：%s", e)

        from .initiative import wants_stop
        if text and wants_stop(text):
            self.mem.set_optout(key, label, True)
            log.info("已停止对 %s 的主动消息", label)
            self.http.send_bubbles(
                target, ["好，那我不主动找你了。想说话随时来。"],
                group=group, msg_id=msg_id,
            )
            return

        ph = None
        if url:
            data = self.http.fetch_url(url)
            if data is None:
                self.http.send_bubbles(
                    target, ["（图没下下来，再发一次试试）"],
                    group=group, msg_id=msg_id,
                )
                return
            try:
                ph = photo_mod.from_bytes(data)
            except Exception as e:
                log.error("QQ 图片解码失败：%s (%d bytes)", type(e).__name__, len(data))
                self.http.send_bubbles(
                    target, ["（这张图打不开，换一种格式发一次？）"],
                    group=group, msg_id=msg_id,
                )
                return
            log.info("QQ 收到图 %d KB", len(data) // 1024)

        seq = _deb.arrive(key, text=text, photo=ph, extra={"msg_id": msg_id})
        time.sleep(_deb.window)
        if not _deb.is_latest(key, seq):
            log.info("合并：%s 还在说，这条不单独回", label)
            return
        with self._lock_for(key):
            batch = _deb.take(key)
            while batch:
                self._reply_batch(
                    key, label, batch, received, group=group, target=target,
                )
                time.sleep(min(1.2, _deb.window))
                batch = _deb.take(key)

    def _reply_batch(self, key: int, label: str, batch, received, *,
                     group: bool, target: str) -> None:
        text, ph = batch.note, batch.photo
        msg_id = batch.extra.get("msg_id")
        if ph is None and not text:
            return
        if ph is None and (eid := self._last_entry.pop(key, None)) is not None:
            self.mem.add_reply(eid, text)

        try:
            if ph is not None:
                moment = Moment.of(ph, self.cfg.tz, received_at=received)
                shot_at, spot = ph.shot_at, moment.spot
            else:
                moment = Moment.text_only(self.cfg.tz, received_at=received)
                shot_at, spot = None, None
            reply = respond(
                moment, self.mem, self.cfg,
                photo=ph, note=text, chat_id=key,
                dossier=_load_dossier(self.cfg, label),
            )
        except Exception as e:
            log.exception("respond failed")
            self.http.send_bubbles(
                target, [f"（出错了：{type(e).__name__}）"],
                group=group, msg_id=msg_id,
            )
            return

        entry_id = self.mem.record(
            chat_id=key, thread=label, shot_at=shot_at, bucket=moment.bucket,
            weekday=moment.weekday, spot=spot,
            scene=reply.scene or ("（纯文字）" if ph is None else ""),
            move=reply.move, said=reply.joined, note=text,
            has_photo=ph is not None,
        )
        if ph is not None:
            try:
                photo_mod.save_preview(self.cfg.db_path.parent, entry_id, ph)
            except Exception as e:
                log.warning("存图失败：%s", e)
        if reply.silent:
            log.info("thread=%s quiet | %s", label, reply.scene)
            return
        self._last_entry[key] = entry_id
        self.http.send_bubbles(target, reply.say, group=group, msg_id=msg_id)
        try:
            refresh(self.cfg, self.mem, key, label, _dossier_root(self.cfg))
        except Exception as e:
            log.warning("整理记忆失败（不影响聊天）：%s: %s", type(e).__name__, e)


def _initiative_loop(cfg: Config, mem: Memory, http: QqHttp) -> None:
    from .engine import initiate
    from .initiative import pick_intent, plan_day, should_hold, split_due

    log.info("QQ 主动消息已启用")
    planned_for: dict[str, str] = {}
    queue: list[tuple[datetime, str]] = []
    seen: set[str] = set()

    while True:
        try:
            now = datetime.now(cfg.tz)
            today = now.date().isoformat()
            users = list(dict.fromkeys(
                sorted(cfg.qq_allowed_users) + [r["user_id"] for r in mem.roster("qq")]
            ))
            if new := [u for u in users if u not in seen]:
                seen.update(new)
                log.info("QQ 主动消息收件人 +%s（共 %d 人）", new, len(users))
            if not users:
                time.sleep(60)
                continue

            for uid in users:
                if planned_for.get(uid) != today:
                    times = plan_day(cfg.tz, now=now)
                    queue += [(t, uid) for t in times]
                    planned_for[uid] = today
                    log.info("QQ %s 今天排了 %d 条：%s", uid, len(times),
                             " ".join(f"{t:%H:%M}" for t in times))
            due, stale, pending = split_due(queue, now)
            if stale:
                log.info("错过了 %d 条主动消息（%s），迟到太久就不补了",
                         len(stale), " ".join(f"{t:%H:%M}" for t, _ in stale))
            queue = due + pending
            if not queue:
                time.sleep(60)
                continue

            when, uid = queue[0]
            if when > now:
                time.sleep(max(1.0, min((when - now).total_seconds(), 60)))
                if datetime.now(cfg.tz) < when:
                    continue
            queue.pop(0)

            key, label = oto_thread(uid)
            if mem.is_opted_out(key):
                continue
            now = datetime.now(cfg.tz)
            last_in = mem.last_inbound_at(key)
            last_dt = datetime.fromisoformat(last_in).astimezone(cfg.tz) if last_in else None
            if hold := should_hold(last_dt, mem.unanswered_outbound(key), now):
                log.info("QQ 主动消息跳过（%s）%s", hold, uid)
                continue
            intent = pick_intent([e.intent for e in mem.recent_outbound(key)])
            try:
                reply = initiate(Moment.text_only(cfg.tz, received_at=now), mem, cfg,
                                 intent, chat_id=key, dossier=_load_dossier(cfg, label))
            except Exception as e:
                log.error("QQ 主动消息失败 %s: %s", type(e).__name__, e)
                continue
            if reply.silent:
                continue
            try:
                http.send_bubbles(uid, reply.say)
            except Exception as e:
                log.error("QQ 投递失败 %s: %s", type(e).__name__, e)
                continue
            m = Moment.text_only(cfg.tz, received_at=now)
            mem.record(chat_id=key, thread=label, shot_at=None, bucket=m.bucket,
                       weekday=m.weekday, spot=None, scene=reply.scene, move=reply.move,
                       said=reply.joined, note=None, kind="out", intent=intent.key)
            log.info("QQ 主动发出 [%s] %s", intent.key, reply.joined)
        except Exception:
            log.exception("QQ 主动消息循环出错，60 秒后继续")
            time.sleep(60)


def run() -> None:
    cfg = Config.load()
    cfg.require_test_bot("QQ")
    import botpy
    from botpy.message import C2CMessage, GroupMessage

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    log.info("QQ 直连（绕过代理）：%s", _bypass_proxy_for_qq())
    if not (cfg.qq_app_id and cfg.qq_client_secret):
        raise RuntimeError(
            "没有 QQ_APP_ID / QQ_CLIENT_SECRET。"
            "去 q.qq.com 创建一个机器人，把 AppID 和 AppSecret 填进 .env.test-bots。"
        )
    if not cfg.api_key:
        raise RuntimeError("没有 OPENCODE_API_KEY。")

    http = QqHttp(cfg.qq_app_id, cfg.qq_client_secret)
    http.token()
    mem = Memory(cfg.db_path)
    handler = Handler(cfg, mem, http)

    class Client(botpy.Client):
        # 外层 while 每次重连都新建一个 Client，心跳任务跟着旧循环一起消失，
        # 所以挂在实例上，每次 on_ready 重新起一个。
        _hb: asyncio.Task | None = None

        async def on_ready(self):
            log.info("QQ 网关已就绪")
            watchdog.beat()
            if self._hb is None or self._hb.done():
                self._hb = asyncio.create_task(beat_forever())

        async def on_c2c_message_create(self, message: C2CMessage):
            watchdog.beat()
            await asyncio.to_thread(handler.handle_c2c, message)

        async def on_group_at_message_create(self, message: GroupMessage):
            watchdog.beat()
            await asyncio.to_thread(handler.handle_group, message)

    log.info("QQ 机器人 %s，模型 %s @ %s", cfg.qq_app_id, cfg.model, cfg.base_url)
    if cfg.qq_sandbox:
        log.info("走沙箱环境（正式上线后设 QQ_SANDBOX=0）")
    if not cfg.qq_allowed_users and not cfg.auto_enroll:
        log.warning("没配 QQ_ALLOWED_USERS 且自动入册已关闭：不会处理任何用户消息。")

    if cfg.qq_initiative:
        threading.Thread(
            target=_initiative_loop, args=(cfg, Memory(cfg.db_path), http),
            name="murmur-qq-initiative", daemon=True,
        ).start()

    watchdog.start("QQ")
    intents = botpy.Intents(public_messages=True)
    client = create_gateway_client(Client, intents, sandbox=cfg.qq_sandbox)
    log.info("QQ WebSocket 连接中……（不需要公网 IP）")
    delay = 5
    while True:
        try:
            client.run(appid=cfg.qq_app_id, secret=cfg.qq_client_secret)
            log.warning("QQ 网关正常退出，5 秒后重连")
            delay = 5
        except KeyboardInterrupt:
            log.info("收到中断，退出")
            return
        except Exception as e:
            log.error("QQ 网关断了：%s: %s，%s 秒后重连", type(e).__name__, e, delay)
        time.sleep(delay)
        delay = min(delay * 2, 60)
        client = create_gateway_client(Client, intents, sandbox=cfg.qq_sandbox)
