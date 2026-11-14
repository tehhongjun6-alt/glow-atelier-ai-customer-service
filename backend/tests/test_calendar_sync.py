"""日历自动补做（booking/bookings.py 的 sync_calendar_once + sql/008_calendar_retry.sql）

情境：Google Calendar 暂时连不上 → 预约照样成功、标成"待补做" → Google 恢复后，背景程序补上去。
"""

import logging
from types import SimpleNamespace

from booking import bookings
from tests.conftest import WEDNESDAY
from tests.test_bookings import CUSTOMER, book, rows


def pending(db):
    return [r for r in rows(db) if r["calendar_pending"]]


def test_new_booking_is_added_to_calendar_later(db, calendar):
    calendar.fail = True
    book()
    assert len(pending(db)) == 1 and calendar.created == []
    assert bookings.sync_calendar_once() == (0, 1)               # Google 还没恢复：这一轮失败
    assert rows(db)[0]["calendar_attempts"] == 1

    calendar.fail = False                                        # Google 恢复了
    assert bookings.sync_calendar_once() == (1, 0)
    row = rows(db)[0]
    assert row["google_event_id"] == "event-1" and not row["calendar_pending"]
    assert row["calendar_attempts"] == 0 and row["calendar_error"] is None


def test_cancel_is_removed_from_calendar_later(db, calendar):
    code = book()["booking"]["booking_code"]
    calendar.fail = True
    assert bookings.cancel_booking(code, CUSTOMER["phone"])["success"]   # 顾客的取消照样成功
    assert rows(db)[0]["status"] == "cancelled" and pending(db)

    calendar.fail = False
    bookings.sync_calendar_once()
    assert calendar.deleted == ["event-1"] and rows(db)[0]["google_event_id"] is None and not pending(db)


def test_reschedule_is_moved_in_calendar_later(db, calendar):
    code = book(time="11:00")["booking"]["booking_code"]
    calendar.fail = True
    assert bookings.reschedule_booking(code, CUSTOMER["phone"], WEDNESDAY, "15:00")["success"]
    assert calendar.updated == [] and pending(db)

    calendar.fail = False
    bookings.sync_calendar_once()
    row = rows(db)[0]
    assert calendar.updated == [("event-1", row["start_at"], row["end_at"])] and not pending(db)   # 用数据库里的新时间


def test_event_already_gone_counts_as_deleted(db, calendar, monkeypatch):
    code = book()["booking"]["booking_code"]
    calendar.fail = True
    bookings.cancel_booking(code, CUSTOMER["phone"])

    def gone(event_id):   # Google 回 410：这个活动已经被删掉了（例如员工手动删了）
        raise type("HttpError", (Exception,), {"resp": SimpleNamespace(status=410)})()
    monkeypatch.setattr(bookings.google_calendar, "delete_event", gone)
    assert bookings.sync_calendar_once() == (1, 0) and not pending(db)


def test_gives_up_after_max_attempts_and_logs_error(db, calendar, caplog):
    calendar.fail = True
    book()
    with caplog.at_level(logging.WARNING, logger="glow.bookings"):
        for _ in range(bookings.CALENDAR_MAX_ATTEMPTS):
            bookings.sync_calendar_once()
    assert rows(db)[0]["calendar_attempts"] == bookings.CALENDAR_MAX_ATTEMPTS
    assert any(r.levelname == "ERROR" and "需要人工处理" in r.getMessage() for r in caplog.records)
    assert bookings.sync_calendar_once() == (0, 0)               # 放弃之后不再一直试
