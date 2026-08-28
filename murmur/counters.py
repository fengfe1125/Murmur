"""无正文的聚合计数：回复质量在生产上唯一的量化窗口。

reply-quality P0 的承诺是"零静默丢失"，但验收需要数字：降级抢救占多少、
主动 quiet 占多少、dossier 整理失败率多少。这些分类此前只写在日志里
（category=full_output 等），日志会滚掉，也没法画曲线。这里把它们落成
一张按天汇总的表——**只有分类名和计数，没有任何正文、用户 id 或时间戳
之外的信息**，与"日志只写结构化元数据"的隐私约束是同一条。

设计上有两个刻意的取舍：

- 每次 bump 自己开一条连接。engine / dossier / worker 手里的连接各有
  事务语义，计数不该掺和进去；而回复路径上一次的模型调用以秒计，
  一次本地 sqlite 开连接的成本可以忽略。
- bump 永远不抛异常。计数挂了不能把回复路径带挂——宁可少一行数据。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from .config import Config

log = logging.getLogger("murmur.counters")

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_counters (
    day   TEXT NOT NULL,
    key   TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);
"""


def bump(cfg: Config, key: str, n: int = 1) -> None:
    """给今天的 key 加 n。key 形如 "respond.full_output"。"""
    try:
        day = datetime.now(cfg.tz).date().isoformat()
        with closing(sqlite3.connect(cfg.db_path, timeout=10.0)) as conn:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO daily_counters (day, key, count) VALUES (?,?,?)"
                " ON CONFLICT(day, key) DO UPDATE SET count = count + ?",
                (day, key, n, n),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 - 计数永远不许带挂主路径
        log.warning("计数写入失败 key=%s error_type=%s", key, type(e).__name__)


def recent(conn: sqlite3.Connection, *, days: int = 14, tz=None) -> list[dict]:
    """最近 N 天的计数，给看板用。按本地日期切，和 quota 曲线一致。"""
    since = (datetime.now(tz).date() - timedelta(days=days - 1)).isoformat()
    conn.executescript(SCHEMA)
    rows = conn.execute(
        "SELECT day, key, count FROM daily_counters WHERE day >= ?"
        " ORDER BY day, key",
        (since,),
    ).fetchall()
    return [{"day": r["day"], "key": r["key"], "count": r["count"]}
            for r in rows]
