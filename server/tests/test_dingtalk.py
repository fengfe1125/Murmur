"""钉钉入口的离线验证。

    python tests/test_dingtalk.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import make_config  # noqa: E402

from murmur import dingtalk  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


print("\n── access_token 缓存（有效期 7200 秒）" + "─" * 21)
calls = {"token": 0, "send": 0}
used: list[str] = []
real_post = dingtalk.requests.post


def fake_post(url, json=None, headers=None, timeout=None):
    if "accessToken" in url:
        calls["token"] += 1
        time.sleep(0.05)   # 让并发刷新的竞争有机会暴露
        return FakeResp({"accessToken": f"T{calls['token']}", "expireIn": 7200})
    calls["send"] += 1
    used.append(headers["x-acs-dingtalk-access-token"])
    return FakeResp({})


dingtalk.requests.post = fake_post
try:
    cfg = make_config("/tmp/murmur-test-dt.db",
                      dingtalk_client_id="k", dingtalk_client_secret="s")
    dingtalk._token, dingtalk._token_until = None, 0.0   # 别被别的用例污染
    dingtalk.send_oto(cfg, ["u1"], ["你好"])
    dingtalk.send_oto(cfg, ["u1"], ["在吗"])
    check("两条消息只取一次 token",
          calls["token"] == 1 and calls["send"] == 2, str(calls))
    dingtalk._token_until = 0   # 模拟过期
    dingtalk.send_oto(cfg, ["u1"], ["又来"])
    check("过期后重新取", calls["token"] == 2 and calls["send"] == 3, str(calls))
    check("发消息用的是缓存的 token", used == ["T1", "T1", "T2"], str(used))

    dingtalk._token_until = 0   # 模拟过期，让四个线程同时需要刷新
    before = calls["token"]
    ts = [threading.Thread(target=dingtalk.send_oto,
                           args=(cfg, ["u1"], ["并发"])) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("并发刷新只取一次", calls["token"] == before + 1,
          f"实际取了 {calls['token'] - before} 次")
finally:
    dingtalk.requests.post = real_post
    dingtalk._token, dingtalk._token_until = None, 0.0

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
