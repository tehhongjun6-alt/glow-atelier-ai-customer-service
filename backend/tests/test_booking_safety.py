"""预约的三个护栏漏洞（修正后）

1. 孕妇订到禁做项目（健康风险）：代码检查，而且对话提到怀孕就一定要先问周数
2. 公共假期：圣诞节、农历新年初一至初三休息
3. 美容师：上班日（林嘉欣只在星期二、四、六）、同一时段不能接两位客人、名字要对得上

"现在"固定在 2026-11-10 星期二 09:00（conftest.py）。
"""

from types import SimpleNamespace

import pytest

from agent import tools
from agent.actions import ActionGate
from booking import bookings, rules
from tests.conftest import WEDNESDAY, at

THURSDAY = "2026-11-12"
CUSTOMER = dict(customer_name="Amy Lee", phone="0123334444", email="amy@example.com")


def book(service="水光补湿护理", date=WEDNESDAY, time="15:00", **extra):
    return bookings.create_booking(**CUSTOMER, service=service, date=date, time=time, **extra)


# ---------- 1. 怀孕 ----------
@pytest.mark.parametrize("service", ["RF 射频紧致护理", "IPL 光子脱毛", "半永久雾眉", "维他命 C 亮肤护理",
                                     "痘痘肌净化护理", "芳疗全身按摩（60 分钟）"])
def test_pregnant_customer_cannot_book_forbidden_service(db, calendar, service):
    result = book(service=service, pregnancy_weeks=20)
    assert not result["success"] and "怀孕期间不能做" in result["error"]
    assert db.tables["bookings"].rows == [] and calendar.created == []


def test_prenatal_massage_needs_16_weeks(db, calendar):
    assert "16 周以上" in book(service="孕妇按摩", pregnancy_weeks=12)["error"]
    assert "怀孕几周" in book(service="孕妇按摩")["error"]                      # 没给周数也不行
    ok = book(service="孕妇按摩", pregnancy_weeks=20)
    assert ok["success"] and ok["booking"]["notes"].startswith("怀孕 20 周")   # 周数写进备注给同事看


def test_pregnant_customer_can_book_allowed_service(db, calendar):
    assert book(service="水光补湿护理", pregnancy_weeks=10)["success"]


def test_unlisted_service_needs_staff_confirmation(db, calendar):
    """资料没写孕妇能不能做的项目：不让 AI 决定，请顾客联系门市。"""
    result = book(service="男士护理", pregnancy_weeks=20)
    assert not result["success"] and "联系门市" in result["error"]


def fake_agent(*customer_lines):
    lines = [{"role": "customer", "text": t} for t in customer_lines]
    return SimpleNamespace(gate=ActionGate(), customer_email=None, transcript=lambda: lines)


@pytest.mark.parametrize("message", ["我怀孕 5 个月了", "I'm pregnant, can I book?", "saya mengandung 20 minggu"])
def test_tool_requires_weeks_when_pregnancy_is_mentioned(db, calendar, message):
    """硬护栏：顾客提过怀孕，AI 却没填周数 → 不让它暂存。"""
    agent = fake_agent(message, "帮我订明天下午 3 点的水光补湿")
    result, _ = tools.request_booking(agent, **CUSTOMER, service="水光补湿护理", date=WEDNESDAY, time="15:00")
    assert not result["success"] and "怀孕几周" in result["error"]
    assert agent.gate.pending is None


def test_tool_blocks_forbidden_service_even_if_weeks_given(db, calendar):
    agent = fake_agent("我怀孕 20 周，想做 RF")
    result, _ = tools.request_booking(agent, **CUSTOMER, service="RF 射频紧致护理", date=WEDNESDAY,
                                      time="15:00", pregnancy_weeks=20)
    assert not result["success"] and agent.gate.pending is None


def test_tool_does_not_bother_non_pregnant_customers(db, calendar):
    agent = fake_agent("帮我订明天下午 3 点的 RF")
    result, _ = tools.request_booking(agent, **CUSTOMER, service="RF 射频紧致护理", date=WEDNESDAY, time="15:00")
    assert result["status"] == "waiting_for_customer_confirmation"


