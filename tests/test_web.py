"""看板里那些"笨但必须对"的地方。

看板本身是只读的，出错顶多是页面难看——除了两件事：
1. **脱敏**。日志里有 Telegram 的完整 token（PTB 把它写进 getUpdates 的 URL），
   漏一个就等于把 bot 的控制权贴在网页上。
2. **聊天流的去重**。entry.reply 存的是"他之后回的话"，而这句话通常又会
   作为下一条 entry 的 note 再存一遍。不去重的话面板上每句都出现两遍，
   看的人会以为它真的把话说了两次。

    python tests/test_web.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config  # noqa: E402

from murmur.memory import Memory, thread_key  # noqa: E402
from murmur.web import (  # noqa: E402
    _bubbles,
    _conversation_label,
    _health,
    _messages,
    _parse_etime,
    _people,
    _person,
    _person_id,
    _platform_of,
    _redact,
    _split_thread,
    _tail,
)

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


def row(**kw) -> dict:
    """造一条 entries 记录。没写的字段按数据库的默认值补 None。"""
    base = dict(id=1, chat_id=1, thread="tg:1:1", logged_at="2026-08-13T04:00:00+00:00",
                shot_at=None, bucket="午间", weekday="周四", spot=None, scene="",
                move="speak", said=None, note=None, reply=None, kind="in", intent=None)
    base.update(kw)
    return base


print("\n── 脱敏：日志不能把 token 带到网页上 " + "─" * 24)

cfg = make_config(telegram_token="8536719757:AAGNbASF6ByAkZp5twqO0dTt6D0-te9jexI",
                  api_key="sk-abcdef0123456789")

line = ('HTTP Request: POST https://api.telegram.org/'
        'bot8536719757:AAGNbASF6ByAkZp5twqO0dTt6D0-te9jexI/getUpdates "200 OK"')
out = _redact(line, cfg)
check("配置里的 token 被擦掉", "AAGNbASF6ByAkZp5twqO0dTt6D0-te9jexI" not in out, out)
check("擦完还看得出这是哪个请求", "getUpdates" in out and "api.telegram.org" in out, out)
check("api key 也擦", "sk-abcdef0123456789" not in _redact("key=sk-abcdef0123456789", cfg))

# 换一个 .env 里没有的 token：配置比对擦不掉它，得靠模式兜底。
# 现实里这会发生——比如日志是上一个 token 时期留下的。
other = "bot7000000001:BBHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
check("配置里没有的 token 靠模式也能擦掉",
      "BBHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" not in _redact(other, cfg),
      _redact(other, cfg))
check("空配置不会炸", _redact("普通日志", make_config()) == "普通日志")


print("\n── 进程健康度：别把‘还活着’误报成‘一切正常’ " + "─" * 12)

check("未配置不报故障", _health(False, [], {})["level"] == "off")
check("进程不在明确标成故障", _health(True, [], {})["level"] == "down")
check("重启过于频繁需要留意",
      _health(True, [{"pid": 1}], {"restarts_24h": 3})["level"] == "degraded")
check("日志末尾错误需要留意",
      _health(True, [{"pid": 1}], {"last_line": "ClientConnectorError"})["level"]
      == "degraded")
check("安静但运行的进程不误报", _health(True, [{"pid": 1}], {})["level"] == "healthy")


print("\n── 聊天流：一句话只画一遍 " + "─" * 34)

rows = [
    row(id=1, note="我好困", said="摸摸头 ⏎ 中午趴一会儿吧", reply="睡的不是很好"),
    row(id=2, note="睡的不是很好", said="怎么啦", reply=None),
]
msgs = _messages(rows)
users = [m["text"] for m in msgs if m["side"] == "user"]
check("reply 和下一条的 note 是同一句话时只画一次",
      users == ["我好困", "睡的不是很好"], str(users))

# 反过来：最后一条的 reply 后面没有 entry 接着了，那必须画出来，
# 否则他说的最后一句话在面板上凭空消失。
rows2 = [row(id=1, note="我好困", said="摸摸头", reply="嗯，晚安")]
users2 = [m["text"] for m in _messages(rows2) if m["side"] == "user"]
check("最后一条的 reply 没有下文时照样画", users2 == ["我好困", "嗯，晚安"], str(users2))

rows3 = [row(id=1, note="我好困", said="摸摸头", reply="嗯"),
         row(id=2, note="今天真累", said="辛苦了")]
users3 = [m["text"] for m in _messages(rows3) if m["side"] == "user"]
check("reply 和下一条 note 不同时两句都画",
      users3 == ["我好困", "嗯", "今天真累"], str(users3))

check("多条气泡按 ⏎ 拆开", _bubbles("摸摸头 ⏎ 中午趴一会儿吧") ==
      ["摸摸头", "中午趴一会儿吧"])
check("没说话就没有气泡", _bubbles(None) == [] and _bubbles("") == [])

quiet = _messages([row(id=1, note="……", move="quiet", said=None)])
check("它选择沉默的那次也要画出来（这是它的核心行为，不能看不见）",
      any(m.get("quiet") for m in quiet))

out_msgs = _messages([row(id=1, kind="out", note=None, said="在干嘛 ⏎ 无聊",
                          intent="无聊")])
check("主动开口的两条都带上意图标记",
      [m["intent"] for m in out_msgs] == ["无聊", "无聊"], str(out_msgs))
check("主动开口前面没有'他说了什么'",
      all(m["side"] == "bot" for m in out_msgs))

photo = _messages([row(id=1, note="到啦", shot_at="2026-08-13T01:00:00+00:00",
                       scene="写字楼电梯间")])
check("有 EXIF 时间就认定是图", photo[0]["photo"] is True)
check("没有 EXIF 也没定位就不敢说是图",
      _messages([row(id=1, note="我好困")])[0]["photo"] is False)
check("has_photo=1 即使没有 EXIF 也是图",
      _messages([row(id=1, note="", has_photo=1, scene="一碗面")])[0]["photo"] is True)
check("图带预览地址",
      _messages([row(id=9, note="", has_photo=1)])[0]["photo_url"] == "/api/photo?id=9")
check("纯文字的占位描述不当画面展示",
      _messages([row(id=1, note="困", scene="（纯文字）")])[0]["scene"] is None)


print("\n── ps 的 etime：macOS 没有 etimes，只能自己拆 " + "─" * 16)

check("mm:ss", _parse_etime("13:30") == 810)
check("hh:mm:ss", _parse_etime("01:13:30") == 4410)
check("dd-hh:mm:ss", _parse_etime("2-01:13:30") == 2 * 86400 + 4410)
check("前后有空格也行", _parse_etime("  13:30 ") == 810)
check("读不出来就返回 None，别拿 0 当'刚启动'", _parse_etime("") is None)


print("\n── 平台归属 " + "─" * 46)

check("从 thread 前缀认平台", _platform_of("dt:oto:123") == "dt")
check("QQ 也认", _platform_of("qq:oto:abc") == "qq")
check("群聊的 thread 里有冒号和 base64 也不影响",
      _platform_of("dt:cidGsUS7Rk6Zh/+R9Qw==:$:LWCP_v1:$Zu==") == "dt")
check("早期记录没有 thread 时退回名册里的平台",
      _platform_of(None, "wx") == "wx")
check("两边都没有就是未知", _platform_of(None) == "?")


print("\n── 拆 thread：发送人自己就带冒号 " + "─" * 28)

check("平常的单聊", _split_thread("dt:oto:123") == ("dt", "oto", "123"))
check("群聊：会话是 base64，带 = 和 /",
      _split_thread("dt:cidGsUS7Rk6ZhfA76tZ/+R9Qw==:01484515655929393400")
      == ("dt", "cidGsUS7Rk6ZhfA76tZ/+R9Qw==", "01484515655929393400"))
check("发送人带冒号时不能按最后一个冒号切（切了他就变成陌生人）",
      _split_thread("dt:cidGsUS7Rk6ZhfA76tZ/+R9Qw==:$:LWCP_v1:$ZuZPx6==")
      == ("dt", "cidGsUS7Rk6ZhfA76tZ/+R9Qw==", "$:LWCP_v1:$ZuZPx6=="))
check("微信的 openId 里有 @ 和 -",
      _split_thread("wx:oto:o9cq80xlHXx67JRRJjOvQQj-NWEA@im.wechat")[2]
      == "o9cq80xlHXx67JRRJjOvQQj-NWEA@im.wechat")
check("没有 thread 的老记录不炸", _split_thread(None) == ("?", "", ""))


print("\n── 谁是谁：同一个人的几个身份要合成一个 " + "─" * 20)

cfg2 = make_config(dingtalk_identities=[["aaa", "bbb"], ["ccc"]])
check("config 里用 | 声明过的两个 staffId 是同一个人",
      _person_id(cfg2, "dt", "bbb") == _person_id(cfg2, "dt", "aaa") == "aaa")
check("没声明过的就是他自己", _person_id(cfg2, "dt", "ccc") == "ccc")
check("poke --conv 拼出来的 'id:id' 收拾掉（不然他会多一个分身）",
      _person_id(cfg2, "dt", "ccc:ccc") == "ccc")
check("真的两个不同 id 用冒号连着时不乱合",
      _person_id(cfg2, "dt", "ccc:ddd") == "ccc:ddd")
check("Telegram / 微信不做归一，发送人就是人",
      _person_id(cfg2, "tg", "7159442364") == "7159442364")

check("单聊", _conversation_label("dt", "oto", "123")[0] == "oto")
check("Telegram 单聊：会话 id 和发送人相同",
      _conversation_label("tg", "999", "999")[0] == "oto")
check("群聊标出来", _conversation_label("dt", "cidAAAA==", "123")[0] == "group")
check("没有会话就是早期记录", _conversation_label("dt", "", "")[0] == "legacy")


print("\n── 聊天窗口按人划分 " + "─" * 40)

with tempfile.TemporaryDirectory() as d:
    dbp = Path(d) / "m.db"
    cfg3 = make_config(dbp, dingtalk_identities=[["u1"]])
    mem = Memory(dbp)

    def say(platform, conv, sender, note):
        key, label = thread_key(platform, conv, sender)
        mem.record(chat_id=key, thread=label, shot_at=None, bucket="午间",
                   weekday="周四", spot=None, scene="", move="speak",
                   said="嗯", note=note)
        return key

    oto = say("dt", "oto", "u1", "单聊说的")
    grp = say("dt", "cidAAA==", "u1", "群里说的")
    say("dt", "oto", "u1:u1", "poke 拼错的")     # 同一个人的畸形 thread
    other = say("dt", "oto", "u2", "另一个人")
    mem.enroll("dt", "u1", oto, "dt:oto:u1", "小明")
    mem.enroll("dt", "u2", other, "dt:oto:u2")

    people = _people(mem.conn, cfg3)
    by_key = {p["key"]: p for p in people}
    check("一个人只占一个窗口，不管他散在几条会话里",
          len(people) == 2, str([p["key"] for p in people]))
    u1 = by_key["dt:u1"]
    check("单聊 + 群 + 畸形 thread 都归到他名下", len(u1["contexts"]) == 3,
          str([c["thread"] for c in u1["contexts"]]))
    check("名册里的昵称跟过来", u1["name"] == "小明")
    check("消息数是几条会话加起来的", u1["total"] == 3)
    check("另一个人不受影响", by_key["dt:u2"]["total"] == 1)

    # v0.1 的 Telegram 直接拿 chat id 当 key，没有 thread
    mem.record(chat_id=7159442364, thread=None, shot_at=None, bucket="午间",
               weekday="周四", spot=None, scene="", move="speak",
               said="老记录", note="早期的话")
    tg_key = say("tg", "7159442364", "7159442364", "现在的话")
    mem.enroll("tg", "7159442364", tg_key, "tg:7159442364:7159442364")
    people = _people(mem.conn, cfg3)
    tg = next(p for p in people if p["platform"] == "tg")
    check("v0.1 的老记录认回原主（chat_id 就是他的 user_id）",
          tg["total"] == 2 and len(tg["contexts"]) == 2,
          str([(c["chat_id"], c["conv_label"]) for c in tg["contexts"]]))

    # CLI 跑出来的 chat_id=0，反推不出是谁
    mem.record(chat_id=0, thread=None, shot_at=None, bucket="午间", weekday="周四",
               spot=None, scene="", move="speak", said="命令行试的", note=None)
    people = _people(mem.conn, cfg3)
    orphan = [p for p in people if p["platform"] == "?"]
    check("认不出是谁的记录单独放，不硬塞给某个人（猜错比不显示更糟）",
          len(orphan) == 1 and orphan[0]["in_roster"] is False)

    check("（前置）此刻没有零消息的人",
          any(p["total"] == 0 for p in _people(mem.conn, cfg3)) is False)
    mem.enroll("dt", "u3", 999, "dt:oto:u3", "还没说过话的人")
    check("刚入册还没说话的人也要有个窗口",
          any(p["name"] == "还没说过话的人" and p["total"] == 0
              for p in _people(mem.conn, cfg3)))
    detail = _person(mem.conn, cfg3, "dt:u1", 50)
    check("聊天窗口每条消息都带上平台",
          detail["platform"] == "dt"
          and detail["messages"]
          and all(m["platform"] == "dt" for m in detail["messages"]),
          str(detail.get("messages", [])[:1]))

    # 看板展示的主动风险必须和 initiative.should_hold() 的四条阈值一致，
    # 但它只读数据，不能改变实际排程。
    for _ in range(4):
        mem.record(chat_id=oto, thread="dt:oto:u1", shot_at=None, bucket="午间",
                   weekday="周四", spot=None, scene="", move="speak",
                   said="在吗", note=None, kind="out", intent="在干嘛")
    u1 = next(p for p in _people(mem.conn, cfg3) if p["key"] == "dt:u1")
    check("主动统计按人合并当天次数", u1["outbound_today"] == 4,
          str(u1["outbound_today"]))
    check("连续四条未回复在看板上显示已冷却",
          u1["unanswered"] == 4 and u1["initiative_state"] == "hold",
          str({k: u1[k] for k in ("unanswered", "initiative_state")}))
    mem.close()


print("\n── 读日志尾巴：900 KB 的文件不能整个读进来 " + "─" * 18)

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "big.log"
    p.write_text("".join(f"第 {i} 行\n" for i in range(5000)), encoding="utf-8")
    lines = _tail(p, 10)
    check("正好拿到最后 10 行", len(lines) == 10 and lines[-1] == "第 4999 行",
          str(lines[-1:]))
    check("要的比文件长就全给", len(_tail(p, 99999)) == 5000)
    check("文件不存在返回空列表", _tail(Path(d) / "没有.log", 10) == [])

    # 尾部截断处不能把一个多字节字符劈成两半
    p2 = Path(d) / "utf8.log"
    p2.write_text("啊" * 20000 + "\n结尾\n", encoding="utf-8")
    check("中文不会因为按字节切而变成乱码", _tail(p2, 1) == ["结尾"])


print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
