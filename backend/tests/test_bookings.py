"""预约功能（booking/bookings.py）：用假的数据库和假的 Google Calendar

重点测"护栏"：手机号码核对、名额、日历失败回滚、prepare 不会写入资料。
"""

from booking import bookings
from tests.conftest import WEDNESDAY, at

CUSTOMER = dict(customer_name="陈小明", phone="012-888 6666", email="chen@example.com")


def book(service="男士护理", date=WEDNESDAY, time="15:00", **extra):
    return bookings.create_booking(**CUSTOMER, service=service, date=date, time=time, **extra)


def book_as(i, service="男士护理", date=WEDNESDAY, time="15:00", **extra):
    """用第 i 位顾客订（每人最多 3 笔未来预约，所以测名额时要用不同的人）。"""
    return bookings.create_booking(customer_name=f"顾客{i}", phone=f"01700000{i:02d}", email=f"c{i}@example.com",
                                   service=service, date=date, time=time, **extra)


def rows(db, table="bookings"):
    return db.tables[table].rows


# ---------- 新增 ----------
def test_prepare_only_checks_and_writes_nothing(db, calendar):
    row, error = bookings.prepare_booking(**CUSTOMER, service="男士护理", date=WEDNESDAY, time="15:00")
    assert error is None
    assert row["phone"] == "0128886666" and row["service"] == "男士护理"
    assert rows(db) == [] and calendar.created == []      # 只检查，什么都没写


def test_create_writes_database_and_calendar(db, calendar):
    result = book()
    assert result["success"]
    assert result["booking"]["booking_code"].startswith("GA-")
    assert len(rows(db)) == 1 and rows(db)[0]["google_event_id"] == "event-1"
    assert len(calendar.created) == 1


def test_calendar_failure_rolls_back_database(db, calendar):
    calendar.fail = True
    result = book()
    assert not result["success"] and result["error"] == bookings.SYSTEM_BUSY   # 不带内部细节
    assert rows(db) == []                                  # 数据库那笔被删掉，两边一致


def test_rejects_bad_input(db, calendar):
    assert "电邮" in bookings.create_booking(**{**CUSTOMER, "email": "not-an-email"},
                                            service="男士护理", date=WEDNESDAY, time="15:00")["error"]
    assert "手机" in bookings.create_booking(**{**CUSTOMER, "phone": "1234"},
                                            service="男士护理", date=WEDNESDAY, time="15:00")["error"]
    assert "找不到服务" in book(service="烫头发")["error"]
    assert "休息" in book(date="2026-11-16")["error"]
    assert rows(db) == []


def test_slot_full_when_no_facial_staff_left(db, calendar):
    """星期三会做脸的只有陈美玲、张雅婷（林嘉欣星期三不上班）→ 同一时段最多 2 位。"""
    first, second = book_as(1), book_as(2)
    assert {first["booking"]["beautician"], second["booking"]["beautician"]} == {"陈美玲", "张雅婷"}
    full = book_as(3)
    assert not full["success"] and "都没空" in full["error"]
    assert book_as(3, time="17:00")["success"]                 # 别的时段还可以订
    assert book_as(4, service="肩颈舒缓按摩")["success"]        # 身体护理是别的美容师，不受影响


def test_cancelled_bookings_do_not_take_a_slot(db, calendar):
    codes = [book_as(i)["booking"]["booking_code"] for i in (1, 2)]
    bookings.cancel_booking(codes[0], "0170000001")
    assert book_as(3)["success"]                               # 取消了一笔，就空出一位美容师


def test_commit_rechecks_capacity(db, calendar):
    """顾客在确认的那段时间，别人刚好把最后一位美容师订走了。"""
    row, _ = bookings.prepare_booking(**CUSTOMER, service="男士护理", date=WEDNESDAY, time="15:00")
    book_as(1), book_as(2)
    result = bookings.commit_booking(row)
    assert not result["success"] and "刚刚有人订走了" in result["error"]


# ---------- 取消 ----------
def test_cancel_requires_matching_phone(db, calendar):
    code = book()["booking"]["booking_code"]
    wrong = bookings.cancel_booking(code, "019 999 9999")
    assert not wrong["success"] and "不符" in wrong["error"]
    assert rows(db)[0]["status"] == "confirmed" and calendar.deleted == []


def test_cancel_accepts_phone_in_any_format(db, calendar):
    code = book()["booking"]["booking_code"]
    result = bookings.cancel_booking(code.lower(), "+60 12-888 6666")   # 编号小写、电话加国码也可以
    assert result["success"] and result["booking"]["status"] == "cancelled"
    assert calendar.deleted == ["event-1"]


def test_cannot_cancel_twice(db, calendar):
    code = book()["booking"]["booking_code"]
    bookings.cancel_booking(code, CUSTOMER["phone"])
    again = bookings.cancel_booking(code, CUSTOMER["phone"])
    assert not again["success"] and "已经取消" in again["error"]


def test_cancel_within_24_hours_is_flagged(db, calendar):
    late = book(date="2026-11-10", time="14:00")["booking"]["booking_code"]    # 今天下午
    early = book(date=WEDNESDAY, time="15:00")["booking"]["booking_code"]      # 明天下午（超过 24 小时）
    assert bookings.cancel_booking(late, CUSTOMER["phone"])["within_24_hours"] is True
    assert bookings.cancel_booking(early, CUSTOMER["phone"])["within_24_hours"] is False


# ---------- 改期 ----------
def test_reschedule_moves_database_and_calendar(db, calendar):
    code = book()["booking"]["booking_code"]
    result = bookings.reschedule_booking(code, CUSTOMER["phone"], "2026-11-12", "11:30")
    assert result["success"]
    assert result["old"]["time"] == "15:00" and result["booking"]["time"] == "11:30"
    assert calendar.updated[0][0] == "event-1"
    assert rows(db)[0]["start_at"] == at("2026-11-12", "11:30").isoformat()


def test_reschedule_checks_rules_and_phone(db, calendar):
    code = book()["booking"]["booking_code"]
    assert "休息" in bookings.reschedule_booking(code, CUSTOMER["phone"], "2026-11-16", "11:00")["error"]
    assert "不符" in bookings.reschedule_booking(code, "0199999999", "2026-11-12", "11:00")["error"]
    assert calendar.updated == []


def test_reschedule_does_not_count_itself(db, calendar):
    """15:00 两位美容师都在忙。其中一位客人改到 15:30：她自己原本占的位子要放出来，不能算她自己。"""
    code = book_as(1)["booking"]["booking_code"]
    book_as(2)
    assert bookings.reschedule_booking(code, "0170000001", WEDNESDAY, "15:30")["success"] is True


def test_reschedule_into_full_slot_is_rejected(db, calendar):
    mine = book(time="11:00")["booking"]["booking_code"]
    book_as(1, time="17:00"), book_as(2, time="17:00")
    result = bookings.reschedule_booking(mine, CUSTOMER["phone"], WEDNESDAY, "17:00")
    assert not result["success"] and "都没空" in result["error"]
    assert calendar.updated == []


def test_find_bookings_only_returns_upcoming_confirmed(db, calendar):
    keep = book()["booking"]["booking_code"]
    gone = book(time="16:00")["booking"]["booking_code"]
    bookings.cancel_booking(gone, CUSTOMER["phone"])
    book_other = bookings.create_booking(customer_name="别人", phone="0177777777", email="x@example.com",
                                         service="男士护理", date=WEDNESDAY, time="15:00")
    assert book_other["success"]
    found = bookings.find_bookings("+60 12 888 6666", CUSTOMER["email"])["bookings"]
    assert [b["booking_code"] for b in found] == [keep]
