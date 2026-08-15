"""微信入口（腾讯官方 ClawBot / iLink 通道）。

和钉钉、Telegram 共用同一套 engine / persona / memory，
只把"收消息、下载图、发消息"换成微信的接口。

## 为什么是这个方案

2026-03-22 腾讯开放了微信官方 Bot API（ClawBot），扫码授权个人微信，
**不是逆向协议、不是网页版模拟**，所以不存在封号风险。这是目前唯一
一条能让个人微信合法接机器人的路。

登录那一步交给 OpenClaw 官方插件做（`openclaw channels login`），
它把 token 落盘到 ~/.openclaw/credentials/openclaw-weixin/accounts/*.json。
之后收发消息由这里直接讲 HTTP API——不需要 OpenClaw 网关在跑。

这么切的原因：登录流程涉及二维码、设备绑定、配对码，反解一遍不值当，
而且腾讯改一次我们就断一次；收发消息的协议反而是文档公开的（见插件
README 的 Backend API Protocol 一节），照着实现很稳。

## 一个必须知道的坑

getUpdates 是**带游标的长轮询**。同一个微信账号，
只能有一个进程在轮询——OpenClaw 网关和 Murmur 同时轮，
会互相把对方的消息取走。所以登录完要把插件关掉：

    openclaw config set plugins.entries.openclaw-weixin.enabled false
    openclaw gateway restart

## 限制（和钉钉/Telegram 不一样的地方）

- **只有单聊**，插件本身就没声明群聊能力。
- **服务端限速约 7 条 / 5 分钟**，超了返回 ret=-2。所以下面有个令牌桶。
- **主动发消息官方不支持**：sendMessage 要带 context_token，
  文档写明"回复时原样带回"。主动发只能复用存下来的 token，属于灰区，
  默认关闭，靠 WECHAT_INITIATIVE=1 打开。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

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

log = logging.getLogger("murmur.wechat")

_deb = Debouncer()

TYPING_CPS = 4.0
MIN_GAP, MAX_GAP = 1.6, 4.2

# 抄自插件源码 src/auth/accounts.ts
BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# 请求头里的三个身份字段，见 src/api/api.ts。
# ILINK_APP_ID 取自插件 package.json 的 ilink_appid 字段。
ILINK_APP_ID = "bot"
# 我们照着 2.4.6 版插件的协议实现，就声明这个版本。
# 编码方式：major<<16 | minor<<8 | patch
_PLUGIN_VERSION = "2.4.6"
_CLIENT_VERSION = (2 << 16) | (4 << 8) | 6
# bot_agent 相当于 HTTP 的 User-Agent，纯粹给腾讯后台看日志用的。
# 老实报自己的名字——出问题时对方能一眼看出是谁的流量。
BOT_AGENT = "Murmur/0.1"

# 微信和钉钉一样是国内服务，不该走境外代理——那只会多一个故障源。
# 钉钉那边实测过：代理 503 时整个断线重连失败，直连 0.26 秒正常返回。
_WECHAT_HOSTS = "ilinkai.weixin.qq.com,.weixin.qq.com,.qq.com"


def _is_fake_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return True
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return True
    return a == 198 and 18 <= b <= 19


def _resolve_direct(host: str) -> str | None:
    for server in ("223.5.5.5", "119.29.29.29"):
        try:
            out = subprocess.check_output(
                ["dig", f"@{server}", host, "A", "+short", "+time=2", "+tries=1"],
                timeout=4, text=True, stderr=subprocess.DEVNULL,
            )
        except Exception:
            continue
        for line in out.splitlines():
            ip = line.strip()
            if ip.count(".") == 3 and not ip.endswith(".") and not _is_fake_ip(ip):
                return ip
    return None


_orig_getaddrinfo = socket.getaddrinfo
_DIRECT_IPS: dict[str, str] = {}


def _forced_getaddrinfo(host, port, *args, **kwargs):
    ip = _DIRECT_IPS.get(host)
    if ip:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, int(port) if port else 0))]
    return _orig_getaddrinfo(host, port, *args, **kwargs)


def _bypass_proxy_for_wechat() -> str:
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    for h in _WECHAT_HOSTS.split(","):
        if h not in parts:
            parts.append(h)
    value = ",".join(parts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("ALL_PROXY", None)
    os.environ.pop("all_proxy", None)
    for host in ("ilinkai.weixin.qq.com", "novac2c.cdn.weixin.qq.com"):
        if ip := _resolve_direct(host):
            _DIRECT_IPS[host] = ip
            log.info("微信 %s 直连 %s（绕过 Fake-IP）", host, ip)
        else:
            log.warning("解析不到 %s 的真实 IP，仍走系统 DNS", host)
    socket.getaddrinfo = _forced_getaddrinfo
    return value


MSG_TYPE_USER, MSG_TYPE_BOT = 1, 2
ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE, ITEM_FILE, ITEM_VIDEO = 1, 2, 3, 4, 5
TYPING_ON, TYPING_OFF = 1, 2

ERR_SESSION_TIMEOUT = -14   # 要重新扫码
ERR_RATE_LIMITED = -2       # 发太快


# ---------------------------------------------------------------------------
# 凭据：OpenClaw 扫码登录后落在磁盘上，我们读出来用
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    return Path(
        os.environ.get("OPENCLAW_STATE_DIR", "").strip()
        or Path.home() / ".openclaw"
    )


def openclaw_accounts_dir() -> Path:
    """OpenClaw 扫码后把每个微信号的凭据放这儿。

    路径是 {state}/openclaw-weixin/accounts/，**不是** {state}/credentials/…——
    credentials/ 底下只有 allowFrom 那类文件和老版本的单文件 token。
    （我第一版就是这里搞错了，明明登录成功了却报"没找到 token"。）
    """
    return _state_dir() / "openclaw-weixin" / "accounts"


def load_credentials(cfg: Config) -> tuple[str, str]:
    """找出 (token, account_id)。

    .env 里显式配了就用配的；否则去 OpenClaw 的凭据目录里翻。
    翻不到就把该跑的命令原样打出来——这是最容易卡住新人的一步。
    """
    if cfg.wechat_token:
        return cfg.wechat_token, cfg.wechat_account_id or "env"

    found: list[tuple[str, str]] = []
    if (accounts := openclaw_accounts_dir()).is_dir():
        for p in sorted(accounts.glob("*.json")):
            # 同目录下还有 {id}.sync.json / {id}.context-tokens.json，跳过
            if p.name.endswith((".sync.json", ".context-tokens.json")):
                continue
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            if tok := (data.get("token") or "").strip():
                found.append((tok, p.stem))

    # 老版本插件把 token 放在单文件里
    legacy = _state_dir() / "credentials" / "openclaw-weixin" / "credentials.json"
    if not found and legacy.exists():
        try:
            tok = (json.loads(legacy.read_text()).get("token") or "").strip()
            if tok:
                found.append((tok, "default"))
        except Exception:
            pass

    if not found:
        raise RuntimeError(
            "没找到微信 token。先扫码登录一次：\n"
            "    npx -y @tencent-weixin/openclaw-weixin-cli install\n"
            "    openclaw channels login --channel openclaw-weixin\n"
            "登录完记得把插件关掉，否则 OpenClaw 会和 Murmur 抢消息：\n"
            "    openclaw config set plugins.entries.openclaw-weixin.enabled false\n"
            "    openclaw gateway restart"
        )

    if cfg.wechat_account_id:
        for tok, acc in found:
            if acc == cfg.wechat_account_id:
                return tok, acc
        raise RuntimeError(
            f"WECHAT_ACCOUNT_ID={cfg.wechat_account_id} 没找到，"
            f"现有的是：{', '.join(a for _, a in found)}"
        )
    if len(found) > 1:
        log.warning(
            "有 %d 个微信账号（%s），默认用第一个。要指定就配 WECHAT_ACCOUNT_ID。",
            len(found), ", ".join(a for _, a in found),
        )
    return found[0]


# ---------------------------------------------------------------------------
# 两个要落盘的小状态
# ---------------------------------------------------------------------------

class _JsonStore:
    """一个键值文件，进程重启后还在。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, str] = json.loads(path.read_text())
        except Exception:
            self._data = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
            tmp.replace(self.path)
            try:
                self.path.chmod(0o600)   # 里面是 token，别让别人读
            except OSError:
                pass