# ---------- 2. 公共假期 ----------
def test_christmas_is_closed(db, calendar):
    assert "圣诞节" in rules.check_start_time(at("2026-12-25", "11:00"), 60)
    assert rules.slots_for_day(at("2026-12-25", "11:00").date(), 60) == []
    availability = bookings.check_availability("水光补湿护理", "2026-12-25")
    assert availability["available_times"] == [] and "圣诞节" in availability["reason"]
    assert "圣诞节" in book(date="2026-12-25", time="11:00")["error"]


@pytest.mark.parametrize("day", ["2027-02-06", "2027-02-07", "2027-02-08"])
def test_chinese_new_year_is_closed(day):
    # 初三（2027-02-08）刚好是星期一，原因会写"星期一"；不管哪个原因，重点是那天休息
    assert rules.closed_reason(at(day, "10:00").date()) is not None


def test_normal_day_is_open():
    assert rules.closed_reason(at(WEDNESDAY, "10:00").date()) is None


# ---------- 3. 美容师 ----------
def test_founder_only_on_tue_thu_sat(db, calendar):
    wednesday = book(beautician="林嘉欣")
    assert not wednesday["success"] and "星期二、星期四、星期六" in wednesday["error"]
    thursday = book(date=THURSDAY, beautician="Jessica")             # 英文名也认得
    assert thursday["success"] and thursday["booking"]["beautician"] == "林嘉欣"


def test_unknown_beautician_is_rejected(db, calendar):
    result = book(beautician="小美")
    assert not result["success"] and "林嘉欣" in result["beauticians"]


def test_same_beautician_cannot_take_overlapping_bookings(db, calendar):
    assert book(time="15:00", beautician="陈美玲")["success"]
    other = dict(customer_name="Ben", phone="0177777777", email="ben@example.com")
    clash = bookings.create_booking(**other, service="水光补湿护理", date=WEDNESDAY, time="15:30", beautician="Melissa")
    assert not clash["success"] and "已经有预约" in clash["error"]
    ok = bookings.create_booking(**other, service="水光补湿护理", date=WEDNESDAY, time="15:30", beautician="张雅婷")
    assert ok["success"]                                            # 换一位就可以


def test_beautician_must_do_that_service(db, calendar):
    result = book(service="睫毛自然卷翘", beautician="陈美玲")
    assert not result["success"] and "Nur Aisyah" in result["error"]


def test_reschedule_reassigns_when_beautician_is_off(db, calendar):
    """原本的美容师新日子不上班：改派另一位会做这项服务的美容师。"""
    code = book(date=THURSDAY, beautician="林嘉欣")["booking"]["booking_code"]
    moved = bookings.reschedule_booking(code, CUSTOMER["phone"], WEDNESDAY, "15:00")
    assert moved["success"] and moved["booking"]["beautician"] != "林嘉欣"


# ---------- 怀孕周数必须是顾客自己说的（测试时 AI 真的编了"16 周"） ----------
@pytest.mark.parametrize("said, weeks, allowed", [
    ("我怀孕了", 16, False),                 # 顾客没说周数，AI 自己编 → 拒绝
    ("我怀孕 20 周", 16, False),             # 说的和填的对不上 → 拒绝
    ("我怀孕 20 周", 20, True),
    ("怀孕五个月了", 20, True),              # 5 个月 = 20–24 周
    ("怀孕五个月了", 22, True),
    ("怀孕五个月了", 30, False),
    ("I'm 18 weeks pregnant", 18, True),
    ("saya mengandung 5 bulan", 21, True),
])
def test_pregnancy_weeks_must_come_from_customer(db, calendar, said, weeks, allowed):
    agent = fake_agent(said, "帮我订明天下午 3 点的水光补湿")
    result, _ = tools.request_booking(agent, **CUSTOMER, service="水光补湿护理", date=WEDNESDAY,
                                      time="15:00", pregnancy_weeks=weeks)
    assert result.get("status") == ("waiting_for_customer_confirmation" if allowed else None)
