"""转人工（handoff/handoffs.py、agent/tools.py 的 request_human_handoff）

状态只能照顺序走：processing（待处理）→ accepted（处理中）→ resolved（处理完毕）
"""

from types import SimpleNamespace

from agent import tools
from handoff import handoffs


def new_ticket(**extra):
    args = {"channel": "chat", "reason": "complaint", "summary": "做完脸过敏", "contact": "0129998888", **extra}
    return handoffs.create_handoff(**args)["handoff"]["ticket_code"]


# ---------- 状态顺序 ----------
def test_full_flow(db):
    code = new_ticket()
    assert handoffs.accept(code, "美玲")["handoff"]["status"] == "accepted"
    done = handoffs.resolve(code, "已致电顾客")["handoff"]
    assert done["status"] == "resolved" and done["assigned_to"] == "美玲" and done["staff_note"] == "已致电顾客"


def test_cannot_skip_accept(db):
    code = new_ticket()
    result = handoffs.resolve(code, "x")
    assert not result["success"] and "下一步只能是「处理中」" in result["error"]


def test_cannot_accept_twice(db):
    code = new_ticket()
    handoffs.accept(code, "美玲")
    result = handoffs.accept(code, "思敏")
    assert not result["success"]
    assert db.tables["handoffs"].rows[0]["assigned_to"] == "美玲"      # 第一位同事没被换掉


def test_cannot_change_after_resolved(db):
    code = new_ticket()
    handoffs.accept(code, "美玲")
    handoffs.resolve(code, "ok")
    assert "不能再改" in handoffs.resolve(code, "again")["error"]


def test_accept_needs_staff_name(db):
    code = new_ticket()
    assert "名字" in handoffs.accept(code, "  ")["error"]
    assert db.tables["handoffs"].rows[0]["status"] == "processing"


def test_unknown_ticket(db):
    assert "找不到" in handoffs.accept("HO-NOPE00", "美玲")["error"]


# ---------- 建立工单 ----------
def test_create_needs_contact(db):
    result = handoffs.create_handoff(channel="chat", reason="complaint", summary="x", contact="")
    assert not result["success"] and db.tables["handoffs"].rows == []


def test_unknown_reason_becomes_other(db):
    code = new_ticket(reason="something-weird")
    assert handoffs.get_handoff(code)["reason"] == "other"


def test_one_open_ticket_per_email_thread(db):
    first = handoffs.create_handoff(channel="email", reason="refund", summary="a", contact="a@x.com", email_thread_id="t1")
    second = handoffs.create_handoff(channel="email", reason="refund", summary="b", contact="a@x.com", email_thread_id="t1")
    assert second["already_exists"] and second["handoff"]["ticket_code"] == first["handoff"]["ticket_code"]
    assert len(db.tables["handoffs"].rows) == 1


def test_thread_is_free_again_after_resolved(db):
    code = new_ticket(email_thread_id="t2")
    assert handoffs.open_ticket_for_thread("t2")["ticket_code"] == code   # AI 不会再回这个对话
    handoffs.accept(code, "美玲")
    handoffs.resolve(code, "ok")
    assert handoffs.open_ticket_for_thread("t2") is None                  # 处理完，AI 可以再回


# ---------- AI 的转人工工具 ----------
def fake_agent(channel="chat", email=None, thread=None):
    return SimpleNamespace(channel=channel, customer_email=email, email_thread_id=thread,
                           transcript=lambda: [{"role": "customer", "text": "我要找真人"}])


def test_tool_refuses_without_contact(db):
    """测试时真的发生过：AI 还没问电话就想转人工。"""
    result, _ = tools.request_human_handoff(fake_agent(), reason="request_human", summary="要找真人")
    assert not result["success"] and "联系方式" in result["error"]
    assert db.tables["handoffs"].rows == []


def test_tool_uses_sender_address_on_email(db):
    result, _ = tools.request_human_handoff(fake_agent("email", "ben@example.com", "t9"),
                                            reason="refund", summary="配套退款")
    row = db.tables["handoffs"].rows[0]
    assert result["success"] and row["contact"] == "ben@example.com" and row["email_thread_id"] == "t9"
    assert row["conversation"][0]["text"] == "我要找真人"                 # 对话记录有存进工单
