"""警报和转人工通知（alerts/telegram.py）：不会真的送到 Telegram"""

import logging
import queue

import pytest
from fastapi.testclient import TestClient

from alerts import telegram
from handoff import handoffs


@pytest.fixture
def sent(monkeypatch):
    """假的 Telegram：送出的讯息记在这个 list。"""
    messages = []
    monkeypatch.setattr(telegram, "send", lambda text: messages.append(telegram.mask(text)) or True)
    return messages


# ---------- 遮蔽个资 ----------
@pytest.mark.parametrize("raw, masked", [
    ("chen.demo@example.com", "ch***@example.com"),
    ("0128886666", "012-***6666"),
    ("电话 012-888 6666", "电话 012-***6666"),
    ("+6012 345 6789", "+6012-***6789"),
    ("工单 HO-82G7YA 2026-12-30 14:00", "工单 HO-82G7YA 2026-12-30 14:00"),   # 日期、编号不能被误遮
])
def test_mask_hides_email_and_phone(raw, masked):
    assert telegram.mask(raw) == masked


def test_send_does_nothing_without_settings(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert telegram.send("hi") is False


def test_send_queues_masked_text_in_background(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(telegram, "_outbox", queue.Queue())
    monkeypatch.setattr(telegram, "_worker", object())     # 假装背景线程已经在跑（不真的连 Telegram）
    assert telegram.send("顾客 amy@gmail.com 0123456789") is True
    assert telegram._outbox.get_nowait() == "顾客 am***@gmail.com 012-***6789"


# ---------- 警报 ----------
def make_handler(sent, clock):
    handler = telegram.AlertHandler(clock=lambda: clock[0])
    logger = logging.getLogger("glow.test-alerts")
    logger.addHandler(handler)
    return logger, handler


def test_error_is_sent_but_warning_is_not(sent):
    clock = [0]
    logger, handler = make_handler(sent, clock)
    try:
        logger.warning("小问题")
        logger.error("Google Calendar 写入失败：%s", "timeout")
    finally:
        logger.removeHandler(handler)
    assert len(sent) == 1 and "Google Calendar 写入失败：timeout" in sent[0] and "glow.test-alerts" in sent[0]


def test_same_error_only_once_per_cooldown(sent):
    clock = [0]
    logger, handler = make_handler(sent, clock)
    try:
        for _ in range(5):
            logger.error("数据库连不上：%s", "x")       # 同一种错误连续 5 次
        clock[0] += telegram.ALERT_COOLDOWN_SECONDS + 1
        logger.error("数据库连不上：%s", "y")           # 10 分钟后又出现
    finally:
        logger.removeHandler(handler)
    assert len(sent) == 2


def test_exception_sends_one_line_not_traceback(sent):
    logger, handler = make_handler(sent, [0])
    try:
        try:
            raise RuntimeError("token 过期")
        except RuntimeError:
            logger.exception("工具执行失败")
    finally:
        logger.removeHandler(handler)
    assert "RuntimeError: token 过期" in sent[0] and "Traceback" not in sent[0]


def test_alert_failures_do_not_trigger_more_alerts(sent):
    handler = telegram.AlertHandler()
    handler.emit(logging.makeLogRecord({"name": "glow.alerts", "levelno": logging.ERROR, "msg": "送不出去"}))
    assert sent == []


# ---------- 转人工通知 ----------
def test_new_handoff_notifies_staff_with_masked_contact(db, sent):
    result = handoffs.create_handoff(channel="chat", reason="complaint", summary="顾客说美容师态度差",
                                     contact="0128886666", customer_name="Amy")
    code = result["handoff"]["ticket_code"]
    assert len(sent) == 1 and code in sent[0] and "投诉" in sent[0]
    assert "0128886666" not in sent[0] and "012-***6666" in sent[0]


def test_existing_email_ticket_does_not_notify_again(db, sent):
    for _ in range(2):
        handoffs.create_handoff(channel="email", reason="other", summary="x", contact="a@b.com", email_thread_id="t1")
    assert len(sent) == 1


# ---------- 健康检查 ----------
def test_health_returns_503_when_database_is_down(monkeypatch):
    import api

    monkeypatch.setattr(api.google_auth, "status", lambda: {"ok": True})
    client = TestClient(api.app)
    monkeypatch.setattr(api, "_database_ok", lambda: True)
    assert client.get("/api/health").status_code == 200
    monkeypatch.setattr(api, "_database_ok", lambda: False)
    down = client.get("/api/health")
    assert down.status_code == 503 and down.json()["database"] is False


# ---------- 程序结束前要送完（实测：短程序结束时警报没送出去） ----------
def test_flush_waits_until_queued_messages_are_sent(monkeypatch):
    import threading
    import time as _time

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(telegram, "_outbox", queue.Queue())
    monkeypatch.setattr(telegram, "_worker", None)
    monkeypatch.setattr(telegram.atexit, "register", lambda f: None)
    posted = []
    monkeypatch.setattr(telegram, "_post", lambda text: _time.sleep(0.3) or posted.append(text))   # 送一则要 0.3 秒
    telegram.send("警报")
    telegram.flush(timeout=5)
    assert posted == ["警报"]


def test_uvicorn_crash_log_also_alerts(monkeypatch):
    """网页 API 没预料到的崩溃是 uvicorn.error 写的，不会经过 root，所以要另外接上警报。"""
    import log_setup

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    uvicorn_error = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(uvicorn_error, "handlers", [])
    monkeypatch.setattr(log_setup, "LOG_DIR", log_setup.LOG_DIR / "test-tmp")
    monkeypatch.setattr(telegram, "configured", lambda: True)
    try:
        log_setup.setup_logging("test.log")
        assert any(isinstance(h, telegram.AlertHandler) for h in uvicorn_error.handlers)
    finally:
        for h in root.handlers:
            h.close()
        import shutil
        shutil.rmtree(log_setup.LOG_DIR / "test-tmp", ignore_errors=True)
