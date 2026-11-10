"""第 6 步：转人工工单

工单的一生：
    AI 判断要转人工 → create_handoff()            状态 processing（后台收到）
    同事在后台按"接受" → accept(ticket, 同事名字)  状态 accepted
    同事处理好按"处理完毕" → resolve(ticket, 记录)  状态 resolved

状态只能照顺序往前走，不能跳步，也不能倒退（用代码检查）。
"""

import random
import string
from datetime import datetime

from booking import rules
from rag.store import supabase

TABLE = "handoffs"

REASONS = {
    "complaint": "投诉／不满意",
    "medical": "过敏／身体不适",
    "refund": "退款／配套纠纷",
    "request_human": "顾客要求真人",
    "cannot_answer": "AI 答不了",
    "other": "其他",
}
STATUS_NAMES = {"processing": "待处理", "accepted": "处理中", "resolved": "处理完毕"}

# 每个状态可以去哪个状态
NEXT_STATUS = {"processing": "accepted", "accepted": "resolved", "resolved": None}


def _new_code():
    letters = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")
    while True:
        code = "HO-" + "".join(random.choices(letters, k=6))
        if not supabase.table(TABLE).select("id").eq("ticket_code", code).execute().data:
            return code


def _local(iso_text):
    return datetime.fromisoformat(iso_text).astimezone(rules.TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _error(message):
    return {"success": False, "error": message}


def describe(row):
    """给 Agent／后台看的工单资料。"""
    return {
        "ticket_code": row["ticket_code"],
        "status": row["status"],
        "status_name": STATUS_NAMES[row["status"]],
        "reason": row["reason"],
        "reason_name": REASONS[row["reason"]],
        "channel": row["channel"],
        "customer_name": row.get("customer_name"),
        "contact": row["contact"],
        "summary": row["summary"],
        "booking_code": row.get("booking_code"),
        "assigned_to": row.get("assigned_to"),
        "staff_note": row.get("staff_note"),
        "created_at": row["created_at"],
        "created_at_local": _local(row["created_at"]),   # 数据库存 UTC，显示用吉隆坡时间
    }


# ---------- 建立（AI 调用） ----------
def create_handoff(channel, reason, summary, contact, customer_name=None, booking_code=None,
                   email_thread_id=None, conversation=None):
    if reason not in REASONS:
        reason = "other"
    if not str(contact).strip():
        return _error("需要顾客的联系方式（电话或电邮），同事才能联络顾客")

    # 同一个电邮对话已经有未处理完的工单，就不要重复开
    if email_thread_id:
        existing = open_ticket_for_thread(email_thread_id)
        if existing:
            return {"success": True, "handoff": describe(existing), "already_exists": True}

    row = {
        "ticket_code": _new_code(),
        "channel": channel,
        "reason": reason,
        "summary": summary.strip(),
        "contact": str(contact).strip(),
        "customer_name": customer_name or None,
        "booking_code": booking_code or None,
        "email_thread_id": email_thread_id,
        "conversation": conversation or [],
    }
    saved = supabase.table(TABLE).insert(row).execute().data[0]
    return {"success": True, "handoff": describe(saved)}


# ---------- 查询（后台、电邮程序用） ----------
def list_handoffs(status=None):
    query = supabase.table(TABLE).select("*").order("created_at")
    if status:
        query = query.eq("status", status)
    return [describe(r) for r in query.execute().data]


def get_handoff(ticket_code):
    rows = supabase.table(TABLE).select("*").eq("ticket_code", ticket_code.strip().upper()).execute().data
    return rows[0] if rows else None


def open_ticket_for_thread(thread_id):
    """这个电邮对话有没有还没处理完的工单（有的话，AI 不再自动回这个对话）。"""
    rows = (
        supabase.table(TABLE).select("*")
        .eq("email_thread_id", thread_id)
        .neq("status", "resolved")
        .execute().data
    )
    return rows[0] if rows else None


# ---------- 改状态（后台同事操作） ----------
def _move(ticket_code, to_status, changes):
    row = get_handoff(ticket_code)
    if not row:
        return _error(f"找不到工单 {ticket_code}")
    expected = NEXT_STATUS[row["status"]]
    if expected != to_status:
        now_name = STATUS_NAMES[row["status"]]
        if expected is None:
            return _error(f"工单已经{now_name}，不能再改")
        return _error(f"工单现在是「{now_name}」，下一步只能是「{STATUS_NAMES[expected]}」")

    updated = (
        supabase.table(TABLE)
        .update({"status": to_status, **changes})
        .eq("id", row["id"]).eq("status", row["status"])   # 防止两位同事同时按
        .execute().data
    )
    if not updated:
        return _error("工单状态刚刚被别人改了，请重新整理")
    return {"success": True, "handoff": describe(updated[0])}


def accept(ticket_code, staff_name):
    """同事按"接受"：processing → accepted"""
    if not staff_name.strip():
        return _error("请填写接手同事的名字")
    return _move(ticket_code, "accepted", {"assigned_to": staff_name.strip(), "accepted_at": rules.now().isoformat()})


def resolve(ticket_code, note=""):
    """同事按"处理完毕"：accepted → resolved"""
    return _move(ticket_code, "resolved", {"staff_note": note.strip() or None, "resolved_at": rules.now().isoformat()})
