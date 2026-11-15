"""日历自动补做：后端启动后，背景每 60 秒补一次"日历待补做"的预约

为什么要有？Google Calendar 偶尔会连不上（网络、Google 那边出错、授权过期）。
以前失败了就要员工手动改日历，常常没人发现 → 顾客到店，美容师的日历上却没有这笔预约。
现在失败的预约会被标成"待补做"（bookings.calendar_pending），这里每分钟照数据库的样子补上去。

真正的补做逻辑在 booking/bookings.py 的 sync_calendar_once()，这里只负责"什么时候跑"。
设定 CALENDAR_SYNC=off 可以关掉（测试会关掉，不连 Google）。
"""

import logging
import os
import threading

from booking import bookings

log = logging.getLogger("glow.bookings")

SYNC_SECONDS = 60
_stop = threading.Event()
_thread = None


def _loop():
    while not _stop.is_set():
        try:
            bookings.sync_calendar_once()
        except Exception:      # 例如数据库连不上：下一轮再试，背景程序不能整个停掉
            log.exception("日历自动补做：这一轮出错，下一轮再试")
        _stop.wait(SYNC_SECONDS)


def start():
    global _thread
    if os.getenv("CALENDAR_SYNC", "on").lower() == "off" or _thread is not None:
        return
    _thread = threading.Thread(target=_loop, name="calendar-sync", daemon=True)
    _thread.start()
    log.info("日历自动补做：开启（每 %d 秒检查一次）", SYNC_SECONDS)
