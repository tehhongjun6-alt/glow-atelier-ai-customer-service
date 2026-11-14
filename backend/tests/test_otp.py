"""改期、取消要验证码（booking/otp.py + agent/actions.py）

用假的寄信（emails fixture），验证码记在 emails.otp_codes，不会真的寄出去。
"""

from datetime import timedelta

from agent import tools
from booking import rules
from tests.test_action_gate import CUSTOMER, FakeAgent, stage_booking


def booked(emails):
    """先订好一笔预约，返回 (agent, 预约编号)。"""
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent)
    agent.gate.new_turn()
    code = tools.confirm_action(agent)[0]["booking"]["booking_code"]
    agent.gate.new_turn()
    return agent, code


def test_cancel_sends_code_to_booking_email(db, calendar, emails):
    agent, code = booked(emails)
    staged, _ = tools.request_cancel(agent, code, CUSTOMER["phone"])
    assert staged["otp_sent_to"] == "ch***@example.com"       # 告诉顾客寄到哪，但遮住地址
    assert len(emails.otp_codes) == 1 and len(emails.otp_codes[0]) == 6


def test_without_code_or_wrong_code_nothing_happens(db, calendar, emails):
    agent, code = booked(emails)
    tools.request_cancel(agent, code, CUSTOMER["phone"])
    agent.gate.new_turn()
    assert tools.confirm_action(agent)[0]["success"] is False                    # 没给验证码
    assert tools.confirm_action(agent, otp_code="000000")[0]["success"] is False  # 给错
    assert db.tables["bookings"].rows[0]["status"] == "confirmed"
    right = emails.otp_codes[-1]
    assert tools.confirm_action(agent, otp_code=right)[0]["booking"]["status"] == "cancelled"


def test_too_many_wrong_codes_voids_the_request(db, calendar, emails):
    agent, code = booked(emails)
    tools.request_cancel(agent, code, CUSTOMER["phone"])
    agent.gate.new_turn()
    for _ in range(5):
        tools.confirm_action(agent, otp_code="000000")
    assert agent.gate.pending is None                                            # 作废了
    assert tools.confirm_action(agent, otp_code=emails.otp_codes[-1])[0]["success"] is False
    assert db.tables["bookings"].rows[0]["status"] == "confirmed"


def test_expired_code_is_rejected(db, calendar, emails, monkeypatch):
    agent, code = booked(emails)
    tools.request_cancel(agent, code, CUSTOMER["phone"])
    agent.gate.new_turn()
    later = rules.now() + timedelta(minutes=11)
    monkeypatch.setattr(rules, "now", lambda: later)
    result, _ = tools.confirm_action(agent, otp_code=emails.otp_codes[-1])
    assert result["success"] is False and "过期" in result["error"]


def test_reschedule_also_needs_code(db, calendar, emails):
    agent, code = booked(emails)
    staged, _ = tools.request_reschedule(agent, code, CUSTOMER["phone"], "2026-11-12", "15:00")
    assert staged["otp_sent_to"]
    agent.gate.new_turn()
    assert tools.confirm_action(agent)[0]["success"] is False
    assert tools.confirm_action(agent, otp_code=emails.otp_codes[-1])[0]["success"] is True


def test_code_cannot_be_sent_then_nothing_is_staged(db, calendar, emails):
    agent, code = booked(emails)
    emails.otp_fail = True
    result, _ = tools.request_cancel(agent, code, CUSTOMER["phone"])
    assert result["success"] is False and result["needs_staff"] is True
    assert agent.gate.pending is None


def test_new_booking_does_not_need_code(db, calendar, emails):
    booked(emails)
    assert emails.otp_codes == []


def test_code_typed_in_different_formats_still_works(db, calendar, emails):
    """全角数字、空格、横线、开头的 0 被吃掉：都应该接受；不同的数字还是要拒绝。"""
    from booking import otp
    def fresh():   # 每次核对都算一次尝试，所以每种写法用一个新的记录
        return {"hash": otp._hash("012345"), "expires_at": rules.now() + timedelta(minutes=10), "attempts": 0}

    for typed in ["012345", "０１２３４５", "012 345", "012-345", " 012345 ", 12345]:
        assert otp.check(fresh(), typed) is None, typed
    assert otp.check(fresh(), "012346") == "wrong_code"
    assert otp.check(fresh(), "0123456") == "wrong_code"     # 多一位也不行


def test_wrong_code_tells_ai_how_many_tries_are_left(db, calendar, emails):
    """AI 看不到代码，所以错的时候要直接告诉它还剩几次（不然它会自己猜、提早转人工）。"""
    agent, code = booked(emails)
    tools.request_cancel(agent, code, CUSTOMER["phone"])
    agent.gate.new_turn()
    first, _ = tools.confirm_action(agent, otp_code="000000")
    second, _ = tools.confirm_action(agent, otp_code="000000")
    assert first["attempts_left"] == 4 and "还可以再试 4 次" in first["error"]
    assert second["attempts_left"] == 3
