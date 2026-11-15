"""Logging 有没有写对（log_setup.py、email_worker.py）

用 pytest 的 caplog "拦截" log，检查：该写的 log 有写、等级对、模块名对。
全部用假的 Gmail / 假的 Agent，不连网络、不碰真的信箱。
"""

import logging
import shutil
import tempfile
from pathlib import Path

import pytest

import email_worker as w
import log_setup
from agent.agent import FALLBACK_ANSWER
from handoff import handoffs

COMPANY = "tehhongjun6+glow@gmail.com"


def make_msg(**changes):
    msg = {"id": "m1", "thread_id": "t1", "message_id": "<m1@x>", "references": "",
           "delivered_to": COMPANY, "to": COMPANY, "from_address": "amy@gmail.com", "from_name": "Amy",
           "subject": "价格", "headers": {}, "body": "Aqua Peel 多少钱？"}
    return {**msg, **changes}


class FakeAgent:
    answer = "您好！"
    trace = []

    def __init__(self, **_):
        pass

    def chat(self, text):
        return {"answer": self.answer, "trace": self.trace}


@pytest.fixture
def fake_inbox(monkeypatch):
    """假的信箱：放进去什么信，run_once 就"收到"什么信。加标签、寄信都会让测试失败。"""
    inbox = []
    monkeypatch.setattr(w, "agents_by_thread", {})
    monkeypatch.setattr(w, "start_epoch", lambda: 0)
    monkeypatch.setattr(w, "CustomerServiceAgent", FakeAgent)
    monkeypatch.setattr(w.gmail, "company_address", lambda: COMPANY)
    monkeypatch.setattr(w.gmail, "search", lambda query, max_results, include_spam=False:
                        [] if include_spam else [m["id"] for m in inbox])   # 垃圾邮件是空的
    monkeypatch.setattr(w.gmail, "get_message", lambda mid: next(m for m in inbox if m["id"] == mid))
    monkeypatch.setattr(w.handoffs, "open_ticket_for_thread", lambda thread_id: None)

    def must_not_touch_gmail(*args, **kwargs):
        raise AssertionError("dry-run 不应该寄信或加标签")
    monkeypatch.setattr(w.gmail, "add_label", must_not_touch_gmail)
    monkeypatch.setattr(w.gmail, "send_email", must_not_touch_gmail)
    return inbox


def email_logs(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "glow.email"]


def test_skipped_mail_is_logged(fake_inbox, caplog):
    fake_inbox.append(make_msg(from_address="no-reply@shopee.com"))
    with caplog.at_level(logging.INFO, logger="glow.email"):
        w.run_once(dry_run=True)
    logs = email_logs(caplog)
    assert ("INFO", "找到 1 封新信（dry-run：不寄信、不加标签）") in logs
    assert any(level == "INFO" and "略过：自动寄件地址" in text for level, text in logs)


def test_answered_mail_logs_the_reply(fake_inbox, caplog):
    fake_inbox.append(make_msg())
    with caplog.at_level(logging.INFO, logger="glow.email"):
        w.run_once(dry_run=True)
    logs = email_logs(caplog)
    assert ("INFO", "新信：amy@gmail.com｜价格") in logs
    assert any("回信内容" in text and "您好！" in text for _, text in logs)


def test_gemini_failure_is_a_warning(fake_inbox, caplog, monkeypatch):
    monkeypatch.setattr(FakeAgent, "answer", FALLBACK_ANSWER)
    fake_inbox.append(make_msg())
    with caplog.at_level(logging.INFO, logger="glow.email"):
        w.run_once(dry_run=True)
    warnings = [text for level, text in email_logs(caplog) if level == "WARNING"]
    assert len(warnings) == 1 and "这封信先不回" in warnings[0]


def test_handoff_thread_is_logged_with_ticket(fake_inbox, caplog, monkeypatch):
    status = next(iter(handoffs.STATUS_NAMES))
    monkeypatch.setattr(w.handoffs, "open_ticket_for_thread",
                        lambda thread_id: {"ticket_code": "HO-TEST01", "status": status})
    fake_inbox.append(make_msg())
    with caplog.at_level(logging.INFO, logger="glow.email"):
        w.run_once(dry_run=True)
    assert any("已经转人工" in text and "HO-TEST01" in text for _, text in email_logs(caplog))


def test_setup_logging_writes_formatted_file(monkeypatch):
    """log 文件真的会写出来，格式是：时间 | 等级 | 模块 | 内容"""
    # 不用 pytest 的 tmp_path：这台电脑的 Temp\pytest-of-user 没有权限，自己建一个临时文件夹
    tmp_path = Path(tempfile.mkdtemp())
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()                         # 假装是刚启动的程序
    monkeypatch.setattr(log_setup, "LOG_DIR", tmp_path)
    try:
        log_setup.setup_logging("test.log")
        logging.getLogger("httpx2").info("HTTP Request: POST ...")   # 杂讯，不应该写进文件
        logging.getLogger("glow.email").warning("测试一条警告")
        for handler in root.handlers:
            handler.flush()
        line = (tmp_path / "test.log").read_text(encoding="utf-8").strip()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        shutil.rmtree(tmp_path, ignore_errors=True)
    date_time, level, name, message = [part.strip() for part in line.split("|")]
    assert len(date_time) == len("2026-11-14 15:30:02")
    assert (level, name, message) == ("WARNING", "glow.email", "测试一条警告")


# ---------- 每封信的处理结果（也是 Langfuse 里 email-message 那条 trace 的 output） ----------
def test_process_message_outcomes(fake_inbox, monkeypatch):
    run = lambda msg, dry_run=True: w.process_message(msg, COMPANY, dry_run)

    assert run(make_msg(from_address="no-reply@shopee.com")) == "略过：自动寄件地址（no-reply／退信）"
    assert run(make_msg()) == "dry-run：回信已写好，没有寄出"

    monkeypatch.setattr(FakeAgent, "answer", FALLBACK_ANSWER)
    assert run(make_msg(thread_id="t2")) == "Gemini 出错或没有答案，这封信先不回，下一轮再试"

    status = next(iter(handoffs.STATUS_NAMES))
    monkeypatch.setattr(w.handoffs, "open_ticket_for_thread",
                        lambda thread_id: {"ticket_code": "HO-TEST01", "status": status})
    assert run(make_msg(thread_id="t3")) == "已转人工（工单 HO-TEST01），AI 不回复"


def test_real_reply_is_sent_and_labelled(fake_inbox, monkeypatch):
    sent, labels = [], []
    monkeypatch.setattr(w.gmail, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, body)))
    monkeypatch.setattr(w.gmail, "add_label", lambda mid, label, mark_read=False: labels.append(label))
    assert w.process_message(make_msg(), COMPANY, dry_run=False) == "已回信"
    assert sent == [("amy@gmail.com", "Re: 价格", "您好！")]
    assert labels == [w.REPLIED_LABEL]
