"""旧平台的所有可执行入口都必须失败关闭。

这个测试不启动任何真实 bot，也不读取凭据；它锁住生产切换最容易回归的
两条路径：绕过 CLI 直接调用模块，以及 run.sh 误读生产 .env 后无限拉起。

    python tests/test_bot_entry_guards.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from murmur import bot, dingtalk, qq, wechat  # noqa: E402

ok = fail = 0


def check(name: str, condition: bool) -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}")


print("\n── 模块入口门禁 " + "─" * 42)
for label, module in (
    ("Telegram", bot),
    ("钉钉", dingtalk),
    ("微信", wechat),
    ("QQ", qq),
):
    source = inspect.getsource(module.run)
    check(f"{label} run() 调用 require_test_bot", "require_test_bot" in source)

print("\n── 本地 supervise 门禁 " + "─" * 37)
run_source = (ROOT / "run.sh").read_text(encoding="utf-8")
check("run.sh 只读取隔离测试配置", ". ./.env.test-bots" in run_source)
check("run.sh 不再 source 生产 .env", ". ./.env\n" not in run_source)
check("run.sh 要求 transition", 'MURMUR_CHANNEL_MODE:-' in run_source)
check("run.sh 要求显式测试开关", "MURMUR_ENABLE_TEST_BOTS" in run_source)

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
raise SystemExit(1 if fail else 0)
