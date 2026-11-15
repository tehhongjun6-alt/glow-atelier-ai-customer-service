"""Telegram 通知：出事（ERROR）和新的转人工工单，马上通知到手机

为什么用 Telegram？免费、手机马上响、不用另外架服务器；作品集阶段"值班的人"就是我自己。

设定（在 backend/.env，自己填，不要上传）：
    TELEGRAM_BOT_TOKEN=...   在 Telegram 找 @BotFather 建机器人拿到
    TELEGRAM_CHAT_ID=...     自己的 chat id（先传一则讯息给机器人，再查 getUpdates）
没设定的话：什么都不送，程序照常运行（测试、别人的电脑都不受影响）。

安全和稳定：
- 在背景线程送出：Telegram 慢或连不上，也不会拖慢顾客的聊天
- 电话、电邮先遮蔽再送（Telegram 是外部服务，不送完整个资）
- 送失败只写一行 log，不会因为通知失败而让主要功能出错
"""

import atexit
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request

log = logging.getLogger("glow.alerts")

MAX_LENGTH = 1000   # Telegram 一则讯息上限 4096 字，太长的只送前面
EMAIL = re.compile(r"([\w.+-]{1,2})[\w.+-]*@([\w-]+\.[\w.-]+)")
PHONE = re.compile(r"(?<!\d)(\+?6?0\d{2})[\d -]{3,8}(\d{4})(?!\d)")

_outbox = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


def configured():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def mask(text):
    """遮蔽电邮和马来西亚手机号码：chen.demo@example.com → ch***@example.com，012-888 6666 → 012-***6666"""
    text = EMAIL.sub(r"\1***@\2", text)
    return PHONE.sub(r"\1-***\2", text)


def _post(text):
    url = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/sendMessage"
    body = json.dumps({"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": text,
                       "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status == 200


def _run():
    while True:
        text = _outbox.get()
        try:
            _post(text)
        except Exception as error:   # 不能用 log.error：会再触发警报 → 无限循环
            log.warning("Telegram 通知送不出去：%s", type(error).__name__)
        finally:
            _outbox.task_done()


def flush(timeout=10):
    """等排队中的通知送完（最多 timeout 秒）。程序结束时自动调用。"""
    deadline = time.monotonic() + timeout
    while _outbox.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.1)


def send(text):
    """排进背景送出，马上返回。没有设定 Telegram 就什么都不做。返回 True = 已排进去。"""
    global _worker
    if not configured():
        return False
    with _worker_lock:
        if _worker is None:
            _worker = threading.Thread(target=_run, name="telegram", daemon=True)
            _worker.start()
            # 背景线程在程序结束时会被直接停掉；像 email_worker.py --once 这种跑完就结束的程序，
            # 警报可能还没送出去就没了（实测发生过）→ 程序结束前先等它送完
            atexit.register(flush)
    _outbox.put(mask(text)[:MAX_LENGTH])
    return True


# ---------- 警报：ERROR 等级的 log 自动送到 Telegram ----------
ALERT_COOLDOWN_SECONDS = 10 * 60   # 同一种错误 10 分钟内只通知一次（不然 Google 坏掉时手机会一直响）
QUIET_LOGGERS = ("glow.alerts", "urllib3", "httpx", "httpcore")   # 通知自己出错不能再触发通知


class AlertHandler(logging.Handler):
    """接在 logging 上：任何地方写了 log.error / log.exception，就通知值班的人。"""

    def __init__(self, clock=None):
        super().__init__(level=logging.ERROR)
        self._last_sent = {}
        self._clock = clock or time.monotonic

    def emit(self, record):
        if record.name.startswith(QUIET_LOGGERS):
            return
        key = (record.name, record.msg)          # 同一行代码的同一种错误算一种（不管参数）
        now = self._clock()
        if now - self._last_sent.get(key, float("-inf")) < ALERT_COOLDOWN_SECONDS:
            return
        self._last_sent[key] = now
        lines = [f"🚨 Glow Atelier 出错（{record.levelname}）", f"模块：{record.name}", f"内容：{record.getMessage()}"]
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            lines.append(f"例外：{type(error).__name__}: {error}")   # 只送一行，不送整段 Traceback
        lines.append("详情请看 backend/logs 和 Langfuse")
        send("\n".join(lines))
