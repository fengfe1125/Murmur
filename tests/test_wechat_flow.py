"""微信通道的端到端链路测试（不联网、不调模型）。

写这个是因为钉钉那次的教训：只单测了解析器，结果 `process` 必须是
async 这件事直到真人发消息才暴露出来，中间丢了两条消息。
所以这里走的是真正的 Handler.handle，只把最外面两层——HTTP 和模型——换掉。

    python tests/test_wechat_flow.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import make_config  # noqa: E402

from murmur import wechat  # noqa: E402
from murmur.config import Config  # noqa: E402
from murmur.memory import Memory  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


class FakeClient(wechat.WeChatClient):
    """真 WeChatClient，只把出网那一层换成录音机。"""

    def __init__(self, root: Path):
        super().__init__("TOK", "acc", root)
        self.calls: list[tuple[str, dict]] = []
        self.typing: list[int] = []

    def _post(self, endpoint, body, timeout=30.0):
        self.calls.append((endpoint, body))
        if endpoint.endswith("getconfig"):
            return {"ret": 0, "typing_ticket": "TICKET"}
        return {"ret": 0}

    def _typing(self, user_id, status):
        self.typing.append(status)

    @property
    def sent(self) -> list[dict]:
        return [b["msg"] for e, b in self.calls if e.endswith("sendmessage")]

    @property
    def sent_texts(self) -> list[str]:
        return [m["item_list"][0]["text_item"]["text"] for m in self.sent]


def make_cfg(tmp: Path, **kw) -> Config:
    kw.setdefault("wechat_token", "TOK")
    kw.setdefault("wechat_account_id", "acc")
    return make_config(tmp / "m.db", **kw)


def fake_reply(bubbles, *, silent=False):
    return SimpleNamespace(
        say=bubbles, joined=" ⏎ ".join(bubbles), silent=silent,
        move="speak", scene="（测试）",
    )


def msg(uid="u1@im.wechat", text=None, image=False, ctx="CTX-1", **kw):
    items = []
    if image:
        items.append({"type": 2, "image_item": {
            "media": {"full_url": "https://cdn/x"}, "aeskey": "00" * 16}})
    if text:
        items.append({"type": 1, "text_item": {"text": text}})
    d = {"from_user_id": uid, "item_list": items, "message_type": 1,
         "create_time_ms": int(time.time() * 1000)}
    if ctx:
        d["context_token"] = ctx
    d.update(kw)
    return d


def setup(tmp: Path, **cfgkw):
    """装一套 Handler，模型和图片解码都换成假的。"""
    cfg = make_cfg(tmp, **cfgkw)
    client = FakeClient(tmp / "wx")
    mem = Memory(cfg.db_path)
    h = wechat.Handler(cfg, mem, client)
    return cfg, client, mem, h


# 缩短静默期，别让测试跑十几秒
wechat._deb.window = 0.3

# 模型不真调，但图片走**真的**解码路径——
# 之前拿 SimpleNamespace 冒充照片，测出来是绿的，真图片来了却会炸。
wechat.respond = lambda *a, **k: fake_reply(["嗯", "在等电梯？"])
wechat.refresh = lambda *a, **k: None


def _real_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 90)).save(buf, "JPEG")
    return buf.getvalue()


FakeClient.fetch_media = lambda self, media, aeskey: _real_jpeg()


print("\n── 一条文字消息走完全程 " + "─" * 34)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    h.handle(msg(text="今天好累"))

    check("回复发出去了", client.sent_texts[-2:] == ["嗯", "在等电梯？"],
          f"实际 {client.sent_texts}")
    check("第一次见面先发了自我介绍",
          client.sent_texts[0] == "嗨，我是 Murmur")
    check("每条都带上了 context_token",
          all(m.get("context_token") == "CTX-1" for m in client.sent[1:]))
    check("收件人对", all(m["to_user_id"] == "u1@im.wechat" for m in client.sent))
    check("context_token 存下来了",
          client.ctx_tokens.get("u1@im.wechat") == "CTX-1")
    check("写进记忆了", len(mem.recent(chat_id=wechat.wechat_thread("u1@im.wechat")[0])) > 0)
    check("打字状态点亮过又熄灭", wechat.TYPING_ON in client.typing
          and client.typing[-1] == wechat.TYPING_OFF)

print("\n── 图片消息 " + "─" * 45)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    mem.mark_greeted(*wechat.wechat_thread("u1@im.wechat"))   # 跳过自我介绍
    h.handle(msg(image=True, text="你看"))
    check("图 + 配文能回", client.sent_texts == ["嗯", "在等电梯？"],
          f"实际 {client.sent_texts}")

print("\n── 白名单 " + "─" * 47)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp, wechat_allowed_users={"friend@im.wechat"})
    h.handle(msg(uid="stranger@im.wechat", text="喂"))
    check("名单外的人一个字都不回", client.sent_texts == [],
          f"实际 {client.sent_texts}")
    h.handle(msg(uid="friend@im.wechat", text="喂"))
    check("名单内的人正常回", "嗯" in client.sent_texts)

print("\n── 自己的回声 " + "─" * 43)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    h.handle(msg(text="这是机器人自己发的", message_type=wechat.MSG_TYPE_BOT))
    check("message_type=2 的消息不处理（否则自问自答）",
          client.sent_texts == [], f"实际 {client.sent_texts}")

print("\n── 连发合并 " + "─" * 45)
with tempfile.TemporaryDirectory() as d:
    import threading
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    mem.mark_greeted(*wechat.wechat_thread("u1@im.wechat"))
    ts = [threading.Thread(target=h.handle, args=(msg(text=f"第{i}句"),))
          for i in range(4)]
    for t in ts:
        t.start()
        time.sleep(0.05)
    for t in ts:
        t.join()
    check("4 句话只回一次（2 条气泡，不是 8 条）",
          len(client.sent_texts) == 2, f"实际发了 {len(client.sent_texts)} 条")

print("\n── 说了别发就不发 " + "─" * 39)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    key, _ = wechat.wechat_thread("u1@im.wechat")
    mem.mark_greeted(key, "wx")
    h.handle(msg(text="别发了"))
    check("退出确认了一句", "不主动找你" in "".join(client.sent_texts))
    check("记进 optout 表", mem.is_opted_out(key))

print("\n── 模型说 quiet 就真的不说话 " + "─" * 30)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    mem.mark_greeted(*wechat.wechat_thread("u1@im.wechat"))
    wechat.respond = lambda *a, **k: fake_reply([], silent=True)
    h.handle(msg(text="……"))
    check("一条都不发", client.sent_texts == [])
    check("但打字状态收干净了", client.typing[-1] == wechat.TYPING_OFF)
    wechat.respond = lambda *a, **k: fake_reply(["嗯", "在等电梯？"])

print("\n── 模型炸了也别把进程带走 " + "─" * 32)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    mem.mark_greeted(*wechat.wechat_thread("u1@im.wechat"))

    def boom(*a, **k):
        raise RuntimeError("模型挂了")

    wechat.respond = boom
    h.handle(msg(text="在吗"))
    check("回了一句错误提示而不是静默失踪",
          any("出错" in t for t in client.sent_texts), f"实际 {client.sent_texts}")
    wechat.respond = lambda *a, **k: fake_reply(["嗯", "在等电梯？"])

print("\n── 两个人互不串线 " + "─" * 39)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    cfg, client, mem, h = setup(tmp)
    for u in ("a@im.wechat", "b@im.wechat"):
        mem.mark_greeted(*wechat.wechat_thread(u))
    h.handle(msg(uid="a@im.wechat", text="我的事", ctx="CTX-A"))
    h.handle(msg(uid="b@im.wechat", text="我的事", ctx="CTX-B"))
    check("各自的 context_token 分开存",
          client.ctx_tokens.get("a@im.wechat") == "CTX-A"
          and client.ctx_tokens.get("b@im.wechat") == "CTX-B")
    ka = wechat.wechat_thread("a@im.wechat")[0]
    kb = wechat.wechat_thread("b@im.wechat")[0]
    check("记忆分开", ka != kb and len(mem.recent(chat_id=ka)) > 0
          and len(mem.recent(chat_id=kb)) > 0)

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
