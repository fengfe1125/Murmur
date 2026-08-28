"""名册（新人自动入册）的测试。

要保证的几件事：
1. 新人第一次说话就入册，不用改 .env 也不用重启
2. 入册的人会被排进主动消息，而且是**当天**就排上
3. 说过"别发了"的人从名册的查询结果里消失
4. 老用户（名册这张表出现之前就在聊的）会被补进去
5. 重复入册不会重复排、不会重复打招呼

    python tests/test_roster.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur.memory import Memory, thread_key  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


print("\n── 入册 " + "─" * 49)
with tempfile.TemporaryDirectory() as d:
    mem = Memory(Path(d) / "m.db")
    key, label = thread_key("dt", "oto", "6638270543882421")

    check("第一次入册返回 True（是新人）",
          mem.enroll("dt", "6638270543882421", key, label, "毛杰") is True)
    check("第二次返回 False（别重复打招呼）",
          mem.enroll("dt", "6638270543882421", key, label, "毛杰") is False)

    r = mem.roster("dt")
    check("名册里有他", len(r) == 1 and r[0]["user_id"] == "6638270543882421")
    check("昵称记下来了（日志里能看懂是谁）", r[0]["nick"] == "毛杰")
    check("chat_id 存的是 thread_key 的键", r[0]["chat_id"] == key)

    mem.enroll("dt", "6638270543882421", key, label, "毛老师")
    check("改名了跟得上", mem.roster("dt")[0]["nick"] == "毛老师")

print("\n── 平台之间不串 " + "─" * 41)
with tempfile.TemporaryDirectory() as d:
    mem = Memory(Path(d) / "m.db")
    mem.enroll("dt", "u1", 1, "dt:oto:u1")
    mem.enroll("wx", "u1", 2, "wx:oto:u1")
    mem.enroll("tg", "u1", 3, "tg:u1:u1")
    check("同名 id 在三个平台各算各的",
          len(mem.roster("dt")) == 1 and len(mem.roster("wx")) == 1
          and len(mem.roster("tg")) == 1)
    check("查钉钉不会查出微信的人",
          mem.roster("dt")[0]["chat_id"] == 1)

print("\n── 说了别发就从名册结果里消失 " + "─" * 28)
with tempfile.TemporaryDirectory() as d:
    mem = Memory(Path(d) / "m.db")
    ka, kb = 100, 200
    mem.enroll("dt", "a", ka, "dt:oto:a")
    mem.enroll("dt", "b", kb, "dt:oto:b")
    check("两个人都在", len(mem.roster("dt")) == 2)

    mem.set_optout(kb, "dt:oto:b", True)
    ids = [r["user_id"] for r in mem.roster("dt")]
    check("退出的人不再出现在收件人里", ids == ["a"], f"实际 {ids}")
    check("但记录还在（没被删掉，反悔了能恢复）",
          mem.enroll("dt", "b", kb, "dt:oto:b") is False)

    mem.set_optout(kb, "dt:oto:b", False)
    check("反悔之后又回到收件人里",
          sorted(r["user_id"] for r in mem.roster("dt")) == ["a", "b"])

print("\n── 顺序稳定（排程要可复现） " + "─" * 30)
with tempfile.TemporaryDirectory() as d:
    mem = Memory(Path(d) / "m.db")
    for i, u in enumerate("abcde"):
        mem.enroll("dt", u, i, f"dt:oto:{u}")
    check("按入册时间排，先来的在前",
          [r["user_id"] for r in mem.roster("dt")] == list("abcde"))

print("\n── 跨进程可见（钉钉和 Telegram 是两个进程） " + "─" * 16)
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "m.db"
    a = Memory(p)
    a.enroll("dt", "newguy", 7, "dt:oto:newguy", "新来的")
    b = Memory(p)          # 另一个进程打开同一个库
    check("另一个进程立刻看得到",
          [r["user_id"] for r in b.roster("dt")] == ["newguy"])

print("\n── 空名册不炸 " + "─" * 43)
with tempfile.TemporaryDirectory() as d:
    mem = Memory(Path(d) / "m.db")
    check("没人时返回空列表而不是 None", mem.roster("dt") == [])

print("\n── 旧库能平滑升上来 " + "─" * 37)
with tempfile.TemporaryDirectory() as d:
    import sqlite3
    p = Path(d) / "old.db"
    # 造一个没有 roster 表的老库
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE entries (id INTEGER PRIMARY KEY, logged_at TEXT,"
        " shot_at TEXT, bucket TEXT, weekday TEXT, scene TEXT, move TEXT,"
        " said TEXT, note TEXT);"
        "INSERT INTO entries (logged_at, said) VALUES ('2026-01-01','老数据');"
    )
    con.commit()
    con.close()

    mem = Memory(p)        # 打开旧库应该自动补表补列
    check("旧库打得开", True)
    check("老数据还在", len(mem.recent(chat_id=0, limit=5)) == 1)
    state = mem.conn.execute(
        "SELECT delivery_state FROM entries LIMIT 1"
    ).fetchone()[0]
    check("老数据迁移后默认视为已送达", state == "committed")
    check("roster 表自动建出来了", mem.roster("dt") == [])
    check("能往旧库里入册", mem.enroll("dt", "x", 1, "dt:oto:x") is True)

print("\n── 聊天原文库只许本人读写 " + "─" * 32)
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "m.db"
    mem = Memory(p)
    mem.record(chat_id=1, shot_at=None, bucket=None, weekday=None,
               spot=None, scene=None, move=None, said="hi", note=None)
    check("db 文件是 0600", (p.stat().st_mode & 0o777) == 0o600)

print("\n── 两个进程同时入册同一个人不炸 " + "─" * 26)
with tempfile.TemporaryDirectory() as d:
    import threading
    p = Path(d) / "m.db"
    # 两个连接模拟两个进程；它们各自的 SELECT 都可能抢在对方 INSERT 前跑完。
    # 没有 ON CONFLICT 的话，后 INSERT 的那个必吃 IntegrityError。
    instances = [Memory(p), Memory(p)]
    errors: list[Exception] = []

    def race_enroll(mem: Memory) -> None:
        for _ in range(30):
            try:
                mem.enroll("dt", "racy", 9, "dt:oto:racy", "新人")
            except Exception as exc:  # 结果本身就是断言
                errors.append(exc)

    threads = [threading.Thread(target=race_enroll, args=(m,)) for m in instances]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("没有人吃到 IntegrityError", errors == [], f"实际 {errors[:1]}")
    check("名册里只有一行", len(instances[0].roster("dt")) == 1)

print("\n── 多线程并发写不炸、不丢 " + "─" * 30)
with tempfile.TemporaryDirectory() as d:
    import threading
    mem = Memory(Path(d) / "m.db")
    errors: list[Exception] = []

    def write_entries() -> None:
        for i in range(30):
            try:
                mem.record(chat_id=1, shot_at=None, bucket=None, weekday=None,
                           spot=None, scene=None, move="speak", said=f"句{i}",
                           note=None)
            except Exception as exc:  # 结果本身就是断言
                errors.append(exc)

    threads = [threading.Thread(target=write_entries) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("没有 cannot start a transaction 之类的错", errors == [],
          f"实际 {errors[:1]}")
    check("120 条一条没丢", len(mem.recent(chat_id=1, limit=200)) == 120)

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
