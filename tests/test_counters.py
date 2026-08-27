"""daily_counters：分类计数落库、同日累加、查询窗口、失败不影响主路径。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config, run_unittest  # noqa: E402

from murmur import counters  # noqa: E402


class CountersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "murmur.db"
        self.cfg = make_config(db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bump_creates_table_and_accumulates_same_day(self):
        counters.bump(self.cfg, "respond.full_output")
        counters.bump(self.cfg, "respond.full_output")
        counters.bump(self.cfg, "dossier.partial_saved", 3)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = counters.recent(conn, tz=self.cfg.tz)
        by_key = {r["key"]: r["count"] for r in rows}
        self.assertEqual(by_key["respond.full_output"], 2)
        self.assertEqual(by_key["dossier.partial_saved"], 3)
        # 一天一行：upsert 累加，不是每次插新行
        days = {r["day"] for r in rows}
        self.assertEqual(len(days), 1)
        conn.close()

    def test_recent_respects_window(self):
        counters.bump(self.cfg, "respond.full_output")
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO daily_counters (day, key, count)"
            " VALUES ('2000-01-01', 'respond.full_output', 9)"
        )
        conn.commit()
        rows = counters.recent(conn, days=14, tz=self.cfg.tz)
        self.assertEqual(
            [r for r in rows if r["day"] == "2000-01-01"], [])
        conn.close()

    def test_bump_never_raises(self):
        # 数据库路径不存在（父目录都没有）时，计数静默失败，
        # 不能把回复路径带挂——这是这个模块的第一条纪律。
        cfg = make_config(db_path="/nonexistent-dir-xyz/murmur.db")
        counters.bump(cfg, "respond.full_output")  # 不抛就算过


if __name__ == "__main__":
    run_unittest()
