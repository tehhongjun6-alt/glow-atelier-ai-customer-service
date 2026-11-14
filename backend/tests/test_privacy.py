"""隐私与错误讯息（第二轮边界修正）

1. 查预约要"电话 + 电邮"都对：只知道别人电话，拿不到预约编号，也就取消不了别人的预约
2. 交给 AI 的错误讯息不带内部细节（档案路径、token、原始错误）；真正的原因只进后端日志
"""

import logging
from types import SimpleNamespace

from agent import tools
from agent.actions import ActionGate
from booking import bookings
from tests.conftest import WEDNESDAY

CUSTOMER = dict(customer_name="陈小明", phone="012-888 6666", email="Chen.Demo@Example.com")
INTERNAL_WORDS = ["token", "secrets", "Google", "invalid_grant", "Traceback", ".py"]


def booked_code():
    return bookings.create_booking(**CUSTOMER, service="男士护理", date=WEDNESDAY, time="15:00")["booking"]["booking_code"]


def fake_agent(email=None):
    return SimpleNamespace(gate=ActionGate(), customer_email=email)


# ---------- 1. 查预约要电话 + 电邮 ----------
def test_phone_alone_is_not_enough(db, calendar):
    code = booked_code()
    result, _ = tools.find_bookings(fake_agent(), phone="0128886666")
    assert not result["success"] and "电邮" in result["error"]
    assert code not in str(result)                         # 编号没有外泄


def test_wrong_email_reveals_nothing(db, calendar):
    code = booked_code()
    result = bookings.find_bookings("0128886666", "stranger@example.com")
    assert result["bookings"] == []
    assert code not in str(result) and "陈小明" not in str(result)


def test_same_answer_for_wrong_phone_and_wrong_email(db, calendar):
    """查不到时不说是哪一样不对，避免被人一个一个试出来。"""
    booked_code()
    wrong_phone = bookings.find_bookings("0199999999", CUSTOMER["email"])
    wrong_email = bookings.find_bookings("0128886666", "x@example.com")
    assert wrong_phone == wrong_email


def test_phone_and_email_find_booking_with_masked_contacts(db, calendar):
    code = booked_code()
    found = bookings.find_bookings("+60 12-888 6666", "  chen.demo@EXAMPLE.com ")["bookings"]   # 大小写、空白都没关系
    assert [b["booking_code"] for b in found] == [code]
    assert found[0]["email"] == "Ch***@Example.com"        # 电邮遮一半
    assert found[0]["phone"] == "012-***6666"              # 电话遮中间


def test_email_channel_uses_sender_address(db, calendar):
    code = booked_code()
    result, _ = tools.find_bookings(fake_agent(email="chen.demo@example.com"), phone="0128886666")
    assert result["bookings"][0]["booking_code"] == code


# ---------- 2. 错误讯息不带内部细节 ----------
def test_calendar_error_is_generic_and_logged(db, calendar, caplog):
    calendar.fail = True
    with caplog.at_level(logging.ERROR, logger="glow.bookings"):
        result = bookings.create_booking(**CUSTOMER, service="男士护理", date=WEDNESDAY, time="15:00")
    assert result["error"] == bookings.SYSTEM_BUSY
    assert not any(w in result["error"] for w in INTERNAL_WORDS)
    assert "Google Calendar 连不上" in caplog.text           # 真正的原因在日志里，工程师看得到


def test_tool_crash_returns_generic_message(monkeypatch, caplog):
    """工具里发生没预料到的错误（例如 Google 授权过期），AI 只会拿到一般说法。"""
    from agent.agent import CustomerServiceAgent

    def crash(*args, **kwargs):
        raise RuntimeError("Google 授权已过期。请删除 backend/secrets/token.json")

    monkeypatch.setitem(tools.TOOL_FUNCTIONS, "check_availability", crash)
    agent = CustomerServiceAgent()
    call = SimpleNamespace(name="check_availability", args={"service": "男士护理", "date": WEDNESDAY})
    with caplog.at_level(logging.ERROR, logger="glow.agent"):
        result, info = agent._run_tool(call)
    assert result == {"error": bookings.SYSTEM_BUSY}
    assert not any(w in str(result) for w in INTERNAL_WORDS)
    assert "secrets/token.json" in caplog.text
