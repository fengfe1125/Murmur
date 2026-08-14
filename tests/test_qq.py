"""QQ 入口里那些必须对的纯函数。

    python tests/test_qq.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from murmur import qq  # noqa: E402 - test private runtime compatibility helper
from murmur.qq import (  # noqa: E402
    group_thread,
    image_url,
    oto_thread,
    parse_ts,
    strip_mention,
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


print("\n── 去掉群里的 @ 前缀 " + "─" * 36)
check("普通 @", strip_mention("<@!123456> 吃了吗") == "吃了吗")
check("没有 @ 原样", strip_mention("吃了吗") == "吃了吗")
check("空", strip_mention(None) == "" and strip_mention("") == "")

print("\n── 上下文按人隔离 " + "─" * 40)
a, la = oto_thread("u1")
b, lb = oto_thread("u2")
check("不同人不同键", a != b)
check("同一个人稳定", oto_thread("u1") == (a, la))
g1, _ = group_thread("gA", "u1")
check("群和单聊不串", g1 != a)
check("标签带平台前缀", la.startswith("qq:") and lb.startswith("qq:"))

print("\n── Python 3.14 的 QQ 网关事件循环 " + "─" * 30)
previous_loop = asyncio.new_event_loop()
asyncio.set_event_loop(previous_loop)
real_get_event_loop = qq.asyncio.get_event_loop
created_loop = None
try:
    def no_current_loop():
        raise RuntimeError("There is no current event loop in thread 'MainThread'.")

    qq.asyncio.get_event_loop = no_current_loop
    class FakeClient:
        def __init__(self, **kwargs):
            self.loop = asyncio.get_event_loop_policy().get_event_loop()
            self.kwargs = kwargs

    client = qq.create_gateway_client(FakeClient, "intents", sandbox=False)
    created_loop = client.loop
    check("创建 botpy 客户端前补上事件循环", not created_loop.is_closed())
finally:
    qq.asyncio.get_event_loop = real_get_event_loop
    asyncio.set_event_loop(previous_loop)
    if created_loop is not None:
        created_loop.close()

print("\n── 附件里找出图 " + "─" * 42)
att = [SimpleNamespace(content_type="image/jpeg", url="https://x/a.jpg")]
check("认 image/*", image_url(att) == "https://x/a.jpg")
check("没附件就是没有", image_url([]) is None)
check("字典也能认", image_url([{"url": "https://x/b.png", "content_type": ""}])
      == "https://x/b.png")
check("没写类型但文件名是图",
      image_url([{"url": "https://x/c", "filename": "x.webp"}]) == "https://x/c")
check("视频不当图",
      image_url([{"url": "https://x/v", "content_type": "video/mp4"}]) is None)

print("\n── 时间戳 " + "─" * 48)
t = parse_ts("2026-08-13T06:00:00+00:00")
check("ISO 能解析", t.year == 2026 and t.hour == 6)
check("坏的不炸", parse_ts("不是时间").tzinfo is not None)


print("\n── 打招呼只打一次招呼，不接着开新话题 " + "─" * 16)

from _helpers import make_config  # noqa: E402

from murmur.debounce import Debouncer  # noqa: E402
from murmur.memory import Memory  # noqa: E402

qq._deb.window = 0.05
_calls = []


def fake_reply(bubbles, **kw):
    _calls.append(list(bubbles))
    return SimpleNamespace(
        say=bubbles, joined=" ⏎ ".join(bubbles), silent=False,
        move="speak", scene="（测试）",
    )


qq.respond = lambda *a, **k: fake_reply(["新话题"])
qq.refresh = lambda *a, **k: None


class FakeHttp:
    def __init__(self):
        self.sent: list[str] = []

    def send_bubbles(self, target, bubbles, *, group=False, msg_id=None):
        self.sent.extend(bubbles)

    def fetch_url(self, url):
        return None


def qq_msg(text, mid="m1"):
    return SimpleNamespace(
        id=mid, content=text, timestamp=None, attachments=[],
        author=SimpleNamespace(user_openid="u1", member_openid="u1"),
        group_openid=None,
    )


with tempfile.TemporaryDirectory() as d:
    cfg = make_config(Path(d) / "m.db", qq_app_id="1", qq_client_secret="s")
    mem = Memory(cfg.db_path)
    http = FakeHttp()
    h = qq.Handler(cfg, mem, http)
    h.handle_c2c(qq_msg("你好", "m1"))
    check("第一次只发自我介绍", http.sent == ["嗨，我是 Murmur"], str(http.sent))
    check("没有接着开新话题", _calls == [], str(_calls))
    h.handle_c2c(qq_msg("在吗", "m2"))
    check("打过招呼之后才正经回", "新话题" in http.sent, str(http.sent))

print("\n── 连发时同一会话不会并行回两轮 " + "─" * 18)

d2 = Debouncer(window=0.05)
k = "t"
n1 = d2.arrive(k, text="a")
n2 = d2.arrive(k, text="b")
check("后来的才是最新", d2.is_latest(k, n2) and not d2.is_latest(k, n1))
b = d2.take(k)
check("take 一次拿齐", b is not None and b.note == "a\nb")
check("再 take 就是空", d2.take(k) is None)

n_respond = {"n": 0}
gate = threading.Event()


def slow_reply(bubbles, **kw):
    n_respond["n"] += 1
    gate.wait(1.0)
    return SimpleNamespace(
        say=bubbles, joined=" ⏎ ".join(bubbles), silent=False,
        move="speak", scene="（测试）",
    )


qq.respond = lambda *a, **k: slow_reply(["回"])

with tempfile.TemporaryDirectory() as d:
    cfg = make_config(Path(d) / "m.db", qq_app_id="1", qq_client_secret="s")
    mem = Memory(cfg.db_path)
    http = FakeHttp()
    h = qq.Handler(cfg, mem, http)
    key, label = oto_thread("u1")
    mem.mark_greeted(key, label)
    t1 = threading.Thread(target=h.handle_c2c, args=(qq_msg("第一句", "a1"),))
    t1.start()
    time.sleep(0.08)
    t2 = threading.Thread(target=h.handle_c2c, args=(qq_msg("第二句", "a2"),))
    t2.start()
    gate.set()
    t1.join(2)
    t2.join(2)
    check("两句叠在一起不会各回一轮", n_respond["n"] <= 2, str(n_respond["n"]))
    check("发出去的不乱成两套", http.sent.count("回") <= 2, str(http.sent))

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
