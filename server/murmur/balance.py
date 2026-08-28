"""DeepSeek 账户余额的采集与存储。

前身是 OpenCode 的额度采集（滚动窗口 / 周 / 月三个百分比）。换成
DeepSeek 直连之后那个接口没有了，能问的只有"账上还剩多少钱"，所以
图上从「用掉了百分之几」变成了「还剩多少」——**方向是反的**，跌到 0
才是要出事，看板上的告警色也跟着反过来。

两个接口形状上的坑：

- 余额接口在 API 根上（`https://api.deepseek.com/user/balance`），
  **不在 base_url 的 /beta 或 /v1 下面**。所以这里从 base_url 里取
  scheme+host 重新拼，而不是往后面接路径。
- 金额是**字符串**（"110.00"），不是数字。存之前转 float，转不动就
  当没取到——宁可图上断一个点，也不要把 "unknown" 塞进 REAL 列。

采集为什么在这儿：原来只活在看板进程里，**看板不开就没有数据**，
生产 VPS 上没人天天开着面板，曲线全是大段空白。所以挪进常驻的
app-worker，看板保留自己那份用于本机单跑看板的场景；snapshot 的去重
（同值一小时内不重复写）让两个 poller 同时存在也不会写重复行。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .config import Config

log = logging.getLogger("murmur.balance")

# 采样间隔。余额是慢变量，5 分钟一采足够看出趋势，又不至于把请求打太密。
BALANCE_EVERY = 300.0
# 就算没变化也留一个点：不然停机一整天，图上会是一条直接连过去的直线，
# 看不出中间其实没有数据。
BALANCE_HEARTBEAT = timedelta(hours=1)

BALANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS balance_snapshots (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    currency      TEXT,
    total         REAL,
    granted       REAL,
    topped_up     REAL,
    available     INTEGER,
    ok            INTEGER NOT NULL DEFAULT 1,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_balance_at ON balance_snapshots(at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _amount(raw) -> float | None:
    """"110.00" → 110.0；空的、缺的、不是数的都当没取到。"""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def balance_url(base_url: str) -> str:
    """从 base_url 推出余额接口的地址。

    `https://api.deepseek.com/beta` → `https://api.deepseek.com/user/balance`。
    余额接口挂在 API 根上，不在 /beta 或 /v1 下面，所以只取 scheme+host。
    """
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}/user/balance"


def fetch_balance(cfg: Config) -> dict:
    """DeepSeek 账户余额：币种 + 总额 / 赠送 / 充值，外加"还够不够用"。

    这个接口只给"此刻"。要看变化就得自己按时间攒——见 poller()。
    """
    if not cfg.api_key:
        return {"ok": False, "detail": "没有配 API key"}
    try:
        import requests

        r = requests.get(
            balance_url(cfg.base_url),
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=15,
        )
        if r.status_code != 200:
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        data = r.json()
    except Exception as e:  # noqa: BLE001 - 网络的锅不该让调用方整个挂掉
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    # 文档上 balance_infos 是个数组（一个币种一条）。实际只会有一条，
    # 但拿第一条也比 [0] 硬索引安全——账户没充过值时它可能是空的。
    infos = data.get("balance_infos") or []
    first = infos[0] if infos else {}
    return {
        "ok": True,
        "at": _now_iso(),
        "available": bool(data.get("is_available")),
        "currency": first.get("currency"),
        "total": _amount(first.get("total_balance")),
        "granted": _amount(first.get("granted_balance")),
        "topped_up": _amount(first.get("topped_up_balance")),
    }


_FIELDS = ("currency", "total", "granted", "topped_up")


def snapshot(conn: sqlite3.Connection, q: dict) -> None:
    """只在有变化、或离上一条超过一小时的时候写一行。

    每 5 分钟无脑插一行的话，一个月就是 8600 行几乎相同的数据，
    图上全是噪点，还得在前端再抽稀一次。顺带地，这也是看板和
    app-worker 两个 poller 能和平共处的原因：同值不重复写。
    """
    last = conn.execute(
        "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    ok = int(bool(q.get("ok")))
    available = int(bool(q.get("available"))) if q.get("ok") else None
    vals = [q.get(k) if q.get("ok") else None for k in _FIELDS]
    if last is not None:
        same = (
            last["ok"] == ok
            and last["available"] == available
            and [last[k] for k in _FIELDS] == vals
        )
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(last["at"])
        except ValueError:
            age = BALANCE_HEARTBEAT
        if same and age < BALANCE_HEARTBEAT:
            return
    conn.execute(
        "INSERT INTO balance_snapshots"
        " (at, currency, total, granted, topped_up, available, ok, detail)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (_now_iso(), *vals, available, ok, q.get("detail")),
    )
    conn.commit()


def poller(cfg: Config, stop: threading.Event) -> None:
    """后台按 BALANCE_EVERY 采一次余额。

    每轮重建连接：备份恢复会整个换掉 db 文件，长驻连接感知不到，
    会一直往已删除的 inode 上写。
    """
    while not stop.is_set():
        try:
            with closing(sqlite3.connect(cfg.db_path, timeout=10.0)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=10000")
                conn.executescript(BALANCE_SCHEMA)
                snapshot(conn, fetch_balance(cfg))
        except Exception as e:  # noqa: BLE001
            log.warning("采余额失败：%s: %s", type(e).__name__, e)
        stop.wait(BALANCE_EVERY)
