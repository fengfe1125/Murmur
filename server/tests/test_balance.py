"""余额快照：接口地址推导、金额解析、同值不重复写、取不到也留痕。

前身是 tests/test_web.py 里的额度去重测试——采集代码搬进
murmur/balance.py 之后，测试跟着代码走，web.py 就不用为了它再导出一个
只给测试用的别名。
"""

import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.balance import (  # noqa: E402
    BALANCE_SCHEMA,
    _amount,
    balance_url,
    snapshot,
)

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


print("── 余额接口挂在 API 根上，不在 base_url 的路径下 " + "─" * 12)

check("/beta 被剥掉，取 scheme+host",
      balance_url("https://api.deepseek.com/beta")
      == "https://api.deepseek.com/user/balance",
      balance_url("https://api.deepseek.com/beta"))
check("/v1 同理",
      balance_url("https://api.deepseek.com/v1")
      == "https://api.deepseek.com/user/balance")
check("末尾斜杠不会拼出双斜杠",
      balance_url("https://api.deepseek.com/beta/")
      == "https://api.deepseek.com/user/balance")
check("换个自建网关也照样只取 host",
      balance_url("https://gw.example.test:8443/openai/v1")
      == "https://gw.example.test:8443/user/balance",
      balance_url("https://gw.example.test:8443/openai/v1"))


print("\n── 金额是字符串，转不动就当没取到 " + "─" * 24)

check('"110.00" → 110.0', _amount("110.00") == 110.0)
check('"0" → 0.0（有账号但没钱，和"没取到"不是一回事）',
      _amount("0") == 0.0 and _amount("0") is not None)
check("None → None", _amount(None) is None)
check('"" → None', _amount("") is None)
check('"unknown" → None（别把字符串塞进 REAL 列）', _amount("unknown") is None)


print("\n── 余额快照：没变化就别记 " + "─" * 34)

with tempfile.TemporaryDirectory() as d:
    conn = sqlite3.connect(Path(d) / "b.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(BALANCE_SCHEMA)

    q = {"ok": True, "available": True, "currency": "CNY",
         "total": 110.0, "granted": 10.0, "topped_up": 100.0}
    snapshot(conn, q)
    snapshot(conn, q)
    snapshot(conn, q)
    n = conn.execute("SELECT COUNT(*) n FROM balance_snapshots").fetchone()["n"]
    check("值没变就只有一行（5 分钟一采，无脑插一个月就是 8600 行噪点）",
          n == 1, f"实际 {n} 行")

    q2 = dict(q, total=107.5, topped_up=97.5)
    snapshot(conn, q2)
    check("花掉了一点就记一行",
          conn.execute(
              "SELECT COUNT(*) n FROM balance_snapshots").fetchone()["n"] == 2)

    # 隔了一小时就算没变也留个点，否则停机一整天在图上是一条直线
    conn.execute("UPDATE balance_snapshots SET at = ? WHERE id = 2",
                 ((datetime.now(UTC) - timedelta(hours=2))
                  .isoformat(timespec="seconds"),))
    snapshot(conn, q2)
    check("离上一条超过一小时就补一个点",
          conn.execute(
              "SELECT COUNT(*) n FROM balance_snapshots").fetchone()["n"] == 3)

    snapshot(conn, {"ok": False, "detail": "HTTP 503"})
    last = conn.execute(
        "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    check("取不到余额也记一行，写清原因（不然图上是断的但没人知道为什么）",
          last["ok"] == 0 and last["detail"] == "HTTP 503")
    check("取不到时金额留空，不是 0——0 的意思是真没钱了",
          last["total"] is None and last["available"] is None)

    # 余额见底和取不到必须是两条不同的记录，看板的红色告警才有意义
    snapshot(conn, dict(q, total=0.0, granted=0.0,
                        topped_up=0.0, available=False))
    last = conn.execute(
        "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    check("余额归零记成 ok=1 / total=0 / available=0",
          last["ok"] == 1 and last["total"] == 0.0 and last["available"] == 0,
          str(dict(last)))
    conn.close()


print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
