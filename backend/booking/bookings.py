"""第 4 步：预约功能（查空档、新增、查询、取消、改期）

每个会改资料的功能都拆成两半：
- prepare_*：只检查（规则、名额、手机号码），不改任何东西
- commit_*：真正写进 Supabase 和 Google Calendar

为什么要拆开？因为 Agent 要"先给顾客确认，顾客同意了才执行"（两段式确认，见 agent/actions.py）。
测试发现：只靠 prompt 规则叫模型"先确认"，模型有时还是会直接下单，所以改用代码强制。

每个功能做三件事：
1. 用代码检查规则（营业时间、提前多久、名额……），不靠 AI 判断
2. 写进 Supabase 的 bookings 表（给程序用）
3. 同步到 Google Calendar（给门市同事看）
"""

import random
import string
from datetime import datetime, timedelta

from booking import rules
from booking.services import SERVICES, find_service
from integrations import google_calendar
from rag.store import supabase

TABLE = "bookings"


# ---------- 小工具 ----------
def _to_local(iso_text):
    return datetime.fromisoformat(iso_text).astimezone(rules.TIMEZONE)


def describe(row):
    """给 Agent 看的预约资料（时间换成吉隆坡时间）。"""
    start = _to_local(row["start_at"])
    return {
        "booking_code": row.get("booking_code"),
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "service": row["service"],
        "date": start.strftime("%Y-%m-%d"),
        "weekday": rules.WEEKDAY_NAMES[start.weekday()],
        "time": start.strftime("%H:%M"),
        "end_time": _to_local(row["end_at"]).strftime("%H:%M"),
        "beautician": row.get("beautician"),
        "notes": row.get("notes"),
        "status": row.get("status", "confirmed"),
    }


def _new_code():
    """预约编号，例如 GA-7K3M9Q（去掉容易看错的 0/O、1/I）。"""
    letters = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")
    while True:
        code = "GA-" + "".join(random.choices(letters, k=6))
        if not supabase.table(TABLE).select("id").eq("booking_code", code).execute().data:
            return code


def _count_overlapping(start, end, exclude_id=None):
    """这段时间里已经有几个有效的预约（时间有重叠就算）。"""
    query = (
        supabase.table(TABLE).select("id")
        .eq("status", "confirmed")
        .lt("start_at", end.isoformat())
        .gt("end_at", start.isoformat())
    )
    if exclude_id:
        query = query.neq("id", exclude_id)
    return len(query.execute().data)


def _error(message, **extra):
    return {"success": False, "error": message, **extra}


def _check_slot(start_text_date, start_text_time, duration, exclude_id=None):
    """检查日期时间格式、营业规则、名额。没问题返回 (start, end, None)。"""
    start = rules.parse_datetime(start_text_date, start_text_time)
    if not start:
        return None, None, _error("日期要是 YYYY-MM-DD，时间要是 HH:MM")
    reason = rules.check_start_time(start, duration)
    if reason:
        return None, None, _error(reason)
    end = start + timedelta(minutes=duration)
    if _count_overlapping(start, end, exclude_id) >= rules.CAPACITY:
        return None, None, _error("这个时段已经满了，请换一个时间")
    return start, end, None


def _get_verified(booking_code, phone):
    """用预约编号找预约，并核对手机号码（避免别人乱取消）。"""
    rows = supabase.table(TABLE).select("*").eq("booking_code", booking_code.strip().upper()).execute().data
    if not rows:
        return None, _error(f"找不到预约编号 {booking_code}")
    row = rows[0]
    if rules.normalize_phone(phone) != row["phone"]:
        return None, _error("手机号码和这笔预约的资料不符")
    if row["status"] != "confirmed":
        return None, _error("这笔预约已经取消了")
    return row, None


# ---------- 1. 查空档（只读） ----------
def check_availability(service, date):
    name = find_service(service)
    if not name:
        return _error(f"找不到服务「{service}」", available_services=list(SERVICES))
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return _error("日期格式要是 YYYY-MM-DD")

    duration = SERVICES[name]
    weekday = rules.WEEKDAY_NAMES[day.weekday()]
    if day.weekday() in rules.CLOSED_WEEKDAYS:
        return {"success": True, "service": name, "date": date, "weekday": weekday,
                "available_times": [], "reason": f"{weekday}中心休息"}

    available = []
    for start in rules.slots_for_day(day, duration):
        end = start + timedelta(minutes=duration)
        if _count_overlapping(start, end) < rules.CAPACITY:
            available.append(start.strftime("%H:%M"))

    return {"success": True, "service": name, "duration_minutes": duration,
            "date": date, "weekday": weekday, "available_times": available}


