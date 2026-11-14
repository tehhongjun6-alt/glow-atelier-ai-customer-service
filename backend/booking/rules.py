"""预约规则（来自知识库的"营业时间""可预约时段"）

- 星期二至星期日 10:00 开始营业，星期一休息；部分公共假期休息（CLOSED_DATES）
- 每 30 分钟一个时段
- 最后预约时间 18:30；90 分钟以上的护理最后是 18:00
- 最迟要在 2 小时前预约，最远可以预约 60 天后
- 同一个时段最多 8 位顾客（6 间护理室 + 美甲区），而且每位顾客要有一位会做这项服务、有上班、有空的美容师

这些规则用代码检查，不交给 AI 判断：AI 可能算错日期或忘记规则，代码不会。
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
OPEN_TIME = time(10, 0)
LAST_START = time(18, 30)
LAST_START_LONG = time(18, 0)     # 90 分钟以上的护理
LONG_TREATMENT_MINUTES = 90
SLOT_MINUTES = 30
MIN_HOURS_AHEAD = 2
MAX_DAYS_AHEAD = 60
CAPACITY = 8                      # 同一时间最多几位顾客（知识库：6 间护理室 + 美甲区 = 8 位）
                                  # 实际上通常是美容师先不够，见 bookings._pick_beautician
CLOSED_WEEKDAYS = {0}             # 0 = 星期一（Python 的 weekday()）

# 公共假期休息日（知识库「公共假期营业安排」：农历新年初一至初三、开斋节两天、屠妖节、圣诞节）。
# ⚠️ 开斋节、屠妖节的日期每年不同，要等官方公布后再加进来；每年年底要更新这份清单。
# 只能预约 60 天内的日子，所以清单只需要涵盖接下来一年。
CLOSED_DATES = {
    "2026-12-25": "圣诞节",
    "2027-02-06": "农历新年初一",
    "2027-02-07": "农历新年初二",
    "2027-02-08": "农历新年初三",
    "2027-12-25": "圣诞节",
}

WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def now():
    return datetime.now(TIMEZONE)


def last_start_for(duration):
    return LAST_START_LONG if duration >= LONG_TREATMENT_MINUTES else LAST_START


def closed_reason(day):
    """这一天休息的话，返回原因；有营业就返回 None。"""
    if day.weekday() in CLOSED_WEEKDAYS:
        return f"{day:%Y-%m-%d} 是{WEEKDAY_NAMES[day.weekday()]}，中心休息"
    holiday = CLOSED_DATES.get(f"{day:%Y-%m-%d}")
    if holiday:
        return f"{day:%Y-%m-%d} 是{holiday}，中心休息"
    return None


def slots_for_day(day, duration):
    """一天里所有可以开始的时间（还没扣掉已经被订满的）。"""
    if closed_reason(day):
        return []
    start = datetime.combine(day, OPEN_TIME, TIMEZONE)
    last = datetime.combine(day, last_start_for(duration), TIMEZONE)
    earliest = now() + timedelta(hours=MIN_HOURS_AHEAD)

    slots = []
    while start <= last:
        if start >= earliest:
            slots.append(start)
        start += timedelta(minutes=SLOT_MINUTES)
    return slots


def check_start_time(start, duration):
    """检查一个开始时间合不合规则。合规返回 None，不合规返回原因（中文）。"""
    closed = closed_reason(start.date())
    if closed:
        return closed
    if start.minute % SLOT_MINUTES != 0:
        return f"预约时间要在整点或半点开始（每 {SLOT_MINUTES} 分钟一个时段）"
    if start.time() < OPEN_TIME:
        return f"最早 {OPEN_TIME:%H:%M} 开始"
    if start.time() > last_start_for(duration):
        return f"这项护理最后预约时间是 {last_start_for(duration):%H:%M}"
    if start < now() + timedelta(hours=MIN_HOURS_AHEAD):
        return f"最迟要在 {MIN_HOURS_AHEAD} 小时前预约"
    if start > now() + timedelta(days=MAX_DAYS_AHEAD):
        return f"最远只能预约 {MAX_DAYS_AHEAD} 天后"
    return None


def parse_datetime(date_text, time_text):
    """'2026-11-12' + '15:00' → 吉隆坡时区的 datetime。格式错了返回 None。"""
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
    except ValueError:
        return None


def hours_until(start):
    return (start - now()).total_seconds() / 3600


def normalize_phone(phone):
    """只留数字，统一成 0 开头：'+60 12-386 2016' → '0123862016'。"""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("60"):
        digits = "0" + digits[2:]
    return digits
