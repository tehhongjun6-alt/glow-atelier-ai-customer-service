"""第 4 步：预约功能（查空档、新增、查询、取消、改期）

每个会改资料的功能都拆成两半：
- prepare_*：只检查（规则、名额、手机号码），不改任何东西
- commit_*：真正写进 Supabase 和 Google Calendar

为什么要拆开？因为 Agent 要"先给顾客确认，顾客同意了才执行"（两段式确认，见 agent/actions.py）。
测试发现：只靠 prompt 规则叫模型"先确认"，模型有时还是会直接下单，所以改用代码强制。

每个功能做三件事：
1. 用代码检查规则（营业时间、美容师排班、名额……），不靠 AI 判断
2. 写进 Supabase 的 bookings 表（给程序用）
3. 同步到 Google Calendar（给门市同事看）

防滥用（网站对外开放才会遇到的问题，知识库不会写）：
- 同一个电话或电邮，最多 MAX_ACTIVE_BOOKINGS 笔未来的预约（防止把时段订满）
- 姓名、备注不能有网址、特殊符号，有长度上限（防止公开日历和通知信被塞广告、钓鱼连结）
- 防重复预约由数据库保证（同一位美容师、同一段时间只能有一笔），开多台服务器也安全
- 通知信每个电邮每天有上限（见 booking/notifications.py）
"""

import logging
import random
import re
import string
from datetime import datetime, timedelta

from langfuse import get_client, observe
from postgrest.exceptions import APIError

from booking import rules
from booking.services import (BEAUTICIANS, SERVICE_STAFF, SERVICES, SHOWER_ROOMS, SHOWER_SERVICES,
                              beautician_works_on, find_beautician, find_service, pregnancy_problem)
from integrations import google_calendar
from rag.store import supabase

TABLE = "bookings"
log = logging.getLogger("glow.bookings")

# 系统出错时给 AI（也就是给顾客）看的话。真正的原因只写进后端日志，不让顾客看到内部细节。
SYSTEM_BUSY = "系统暂时无法完成这个操作，请稍后再试，或直接联系门市同事。"

MAX_ACTIVE_BOOKINGS = 10       # 同一个电话或电邮最多几笔未来的预约（系统防滥用设定，不是公司公开的规定）
MAX_NAME_LENGTH = 30
MAX_NOTES_LENGTH = 100
URL_PATTERN = re.compile(r"https?://|www\.|\b[\w-]+\.(com|net|org|my|io|xyz|top|cn|link|info|biz|co|me|ly)\b", re.I)
SPECIAL_CHARACTERS = re.compile(r"[<>{}\[\]\\|`$]")

# 防重复预约：交给数据库（sql/007_no_double_booking.sql）
# 以前用程序里的锁（threading.Lock），但锁只在"一台服务器"有效；开两台就各锁各的，还是会重复。
# 现在数据库规定"同一位美容师、同一段时间只能有一笔有效预约"，两台服务器同时写，只有一台会成功，
# 另一台会收到错误代码 23P01 → 重新排一位美容师，或告诉顾客"刚刚被订走了"。
DOUBLE_BOOKING = "23P01"   # Postgres 的 exclusion_violation
MAX_PICK_ATTEMPTS = 3


def _is_double_booking(error):
    return isinstance(error, APIError) and error.code == DOUBLE_BOOKING


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


def _error(message, **extra):
    return {"success": False, "error": message, **extra}


def _overlapping(start, end, exclude_id=None):
    """这段时间里所有有效的预约（时间有重叠就算）。"""
    query = (
        supabase.table(TABLE).select("*")
        .eq("status", "confirmed")
        .lt("start_at", end.isoformat())
        .gt("end_at", start.isoformat())
    )
    if exclude_id:
        query = query.neq("id", exclude_id)
    return query.execute().data


