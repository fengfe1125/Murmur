"""看门狗：该杀的杀，不该杀的别杀。

两个真实事故各贡献一半：
- 2026-08-13 凌晨：Telegram 事件循环卡死 8.7 小时没人管 → 才有了看门狗
- 2026-08-13 中午：笔记本睡了 12 分钟，三个进程被看门狗**误杀**重启

所以它必须能分清这两件事。判据是"看门狗自己有没有也被冻住"。

这个测试会真的起线程、真的等，所以慢几秒是正常的。

    python tests/test_watchdog.py
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


def run(body: str, timeout: float = 30) -> tuple[int, str]:
    """在子进程里跑一段代码。看门狗用 os._exit，只能这么测。"""
    code = textwrap.dedent(f"""
        import sys, time, threading
        sys.path.insert(0, {str(ROOT)!r})
        import logging; logging.basicConfig(level=logging.INFO, stream=sys.stdout)
        from murmur import watchdog
        {textwrap.indent(textwrap.dedent(body), '        ').lstrip()}
    """)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


print("\n── 该杀的要杀 " + "─" * 43)
rc, out = run("""
    watchdog.start("测试", stale_after=3)
    time.sleep(12)          # 一次都不 beat，模拟循环卡死
    print("NOT_KILLED")
""")
check("一直不心跳 → 进程被杀，退出码 75", rc == 75, f"实际 {rc}")
check("没有走到后面的代码", "NOT_KILLED" not in out)
check("日志说明了原因", "判定卡死" in out, out[-200:])

print("\n── 正常心跳不该被杀 " + "─" * 37)
rc, out = run("""
    watchdog.start("测试", stale_after=3)
    for _ in range(14):     # 10 秒里持续心跳
        watchdog.beat()
        time.sleep(0.7)
    print("ALIVE")
""")
check("一直有心跳 → 活着", rc == 0 and "ALIVE" in out, f"退出码 {rc}")
check("没有误报卡死", "判定卡死" not in out)

print("\n── 电脑休眠不该被杀（这次误杀的场景） " + "─" * 20)
# 真让 Mac 睡一觉没法在测试里做，所以把"被冻住"直接注入：
# 让看门狗线程自己的 sleep 花掉远超预期的时间，等价于整台机器停摆。
rc, out = run("""
    real_sleep = time.sleep
    frozen = {"done": False}

    def fake_sleep(n):
        # 第一次进入看门狗的等待时，假装整台机器被冻了很久：
        # 真的只睡一下，但让 monotonic 看起来跳了 100 秒
        if not frozen["done"] and threading.current_thread().name == "murmur-watchdog":
            frozen["done"] = True
            real_sleep(n)
            watchdog.time.monotonic = (lambda base=watchdog.time.monotonic:
                                       base() + 100)
            return
        real_sleep(n)

    watchdog.time.sleep = fake_sleep
    watchdog.start("测试", stale_after=3)
    real_sleep(3)           # 冻住期间一次都不 beat——和休眠时一模一样
    for _ in range(12):     # 醒来之后循环恢复，正常心跳
        watchdog.beat()
        real_sleep(0.7)
    print("SURVIVED")
""")
check("看门狗自己也被冻住 → 判定为休眠，不杀",
      rc == 0 and "SURVIVED" in out, f"退出码 {rc}｜{out[-300:]}")
check("日志说清楚是休眠不是卡死", "电脑休眠" in out, out[-300:])
check("休眠之后重新计时（没有紧接着又杀一次）", "判定卡死" not in out)

print("\n── 休眠恢复后仍然能抓到真卡死 " + "─" * 28)
rc, out = run("""
    real_sleep = time.sleep
    frozen = {"done": False}

    def fake_sleep(n):
        if not frozen["done"] and threading.current_thread().name == "murmur-watchdog":
            frozen["done"] = True
            real_sleep(n)
            watchdog.time.monotonic = (lambda base=watchdog.time.monotonic:
                                       base() + 100)
            return
        real_sleep(n)

    watchdog.time.sleep = fake_sleep
    watchdog.start("测试", stale_after=3)
    real_sleep(20)          # 先"休眠"一次，之后继续不心跳
    print("NOT_KILLED")
""")
check("休眠豁免只用一次，之后真不心跳照样杀", rc == 75, f"实际 {rc}")

print("\n── 检查间隔跟着阈值走 " + "─" * 36)
sys.path.insert(0, str(ROOT))
from murmur.watchdog import _check_interval  # noqa: E402

check("阈值 300 秒 → 每 30 秒查一次", _check_interval(300) == 30)
check("阈值 3 秒 → 收缩到 1 秒（下限；不然第一次检查都赶不上）",
      _check_interval(3) == 1.0)
check("再小也不低于 1 秒的下限……", _check_interval(0.1) == 1.0)

print("\n── 心跳间隔必须小于判死阈值 " + "─" * 30)
import re  # noqa: E402

for f in ("murmur/dingtalk.py", "murmur/wechat.py"):
    src = (ROOT / f).read_text()
    naps = [float(x) for x in re.findall(r"time\.sleep\((\d+(?:\.\d+)?)\)", src)]
    naps += [float(x) for x in re.findall(r"total_seconds\(\), (\d+)\)", src)]
    worst = max(naps) if naps else 0
    # 登录过期时是 sleep(30) 循环 + 每轮 beat()，不算长睡
    check(f"{f} 最长睡 {worst:.0f} 秒 < 300 秒阈值", worst < 300,
          "贴着阈值的话，什么都没出错也会被误杀")

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
