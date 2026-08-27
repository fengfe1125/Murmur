"""OpenCode 额度快照的采集与存储。

官方接口只给"此刻的百分比"，要看变化就得自己按时间攒。
原来这个采集只活在看板进程里——**看板不开就没有数据**，生产 VPS 上
没人天天开着面板，曲线全是大段空白。所以采集挪进常驻的 app-worker
（`AppWorker.serve_forever` 里起一个同样的 poller），看板保留自己的
那份用于本机单独跑看板的场景；snapshot 的去重逻辑（同值一小时内不重复
写）让两个 poller 同时存在也不会写重复行。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta

from .config import Config

log = logging.getLogger("murmur.quota")

# 采样间隔。OpenCode 的百分比是整数，5 分钟一采样足够看出趋势，
# 又不至于把请求打得太密。
QUOTA_EVERY = 300.0
# 就算没变化也留一个点：不然停机一整天，图上会是一条直接连过去的直线，
# 看不出中间其实没有数据。
QUOTA_HEARTBEAT = timedelta(hours=1)

QUOTA_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_snapshots (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    rolling       REAL,
    weekly        REAL,
    monthly       REAL,
    rolling_reset TEXT,
    weekly_reset  TEXT,
    monthly_reset TEXT,
    ok            INTEGER NOT NULL DEFAULT 1,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_quota_at ON quota_snapshots(at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch_quota(cfg: Config) -> dict:
    """OpenCode Go 订阅的额度：滚动窗口 / 周 / 月，各是一个百分比。

    这个接口只给"此刻"。要看变化就得自己按时间攒——见 poller()。
    """
    if not cfg.api_key:
        return {"ok": False, "detail": "没有配 API key"}
    base = cfg.base_url.rstrip("/")
    try:
        import requests

        r = requests.get(
            f"{base}/usage",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=15,
        )
        if r.status_code != 200:
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        data = r.json().get("usage", {})
    except Exception as e:  # noqa: BLE001 - 网络的锅不该让调用方整个挂掉
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    out = {"ok": True, "at": _now_iso()}
    for k in ("rolling", "weekly", "monthly"):
        blk = data.get(k) or {}
        out[k] = {
            "percent": blk.get("percent"),
            "status": blk.get("status"),
            "resets_at": blk.get("resetsAt"),
        }
    return out


def snapshot(conn: sqlite3.Connection, q: dict) -> None:
    """只在有变化、或离上一条超过一小时的时候写一行。

    每 5 分钟无脑插一行的话，一个月就是 8600 行几乎相同的数据，
    图上全是噪点，还得在前端再抽稀一次。顺带地，这也是看板和
    app-worker 两个 poller 能和平共处的原因：同值不重复写。
    """
    last = conn.execute(
        "SELECT * FROM quota_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    vals = [
        (q.get(k) or {}).get("percent") if q.get("ok") else None
        for k in ("rolling", "weekly", "monthly")
    ]
    if last is not None:
        same = (
            last["ok"] == int(bool(q.get("ok")))
            and [last["rolling"], last["weekly"], last["monthly"]] == list(vals)
        )
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(last["at"])
        except ValueError:
            age = QUOTA_HEARTBEAT
        if same and age < QUOTA_HEARTBEAT:
            return
    conn.execute(
        "INSERT INTO quota_snapshots"
        " (at, rolling, weekly, monthly, rolling_reset, weekly_reset,"
        "  monthly_reset, ok, detail) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            _now_iso(), *vals,
            *[(q.get(k) or {}).get("resets_at") for k in
              ("rolling", "weekly", "monthly")],
            int(bool(q.get("ok"))), q.get("detail"),
        ),
    )
    conn.commit()


def poller(cfg: Config, stop: threading.Event) -> None:
    """后台按 QUOTA_EVERY 采一次额度。

    每轮重建连接：备份恢复会整个换掉 db 文件，长驻连接感知不到，
    会一直往已删除的 inode 上写。
    """
    while not stop.is_set():
        try:
            with closing(sqlite3.connect(cfg.db_path, timeout=10.0)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=10000")
                conn.executescript(QUOTA_SCHEMA)
                snapshot(conn, fetch_quota(cfg))
        except Exception as e:  # noqa: BLE001
            log.warning("采额度失败：%s: %s", type(e).__name__, e)
        stop.wait(QUOTA_EVERY)
