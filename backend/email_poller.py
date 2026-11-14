"""网页后台的「邮箱」开关：在后端里开一个背景线程，每 60 秒检查一次 Gmail

- 预设是「关」：后端启动（或改代码自动重载）之后都是关着的，不会自己去读信箱
- 打开：开一个背景线程，每一轮调用 email_worker.run_once()（真的会回信）
- 关闭：通知线程停下来；正在处理的那一轮会先做完
- 同一时间最多只有一个线程（用锁保护），连按两次「开」也不会重复回信

安全规则（只看 +glow、不碰旧信、加标签……）全部沿用 email_worker.py，这里只负责"什么时候跑"。
⚠️ 这是本机 demo 用的开关：后台 API 没有登录保护，不要部署到公开网址。
⚠️ 开着这个开关时，不要同时在终端跑 email_worker.py，两边可能会抢着回同一封信。
"""

import logging
import threading
from datetime import datetime

import email_worker
from booking import rules

log = logging.getLogger("glow.email")


class EmailPoller:
    def __init__(self, run_once=email_worker.run_once, interval=email_worker.POLL_SECONDS):
        self._run_once = run_once
        self.interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.last_check = None    # 上次成功检查完的时间
        self.last_error = None    # 上一轮的错误（成功一轮就清掉）

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and not self._stop.is_set()

    def start(self):
        with self._lock:
            self._stop.clear()
            if self._thread is None:    # 线程还在（例如刚按关、这一轮还没做完）就沿用它，不开第二个
                self._thread = threading.Thread(target=self._loop, name="email-poller", daemon=True)
                self._thread.start()
                log.info("邮箱自动回复：开启（每 %d 秒检查一次）", self.interval)

    def stop(self):
        with self._lock:
            if self._thread is not None and not self._stop.is_set():
                log.info("邮箱自动回复：关闭")
            self._stop.set()

    def _loop(self):
        while True:
            with self._lock:
                if self._stop.is_set():
                    self._thread = None
                    return
            try:
                self._run_once(dry_run=False)
                self.last_check = datetime.now(rules.TIMEZONE)
                self.last_error = None
            except Exception as error:   # 网络断了、授权过期……下一轮再试，不要整个停掉
                log.exception("邮箱自动回复：这一轮出错，等下一轮再试")
                self.last_error = f"{type(error).__name__}: {error}"
            self._stop.wait(self.interval)   # 睡到下一轮；按「关」会马上醒来

    def status(self):
        try:
            address = email_worker.gmail.company_address()
        except RuntimeError as error:
            address, self.last_error = None, str(error)
        return {
            "running": self.running,
            "address": address,
            "interval_seconds": self.interval,
            "last_check": self.last_check.strftime("%Y-%m-%d %H:%M:%S") if self.last_check else None,
            "last_error": self.last_error,
        }


poller = EmailPoller()
