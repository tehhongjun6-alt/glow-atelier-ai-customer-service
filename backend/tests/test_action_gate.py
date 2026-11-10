"""两段式确认（agent/actions.py）：不可逆的动作，要顾客在"下一则讯息"确认后才执行

这是测试时抓到"模型没问顾客就直接预约"之后加的硬护栏，所以要确保它一直有效。
"""

from agent import tools
from agent.actions import ActionGate
from tests.conftest import WEDNESDAY

CUSTOMER = dict(customer_name="陈小明", phone="0128886666", email="chen@example.com")


class FakeAgent:
    """工具只会用到 agent.gate，不需要真的 Gemini。"""
    def __init__(self):
        self.gate = ActionGate()


def stage_booking(agent, time="15:00"):
    result, _ = tools.request_booking(agent, **CUSTOMER, service="男士护理", date=WEDNESDAY, time=time)
    return result


def test_confirm_without_anything_staged(db, calendar, emails):
    agent = FakeAgent()
    agent.gate.new_turn()
    result, _ = tools.confirm_action(agent)
    assert not result["success"] and "没有等待确认" in result["error"]


def test_same_turn_confirm_is_rejected(db, calendar, emails):
    """模型在同一轮里暂存完马上确认 = 根本没问顾客 → 一定要挡下来。"""
    agent = FakeAgent()
    agent.gate.new_turn()
    assert stage_booking(agent)["status"] == "waiting_for_customer_confirmation"
    result, _ = tools.confirm_action(agent)
    assert not result["success"] and "还没有回覆确认" in result["error"]
    assert db.tables["bookings"].rows == [] and calendar.created == [] and emails.sent == []


def test_next_turn_confirm_books_and_sends_email(db, calendar, emails):
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent)
    agent.gate.new_turn()                                  # 顾客回了"确认"
    result, _ = tools.confirm_action(agent)
    assert result["success"] and result["action"] == "booking"
    assert result["confirmation_email_sent"] is True
    assert len(db.tables["bookings"].rows) == 1 and emails.sent[0][0] == "booking"


def test_email_failure_does_not_undo_booking(db, calendar, emails):
    """寄信失败：预约照样成功，但要回报 confirmation_email_sent=False，AI 才不会说'已寄出'。"""
    emails.fail = True
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent)
    agent.gate.new_turn()
    result, _ = tools.confirm_action(agent)
    assert result["success"] and result["confirmation_email_sent"] is False and "email_error" in result


def test_pending_action_is_used_only_once(db, calendar, emails):
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent)
    agent.gate.new_turn()
    tools.confirm_action(agent)
    agent.gate.new_turn()
    again, _ = tools.confirm_action(agent)                 # 顾客再说一次"确认"，不能订两笔
    assert not again["success"] and len(db.tables["bookings"].rows) == 1


def test_new_request_replaces_old_one(db, calendar, emails):
    """顾客改了时间：只会执行最新的那一个。"""
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent, time="15:00")
    agent.gate.new_turn()
    stage_booking(agent, time="17:00")
    agent.gate.new_turn()
    result, _ = tools.confirm_action(agent)
    assert result["booking"]["time"] == "17:00" and len(db.tables["bookings"].rows) == 1


def test_invalid_request_is_not_staged(db, calendar, emails):
    agent = FakeAgent()
    agent.gate.new_turn()
    result, _ = tools.request_booking(agent, **CUSTOMER, service="男士护理", date="2026-11-16", time="11:00")
    assert not result["success"] and agent.gate.pending is None


def test_cancel_also_needs_two_steps(db, calendar, emails):
    agent = FakeAgent()
    agent.gate.new_turn()
    stage_booking(agent)
    agent.gate.new_turn()
    code = tools.confirm_action(agent)[0]["booking"]["booking_code"]

    agent.gate.new_turn()
    staged, _ = tools.request_cancel(agent, code, CUSTOMER["phone"])
    assert staged["status"] == "waiting_for_customer_confirmation"
    assert tools.confirm_action(agent)[0]["success"] is False          # 同一轮
    assert db.tables["bookings"].rows[0]["status"] == "confirmed"
    agent.gate.new_turn()
    assert tools.confirm_action(agent)[0]["booking"]["status"] == "cancelled"
