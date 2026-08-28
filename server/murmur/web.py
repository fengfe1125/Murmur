"""管理员运维面板：查看状态与诊断，不是用户产品入口。

使用 stdlib 的 http.server；共享 App API 则使用 FastAPI。

面板会写 balance_snapshots，授权写接口也可通过既有 SSH 权限创建邀请码；
不能统称为只读。产品聊天、照片房间存档和未来日记是不同的数据概念。

默认只绑 127.0.0.1。面板上有聊天原文和记忆文件，那是很私人的东西，
不该因为在咖啡馆连了个 wifi 就暴露在局域网里。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import counters, vps_panel
from .balance import BALANCE_EVERY, BALANCE_SCHEMA, poller
from .config import Config
from .dossier import BLOCKS, REFRESH_EVERY, Dossier, _safe

log = logging.getLogger("murmur.web")

UI_DIR = Path(__file__).parent / "webui"

# run.sh 和看板读同一个日志目录：Config.log_dir（.env 的 MURMUR_LOGDIR，
# 默认 db 旁边的 logs/）。两个入口必须一致，不然面板上的日志尾巴是空的。

# 三个入口进程。key 同时是 CLI 子命令名和日志文件名的后缀。
PLATFORMS = {
    "bot": {"label": "Telegram", "platform": "tg", "short": "TG"},
    "dingtalk": {"label": "钉钉", "platform": "dt", "short": "DT"},
    "wechat": {"label": "微信", "platform": "wx", "short": "WX"},
    "qq": {"label": "QQ", "platform": "qq", "short": "QQ"},
}

# 余额快照间隔与表结构都收在 murmur/balance.py——采集现在有两个入口：
# 这里的 poller（本机单跑看板的场景）和 app-worker 里的常驻 poller
#（生产 VPS 上看板几乎不开，不挪过去曲线全是大段空白）。

# /api/vps/status 的结果缓存：前端 30 秒一轮询，直连 ssh 最长要 45 秒，
# 弱网下不缓存的话请求会叠在一起（ThreadingHTTPServer 没有线程上限）。
VPS_STATUS_TTL = 20.0


# ---------------------------------------------------------------------------
# 脱敏
#
# 日志里有 Telegram 的完整 token（PTB 把它写进 getUpdates 的 URL 里），
# 面板要展示日志尾巴，不擦掉等于把 bot 的控制权贴在网页上。

_SECRET_PATTERNS = [
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(access_?token|client_secret|api_?key)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-\.]{8,}"),
]


def _secrets(cfg: Config) -> tuple[str | None, ...]:
    """.env 里的密钥真值。vps_panel 脱敏远端日志时复用同一份。"""
    return (cfg.telegram_token, cfg.api_key, cfg.dingtalk_client_secret,
            cfg.wechat_token, cfg.qq_client_secret)


def _redact(text: str, cfg: Config) -> str:
    """先按配置里的真值精确擦，再按模式兜底。顺序不能反——
    精确擦掉之后，模式匹配剩下的才是没预料到的那些。"""
    for secret in _secrets(cfg):
        if secret and len(secret) >= 8:
            text = text.replace(secret, "***")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(0)[:6] + "***", text)
    return text


def _tail(path: Path, n: int) -> list[str]:
    """从文件尾部读 n 行。日志有 900 KB 了，别整个读进来。"""
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block, data = 8192, b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
    return data.decode("utf-8", "replace").splitlines()[-n:]


# ---------------------------------------------------------------------------
# 进程 / 端口

def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_etime(raw: str) -> int | None:
    """ps 的 etime：[[dd-]hh:]mm:ss。macOS 没有 etimes，只能自己拆。"""
    raw = raw.strip()
    days = 0
    if "-" in raw:
        d, raw = raw.split("-", 1)
        days = int(d)
    parts = [int(x) for x in raw.split(":")] if raw else []
    if not parts:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


def _processes() -> dict[str, list[dict]]:
    """按子命令归类正在跑的 murmur 进程。

    同一个子命令可能有多份（上一个还没死透、或者手滑跑了两遍）——
    那本身就是要在面板上看见的问题，所以返回列表而不是取第一个。
    """
    out: dict[str, list[dict]] = {k: [] for k in PLATFORMS}
    text = _run(["ps", "-axo", "pid=,etime=,rss=,pcpu=,args="])
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, etime, rss, pcpu, args = parts
        # 看板自己也是 murmur 进程，别把自己算进去
        if "murmur" not in args or "run.sh" in args or "run-test-bots.sh" in args \
                or re.search(r"murmur\s+web(\s|$)", args):
            continue
        for kind in PLATFORMS:
            # 命令行长这样：.../python3 .venv/bin/murmur bot
            if re.search(rf"murmur\s+{kind}(\s|$)", args):
                out[kind].append({
                    "pid": int(pid),
                    "uptime_s": _parse_etime(etime),
                    "rss_mb": round(int(rss) / 1024, 1),
                    "cpu": float(pcpu),
                    "cmd": args.strip(),
                })
                break
    return out


def _sockets(pid: int) -> dict:
    """这个进程占了哪些端口。

    实际情况：三个 bot 都是**主动连出去**的（Telegram 长轮询、钉钉和微信
    是 websocket），谁都不监听端口。所以面板要同时给出监听端口和出站连接，
    只显示前者的话看起来像"什么都没有"，反而让人以为进程死了。
    """
    if not shutil.which("lsof"):
        return {"listen": [], "outbound": [], "note": "系统里没有 lsof"}
    # -a 是把 -p 和 -iTCP 取交集。少了它 lsof 会把两个条件当"或"，
    # 结果是整机的连接都列出来——排查时被这个坑过一次。
    text = _run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP"], timeout=6.0)
    listen, outbound = [], []
    for line in text.splitlines()[1:]:
        m = re.search(r"(\S+)\s+\((LISTEN|ESTABLISHED)\)$", line)
        if not m:
            continue
        addr, state = m.group(1), m.group(2)
        if state == "LISTEN":
            listen.append(addr)
        elif "->" in addr:
            outbound.append(addr.split("->", 1)[1])
    return {
        "listen": sorted(set(listen)),
        "outbound": sorted(set(outbound)),
    }


def _log_state(kind: str, cfg: Config) -> dict:
    """日志里能读出来的两件事：最后一次动静是什么时候、今天重启了几次。"""
    path = cfg.log_dir / f"murmur_{kind}.log"
    if not path.exists():
        return {"path": str(path), "exists": False}
    lines = _tail(path, 400)
    restarts, last_start = 0, None
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    for ln in lines:
        # run.sh 写的分隔行：=== 2026-08-13 12:44:01 启动 bot ===
        if m := re.match(r"===\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+启动", ln):
            last_start = m.group(1)
            try:
                at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                if at.astimezone(UTC) >= cutoff:
                    restarts += 1
            except ValueError:
                pass
    body = [ln for ln in lines if ln.strip()]
    return {
        "path": str(path),
        "exists": True,
        "size": path.stat().st_size,
        "mtime": datetime.fromtimestamp(
            path.stat().st_mtime, UTC
        ).isoformat(timespec="seconds"),
        "restarts_24h": restarts,
        "last_start": last_start,
        "last_line": _redact(body[-1], cfg) if body else "",
    }


def _health(configured: bool, instances: list[dict], log_state: dict) -> dict:
    """把“进程还在”翻译成更适合扫一眼的运行健康度。

    这是一个看板信号，而不是存活探针：某些平台长时间没有新消息时日志
    本来就不会滚动，所以不能仅因日志旧就报故障。只标记已有直接证据的情况，
    避免把安静误报成异常。
    """
    if not configured:
        return {"level": "off", "label": "未配置", "reasons": []}
    if not instances:
        return {"level": "down", "label": "进程未运行", "reasons": ["没有找到进程"]}

    reasons: list[str] = []
    if len(instances) > 1:
        reasons.append(f"同时运行 {len(instances)} 个进程")
    restarts = log_state.get("restarts_24h") or 0
    if restarts >= 3:
        reasons.append(f"24 小时内重启 {restarts} 次")
    last_line = (log_state.get("last_line") or "").lower()
    if any(word in last_line for word in ("error", "exception", "traceback", "failed")):
        reasons.append("日志最后一行是错误")
    if reasons:
        return {"level": "degraded", "label": "需要留意", "reasons": reasons}
    return {"level": "healthy", "label": "运行正常", "reasons": []}


def _configured(kind: str, cfg: Config) -> tuple[bool, str]:
    """没配就不算"挂了"。区分这两者，否则面板上永远有两个红灯。"""
    if kind == "bot":
        return bool(cfg.telegram_token), "缺 TELEGRAM_BOT_TOKEN"
    if kind == "dingtalk":
        return (
            bool(cfg.dingtalk_client_id and cfg.dingtalk_client_secret),
            "缺 DINGTALK_CLIENT_ID / SECRET",
        )
    if kind == "qq":
        return (
            bool(cfg.qq_app_id and cfg.qq_client_secret),
            "缺 QQ_APP_ID / QQ_CLIENT_SECRET",
        )
    state = Path(os.getenv("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))
    ok = bool(cfg.wechat_token) or any(
        (state / "openclaw-weixin" / "accounts").glob("*.json")
    ) or (state / "credentials" / "openclaw-weixin" / "credentials.json").exists()
    return ok, "微信还没扫码登录"


# ---------------------------------------------------------------------------
# 额度

def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 会话 / 消息

def _platform_of(thread: str | None, fallback: str | None = None) -> str:
    if thread:
        head = thread.split(":", 1)[0]
        if head in ("tg", "dt", "wx", "qq"):
            return head
    return fallback or "?"


def _split_thread(thread: str | None) -> tuple[str, str, str]:
    """thread 是 thread_key() 拼的 "平台:会话:发送人"。

    只能切两刀：**发送人自己就可能带冒号**（钉钉群里拿到的是
    `$:LWCP_v1:$ZuZPx6...` 这种），会话是 base64 也可能带 = 和 /。
    按最后一个冒号切的话，这些人会各自变成一个陌生人。
    """
    if not thread:
        return "?", "", ""
    parts = thread.split(":", 2)
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _person_id(cfg: Config, platform: str, sender: str) -> str:
    """把一个发送人身份归到"这是谁"。

    钉钉的 staffId 是按组织发的，同一个人在两家企业里是两个 id——
    config 里用 `a|b` 声明过的，这里合成同一个人。

    另外还要收拾一个历史遗留：`murmur poke --conv <userId>` 会拼出
    `dt:oto:<id>:<id>`，切出来的发送人是 "id:id"。那就是同一个人，
    不收拾的话他会在名单上多出一个只有一条消息的分身。
    """
    if platform == "dt":
        bits = sender.split(":")
        if len(bits) == 2 and bits[0] == bits[1]:
            sender = bits[0]
        return cfg.canonical_dingtalk_id(sender)
    return sender


def _conversation_label(platform: str, conversation: str, sender: str) -> tuple[str, str]:
    """(种类, 给人看的名字)。单聊是常态，群聊要在消息上标出来。"""
    if not conversation:
        return "legacy", "早期记录"
    if conversation == "oto" or conversation == sender:
        return "oto", "单聊"
    if platform == "dt":
        return "group", "群聊 " + conversation[3:11].rstrip("=/+")
    return "group", "群聊 " + conversation[:10]


def _bubbles(said: str | None) -> list[str]:
    """存库时多条气泡是用 ' ⏎ ' 拼起来的，展示时拆回去。"""
    if not said:
        return []
    return [x.strip() for x in said.split("⏎") if x.strip()]


def _row_has_photo(r) -> bool:
    try:
        if r["has_photo"]:
            return True
    except (IndexError, KeyError):
        pass
    return bool(r["shot_at"] or r["spot"])


def _messages(rows: list[sqlite3.Row]) -> list[dict]:
    """把 entries 还原成一条聊天流。

    数据结构上有个坑：entry.reply 存的是"他在这句之后回的话"，
    而这句话通常**又会作为下一条 entry 的 note 存一遍**（实测 id 58 的
    reply 和 id 59 的 note 一模一样）。两处都渲染的话，面板上每句话
    都会出现两遍。所以 reply 只在它和下一条的 note 不同时才画。
    """
    msgs: list[dict] = []
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        at = r["logged_at"]
        scene = (r["scene"] or "").strip()
        has_photo = _row_has_photo(r)

        if r["kind"] == "out":
            # 它主动开口，前面没有"他说了什么"
            for b in _bubbles(r["said"]):
                msgs.append({"side": "bot", "text": b, "at": at,
                             "intent": r["intent"], "initiated": True})
        else:
            if r["note"] or has_photo:
                msgs.append({
                    "side": "user", "text": r["note"] or "", "at": at,
                    "photo": has_photo,
                    "photo_url": f"/api/photo?id={r['id']}" if has_photo else None,
                    "shot_at": r["shot_at"],
                    "scene": scene if scene not in ("（纯文字）", "") else None,
                })
            if r["move"] == "quiet" or not r["said"]:
                msgs.append({"side": "bot", "text": "", "at": at, "quiet": True,
                             "scene": scene if scene != "（纯文字）" else None})
            else:
                for b in _bubbles(r["said"]):
                    msgs.append({"side": "bot", "text": b, "at": at,
                                 "move": r["move"]})

        if r["reply"] and not (nxt and (nxt["note"] or "").strip() == r["reply"].strip()):
            msgs.append({"side": "user", "text": r["reply"],
                         "at": at, "is_reply": True})
    return msgs


def _contexts(conn: sqlite3.Connection, cfg: Config) -> list[dict]:
    """数据库里出现过的每一条上下文（= 一个 chat_id）。

    这是 Murmur 内部的划分单位：(平台, 会话, 发送人)。同一个人在单聊和
    群里是两条，记忆也是两份——面板按人合并展示，但这一层要先算出来。
    """
    roster = {
        int(r["chat_id"]): dict(r)
        for r in conn.execute("SELECT * FROM roster")
    }
    local_midnight = datetime.now(cfg.tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT chat_id,"
        "       MAX(thread)          AS thread,"
        "       COUNT(*)             AS total,"
        "       SUM(kind = 'out')    AS outbound,"
        "       SUM(CASE WHEN kind = 'out' AND datetime(logged_at) >= datetime(?)"
        "                THEN 1 ELSE 0 END) AS outbound_today,"
        "       SUM(reply IS NOT NULL AND reply != '') AS answered,"
        "       MIN(logged_at)       AS first_at,"
        "       MAX(logged_at)       AS last_at,"
        "       MAX(CASE WHEN kind = 'out' THEN logged_at END) AS last_outbound"
        " FROM entries WHERE delivery_state = 'committed' GROUP BY chat_id",
        (local_midnight,),
    ).fetchall()

    out: list[dict] = []
    seen = set()
    for r in rows:
        cid = int(r["chat_id"])
        seen.add(cid)
        who = roster.get(cid)
        thread = r["thread"] or (who or {}).get("thread")
        platform = _platform_of(thread, (who or {}).get("platform"))
        _, conv, sender = _split_thread(thread)
        kind, label = _conversation_label(platform, conv, sender)
        last = conn.execute(
            "SELECT note, said, move FROM entries"
            " WHERE chat_id = ? AND delivery_state = 'committed'"
            " ORDER BY id DESC LIMIT 1", (cid,)
        ).fetchone()
        preview = (last["said"] or last["note"] or "") if last else ""
        if last is not None and last["move"] == "quiet" and not last["said"]:
            preview = "（它选择了不说话）"
        unanswered_rows = conn.execute(
            "SELECT kind, reply FROM entries WHERE chat_id = ?"
            " AND delivery_state = 'committed' ORDER BY id DESC LIMIT 12",
            (cid,),
        ).fetchall()
        unanswered = 0
        for item in unanswered_rows:
            if item["kind"] != "out" or item["reply"]:
                break
            unanswered += 1
        out.append({
            "chat_id": cid,
            "thread": thread,
            "platform": platform,
            "sender": sender or (who or {}).get("user_id"),
            "conversation": conv,
            "conv_kind": kind,
            "conv_label": label,
            "total": r["total"],
            "outbound": r["outbound"] or 0,
            "outbound_today": r["outbound_today"] or 0,
            "unanswered": unanswered,
            "answered": r["answered"] or 0,
            "first_at": r["first_at"],
            "last_at": r["last_at"],
            "last_outbound": r["last_outbound"],
            "preview": preview.replace("⏎", " ")[:60],
            "opted_out": conn.execute(
                "SELECT 1 FROM optouts WHERE chat_id = ?", (cid,)
            ).fetchone() is not None,
            "greeted": conn.execute(
                "SELECT 1 FROM greeted WHERE chat_id = ?", (cid,)
            ).fetchone() is not None,
            "dossier": _dossier_meta(cfg, thread),
        })

    # 在册但还一条记录都没有的人（刚入册、还没说过话）
    for cid, who in roster.items():
        if cid in seen:
            continue
        _, conv, sender = _split_thread(who["thread"])
        kind, label = _conversation_label(who["platform"], conv, sender)
        out.append({
            "chat_id": cid, "thread": who["thread"], "platform": who["platform"],
            "sender": sender or who["user_id"], "conversation": conv,
            "conv_kind": kind, "conv_label": label,
            "total": 0, "outbound": 0, "outbound_today": 0, "unanswered": 0,
            "answered": 0, "first_at": who["added_at"], "last_at": None,
            "last_outbound": None,
            "preview": "（还没说过话）", "opted_out": False, "greeted": False,
            "dossier": _dossier_meta(cfg, who["thread"]),
        })
    return out


def _people(conn: sqlite3.Connection, cfg: Config) -> list[dict]:
    """把上下文按"这是谁"合并成聊天窗口。

    一个人会散成好几条上下文：单聊一条、每个群各一条、钉钉换个组织
    又是一条。按上下文列的话，同一个人在名单上出现四次，四个名字一模一样，
    根本认不出哪个是哪个——实测 01484515655929393400 就是这样。

    合并只是**展示**层面的。Murmur 自己仍然是一条上下文一份记忆，
    群里说的话不会串进单聊——所以窗口里要标出每条消息来自哪个会话。
    """
    roster = list(conn.execute("SELECT * FROM roster"))
    # 名册里的人：(平台, 归一化后的 id) → 昵称等
    known: dict[tuple[str, str], dict] = {}
    for r in roster:
        pid = _person_id(cfg, r["platform"], r["user_id"])
        known.setdefault((r["platform"], pid), dict(r))

    # v0.1 的 Telegram 直接拿 chat id 当 key，没有 thread。那些记录属于
    # 同一个人，靠 chat_id == int(user_id) 认回来，不然他早期的话全丢在
    # "未归属"里。
    legacy_tg = {
        int(r["user_id"]): _person_id(cfg, "tg", r["user_id"])
        for r in roster if r["platform"] == "tg" and r["user_id"].isdigit()
    }

    people: dict[str, dict] = {}
    for ctx in _contexts(conn, cfg):
        platform, sender = ctx["platform"], ctx["sender"] or ""
        if ctx["thread"]:
            pid = _person_id(cfg, platform, sender)
        elif ctx["chat_id"] in legacy_tg:
            platform, pid = "tg", legacy_tg[ctx["chat_id"]]
            ctx["conv_label"] = "早期记录"
        else:
            # CLI 跑出来的是 chat_id=0，还有几条老记录反推不出是谁。
            # 不硬塞给某个人——猜错比不显示更糟。
            platform, pid = "?", str(ctx["chat_id"])
        key = f"{platform}:{pid}"
        p = people.setdefault(key, {
            "key": key, "platform": platform, "user_id": pid,
            "nick": None, "in_roster": False, "contexts": [],
            "total": 0, "outbound": 0, "outbound_today": 0, "unanswered": 0,
            "answered": 0, "last_outbound": None,
            "first_at": None, "last_at": None, "preview": "",
            "opted_out": False, "greeted": False,
        })
        p["contexts"].append(ctx)
        p["total"] += ctx["total"]
        p["outbound"] += ctx["outbound"]
        p["outbound_today"] += ctx["outbound_today"]
        p["unanswered"] = max(p["unanswered"], ctx["unanswered"])
        p["answered"] += ctx["answered"]
        p["opted_out"] = p["opted_out"] or ctx["opted_out"]
        p["greeted"] = p["greeted"] or ctx["greeted"]
        for k in ("first_at", "last_at"):
            cur, new = p[k], ctx[k]
            if new and (not cur or (new < cur if k == "first_at" else new > cur)):
                p[k] = new
                if k == "last_at":
                    p["preview"] = ctx["preview"]
        if ctx["last_outbound"] and (
            not p["last_outbound"] or ctx["last_outbound"] > p["last_outbound"]
        ):
            p["last_outbound"] = ctx["last_outbound"]

    for _key, p in people.items():
        who = known.get((p["platform"], p["user_id"]))
        if who:
            p["nick"] = who["nick"]
            p["in_roster"] = True
            p["added_at"] = who["added_at"]
        p["contexts"].sort(key=lambda c: c["last_at"] or "", reverse=True)
        # 有记忆文件的上下文数——一个人有好几份记忆是要看得见的事
        p["dossiers"] = sum(1 for c in p["contexts"] if c["dossier"]["exists"])
        p["name"] = p["nick"] or p["user_id"] or f"chat {p['contexts'][0]['chat_id']}"
        # 跟 initiative.should_hold() 的“连续 4 条不回就收敛”保持同一口径；
        # 看板只展示风险，不替机器人做任何发送决策。
        if p["opted_out"]:
            p["initiative_state"] = "off"
            p["initiative_note"] = "对方已退出"
        elif p["unanswered"] >= 4:
            p["initiative_state"] = "hold"
            p["initiative_note"] = f"连续 {p['unanswered']} 条主动消息未回复"
        elif p["unanswered"] >= 2 or p["outbound_today"] >= 8:
            p["initiative_state"] = "watch"
            p["initiative_note"] = (
                f"今日主动 {p['outbound_today']} 次 · 连续 {p['unanswered']} 条未回复"
            )
        else:
            p["initiative_state"] = "normal"
            p["initiative_note"] = f"今日主动 {p['outbound_today']} 次"

    out = list(people.values())
    out.sort(key=lambda x: x["last_at"] or "", reverse=True)
    return out


def _person(conn: sqlite3.Connection, cfg: Config, key: str, limit: int) -> dict:
    """一个人的完整窗口：几条上下文合成一条时间线 + 每份记忆文件。"""
    p = next((x for x in _people(conn, cfg) if x["key"] == key), None)
    if p is None:
        return {"error": "没有这个人"}

    merged: list[dict] = []
    for ctx in p["contexts"]:
        rows = conn.execute(
            "SELECT * FROM entries WHERE chat_id = ?"
            " AND delivery_state = 'committed' ORDER BY id DESC LIMIT ?",
            (ctx["chat_id"], limit),
        ).fetchall()
        # 去重要靠前后相邻，所以必须**先按上下文各自还原**，再合并时间线
        for m in _messages(list(reversed(rows))):
            m["chat_id"] = ctx["chat_id"]
            m["conv_label"] = ctx["conv_label"]
            m["conv_kind"] = ctx["conv_kind"]
            m["platform"] = ctx["platform"]
            merged.append(m)
        ctx["dossier"] = _dossier_full(cfg, ctx["thread"], conn, ctx["chat_id"])
    # 稳定排序：同一条 entry 的几个气泡时间戳相同，靠稳定性保持先后
    merged.sort(key=lambda m: m["at"] or "")

    p["messages"] = merged
    return p


def _dossier_root(cfg: Config) -> Path:
    return cfg.db_path.parent / "dossiers"


def _dossier_meta(cfg: Config, thread: str | None) -> dict:
    if not thread:
        return {"exists": False}
    path = _dossier_root(cfg) / f"{_safe(thread)}.md"
    if not path.exists():
        return {"exists": False, "path": str(path)}
    d = Dossier.load(_dossier_root(cfg), thread)
    return {
        "exists": True,
        "path": str(path),
        "updated_at": d.updated_at,
        "covered_upto": d.covered_upto,
        "empty": d.is_empty,
    }


def _dossier_full(cfg: Config, thread: str | None, conn: sqlite3.Connection,
                  chat_id: int) -> dict:
    meta = _dossier_meta(cfg, thread)
    if not thread:
        return meta
    d = Dossier.load(_dossier_root(cfg), thread)
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM entries WHERE chat_id = ? AND id > ?"
        " AND delivery_state = 'committed'",
        (chat_id, d.covered_upto),
    ).fetchone()["n"]
    meta["blocks"] = [
        {
            "name": name,
            "hint": hint,
            "limit": limit,
            "body": (d.blocks.get(name) or "").strip(),
            "used": len((d.blocks.get(name) or "").strip()),
        }
        for name, (hint, limit) in BLOCKS.items()
    ]
    meta["pending"] = pending
    meta["refresh_every"] = REFRESH_EVERY
    meta["raw"] = d.path.read_text(encoding="utf-8") if d.path.exists() else ""
    return meta


# ---------------------------------------------------------------------------
# HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "murmur-web"

    def __init__(self, cfg: Config, vps: vps_panel.VpsConfig, *args, **kw):
        self.cfg = cfg
        self.vps = vps
        super().__init__(*args, **kw)

    # 默认实现每个请求打一行到 stderr，前端 5 秒一轮询会把终端刷爆
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        """配了 MURMUR_WEB_TOKEN 就要求带上，否则一切照旧（只绑本机）。

        优先看 Header（X-Murmur-Token），也认 ?token= 方便浏览器里直接开。
        用 compare_digest 比较，别给时间侧信道。
        """
        token = self.cfg.web_token
        if not token:
            return True
        supplied = self.headers.get("X-Murmur-Token", "")
        if not supplied:
            supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return bool(supplied) and hmac.compare_digest(supplied, token)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._send(401, b"unauthorized", "text/plain")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            self._route(url.path, q)
        except BrokenPipeError:
            pass  # 用户刷新页面时前一个请求会断，这不是错误
        except Exception as e:  # noqa: BLE001
            # self.path 可能带着 ?token= 凭证，整串写进日志等于把钥匙存起来
            log.exception("请求出错 %s", url.path)
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        """唯一的写入口：在 VPS 上创建邀请码。鉴权与 GET 同一套。"""
        if not self._authorized():
            return self._send(401, b"unauthorized", "text/plain")
        url = urlparse(self.path)
        try:
            if url.path != "/api/vps/invite":
                return self._json({"error": "没有这个接口"}, 404)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "body 不是合法 JSON"}, 400)
            self._json(vps_panel.create_invite(
                self.vps,
                alias=data.get("alias"),
                days=data.get("days", 7),
                reusable=bool(data.get("reusable")),
            ))
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("请求出错 %s", url.path)  # 同 do_GET，不记 query
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _route(self, path: str, q: dict) -> None:
        if path in ("/", "/index.html"):
            html = (UI_DIR / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/mark.svg":
            # webui 目录里放了一份拷贝，装成 wheel 之后 brand/ 是不在包里的
            return self._send(200, (UI_DIR / "mark.svg").read_bytes(),
                              "image/svg+xml")
        if path == "/vps":
            return self._send(200, (UI_DIR / "vps.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path == "/api/vps/status":
            return self._json(self._vps_status())
        if path == "/api/vps/invites":
            return self._json(vps_panel.list_invites(self.vps))
        if path == "/api/overview":
            return self._json(self.overview())
        if path == "/api/balance":
            try:
                hours = int(q.get("hours", ["168"])[0])
            except ValueError:
                return self._json({"error": "hours 得是整数"}, 400)
            # 没上限的话一个 hours=99999999 就是一次全表扫
            return self._json(self.balance(min(max(hours, 1), 24 * 90)))
        if path == "/api/people":
            with closing(self.db()) as c:
                return self._json({"people": _people(c, self.cfg)})
        if path == "/api/person":
            with closing(self.db()) as c:
                return self._json(_person(c, self.cfg, q["key"][0],
                                          int(q.get("limit", ["400"])[0])))
        if path == "/api/logs":
            return self._json(self.logs(q.get("name", ["bot"])[0],
                                        int(q.get("n", ["120"])[0])))
        if path == "/api/photo":
            try:
                eid = int(q.get("id", [""])[0])
            except ValueError:
                return self._json({"error": "没有这张图"}, 404)
            p = self.cfg.db_path.parent / "photos" / f"{eid}.jpg"
            if not p.is_file() or p.stat().st_size <= 0:
                return self._json({"error": "没有这张图"}, 404)
            return self._send(200, p.read_bytes(), "image/jpeg")
        self._json({"error": "没有这个接口"}, 404)

    # ---- 数据 ----

    def db(self) -> sqlite3.Connection:
        """一个请求一条连接。看板的并发就是"我按了下刷新"，够用了。"""
        conn = sqlite3.connect(self.cfg.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    # ThreadingHTTPServer 每个请求新建一个 Handler，缓存只能挂在类上。
    _vps_status_cache: tuple[float, dict] | None = None
    _vps_status_lock = threading.Lock()

    def _vps_status(self) -> dict:
        """带缓存的 vps_panel.status()。锁拿全程：ssh 期间并发的请求
        排队等同一份结果，而不是各自再开一条 ssh。"""
        with self._vps_status_lock:
            cache = type(self)._vps_status_cache
            if cache and time.monotonic() - cache[0] < VPS_STATUS_TTL:
                return cache[1]
            result = vps_panel.status(self.vps, secrets=_secrets(self.cfg))
            type(self)._vps_status_cache = (time.monotonic(), result)
            return result

    def overview(self) -> dict:
        cfg = self.cfg
        procs = _processes()
        plats = []
        for kind, meta in PLATFORMS.items():
            configured, why = _configured(kind, cfg)
            insts = procs[kind]
            for p in insts:
                p["sockets"] = _sockets(p["pid"])
            log_state = _log_state(kind, cfg)
            plats.append({
                "kind": kind,
                "label": meta["label"],
                "short": meta["short"],
                "platform": meta["platform"],
                "configured": configured,
                "why": None if configured else why,
                "running": bool(insts),
                "instances": insts,
                "log": log_state,
                "health": _health(configured, insts, log_state),
            })

        with closing(self.db()) as c:
            c.executescript(BALANCE_SCHEMA)
            today = datetime.now(cfg.tz).strftime("%Y-%m-%d")
            # logged_at 存的是 UTC，按本地日期筛要先转过去
            stats = {}
            rows = c.execute(
                "SELECT kind, COUNT(*) n FROM entries"
                " WHERE delivery_state = 'committed'"
                " AND datetime(logged_at) >= datetime(?) GROUP BY kind",
                (self._local_midnight_utc(),),
            ).fetchall()
            for r in rows:
                stats["today_out" if r["kind"] == "out" else "today_in"] = r["n"]
            stats.setdefault("today_in", 0)
            stats.setdefault("today_out", 0)
            stats["entries"] = c.execute(
                "SELECT COUNT(*) n FROM entries"
                " WHERE delivery_state = 'committed'").fetchone()["n"]
            stats["optouts"] = c.execute(
                "SELECT COUNT(*) n FROM optouts").fetchone()["n"]

            # 人数按合并后的人算，不是按 roster 行数：钉钉同一个人在两家
            # 企业里是两行，按行数报会多出一个不存在的人。
            people = _people(c, cfg)
            known = [p for p in people if p["in_roster"]]
            midnight = datetime.fromisoformat(
                self._local_midnight_utc().replace(" ", "T") + "+00:00")
            stats["people"] = len(known)
            stats["contexts"] = sum(len(p["contexts"]) for p in people)
            stats["active_today"] = sum(
                1 for p in people if p["last_at"]
                and datetime.fromisoformat(p["last_at"]) >= midnight
            )
            stats["by_platform"] = {}
            for p in known:
                stats["by_platform"][p["platform"]] = \
                    stats["by_platform"].get(p["platform"], 0) + 1
            last = c.execute(
                "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            balance = dict(last) if last else None
            # 回复质量的无正文计数（counters.py）：只有分类名和次数，
            # 给 P0 生产验收用，不碰任何聊天内容。
            counter_rows = counters.recent(c, days=14, tz=cfg.tz)

        return {
            "now": _now_iso(),
            "today": today,
            "tz": str(cfg.tz),
            "counters": counter_rows,
            "model": {
                "name": cfg.model,
                "base_url": cfg.base_url,
                "key_set": bool(cfg.api_key),
                "memory_model": cfg.memory_model or cfg.model,
            },
            "settings": {
                "auto_enroll": cfg.auto_enroll,
                "wechat_initiative": cfg.wechat_initiative,
                "qq_sandbox": cfg.qq_sandbox,
                "allowed_chat_ids": sorted(cfg.allowed_chat_ids),
                "dingtalk_initiative": cfg.dingtalk_initiative,
                "db_path": str(cfg.db_path),
                "db_size": cfg.db_path.stat().st_size
                if cfg.db_path.exists() else 0,
                "dossier_dir": str(_dossier_root(cfg)),
                "log_dir": str(cfg.log_dir),
                "web_token_set": bool(cfg.web_token),
            },
            "platforms": plats,
            "stats": stats,
            "balance": balance,
        }

    def _local_midnight_utc(self) -> str:
        mid = datetime.now(self.cfg.tz).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return mid.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

    def balance(self, hours: int) -> dict:
        since = (datetime.now(UTC)
                 - timedelta(hours=hours)).isoformat(timespec="seconds")
        with closing(self.db()) as c:
            c.executescript(BALANCE_SCHEMA)
            rows = c.execute(
                "SELECT * FROM balance_snapshots WHERE at >= ? ORDER BY at",
                (since,),
            ).fetchall()
            latest = c.execute(
                "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "points": [dict(r) for r in rows],
            "latest": dict(latest) if latest else None,
            "poll_every_s": BALANCE_EVERY,
        }

    def logs(self, name: str, n: int) -> dict:
        if name not in PLATFORMS:
            return {"error": "只认 bot / dingtalk / wechat / qq"}
        path = self.cfg.log_dir / f"murmur_{name}.log"
        lines = [_redact(x, self.cfg) for x in _tail(path, min(n, 500))]
        return {"name": name, "path": str(path), "lines": lines}


# ---------------------------------------------------------------------------

def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False,
        vps: vps_panel.VpsConfig | None = None) -> None:
    cfg = Config.load()
    vps = vps or vps_panel.VpsConfig.resolve()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    stop = threading.Event()
    threading.Thread(target=poller, args=(cfg, stop),
                     name="murmur-balance", daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), partial(Handler, cfg, vps))
    url = f"http://{host}:{port}"
    log.info("看板在 %s（只读；Ctrl-C 停）；VPS 面板 %s/vps → %s",
             url, url, vps.describe())
    if open_browser:
        threading.Timer(0.6, lambda: __import__("webbrowser").open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