# ---------- 排班：照美容师来排，不是只看房间 ----------
def _pick_beautician(service, start, end, exclude_id=None, preferred=None):
    """找一位会做这项服务、当天有上班、这个时段没在接客的美容师。

    preferred：顾客指定的美容师（一定要是她）。
    返回 (美容师名字, None)；排不进去就返回 (None, 原因)。
    """
    rows = _overlapping(start, end, exclude_id)
    if len(rows) >= rules.CAPACITY:
        return None, "这个时段已经满了，请换一个时间"
    if service in SHOWER_SERVICES and sum(r["service"] in SHOWER_SERVICES for r in rows) >= SHOWER_ROOMS:
        return None, f"有淋浴间的房间只有 {SHOWER_ROOMS} 间，这个时段已经满了，请换一个时间"

    busy = {r["beautician"] for r in rows if r.get("beautician")}
    staff = SERVICE_STAFF.get(service, list(BEAUTICIANS))
    weekday = start.weekday()

    if preferred:
        if preferred not in staff:
            return None, f"{preferred} 不负责「{service}」，可以做的美容师是：{'、'.join(staff)}"
        if not beautician_works_on(preferred, weekday):
            days = "、".join(rules.WEEKDAY_NAMES[d] for d in sorted(BEAUTICIANS[preferred][1]))
            return None, f"{preferred} 只在{days}接预约"
        if preferred in busy:
            return None, f"{preferred} 这个时段已经有预约了，请换时间或换一位美容师"
        return preferred, None

    for name in staff:
        if beautician_works_on(name, weekday) and name not in busy:
            return name, None
    return None, f"这个时段会做「{service}」的美容师都没空，请换一个时间"


def _check_slot(date_text, time_text, service, exclude_id=None, preferred=None):
    """检查日期时间格式、营业规则（含公共假期）、名额、美容师。
    没问题返回 (start, end, 分配到的美容师, None)。"""
    start = rules.parse_datetime(date_text, time_text)
    if not start:
        return None, None, None, _error("日期要是 YYYY-MM-DD，时间要是 HH:MM")
    duration = SERVICES.get(service, 60)
    reason = rules.check_start_time(start, duration)
    if reason:
        return None, None, None, _error(reason)
    end = start + timedelta(minutes=duration)
    beautician, problem = _pick_beautician(service, start, end, exclude_id, preferred)
    if problem:
        return None, None, None, _error(problem)
    return start, end, beautician, None


