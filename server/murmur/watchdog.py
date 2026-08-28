"""看门狗：事件循环卡死时把自己杀掉，让外面的 supervisor 重启。

为什么需要它：
2026-08-13 凌晨，Telegram 进程在两次网络错误后整个 asyncio 事件循环卡死——
进程还在（STAT=SN），8.7 小时只消耗 2.57 秒 CPU，日志一片空白，
当天排的 14 条主动消息一条都没发出去。PTB 自己的重试没能恢复。

关键点：**看门狗必须跑在独立线程上**。如果它挂在事件循环里（比如用
JobQueue 定时检查），循环卡死时它自己也一起卡死，等于没有。

机制很笨但可靠：事件循环里有个心跳任务定期戳一下时间戳，
独立线程盯着这个时间戳；超过阈值没更新就 os._exit()，
交给外面的 systemd / 重启脚本把进程拉起来。
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("murmur.watchdog")

# 心跳多久算失联。轮询间隔 10 秒，正常情况下心跳是 60 秒一次，
# 给到 5 分钟足够容忍网络慢，又不至于让它死太久没人管。
STALE_AFTER = 300.0
CHECK_EVERY = 30.0

# 检查间隔必须跟阈值挂钩。写死 30 秒的话，阈值设小于 30 秒时
# 第一次检查都还没到就错过了——测试时就是这么发现的。
def _check_interval(stale_after: float) -> float:
    return max(1.0, min(CHECK_EVERY, stale_after / 4))

_last_beat = time.monotonic()
_lock = threading.Lock()


def beat() -> None:
    """事件循环里定期调用。能调到就说明循环还活着。"""
    global _last_beat
    with _lock:
        _last_beat = time.monotonic()


def _age() -> float:
    with _lock:
        return time.monotonic() - _last_beat


def start(name: str, stale_after: float = STALE_AFTER) -> None:
    """起一个守护线程盯着心跳。"""

    every = _check_interval(stale_after)

    def loop() -> None:
        while True:
            # 量一下自己这一觉睡了多久。合盖休眠时**看门狗线程和被监控的
            # 循环一起被冻住**，醒来只看到"很久没心跳"，分不清是卡死还是
            # 机器睡过去了——2026-08-13 中午笔记本睡了 12 分钟，
            # 三个进程全被误杀重启了一轮。
            #
            # 判据：如果连我自己的 sleep(every) 都严重超时，那不是对方卡了，
            # 是整台机器停了。这个判据不依赖 monotonic 在休眠时是否走字，
            # 各平台行为不一致，实测靠不住。
            before = time.monotonic()
            time.sleep(every)
            overslept = (time.monotonic() - before) - every
            if overslept > every:
                log.info(
                    "%s 看门狗自己被冻了 %.0f 秒（多半是电脑休眠），"
                    "重新计时，不当作卡死",
                    name, overslept,
                )
                beat()
                continue

            age = _age()
            if age > stale_after:
                log.error(
                    "%s 事件循环已 %.0f 秒无心跳，判定卡死，退出让 supervisor 重启",
                    name, age,
                )
                # 不用 sys.exit：那只是抛异常，卡死的循环收不到。
                # os._exit 直接终止进程，是这里唯一可靠的手段。
                os._exit(75)   # EX_TEMPFAIL

    threading.Thread(target=loop, name="murmur-watchdog", daemon=True).start()
    log.info("看门狗已启动（每 %.0f 秒查一次，%.0f 秒无心跳即重启）",
             every, stale_after)
