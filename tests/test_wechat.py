"""微信通道的离线验证。

真账号要扫码才能有，所以这里只测不依赖网络的那些部分：
协议头、AES 解密、两种 key 编码、消息解析、限速桶、凭据发现。
这些恰好是最容易写错、又最难在真环境里 debug 的地方。

    python tests/test_wechat.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from murmur import wechat  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


def _encrypt(plain: bytes, key: bytes) -> bytes:
    pad = 16 - (len(plain) % 16)
    padded = plain + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(padded) + enc.finalize()


print("\n── AES-128-ECB 解密 " + "─" * 40)
key = bytes(range(16))
for label, payload in [
    ("短数据", b"hello wechat"),
    ("正好 16 字节", b"0123456789abcdef"),
    ("一张假 JPEG", b"\xff\xd8\xff\xe0" + os.urandom(5000) + b"\xff\xd9"),
]:
    got = wechat._aes_ecb_decrypt(_encrypt(payload, key), key)
    check(f"{label}（{len(payload)} 字节）往返一致", got == payload,
          f"得到 {len(got)} 字节")

print("\n── aes_key 的两种编码 " + "─" * 36)
# 图片：image_item.aeskey 是十六进制字符串
check("hex 字符串（收图用的那种）",
      wechat._parse_aes_key(key.hex(), None) == key)
# 图片 media.aes_key：base64(原始 16 字节)
check("base64(原始 16 字节)",
      wechat._parse_aes_key(None, base64.b64encode(key).decode()) == key)
# 文件/语音/视频：base64(32 个 ASCII 十六进制字符)，要再解一层
check("base64(32 位 hex 字符串) —— 文件/语音走这条",
      wechat._parse_aes_key(None, base64.b64encode(key.hex().encode()).decode()) == key)
check("两个都没有 → None", wechat._parse_aes_key(None, None) is None)
check("垃圾数据 → None 而不是崩", wechat._parse_aes_key("zzz", "!!!!") is None)
check("hex 优先于 base64",
      wechat._parse_aes_key(key.hex(), base64.b64encode(b"\x00" * 16).decode()) == key)

print("\n── 消息解析 " + "─" * 45)
text, media, aeskey = wechat.extract({
    "item_list": [{"type": 1, "text_item": {"text": "在等电梯"}}]
})
check("纯文字", (text, media) == ("在等电梯", None))

text, media, aeskey = wechat.extract({
    "item_list": [
        {"type": 2, "image_item": {
            "media": {"encrypt_query_param": "QP", "full_url": "https://x/y"},
            "aeskey": key.hex(),
        }},
        {"type": 1, "text_item": {"text": "你看这个"}},
    ]
})
check("图 + 配文", text == "你看这个" and media.get("full_url") == "https://x/y")
check("图的 aeskey 取到了", aeskey == key.hex())

text, media, _ = wechat.extract({
    "item_list": [
        {"type": 1, "text_item": {"text": "第一句"}},
        {"type": 1, "text_item": {"text": "第二句"}},
    ]
})
check("多段文字合成一条", text == "第一句\n第二句")

text, media, _ = wechat.extract({"item_list": [{"type": 3, "voice_item": {}}]})
check("语音（还不支持）不炸，返回空", (text, media) == (None, None))

text, media, _ = wechat.extract({})
check("空消息不炸", (text, media) == (None, None))

# 只有缩略图时退而求其次
_, media, _ = wechat.extract({
    "item_list": [{"type": 2, "image_item": {"thumb_media": {"encrypt_query_param": "T"}}}]
})
check("没有原图就用缩略图", media.get("encrypt_query_param") == "T")

print("\n── 限速桶（服务端 7 条/5 分钟）" + "─" * 26)
t = wechat._Throttle(limit=3, window=1.0)
start = time.monotonic()
for _ in range(3):
    t.take()
check("额度内不阻塞", time.monotonic() - start < 0.1)
t.take()   # 第 4 条要等窗口滑过去
check("超额会等待", 0.9 < time.monotonic() - start < 3.0,
      f"实际等了 {time.monotonic() - start:.2f}s")

print("\n── 请求头 " + "─" * 47)
with tempfile.TemporaryDirectory() as d:
    c = wechat.WeChatClient("TOK123", "acc@im-bot", Path(d))
    h = c._headers()
    check("Authorization 是 Bearer", h["Authorization"] == "Bearer TOK123")
    check("AuthorizationType 固定值", h["AuthorizationType"] == "ilink_bot_token")
    check("iLink-App-Id = bot", h["iLink-App-Id"] == "bot")
    check("客户端版本按 major<<16|minor<<8|patch 编码",
          h["iLink-App-ClientVersion"] == str((2 << 16) | (4 << 8) | 6))
    uin = base64.b64decode(h["X-WECHAT-UIN"]).decode()
    check("X-WECHAT-UIN 是 base64 包的十进制随机数",
          uin.isdigit() and int(uin) < 2**32)
    check("每次请求换一个 UIN", c._headers()["X-WECHAT-UIN"] != h["X-WECHAT-UIN"])

    print("\n── context_token 落盘 " + "─" * 36)
    c.ctx_tokens.put("u@im.wechat", "CTX-1")
    check("写进去读得出", c.ctx_tokens.get("u@im.wechat") == "CTX-1")
    again = wechat.WeChatClient("TOK123", "acc@im-bot", Path(d))
    check("重启进程后还在（主动消息全靠它）",
          again.ctx_tokens.get("u@im.wechat") == "CTX-1")
    check("文件权限 600（里面是凭据）",
          oct(Path(d, "context-tokens.json").stat().st_mode)[-3:] == "600")
    c.ctx_tokens.put("u@im.wechat", "CTX-2")
    check("覆盖生效", wechat.WeChatClient("T", "a", Path(d)).ctx_tokens.get(
        "u@im.wechat") == "CTX-2")

print("\n── 凭据发现 " + "─" * 45)
from _helpers import make_config  # noqa: E402


def _cfg(**kw):
    return make_config("/tmp/x.db", **kw)


with tempfile.TemporaryDirectory() as d:
    os.environ["OPENCLAW_STATE_DIR"] = d
    # 真实路径是 {state}/openclaw-weixin/accounts/。
    # 第一版我写成了 {state}/credentials/openclaw-weixin/accounts/，
    # 结果人家扫码明明成功了，却报"没找到 token"——夹具跟着错，所以测试全绿。
    acc = Path(d, "openclaw-weixin", "accounts")
    acc.mkdir(parents=True)
    check("凭据目录就是 OpenClaw 实际写入的那个",
          wechat.openclaw_accounts_dir() == acc)

    try:
        wechat.load_credentials(_cfg())
        check("没登录时报错", False)
    except RuntimeError as e:
        check("没登录时给出扫码命令", "channels login" in str(e))
        check("并提醒关掉插件免得抢消息", "enabled false" in str(e))

    (acc / "b0f5@im-bot.json").write_text(json.dumps({"token": "T-A", "userId": "u1"}))
    # 同目录下的姊妹文件不能被当成账号
    (acc / "b0f5@im-bot.sync.json").write_text(json.dumps({"buf": "x"}))
    (acc / "b0f5@im-bot.context-tokens.json").write_text(json.dumps({"u": "c"}))
    tok, aid = wechat.load_credentials(_cfg())
    check("读到 token", tok == "T-A")
    check("account id 用文件名", aid == "b0f5@im-bot")

    (acc / "c1a2@im-bot.json").write_text(json.dumps({"token": "T-B"}))
    tok, aid = wechat.load_credentials(_cfg(wechat_account_id="c1a2@im-bot"))
    check("多账号时能按 id 指定", (tok, aid) == ("T-B", "c1a2@im-bot"))
    try:
        wechat.load_credentials(_cfg(wechat_account_id="nope"))
        check("指定了不存在的 id 要报错", False)
    except RuntimeError as e:
        check("指定了不存在的 id 会列出可选项", "b0f5@im-bot" in str(e))

    check(".env 里的 WECHAT_TOKEN 优先",
          wechat.load_credentials(_cfg(wechat_token="ENV"))[0] == "ENV")

    print("\n── 接管 OpenClaw 已有的状态 " + "─" * 31)
    # 扫码时 OpenClaw 可能已经收发过消息了。游标和 context_token 都得接过来：
    # 游标不接，服务端可能重放历史，机器人会对着几天前的消息一条条回。
    (acc / "b0f5@im-bot.sync.json").write_text(
        json.dumps({"get_updates_buf": "CURSOR-9"}))
    (acc / "b0f5@im-bot.context-tokens.json").write_text(
        json.dumps({"someone@im.wechat": "CTX-OLD"}))
    with tempfile.TemporaryDirectory() as d2:
        c = wechat.WeChatClient("T-A", "b0f5@im-bot", Path(d2))
        check("接管前是空的", c._cursor.get("b0f5@im-bot") is None)
        wechat.adopt_openclaw_state(c)
        check("接管了同步游标（不会重放历史消息）",
              c._cursor.get("b0f5@im-bot") == "CURSOR-9")
        check("接管了 context_token（不用等对方先开口）",
              c.ctx_tokens.get("someone@im.wechat") == "CTX-OLD")

    with tempfile.TemporaryDirectory() as d2:
        c = wechat.WeChatClient("T-A", "b0f5@im-bot", Path(d2))
        c._cursor.put("b0f5@im-bot", "MINE")
        c.ctx_tokens.put("someone@im.wechat", "CTX-MINE")
        wechat.adopt_openclaw_state(c)
        check("已经有自己的状态就不覆盖",
              c._cursor.get("b0f5@im-bot") == "MINE"
              and c.ctx_tokens.get("someone@im.wechat") == "CTX-MINE")

    with tempfile.TemporaryDirectory() as d2:
        c = wechat.WeChatClient("T", "从来没登录过", Path(d2))
        wechat.adopt_openclaw_state(c)      # 没有对应文件
        check("没有可接管的文件时安静跳过，不抛异常", True)

    print("\n── 老版本布局 " + "─" * 43)
    for p in acc.glob("*.json"):
        p.unlink()
    legacy = Path(d, "credentials", "openclaw-weixin")
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "credentials.json").write_text(json.dumps({"token": "T-LEGACY"}))
    check("兼容老版本的 credentials.json（这个确实在 credentials/ 底下）",
          wechat.load_credentials(_cfg())[0] == "T-LEGACY")
    del os.environ["OPENCLAW_STATE_DIR"]

print("\n── 会话键 " + "─" * 47)
k1, l1 = wechat.wechat_thread("aaa@im.wechat")
k2, l2 = wechat.wechat_thread("bbb@im.wechat")
check("不同人不同上下文", k1 != k2)
check("同一个人稳定", wechat.wechat_thread("aaa@im.wechat") == (k1, l1))
from murmur.dingtalk import oto_thread  # noqa: E402

check("和钉钉的同名用户不串线", wechat.wechat_thread("u1")[0] != oto_thread("u1")[0])

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
