"""营业规则（booking/rules.py）和服务名称（booking/services.py）

"现在"固定在 2026-11-10 星期二 09:00（见 conftest.py）。
"""

import pytest

from booking import rules
from booking.services import find_service
from tests.conftest import MONDAY, WEDNESDAY, at


# ---------- 开始时间合不合规则 ----------
@pytest.mark.parametrize("day, time, minutes, expected", [
    (MONDAY, "11:00", 60, "休息"),                       # 星期一休息
    (WEDNESDAY, "09:30", 60, "最早"),                    # 10:00 才开始
    (WEDNESDAY, "15:15", 60, "整点或半点"),              # 每 30 分钟一个时段
    (WEDNESDAY, "18:30", 150, "18:00"),                  # 90 分钟以上最后 18:00
    ("2026-11-10", "10:30", 60, "2 小时"),               # 最迟 2 小时前预约
    ("2027-01-20", "11:00", 60, "60 天"),                # 最远 60 天后
])
def test_rejects_invalid_start_time(day, time, minutes, expected):
    reason = rules.check_start_time(at(day, time), minutes)
    assert reason and expected in reason


@pytest.mark.parametrize("day, time, minutes", [
    (WEDNESDAY, "10:00", 60),          # 最早
    (WEDNESDAY, "18:30", 60),          # 一般护理最后时段
    (WEDNESDAY, "18:00", 150),         # 长护理最后时段
    ("2026-11-10", "11:00", 60),       # 刚好 2 小时后
])
def test_accepts_valid_start_time(day, time, minutes):
    assert rules.check_start_time(at(day, time), minutes) is None


# ---------- 一天有哪些时段 ----------
def test_monday_has_no_slots():
    assert rules.slots_for_day(at(MONDAY, "10:00").date(), 60) == []


def test_slots_for_normal_and_long_treatment():
    day = at(WEDNESDAY, "10:00").date()
    normal = [s.strftime("%H:%M") for s in rules.slots_for_day(day, 60)]
    long = [s.strftime("%H:%M") for s in rules.slots_for_day(day, 150)]
    assert normal[0] == "10:00" and normal[-1] == "18:30" and len(normal) == 18
    assert long[-1] == "18:00" and len(long) == 17


def test_today_slots_start_two_hours_from_now():
    today = [s.strftime("%H:%M") for s in rules.slots_for_day(at("2026-11-10", "10:00").date(), 60)]
    assert today[0] == "11:00"


# ---------- 小工具 ----------
@pytest.mark.parametrize("raw", ["+60 12-386 2016", "012 386 2016", "60123862016", "0123862016"])
def test_normalize_phone(raw):
    assert rules.normalize_phone(raw) == "0123862016"


def test_parse_datetime_rejects_bad_format():
    assert rules.parse_datetime("18/11/2026", "3pm") is None


# ---------- 服务名称 ----------
@pytest.mark.parametrize("name, expected", [
    ("半永久雾眉", "半永久雾眉"),           # 完全一样
    ("雾眉", "半永久雾眉"),                 # 顾客的简称
    ("睫毛嫁接", None),                     # 有自然款和浓密款，不能乱猜
    ("烫头发", None),                       # 没有这项服务
])
def test_find_service(name, expected):
    assert find_service(name) == expected
