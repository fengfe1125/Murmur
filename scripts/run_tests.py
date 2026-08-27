#!/usr/bin/env python3
"""统一跑全部测试：每个 tests/test_*.py 当脚本跑，全部跑完再汇总。

为什么存在：裸 `for f in tests/test_*.py` 循环在第一个失败就停，一次
只能看到一个问题；这里全部跑完，把失败名单一次给全。为什么不用
unittest discover：一半测试是脚本式的，import 那一刻就把自己跑完再
sys.exit()，discover 会把它们整批报成 import error（README「测试」
一节）。逐个当脚本跑是唯一对两种风格都成立的方式。

谁调用它就用谁的解释器：本机 `.venv/bin/python scripts/run_tests.py`，
CI 里是 pip install 过的系统 python，VPS 上是 murmur-update 的
`.venv/bin/python`——三处跑的是同一份名单，答案才有可比性。

用法：
    scripts/run_tests.py                    # 全部
    scripts/run_tests.py tests/test_web.py  # 只跑指定的几个
    scripts/run_tests.py --fail-fast        # 第一个失败就停（旧行为）
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 单个测试的超时。绝大多数几秒跑完；给足余量是防某个测试死等网络
# 把整个闸门挂住——超时按失败算，名单里能看到是哪个。
PER_TEST_TIMEOUT = 300


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--fail-fast"]
    fail_fast = "--fail-fast" in sys.argv[1:]
    if args:
        tests = [(ROOT / a).resolve() for a in args]
        missing = [str(t) for t in tests if not t.is_file()]
        if missing:
            print("找不到测试文件：" + ", ".join(missing), file=sys.stderr)
            return 2
    else:
        tests = sorted(ROOT.glob("tests/test_*.py"))
    if not tests:
        print("tests/ 下没有 test_*.py", file=sys.stderr)
        return 2

    failures: list[str] = []
    started = time.monotonic()
    for test in tests:
        rel = test.relative_to(ROOT)
        print(f"── {rel} ", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, str(test)], cwd=ROOT, timeout=PER_TEST_TIMEOUT
            )
            ok = result.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
            print(f"   超时（>{PER_TEST_TIMEOUT}s），按失败算", flush=True)
        if not ok:
            failures.append(str(rel))
            if fail_fast:
                break

    elapsed = time.monotonic() - started
    print()
    if failures:
        print(f"通过 {len(tests) - len(failures)} 个文件，失败 {len(failures)} 个"
              f"（{elapsed:.1f}s）：")
        for name in failures:
            print(f"  FAILED: {name}")
        return 1
    print(f"全部通过：{len(tests)} 个文件（{elapsed:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