class _Throttle:
    """令牌桶。服务端限的是约 7 条 / 5 分钟，我们按 6 条留一点余量。

    超了服务端返回 ret=-2，等于这条消息**丢了**——他看不到。
    宁可让气泡晚几秒出来，也不能丢。
    """

    def __init__(self, limit: int = 6, window: float = 300.0):
        self.limit, self.window = limit, window
        self._sent: list[float] = []
        self._lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._sent = [t for t in self._sent if now - t < self.window]
                if len(self._sent) < self.limit:
                    self._sent.append(now)
                    return
                wait = self.window - (now - self._sent[0]) + 0.5
            log.warning("微信限速，等 %.0f 秒再发（服务端上限 7 条/5 分钟）", wait)
            time.sleep(wait)


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------

class WeChatClient:
    def __init__(self, token: str, account_id: str, root: Path):
        self.token = token
        self.account_id = account_id
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = {"http": None, "https": None}
        self.throttle = _Throttle()
        # 游标存自己的目录，不碰 OpenClaw 那份，免得两边互相覆盖
        self._cursor = _JsonStore(root / "cursor.json")
        # 回复要带 context_token，主动发也只能复用它——所以必须持久化
        self.ctx_tokens = _JsonStore(root / "context-tokens.json")
        self._typing_ticket: str | None = None

    # ---- 底层 ----

    def _headers(self) -> dict[str, str]:
        # X-WECHAT-UIN：随机 uint32 → 十进制字符串 → base64（见 api.ts randomWechatUin）
        uin = str(random.getrandbits(32))
        return {
            "Content-Type": "application/json",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(_CLIENT_VERSION),
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": base64.b64encode(uin.encode()).decode(),
        }

    def _post(self, endpoint: str, body: dict, timeout: float = 30.0) -> dict:
        body = dict(body)
        body["base_info"] = {
            "channel_version": _PLUGIN_VERSION,
            "bot_agent": BOT_AGENT,
        }
        r = self.session.post(
            f"{BASE_URL}/{endpoint}",
            headers=self._headers(),
            data=json.dumps(body, ensure_ascii=False).encode(),
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    # ---- 收 ----

    def get_updates(self) -> list[dict]:
        """长轮询。服务端有新消息或超时才返回。"""
        cursor = self._cursor.get(self.account_id) or ""
        resp = self._post(
            "ilink/bot/getupdates", {"get_updates_buf": cursor}, timeout=70.0
        )
        ret, errcode = resp.get("ret") or 0, resp.get("errcode")
        if errcode == ERR_SESSION_TIMEOUT or ret == ERR_SESSION_TIMEOUT:
            raise SessionExpired(resp.get("errmsg") or "session timeout")
        if ret:
            raise RuntimeError(f"getUpdates ret={ret} {resp.get('errmsg') or ''}")
        if (buf := resp.get("get_updates_buf")) is not None:
            self._cursor.put(self.account_id, buf)
        return resp.get("msgs") or []

    def fetch_media(self, media: dict, aeskey_hex: str | None) -> bytes | None:
        """从 CDN 下载并解密。微信的图全程 AES-128-ECB 加密。"""
        url = media.get("full_url")
        if not url:
            if not (q := media.get("encrypt_query_param")):
                return None
            url = f"{CDN_BASE_URL}/download?encrypted_query_param={requests.utils.quote(q, safe='')}"
        try:
            r = self.session.get(url, timeout=60)
            r.raise_for_status()
            blob = r.content
        except Exception as e:
            log.error("CDN 下载失败：%s: %s", type(e).__name__, e)
            return None

        key = _parse_aes_key(aeskey_hex, media.get("aes_key"))
        if key is None:
            log.error("没有可用的 AES key，图解不开")
            return None
        try:
            return _aes_ecb_decrypt(blob, key)
        except Exception as e:
            log.error("解密失败：%s: %s", type(e).__name__, e)
            return None

    # ---- 发 ----

    def _typing(self, user_id: str, status: int) -> None:
        """"正在输入"。微信是三个平台里唯一能真发这个状态的。

        失败就算了——没有它只是少一点真实感，不该让整条回复挂掉。
        """
        try:
            if self._typing_ticket is None:
                resp = self._post(
                    "ilink/bot/getconfig", {"ilink_user_id": user_id}, timeout=15
                )
                self._typing_ticket = resp.get("typing_ticket") or ""
            if not self._typing_ticket:
                return
            self._post("ilink/bot/sendtyping", {
                "ilink_user_id": user_id,
                "typing_ticket": self._typing_ticket,
                "status": status,
            }, timeout=15)
        except Exception as e:
            log.debug("sendTyping 失败（不影响）：%s", e)

    def send_text(self, user_id: str, text: str, context_token: str | None) -> None:
        self.throttle.take()
        msg: dict = {
            "to_user_id": user_id,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        resp = self._post("ilink/bot/sendmessage", {"msg": msg})
        ret = resp.get("ret") or 0
        if ret == ERR_RATE_LIMITED:
            # 令牌桶算漏了（比如别的客户端也在发，限速是账号级共享的）
            log.warning("撞上服务端限速，等 10 秒重发一次")
            time.sleep(10)
            resp = self._post("ilink/bot/sendmessage", {"msg": msg})
            ret = resp.get("ret") or 0
        if ret:
            raise RuntimeError(f"sendMessage ret={ret} {resp.get('errmsg') or ''}")
        log.info("微信已发出 → %s｜%s", user_id, text[:40])

    def send_bubbles(self, user_id: str, bubbles: list[str]) -> None:
        """一条一条发，中间留出打字的时间——和钉钉/Telegram 一个节奏。"""
        ctx = self.ctx_tokens.get(user_id)
        for i, text in enumerate(bubbles):
            gap = min(MAX_GAP, max(MIN_GAP, len(text) / TYPING_CPS))
            if i:
                self._typing(user_id, TYPING_ON)
                time.sleep(gap)
            self.send_text(user_id, text, ctx)
        self._typing(user_id, TYPING_OFF)


def adopt_openclaw_state(client: WeChatClient) -> None:
    """第一次跑的时候，把 OpenClaw already 存下来的状态接管过来。

    两样东西值得拿：

    - **同步游标**。空游标去拉 getUpdates，服务端可能把历史消息重放一遍——
      那样 Murmur 会对着几天前的消息一条条回，很吓人。用 OpenClaw 停在
      哪儿我们就从哪儿接着走。
    - **context_token**。发消息必须带它。不接管的话，得等对方先开口
      才能回第一句；接管了就立刻能用（主动消息也才有可能）。

    只在我们自己还没有对应记录时才拿，不会覆盖已经跑起来的状态。
    """
    src = openclaw_accounts_dir()
    if not src.is_dir():
        return

    if not client._cursor.get(client.account_id):
        try:
            buf = json.loads(
                (src / f"{client.account_id}.sync.json").read_text()
            ).get("get_updates_buf")
            if buf:
                client._cursor.put(client.account_id, buf)
                log.info("接管 OpenClaw 的同步游标，不会重放历史消息")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("读 OpenClaw 游标失败（从头开始拉）：%s", e)

    try:
        tokens = json.loads(
            (src / f"{client.account_id}.context-tokens.json").read_text()
        )
    except FileNotFoundError:
        return
    except Exception as e:
        log.warning("读 OpenClaw 的 context_token 失败：%s", e)
        return

    n = 0
    for uid, tok in (tokens or {}).items():
        if isinstance(tok, str) and tok and not client.ctx_tokens.get(uid):
            client.ctx_tokens.put(uid, tok)
            n += 1
    if n:
        log.info("接管了 %d 个 context_token，现在就能回话了", n)


class SessionExpired(RuntimeError):
    """登录过期，必须人肉重新扫码。重启进程没用。"""


def _parse_aes_key(aeskey_hex: str | None, aes_key_b64: str | None) -> bytes | None:
    """两种编码都见过（见插件 pic-decrypt.ts 的注释）：

    - image_item.aeskey：十六进制字符串，收图时优先用这个
    - media.aes_key：base64。解出来 16 字节就是原始 key；
      解出来 32 个 ASCII 十六进制字符，还要再按 hex 解一层（文件/语音/视频）
    """
    if aeskey_hex:
        try:
            k = bytes.fromhex(aeskey_hex.strip())
            if len(k) == 16:
                return k
        except ValueError:
            pass
    if aes_key_b64:
        try:
            d = base64.b64decode(aes_key_b64)
        except Exception:
            return None
        if len(d) == 16:
            return d
        if len(d) == 32:
            try:
                return bytes.fromhex(d.decode("ascii"))
            except (ValueError, UnicodeDecodeError):
                return None
    return None


def _aes_ecb_decrypt(blob: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    out = dec.update(blob) + dec.finalize()
    # PKCS7 去填充。微信这边偶尔会给未填充的整块，所以校验不过就原样返回，
    # 别为了严谨把一张本来能看的图丢掉。
    if out and 1 <= out[-1] <= 16 and len(out) > out[-1]:
        pad = out[-1]
        if out[-pad:] == bytes([pad]) * pad:
            return out[:-pad]
    return out


# ---------------------------------------------------------------------------
# 消息解析
# ---------------------------------------------------------------------------

def extract(msg: dict) -> tuple[str | None, dict | None, str | None]:
    """从一条 WeixinMessage 里取出 (文字, 图片 item, aeskey)。"""
    text_parts: list[str] = []
    image_item = None
    for item in msg.get("item_list") or []:
        t = item.get("type")
        if t == ITEM_TEXT:
            if s := (item.get("text_item") or {}).get("text"):
                text_parts.append(s)
        elif t == ITEM_IMAGE and image_item is None:
            image_item = item.get("image_item") or {}
    text = "\n".join(p for p in text_parts if p).strip() or None
    if image_item is None:
        return text, None, None
    media = image_item.get("media") or image_item.get("thumb_media") or {}
    return text, media, image_item.get("aeskey")


def wechat_thread(user_id: str) -> tuple[int, str]:
    """一个人一条上下文。微信只有单聊，按人算就够了。"""
    return thread_key("wx", "oto", user_id)


def _dossier_root(cfg: Config) -> Path:
    return cfg.db_path.parent / "dossiers"


def _load_dossier(cfg: Config, label: str) -> str | None:
    d = Dossier.load(_dossier_root(cfg), label)
    return None if d.is_empty else d.as_prompt()


def ensure_greeted(cfg: Config, mem: Memory, client: WeChatClient,
                   uid: str, key: int, label: str) -> bool:
    """新人首次出现：建记忆线、发自我介绍。已经打过就什么都不做。"""
    if mem.has_greeted(key):
        return False
    client.send_bubbles(uid, INTRO)
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


# ---------------------------------------------------------------------------
# 处理一条消息
# ---------------------------------------------------------------------------

class Handler:
    def __init__(self, cfg: Config, mem: Memory, client: WeChatClient):
        self.cfg, self.mem, self.client = cfg, mem, client
        self._last_entry: dict[int, int] = {}

    def handle(self, msg: dict) -> None:
        try:
            self._handle(msg)
        except Exception:
            log.exception("处理消息失败")

    def _handle(self, msg: dict) -> None:
        uid = msg.get("from_user_id")
        if not uid:
            return
        # message_type: 1=用户 2=机器人。自己发的回声要跳过，否则会自问自答。
        if (msg.get("message_type") or MSG_TYPE_USER) == MSG_TYPE_BOT:
            return

        key, label = wechat_thread(uid)

        if uid not in self.cfg.wechat_allowed_users:
            if not self.cfg.auto_enroll:
                log.info(
                    "已忽略非白名单用户｜openId=%s｜要放行就把它加进 WECHAT_ALLOWED_USERS",
                    uid,
                )
                return
            if self.mem.enroll("wx", uid, key, label):
                log.info("新人入册｜openId=%s｜已建上下文和记忆文件", uid)

        # context_token 必须原样带回才能回复。存下来，主动发消息时也只能靠它。
        if ct := msg.get("context_token"):
            self.client.ctx_tokens.put(uid, ct)

        try:
            ensure_greeted(self.cfg, self.mem, self.client, uid, key, label)
        except Exception as e:
            log.warning("打招呼失败：%s", e)

        received = (
            datetime.fromtimestamp(int(msg["create_time_ms"]) / 1000, tz=UTC)
            if msg.get("create_time_ms")
            else datetime.now(UTC)
        )

        text, media, aeskey = extract(msg)

        from .initiative import wants_stop
        if text and wants_stop(text):
            self.mem.set_optout(key, label, True)
            log.info("已停止对 %s 的主动消息", label)
            self.client.send_bubbles(uid, ["好，那我不主动找你了。想说话随时来。"])
            return

        ph = None
        if media:
            data = self.client.fetch_media(media, aeskey)
            if data is None:
                self.client.send_bubbles(uid, ["（图没下下来，再发一次试试）"])
                return
            ph = photo_mod.from_bytes(data)

        # 连发合并：他一口气发几句，等他说完再回一次
        seq = _deb.arrive(key, text=text, photo=ph)
        time.sleep(_deb.window)
        batch = _deb.claim(key, seq)
        if batch is None:
            log.info("合并：%s 还在说，这条不单独回", label)
            return
        text, ph = batch.note, batch.photo

        if ph is None and not text:
            return
        if ph is None and (eid := self._last_entry.pop(key, None)) is not None:
            self.mem.add_reply(eid, text)

        # 模型在想的时候把"正在输入"点起来——这是微信独有的，别浪费
        self.client._typing(uid, TYPING_ON)
        try:
            # Moment.of 也要包进来：EXIF 里有奇怪的东西时它会抛，
            # 漏在外面的话异常被 handle() 吞掉，他那边是彻底的静默——
            # 比收到一句"出错了"难受得多。
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
            self.client._typing(uid, TYPING_OFF)
            self.client.send_bubbles(uid, [f"（出错了：{type(e).__name__}）"])
            return

        if reply.silent:
            self.client._typing(uid, TYPING_OFF)
            log.info("thread=%s quiet | %s", label, reply.scene)
        else:
            self.client.send_bubbles(uid, reply.say)

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
        if not reply.silent:
            self._last_entry[key] = entry_id
        try:
            refresh(self.cfg, self.mem, key, label, _dossier_root(self.cfg))
        except Exception as e:
            log.warning("整理记忆失败（不影响聊天）：%s: %s", type(e).__name__, e)


# ---------------------------------------------------------------------------
# 主动消息（默认关闭）
# ---------------------------------------------------------------------------

def _initiative_loop(cfg: Config, mem: Memory, client: WeChatClient) -> None:
    """微信的主动排程。

    **默认不开。** 官方 sendMessage 的设计前提是被动回复：context_token
    要从收到的消息里原样带回。主动发只能复用上一次存下来的 token，
    这是文档没承诺的行为，腾讯随时可以关掉。加上限速 7 条/5 分钟，
    这条路能走但不牢靠。要开就 WECHAT_INITIATIVE=1。
    """
    from .engine import initiate
    from .initiative import pick_intent, plan_day, should_hold, split_due

    log.warning(
        "微信主动消息已开启。注意这是官方未承诺的用法"
        "（sendMessage 本意是被动回复，只能复用存下来的 context_token）。"
    )
    planned_for: dict[str, str] = {}
    queue: list[tuple[datetime, str]] = []
    seen: set[str] = set()

    while True:
        now = datetime.now(cfg.tz)
        today = now.date().isoformat()

        # 名册是活的，今天新认识的人今天就排上
        users = list(dict.fromkeys(
            sorted(cfg.wechat_allowed_users) + [r["user_id"] for r in mem.roster("wx")]
        ))
        if new := [u for u in users if u not in seen]:
            seen.update(new)
            log.info("微信主动消息收件人 +%s（共 %d 人）", new, len(users))
        if not users:
            time.sleep(60)
            continue

        for uid in users:
            if planned_for.get(uid) != today:
                times = plan_day(cfg.tz, now=now)
                queue += [(t, uid) for t in times]
                planned_for[uid] = today
                log.info("微信 %s 今天排了 %d 条：%s", uid, len(times),
                         " ".join(f"{t:%H:%M}" for t in times))
        due, stale, pending = split_due(queue, now)
        if stale:
            log.info("错过了 %d 条主动消息（%s），迟到太久就不补了",
                     len(stale), " ".join(f"{t:%H:%M}" for t, _ in stale))
        queue = due + pending

        if not queue:
            time.sleep(60)    # 别贴着看门狗 300 秒的判死阈值
            continue

        when, uid = queue[0]
        if when > now:
            time.sleep(max(1.0, min((when - now).total_seconds(), 60)))
            if datetime.now(cfg.tz) < when:
                continue
        queue.pop(0)

        key, label = wechat_thread(uid)
        if mem.is_opted_out(key):
            continue
        # 没有 context_token 就发不出去——他从来没跟机器人说过话。
        # 这时候只能等他先开口，硬发必然失败。
        if not client.ctx_tokens.get(uid):
            log.info("微信 %s 还没有 context_token（他没先说过话），这条跳过", uid)
            continue

        now = datetime.now(cfg.tz)
        last_in = mem.last_inbound_at(key)
        last_dt = datetime.fromisoformat(last_in).astimezone(cfg.tz) if last_in else None
        if hold := should_hold(last_dt, mem.unanswered_outbound(key), now):
            log.info("微信主动消息跳过（%s）%s", hold, uid)
            continue

        intent = pick_intent([e.intent for e in mem.recent_outbound(key)])
        try:
            reply = initiate(Moment.text_only(cfg.tz, received_at=now), mem, cfg,
                             intent, chat_id=key, dossier=_load_dossier(cfg, label))
        except Exception as e:
            log.error("微信主动消息失败 %s: %s", type(e).__name__, e)
            continue
        if reply.silent:
            continue

        try:
            client.send_bubbles(uid, reply.say)
        except Exception as e:
            log.error("微信投递失败 %s: %s", type(e).__name__, e)
            continue
        m = Moment.text_only(cfg.tz, received_at=now)
        mem.record(chat_id=key, thread=label, shot_at=None, bucket=m.bucket,
                   weekday=m.weekday, spot=None, scene=reply.scene, move=reply.move,
                   said=reply.joined, note=None, kind="out", intent=intent.key)
        log.info("微信主动发出 [%s] %s", intent.key, reply.joined)


# ---------------------------------------------------------------------------

def run() -> None:
    cfg = Config.load()
    cfg.require_test_bot("微信")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    log.info("微信直连（绕过代理）：%s", _bypass_proxy_for_wechat())
    if not cfg.api_key:
        raise RuntimeError("没有 OPENCODE_API_KEY。")

    token, account_id = load_credentials(cfg)
    root = cfg.db_path.parent / "wechat" / account_id
    root.mkdir(parents=True, exist_ok=True)
    client = WeChatClient(token, account_id, root)
    adopt_openclaw_state(client)

    mem = Memory(cfg.db_path)
    handler = Handler(cfg, mem, client)

    log.info("微信账号 %s，模型 %s @ %s", account_id, cfg.model, cfg.base_url)
    if not cfg.wechat_allowed_users and not cfg.auto_enroll:
        log.warning("没配 WECHAT_ALLOWED_USERS 且自动入册已关闭：不会处理任何用户消息。")

    if cfg.wechat_initiative:
        threading.Thread(
            target=_initiative_loop, args=(cfg, Memory(cfg.db_path), client),
            name="murmur-wechat-initiative", daemon=True,
        ).start()
    else:
        log.info("微信主动消息未开启（官方接口不支持，要开：WECHAT_INITIATIVE=1）")

    watchdog.start("微信")

    # 每条消息丢进线程池：debounce 要 sleep 3.5 秒，
    # 挂在轮询线程上会把后面的消息全堵住。
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wx")
    log.info("微信长轮询开始……（不需要公网 IP）")

    delay = 5
    while True:
        try:
            msgs = client.get_updates()
            watchdog.beat()
            delay = 5
            if msgs:
                log.info("微信收到 %d 条", len(msgs))
            for m in msgs:
                pool.submit(handler.handle, m)
        except SessionExpired as e:
            # 重启进程没用，必须人重新扫码。所以这里不快速重试，
            # 免得 run.sh 拉起来一个只会刷日志的进程。
            log.error(
                "微信登录已过期（%s）。重新扫码：\n"
                "    openclaw channels login --channel openclaw-weixin", e,
            )
            # 等着人来重新扫码。**要一直心跳**：这个进程没卡死，
            # 是在正常地等一件只有人能做的事。不心跳的话看门狗会每 5 分钟
            # 杀一次，run.sh 再拉起来，日志被同一句话刷爆。
            for _ in range(10):
                watchdog.beat()
                time.sleep(30)
        except KeyboardInterrupt:
            log.info("收到中断，退出")
            return
        except Exception as e:
            log.error("轮询出错 %s: %s，%s 秒后重试", type(e).__name__, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)
