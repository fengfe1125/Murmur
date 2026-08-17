"""钉钉入口（Stream 长连接模式）。

选 Stream 而不是 Webhook 的原因：Stream 是机器人主动连出去的长连接，
**不需要公网 IP、不需要域名、不需要 HTTPS 证书**——笔记本在家里就能跑，
和 Telegram 的轮询是一个体感。企业微信和公众号都做不到这点。

跟 bot.py（Telegram）共用同一套 engine / persona / memory，
只是把"收消息、下载图、发消息"这三件事换成钉钉的接口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime

import requests
from dingtalk_stream import (
    AckMessage,
    CallbackMessage,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
)

from . import photo as photo_mod
from . import watchdog
from .config import Config
from .debounce import Debouncer
from .dossier import Dossier, refresh
from .engine import respond
from .greeting import INTRO, JOINED
from .memory import Memory, thread_key
from .moment import Moment

log = logging.getLogger("murmur.dingtalk")

# 连发合并：他一口气发几句，等他说完一起回
_deb = Debouncer()

TYPING_CPS = 4.0
MIN_GAP, MAX_GAP = 1.6, 4.2


# 钉钉是国内服务，不需要走代理。挂在境外代理后面只会多一个故障源——
# 实测代理 503 时钉钉整个断线重连失败，直连却是 0.26 秒正常返回。
_DINGTALK_HOSTS = "api.dingtalk.com,.dingtalk.com,.aliyuncs.com"


def _bypass_proxy_for_dingtalk() -> str:
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    for h in _DINGTALK_HOSTS.split(","):
        if h not in parts:
            parts.append(h)
    value = ",".join(parts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


def oto_thread(user_id: str) -> tuple[int, str]:
    """钉钉单聊的上下文键。

    刻意**不含 conversation_id**：主动发消息时我们只有 userId，
    而用户回复时钉钉给的是真实的私聊 cid。如果把 cid 算进键里，
    主动消息和他的回复会落到两条互不相通的上下文里。
    单聊本来就是一对一，按人算就够了。
    """
    return thread_key("dt", "oto", user_id)


def _thread(incoming: ChatbotMessage, cfg: Config | None = None) -> tuple[int, str]:
    """一个人一条上下文。群里也要按人分开——
    只按 conversation_id 的话，群里 A 的心事会串进 B 的回复。"""
    sender = incoming.sender_staff_id or incoming.sender_id or "?"
    if cfg is not None:
        sender = cfg.canonical_dingtalk_id(sender)
    # conversation_type: "1" 单聊 / "2" 群聊
    if str(incoming.conversation_type or "1") == "1":
        return oto_thread(sender)
    return thread_key("dt", incoming.conversation_id or "?", sender)


def _dossier_root(cfg: Config):
    return cfg.db_path.parent / "dossiers"


def _load_dossier(cfg: Config, label: str) -> str | None:
    d = Dossier.load(_dossier_root(cfg), label)
    return None if d.is_empty else d.as_prompt()


# access_token 有效期 7200 秒，每条消息都现取等于每轮回复多打好几次
# oauth 接口。缓存到过期前 2 分钟——和 qq.py 的 QqHttp 一个做法。
_token: str | None = None
_token_until = 0.0
_token_lock = threading.Lock()


def _access_token(cfg: Config) -> str:
    global _token, _token_until
    now = time.time()
    with _token_lock:
        # 等锁的工夫别的线程可能已经刷新过了，再查一遍
        if _token and now < _token_until:
            return _token
        r = requests.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": cfg.dingtalk_client_id,
                  "appSecret": cfg.dingtalk_client_secret},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        ttl = int(data.get("expireIn") or 7200)
        _token = data["accessToken"]
        _token_until = now + max(60, ttl - 120)
        return _token


def send_oto(cfg: Config, user_ids: list[str], bubbles: list[str]) -> dict:
    """主动给单聊发消息。

    不能用收消息时给的 sessionWebhook——那个会过期。主动发必须走这个接口，
    并且要在 open-dev 后台开「机器人发送单聊消息」权限。
    """
    _bypass_proxy_for_dingtalk()
    token = _access_token(cfg)

    out: dict = {}
    for i, text in enumerate(bubbles):
        if i:
            time.sleep(min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS)))
        r = requests.post(
            "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
            headers={"x-acs-dingtalk-access-token": token},
            json={
                "robotCode": cfg.dingtalk_client_id,
                "userIds": user_ids,
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": text}, ensure_ascii=False),
            },
            timeout=25,
        )
        r.raise_for_status()
        out = r.json()
        if out.get("invalidStaffIdList"):
            log.error("无效的 userId：%s", out["invalidStaffIdList"])
    return out


# 新人连发图+文字时两个处理线程会同时进来，都看到 has_greeted==False，
# 自我介绍就发了两遍。按会话键加锁，第二个进来的会看到已经打过招呼。
_greet_locks: dict[int, threading.Lock] = {}
_greet_locks_guard = threading.Lock()


def ensure_greeted(cfg: Config, mem: Memory, uid: str, key: int, label: str) -> bool:
    """新人首次出现：建记忆线、发自我介绍。已经打过招呼就什么都不做。

    返回 True 表示这次发了介绍（调用方据此决定要不要再发别的，
    别让人一上来同时收到介绍和一句"在干嘛"）。
    """
    with _greet_locks_guard:
        lock = _greet_locks.setdefault(key, threading.Lock())
    with lock:
        if mem.has_greeted(key):
            return False
        send_oto(cfg, [uid], INTRO)
        m = Moment.text_only(cfg.tz)
        mem.record(chat_id=key, thread=label, shot_at=None, bucket=m.bucket,
                   weekday=m.weekday, spot=None, scene="（自我介绍）", move="speak",
                   said=JOINED, note=None, kind="out", intent="自我介绍")
        mem.mark_greeted(key, label)
        # 顺手把记忆文件建出来，这样每个人一来就有属于自己的那份
        try:
            from .dossier import Dossier
            d = Dossier.load(_dossier_root(cfg), label)
            if not d.path.exists():
                d.save()
        except Exception as e:
            log.warning("建记忆文件失败：%s", e)
        log.info("已向新用户 %s 发送自我介绍", label)
        return True


def _initiative_loop(cfg: Config, mem: Memory) -> None:
    """钉钉的主动消息排程。

    这边没有 PTB 那样的 JobQueue，自己起一个线程：每天排一批时刻，
    到点就醒一次。用 sleep 而不是 cron 是因为要的正是"不定时"。
    """
    from .engine import initiate
    from .initiative import pick_intent, plan_day, should_hold, split_due

    log.info("钉钉主动消息已启用，.env.test-bots 里指定的收件人 %s", cfg.dingtalk_initiative or "（无）")
    planned_for: dict[str, str] = {}   # user -> 已排过的日期
    queue: list[tuple[datetime, str]] = []
    seen: set[str] = set()

    while True:
        try:
            now = datetime.now(cfg.tz)
            today = now.date().isoformat()

            # 每轮都重新算收件人：名册是活的，今天新认识的人今天就能排上，
            # 不用等重启。已经说过"别发了"的人 roster() 已经滤掉了。
            users = list(dict.fromkeys(
                list(cfg.dingtalk_initiative) + [r["user_id"] for r in mem.roster("dt")]
            ))
            if new := [u for u in users if u not in seen]:
                seen.update(new)
                log.info("钉钉主动消息收件人 +%s（共 %d 人）", new, len(users))
            if not users:
                time.sleep(60)    # 还没认识任何人，等新人自己来
                watchdog.beat()
                continue

            for uid in users:
                if planned_for.get(uid) != today:
                    times = plan_day(cfg.tz, now=now)
                    queue += [(t, uid) for t in times]
                    planned_for[uid] = today
                    log.info(
                        "钉钉 %s 今天排了 %d 条：%s",
                        uid, len(times), " ".join(f"{t:%H:%M}" for t in times),
                    )
            watchdog.beat()
            # 合盖睡一觉醒来，错过的时刻要么补发要么丢弃，但不能不吭声。
            due, stale, pending = split_due(queue, now)
            if stale:
                log.info(
                    "错过了 %d 条主动消息（%s），迟到太久就不补了——"
                    "多半是电脑睡过去了",
                    len(stale), " ".join(f"{t:%H:%M}" for t, _ in stale),
                )
            queue = due + pending

            if not queue:
                # 最长只睡 60 秒：看门狗的判死阈值是 300 秒，
                # 心跳间隔贴着阈值的话，什么都没出错也会被误杀。
                time.sleep(60)    # 今天发完了，等过午夜重排
                continue

            when, uid = queue[0]
            if when > now:
                nap = max(1.0, min((when - now).total_seconds(), 60))
                time.sleep(nap)
                if datetime.now(cfg.tz) < when:
                    continue      # 还没到，下一轮继续等
            else:
                log.info("补发一条迟到 %.0f 分钟的主动消息（%s）",
                         (now - when).total_seconds() / 60, uid)
            queue.pop(0)

            key, label = oto_thread(uid)
            if mem.is_opted_out(key):
                continue
            try:
                if ensure_greeted(cfg, mem, uid, key, label):
                    continue     # 这次只发介绍，正常内容留到下一个时刻
            except Exception as e:
                log.error("打招呼失败 %s: %s", type(e).__name__, e)
            now = datetime.now(cfg.tz)
            last_in = mem.last_inbound_at(key)
            last_dt = datetime.fromisoformat(last_in).astimezone(cfg.tz) if last_in else None
            if hold := should_hold(last_dt, mem.unanswered_outbound(key), now):
                log.info("钉钉主动消息跳过（%s）%s", hold, uid)
                continue

            intent = pick_intent([e.intent for e in mem.recent_outbound(key)])
            try:
                reply = initiate(Moment.text_only(cfg.tz, received_at=now), mem, cfg,
                                 intent, chat_id=key,
                                 dossier=_load_dossier(cfg, label))
            except Exception as e:
                log.error("钉钉主动消息失败 %s: %s", type(e).__name__, e)
                continue
            if reply.silent:
                log.info("钉钉主动消息 quiet（%s）%s", intent.key, uid)
                continue

            try:
                send_oto(cfg, [uid], reply.say)
            except Exception as e:
                log.error("钉钉投递失败 %s: %s", type(e).__name__, e)
                continue
            m = Moment.text_only(cfg.tz, received_at=now)
            mem.record(chat_id=key, thread=label, shot_at=None, bucket=m.bucket,
                       weekday=m.weekday, spot=None, scene=reply.scene, move=reply.move,
                       said=reply.joined, note=None, kind="out", intent=intent.key)
            log.info("钉钉主动发出 [%s] %s", intent.key, reply.joined)
        except Exception:
            log.exception("钉钉主动消息循环出错，60 秒后继续")
            watchdog.beat()
            time.sleep(60)


class MurmurHandler(ChatbotHandler):
    def __init__(self, cfg: Config, mem: Memory):
        super().__init__()
        self.cfg = cfg
        self.mem = mem
        self.logger = log
        # 它上一句话对应的记录 id，按会话存。用户接着回的话会写回这条。
        self._last_entry: dict[int, int] = {}

    # ---------- 发消息 ----------

    def _send_bubbles(self, bubbles: list[str], incoming: ChatbotMessage) -> None:
        """一条一条发，中间留出打字的时间。钉钉没有"正在输入"状态，
        所以停顿就是全部的节奏感来源。"""
        for i, text in enumerate(bubbles):
            if i:
                time.sleep(min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS)))
            self.reply_text(text, incoming)

    # ---------- 收图 ----------

    def _fetch_image(self, download_code: str) -> bytes | None:
        url = self.get_image_download_url(download_code)
        if not url:
            log.error("拿不到图片下载地址，downloadCode=%s", download_code)
            return None
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.content
        except Exception as e:
            log.error("下载图片失败: %s", e)
            return None

    # ---------- 主流程 ----------

    async def process(self, callback: CallbackMessage):
        """SDK 的 raw_process 里是 `await self.process(...)`，所以这里必须是 async。

        但真正干活的部分全是阻塞的（调模型、下载图、发消息），
        直接写在 async 里会卡住整个事件循环，后面的消息全排队。
        所以整块丢进线程，async 这层只负责等。
        """
        return await asyncio.to_thread(self._handle, callback)

    def _handle(self, callback: CallbackMessage):
        incoming = ChatbotMessage.from_dict(callback.data)
        key, label = _thread(incoming, self.cfg)

        sender_uid = incoming.sender_staff_id or incoming.sender_id or "?"
        known = (
            incoming.sender_staff_id in self.cfg.allowed_dingtalk_users
            or incoming.sender_id in self.cfg.allowed_dingtalk_users
        )
        if not known:
            if not self.cfg.auto_enroll:
                # 日志要能直接照着加白名单。钉钉在群聊/外部联系人场景下
                # 不给 staffId，只给一个加密的 senderId——只打 staff_id=None
                # 等于什么都没说，不知道该把谁加进来。
                log.info(
                    "已忽略非白名单用户｜昵称=%s staff_id=%s sender_id=%s "
                    "会话=%s(%s)｜要放行就把上面某个 id 加进 DINGTALK_ALLOWED_USERS",
                    incoming.sender_nick,
                    incoming.sender_staff_id,
                    incoming.sender_id,
                    "单聊" if str(incoming.conversation_type or "1") == "1" else "群聊",
                    (incoming.conversation_title or incoming.conversation_id or "?")[:24],
                )
                return AckMessage.STATUS_OK, "ignored"
            # 新人自动入册。主动消息只能发单聊——群里冒出来的人不排，
            # 否则等于在群里定时刷屏。
            if str(incoming.conversation_type or "1") == "1":
                if self.mem.enroll("dt", sender_uid, key, label, incoming.sender_nick):
                    log.info(
                        "新人入册｜昵称=%s id=%s｜已建上下文和记忆文件，"
                        "从下一轮起也会收到主动消息",
                        incoming.sender_nick, sender_uid,
                    )
        try:
            ensure_greeted(self.cfg, self.mem,
                           self.cfg.canonical_dingtalk_id(sender_uid), key, label)
        except Exception as e:
            log.warning("打招呼失败：%s", e)

        # 钉钉的 createAt 是毫秒时间戳
        received = (
            datetime.fromtimestamp(int(incoming.create_at) / 1000, tz=UTC)
            if incoming.create_at
            else datetime.now(UTC)
        )

        # 注意：SDK 这个方法返回的是 list 不是 str——富文本会按行拆成多段，
        # 纯文本也包成单元素列表。直接 .strip() 会炸。
        parts = self.extract_text_from_incoming_message(incoming) or []
        if isinstance(parts, str):  # 防 SDK 以后改签名
            parts = [parts]
        text = "\n".join(p for p in parts if p).strip() or None
        codes = incoming.get_image_list() or []

        # 说了"别发了"就真的不发。不宣传这个功能，但它得管用。
        from .initiative import wants_stop
        if text and wants_stop(text):
            self.mem.set_optout(key, label, True)
            log.info("已停止对 %s 的主动消息", label)
            self.reply_text("好，那我不主动找你了。想说话随时来。", incoming)
            return AckMessage.STATUS_OK, "opted out"

        # 连发合并：登记这一条，等静默期，只有最后一条负责回复。
        ph_early = None
        if codes:
            data = self._fetch_image(codes[0])
            if data is None:
                self.reply_text("（图没下下来，再发一次试试）", incoming)
                return AckMessage.STATUS_OK, "download failed"
            ph_early = photo_mod.from_bytes(data)
        seq = _deb.arrive(key, text=text, photo=ph_early)
        time.sleep(_deb.window)
        batch = _deb.claim(key, seq)
        if batch is None:
            log.info("合并：%s 还在说，这条不单独回", label)
            return AckMessage.STATUS_OK, "merged"
        text, ph_early = batch.note, batch.photo

        try:
            if ph_early is not None:
                ph = ph_early
                moment = Moment.of(ph, self.cfg.tz, received_at=received)
                reply = respond(
                    moment, self.mem, self.cfg,
                    photo=ph, note=text, chat_id=key,
                    dossier=_load_dossier(self.cfg, label),
                )
                shot_at, spot = ph.shot_at, moment.spot
            else:
                if not text:
                    return AckMessage.STATUS_OK, "empty"
                # 纯文字：如果它刚说过话，这句就是用户的回应，记回去
                if (eid := self._last_entry.pop(key, None)) is not None:
                    self.mem.add_reply(eid, text)
                moment = Moment.text_only(self.cfg.tz, received_at=received)
                reply = respond(
                    moment, self.mem, self.cfg,
                    photo=None, note=text, chat_id=key,
                    dossier=_load_dossier(self.cfg, label),
                )
                shot_at, spot = None, None
        except Exception as e:
            log.exception("respond failed")
            self.reply_text(f"（出错了：{type(e).__name__}）", incoming)
            return AckMessage.STATUS_OK, "error"

        entry_id = self.mem.record(
            chat_id=key,
            thread=label,
            shot_at=shot_at,
            bucket=moment.bucket,
            weekday=moment.weekday,
            spot=spot,
            scene=reply.scene or ("（纯文字）" if not codes else ""),
            move=reply.move,
            said=reply.joined,
            note=text,
            has_photo=ph_early is not None,
        )
        if ph_early is not None:
            try:
                # photo_mod 已在模块顶部导入。这里绝不能再来一次函数内
                # import——那会把它变成局部变量，让上面第 383 行的
                # photo_mod.from_bytes 变成 UnboundLocalError。
                photo_mod.save_preview(self.cfg.db_path.parent, entry_id, ph_early)
            except Exception as e:
                log.warning("存图失败：%s", e)

        if reply.silent:
            log.info("thread=%s quiet | %s", label, reply.scene)
            return AckMessage.STATUS_OK, "quiet"  # 真的不发消息，这是设计

        self._last_entry[key] = entry_id
        self._send_bubbles(reply.say, incoming)
        try:
            refresh(self.cfg, self.mem, key, label, _dossier_root(self.cfg))
        except Exception as e:
            log.warning("整理记忆失败（不影响聊天）：%s: %s", type(e).__name__, e)
        return AckMessage.STATUS_OK, "OK"


def run() -> None:
    cfg = Config.load()
    cfg.require_test_bot("钉钉")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    log.info("钉钉直连（绕过代理）：%s", _bypass_proxy_for_dingtalk())
    if not (cfg.dingtalk_client_id and cfg.dingtalk_client_secret):
        raise RuntimeError(
            "没有 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET。"
            "去 open-dev.dingtalk.com 建个企业内部应用，把 AppKey/AppSecret 填进 .env.test-bots。"
        )
    if not cfg.api_key:
        raise RuntimeError("没有 OPENCODE_API_KEY。")

    mem = Memory(cfg.db_path)
    client = DingTalkStreamClient(
        Credential(cfg.dingtalk_client_id, cfg.dingtalk_client_secret)
    )
    client.register_callback_handler(
        ChatbotMessage.TOPIC, MurmurHandler(cfg, mem)
    )
    log.info("模型 %s @ %s", cfg.model, cfg.base_url)
    if not cfg.allowed_dingtalk_users and not cfg.auto_enroll:
        log.warning("没配白名单且自动入册已关闭：不会处理任何用户消息。")
    # 主动消息跑在独立线程里，和 Stream 长连接互不干扰
    threading.Thread(
        target=_initiative_loop, args=(cfg, Memory(cfg.db_path)),
        name="murmur-initiative", daemon=True,
    ).start()

    watchdog.start("钉钉")
    log.info("钉钉 Stream 连接中……（不需要公网 IP）")

    # SDK 自己的重连会一路退避到几十分钟一次，网络抖一下就等于当天不干活了。
    # 外面再套一层看门狗：断了就自己爬起来，间隔固定在 1 分钟以内。
    delay = 5
    while True:
        try:
            client.start_forever()
            log.warning("Stream 正常退出，5 秒后重连")
            delay = 5
        except KeyboardInterrupt:
            log.info("收到中断，退出")
            return
        except Exception as e:
            log.error("Stream 断了：%s: %s，%s 秒后重连", type(e).__name__, e, delay)
        time.sleep(delay)
        delay = min(delay * 2, 60)