# ---------- 2. 查顾客的预约（只读） ----------
def find_bookings(phone):
    rows = (
        supabase.table(TABLE).select("*")
        .eq("phone", rules.normalize_phone(phone))
        .eq("status", "confirmed")
        .gte("start_at", rules.now().isoformat())
        .order("start_at")
        .execute().data
    )
    return {"success": True, "bookings": [describe(r) for r in rows]}


# ---------- 3. 新增预约 ----------
def prepare_booking(customer_name, phone, email, service, date, time, beautician=None, notes=None):
    """只检查，返回要写入的资料（还没有编号）。"""
    name = find_service(service)
    if not name:
        return None, _error(f"找不到服务「{service}」", available_services=list(SERVICES))

    phone = rules.normalize_phone(phone)
    if len(phone) < 9:
        return None, _error("手机号码好像不完整")
    if "@" not in email or "." not in email.split("@")[-1]:
        return None, _error("电邮地址格式不对")

    start, end, error = _check_slot(date, time, SERVICES[name])
    if error:
        return None, error

    row = {
        "customer_name": customer_name.strip(),
        "phone": phone,
        "email": email.strip(),
        "service": name,
        "beautician": beautician or None,
        "notes": notes or None,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
    }
    return row, None


def commit_booking(row):
    """真正写入。写入前再检查一次名额（顾客确认的这段时间，别人可能刚好订走了）。"""
    start, end = _to_local(row["start_at"]), _to_local(row["end_at"])
    if _count_overlapping(start, end) >= rules.CAPACITY:
        return _error("这个时段刚刚被订满了，请换一个时间")

    row = {**row, "booking_code": _new_code()}
    # 先写数据库，再写日历；日历失败就把数据库那笔删掉，两边保持一致
    saved = supabase.table(TABLE).insert(row).execute().data[0]
    try:
        event_id = google_calendar.create_event(row)
    except Exception as error:
        supabase.table(TABLE).delete().eq("id", saved["id"]).execute()
        return _error(f"Google Calendar 写入失败：{error}")
    saved = supabase.table(TABLE).update({"google_event_id": event_id}).eq("id", saved["id"]).execute().data[0]
    return {"success": True, "booking": describe(saved)}


def create_booking(**kwargs):
    """一步完成（不经过顾客确认）。只给测试和后台使用，Agent 不能直接调用。"""
    row, error = prepare_booking(**kwargs)
    return error or commit_booking(row)


# ---------- 4. 取消预约 ----------
def prepare_cancel(booking_code, phone):
    row, error = _get_verified(booking_code, phone)
    if error:
        return None, error
    return row, None


def commit_cancel(row):
    late = rules.hours_until(_to_local(row["start_at"])) < 24
    if row.get("google_event_id"):
        try:
            google_calendar.delete_event(row["google_event_id"])
        except Exception as error:
            return _error(f"Google Calendar 删除失败：{error}")

    updated = (
        supabase.table(TABLE)
        .update({"status": "cancelled", "updated_at": rules.now().isoformat()})
        .eq("id", row["id"]).execute().data[0]
    )
    return {"success": True, "booking": describe(updated), "within_24_hours": late}


def cancel_booking(booking_code, phone):
    """一步完成（测试和后台使用）。"""
    row, error = prepare_cancel(booking_code, phone)
    return error or commit_cancel(row)


# ---------- 5. 改期 ----------
def prepare_reschedule(booking_code, phone, new_date, new_time):
    row, error = _get_verified(booking_code, phone)
    if error:
        return None, error
    duration = SERVICES.get(row["service"], 60)
    start, end, error = _check_slot(new_date, new_time, duration, exclude_id=row["id"])
    if error:
        return None, error
    return {"row": row, "start_at": start.isoformat(), "end_at": end.isoformat()}, None


def commit_reschedule(plan):
    row = plan["row"]
    start, end = _to_local(plan["start_at"]), _to_local(plan["end_at"])
    if _count_overlapping(start, end, exclude_id=row["id"]) >= rules.CAPACITY:
        return _error("新的时段刚刚被订满了，请换一个时间")

    late = rules.hours_until(_to_local(row["start_at"])) < 24
    old = describe(row)
    if row.get("google_event_id"):
        try:
            google_calendar.update_event_time(row["google_event_id"], plan["start_at"], plan["end_at"])
        except Exception as error:
            return _error(f"Google Calendar 更新失败：{error}")

    updated = (
        supabase.table(TABLE)
        .update({"start_at": plan["start_at"], "end_at": plan["end_at"], "updated_at": rules.now().isoformat()})
        .eq("id", row["id"]).execute().data[0]
    )
    return {"success": True, "old": old, "booking": describe(updated), "within_24_hours": late}


def reschedule_booking(booking_code, phone, new_date, new_time):
    """一步完成（测试和后台使用）。"""
    plan, error = prepare_reschedule(booking_code, phone, new_date, new_time)
    return error or commit_reschedule(plan)