def _check_text(customer_name, notes):
    """姓名和备注会出现在公开日历和通知信里，不能塞网址、特殊符号或很长的文字。"""
    name = (customer_name or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        return f"姓名要是 1–{MAX_NAME_LENGTH} 个字"
    if URL_PATTERN.search(name) or SPECIAL_CHARACTERS.search(name):
        return "姓名不能包含网址或特殊符号"
    if notes:
        if len(notes) > MAX_NOTES_LENGTH:
            return f"备注最多 {MAX_NOTES_LENGTH} 个字"
        if URL_PATTERN.search(notes) or SPECIAL_CHARACTERS.search(notes):
            return "备注不能包含网址或特殊符号"
    return None


def _active_bookings(phone, email):
    """这个电话或电邮有几笔未来的有效预约。"""
    future = rules.now().isoformat()
    by_phone = (supabase.table(TABLE).select("id").eq("status", "confirmed")
                .eq("phone", phone).gte("start_at", future).execute().data)
    by_email = (supabase.table(TABLE).select("id").eq("status", "confirmed")
                .eq("email", email).gte("start_at", future).execute().data)
    return len({r["id"] for r in by_phone + by_email})


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
    # 预约时间已经过了，就不能再改期或取消（不然缺席之后可以用"改期"躲掉缺席的处罚）
    if _to_local(row["start_at"]) <= rules.now():
        return None, _error("这个预约的时间已经过了，不能再改期或取消。如有疑问，请联系门市同事。",
                            needs_staff=True)
    return row, None


# ---------- 1. 查空档（只读） ----------
def check_availability(service, date, beautician=None):
    name = find_service(service)
    if not name:
        return _error(f"找不到服务「{service}」", available_services=list(SERVICES))
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return _error("日期格式要是 YYYY-MM-DD")
    preferred = None
    if beautician:
        preferred = find_beautician(beautician)
        if not preferred:
            return _error(f"没有这位美容师「{beautician}」", beauticians=list(BEAUTICIANS))

    duration = SERVICES[name]
    weekday = rules.WEEKDAY_NAMES[day.weekday()]
    closed = rules.closed_reason(day)
    if closed:
        return {"success": True, "service": name, "date": date, "weekday": weekday,
                "available_times": [], "reason": closed}

    available = []
    for start in rules.slots_for_day(day, duration):
        end = start + timedelta(minutes=duration)
        assigned, _ = _pick_beautician(name, start, end, preferred=preferred)
        if assigned:
            available.append(start.strftime("%H:%M"))

    return {"success": True, "service": name, "duration_minutes": duration,
            "date": date, "weekday": weekday, "available_times": available}


# ---------- 2. 查顾客的预约（只读） ----------
def _mask_email(email):
    """chen.demo@example.com → ch***@example.com"""
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


def _mask_phone(phone):
    """0128886666 → 012-***6666"""
    return f"{phone[:3]}-***{phone[-4:]}" if len(phone) >= 7 else "***"


def find_bookings(phone, email):
    """顾客忘了预约编号时用：电话和电邮都要和预约时登记的一样，才查得到。

    只用电话就能查的话，知道别人电话的人就能拿到预约编号，再用"编号 + 电话"取消别人的预约。
    查不到时，不说是"电话不对"还是"电邮不对"，避免被人一个一个试出来。
    """
    rows = (
        supabase.table(TABLE).select("*")
        .eq("phone", rules.normalize_phone(phone))
        .eq("status", "confirmed")
        .gte("start_at", rules.now().isoformat())
        .order("start_at")
        .execute().data
    )
    matched = [r for r in rows if r["email"].strip().lower() == str(email).strip().lower()]
    if not matched:
        return {"success": True, "bookings": [],
                "note": "没有找到符合的预约。电话和电邮都要和预约时登记的一样。"}
    found = []
    for r in matched:
        b = describe(r)
        b["phone"], b["email"] = _mask_phone(b["phone"]), _mask_email(b["email"])
        found.append(b)
    return {"success": True, "bookings": found}


# ---------- 3. 新增预约 ----------
def prepare_booking(customer_name, phone, email, service, date, time, beautician=None, notes=None,
                    pregnancy_weeks=None):
    """只检查，返回要写入的资料（还没有编号）。

    pregnancy_weeks：顾客怀孕几周（没怀孕就不填）。有健康风险，所以由代码检查禁做项目。
    beautician：顾客指定的美容师；没指定就由系统分配一位有空的。
    """
    name = find_service(service)
    if not name:
        return None, _error(f"找不到服务「{service}」", available_services=list(SERVICES))

    problem = _check_text(customer_name, notes)
    if problem:
        return None, _error(problem)

    weeks = int(pregnancy_weeks) if pregnancy_weeks else 0
    if name == "孕妇按摩" and not weeks:
        return None, _error("预约孕妇按摩需要先知道怀孕几周（要 16 周以上）")
    problem = pregnancy_problem(name, weeks)
    if problem:
        return None, _error(problem, needs_staff="联系门市" in problem)

    preferred = None
    if beautician:
        preferred = find_beautician(beautician)
        if not preferred:
            return None, _error(f"没有这位美容师「{beautician}」", beauticians=list(BEAUTICIANS))

    phone = rules.normalize_phone(phone)
    if len(phone) < 9:
        return None, _error("手机号码好像不完整")
    email = (email or "").strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        return None, _error("电邮地址格式不对")
    if _active_bookings(phone, email) >= MAX_ACTIVE_BOOKINGS:
        return None, _error(f"同一个电话或电邮最多只能有 {MAX_ACTIVE_BOOKINGS} 笔未来的预约，"
                            "请先取消不需要的预约，或联系门市同事。", needs_staff=True)

    start, end, assigned, error = _check_slot(date, time, name, preferred=preferred)
    if error:
        return None, error

    if weeks:   # 怀孕周数写进备注，同事一看就知道
        notes = f"怀孕 {weeks} 周" + (f"；{notes}" if notes else "")

    row = {
        "customer_name": customer_name.strip(),
        "phone": phone,
        "email": email,
        "service": name,
        "beautician": assigned,
        "notes": notes or None,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "_preferred": preferred,        # 内部用：顾客有没有指定美容师（写入前会拿掉）
    }
    return row, None


@observe(name="save-booking")   # Observability：在 Langfuse 步骤树里显示"写数据库 + 写日历"
def commit_booking(row):
    """真正写入。写入前再排一次班（顾客确认的这段时间，别人可能刚好订走了）。"""
    row = dict(row)
    preferred = row.pop("_preferred", None)
    start, end = _to_local(row["start_at"]), _to_local(row["end_at"])

    for _ in range(MAX_PICK_ATTEMPTS):
        assigned, problem = _pick_beautician(row["service"], start, end, preferred=preferred)
        if problem:
            return _error(f"刚刚有人订走了：{problem}")
        row["beautician"] = assigned
        row["booking_code"] = _new_code()
        try:
            # 先写数据库，再写日历；日历失败就把数据库那笔删掉，两边保持一致
            saved = supabase.table(TABLE).insert(row).execute().data[0]
            break
        except APIError as error:
            if not _is_double_booking(error):
                raise
            # 检查完到写入之间，别的服务器刚好订走了这位美容师 → 数据库挡下，重新排一次
            log.info("美容师 %s 在 %s 刚被订走（数据库挡下），重新排班", assigned, start.strftime("%Y-%m-%d %H:%M"))
    else:
        return _error("刚刚有人订走了，这个时段已经满了，请换一个时间")

    try:
        event_id = google_calendar.create_event(row)
    except Exception as error:
        # 数据库已经存好，预约就算成功；日历交给背景程序补做（以前是删掉预约、叫顾客重订）
        _calendar_failed(saved["id"], "新增", error)
        return {"success": True, "booking": describe(saved)}
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


@observe(name="cancel-booking")
def commit_cancel(row):
    late = rules.hours_until(_to_local(row["start_at"])) < 24
    # 先改数据库，再删日历：日历删除失败的话，日历上多一笔"已取消"的活动，比反过来安全
    updated = (
        supabase.table(TABLE)
        .update({"status": "cancelled", "updated_at": rules.now().isoformat()})
        .eq("id", row["id"]).execute().data[0]
    )
    if row.get("google_event_id"):
        try:
            google_calendar.delete_event(row["google_event_id"])
        except Exception as error:
            _calendar_failed(row["id"], "删除", error)
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

    # 知识库：24 小时内每个预约可以免费改一次；第二次会被当作取消
    late = rules.hours_until(_to_local(row["start_at"])) < 24
    if late and (row.get("late_changes") or 0) >= 1:
        return None, _error("这个预约在 24 小时内已经免费改过一次，按规定再改会被当作取消处理。"
                            "请联系门市同事处理。", needs_staff=True)

    # 先试原本的美容师；她没空就分配另一位会做这项服务的
    start, end, assigned, error = _check_slot(new_date, new_time, row["service"], row["id"],
                                              preferred=None)
    if error:
        return None, error
    keep, _ = _pick_beautician(row["service"], start, end, row["id"], preferred=row.get("beautician")) \
        if row.get("beautician") else (None, None)
    beautician = keep or assigned
    return {"row": row, "start_at": start.isoformat(), "end_at": end.isoformat(),
            "beautician": beautician, "late": late}, None


@observe(name="reschedule-booking")
def commit_reschedule(plan):
    row = plan["row"]
    start, end = _to_local(plan["start_at"]), _to_local(plan["end_at"])
    _, problem = _pick_beautician(row["service"], start, end, row["id"], preferred=plan["beautician"])
    if problem:
        return _error(f"刚刚有人订走了：{problem}")
    changes = {"start_at": plan["start_at"], "end_at": plan["end_at"], "beautician": plan["beautician"],
               "updated_at": rules.now().isoformat()}
    if plan["late"]:
        changes["late_changes"] = (row.get("late_changes") or 0) + 1
    old = describe(row)
    try:
        updated = supabase.table(TABLE).update(changes).eq("id", row["id"]).execute().data[0]
    except APIError as error:
        if not _is_double_booking(error):
            raise
        log.info("改期：美容师 %s 在 %s 刚被订走（数据库挡下）", plan["beautician"], start.strftime("%Y-%m-%d %H:%M"))
        return _error("刚刚有人订走了这个时段，请换一个时间")

    if row.get("google_event_id"):
        try:
            google_calendar.update_event_time(row["google_event_id"], plan["start_at"], plan["end_at"])
        except Exception as error:
            _calendar_failed(row["id"], "改期", error)
    return {"success": True, "old": old, "booking": describe(updated), "within_24_hours": plan["late"]}


def reschedule_booking(booking_code, phone, new_date, new_time):
    """一步完成（测试和后台使用）。"""
    plan, error = prepare_reschedule(booking_code, phone, new_date, new_time)
    return error or commit_reschedule(plan)


# ---------- 6. 日历自动补做（sql/008_calendar_retry.sql） ----------
# 数据库是"真正的资料"，Google Calendar 只是给门市看的副本。
# 日历失败时先标成"待补做"，背景程序（booking/calendar_sync.py）每分钟照数据库的样子补上去。
CALENDAR_MAX_ATTEMPTS = 10


def _calendar_failed(booking_id, action, error):
    """日历操作失败：记下来等背景程序补做。顾客看到的结果不受影响。"""
    supabase.table(TABLE).update({"calendar_pending": True, "calendar_error": f"{action}：{error}"[:300]})         .eq("id", booking_id).execute()
    log.warning("Google Calendar %s失败，已排进自动补做（预约 id %s）：%s", action, booking_id, error)
    get_client().update_current_span(level="WARNING", status_message=f"日历{action}失败，稍后自动补做")


def _sync_one(row):
    """让日历和数据库一致：有效预约 → 日历要有、时间要对；已取消 → 日历要删掉。"""
    changes = {"calendar_pending": False, "calendar_attempts": 0, "calendar_error": None}
    if row["status"] == "confirmed":
        if row.get("google_event_id"):
            google_calendar.update_event_time(row["google_event_id"], row["start_at"], row["end_at"])
        else:
            changes["google_event_id"] = google_calendar.create_event(row)
    elif row.get("google_event_id"):
        try:
            google_calendar.delete_event(row["google_event_id"])
        except Exception as error:
            if getattr(getattr(error, "resp", None), "status", None) not in (404, 410):   # 已经不在日历上 = 已经做好
                raise
        changes["google_event_id"] = None
    supabase.table(TABLE).update(changes).eq("id", row["id"]).execute()


def sync_calendar_once():
    """补做所有"日历待补做"的预约。返回 (成功几笔, 失败几笔)。"""
    rows = supabase.table(TABLE).select("*").eq("calendar_pending", True).execute().data
    done = failed = 0
    for row in rows:
        if row["calendar_attempts"] >= CALENDAR_MAX_ATTEMPTS:
            continue                    # 已经放弃，等工程师处理（log 里有 ERROR）
        try:
            _sync_one(row)
            done += 1
            log.info("日历补做成功：%s", row["booking_code"])
        except Exception as error:
            failed += 1
            attempts = row["calendar_attempts"] + 1
            supabase.table(TABLE).update({"calendar_attempts": attempts, "calendar_error": str(error)[:300]})                 .eq("id", row["id"]).execute()
            if attempts >= CALENDAR_MAX_ATTEMPTS:
                log.error("日历补做失败 %d 次，放弃，需要人工处理：%s（%s）", attempts, row["booking_code"], error)
            else:
                log.warning("日历补做失败（第 %d 次），下一轮再试：%s（%s）", attempts, row["booking_code"], error)
    return done, failed
