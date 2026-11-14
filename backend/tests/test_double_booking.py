"""防重复预约（sql/007_no_double_booking.sql + booking/bookings.py）

模拟"两台服务器同时订"：第一台检查完、还没写进去的那一瞬间，第二台先写进去了。
以前靠程序里的锁，两台服务器各锁各的挡不住；现在由数据库挡下（假数据库也照同样规则）。
"""

import pytest
from postgrest.exceptions import APIError

from booking import bookings
from tests.conftest import WEDNESDAY
from tests.test_bookings import CUSTOMER, book, rows

real_pick = bookings._pick_beautician


def other_server_books_first(monkeypatch, db):
    """第一次排班之后，"另一台服务器"马上把同一位美容师、同一时段订走。"""
    state = {"done": False}

    def pick(service, start, end, exclude_id=None, preferred=None):
        name, problem = real_pick(service, start, end, exclude_id, preferred)
        if name and not state["done"]:
            state["done"] = True
            db.tables["bookings"].add({"booking_code": "GA-OTHER1", "customer_name": "别台服务器的顾客",
                                       "phone": "0170000000", "email": "o@example.com", "service": service,
                                       "beautician": name, "start_at": start.isoformat(), "end_at": end.isoformat()})
        return name, problem

    monkeypatch.setattr(bookings, "_pick_beautician", pick)


def test_fake_database_blocks_same_beautician_same_time(db, calendar):
    first = book()["booking"]
    row = dict(rows(db)[0]); row.pop("id")
    with pytest.raises(APIError) as error:
        db.table("bookings").insert({**row, "booking_code": "GA-COPY01"}).execute()
    assert error.value.code == "23P01" and first["beautician"] == row["beautician"]


def test_race_gets_another_beautician(db, calendar, monkeypatch):
    other_server_books_first(monkeypatch, db)
    result = book()                                   # 男士护理：有 3 位美容师会做
    assert result["success"]
    beauticians = [r["beautician"] for r in rows(db)]
    assert len(beauticians) == 2 and len(set(beauticians)) == 2      # 两笔、两位不同的美容师，没有重复


def test_race_with_chosen_beautician_tells_customer(db, calendar, monkeypatch):
    other_server_books_first(monkeypatch, db)
    result = book(beautician="Melissa")              # 顾客指定陈美玲，她刚被别人订走
    assert not result["success"] and "刚刚有人订走了" in result["error"]
    assert len(rows(db)) == 1 and calendar.created == []           # 只有别台那一笔，日历没多写


def test_reschedule_into_just_taken_slot_is_blocked(db, calendar, monkeypatch):
    code = book(time="11:00")["booking"]["booking_code"]
    plan, error = bookings.prepare_reschedule(code, CUSTOMER["phone"], WEDNESDAY, "15:00")
    assert error is None
    # 顾客还在确认的时候，别台服务器把同一位美容师的 15:00 订走了
    db.tables["bookings"].add({"booking_code": "GA-OTHER2", "customer_name": "别人", "phone": "0170000001",
                               "email": "o@example.com", "service": "男士护理", "beautician": plan["beautician"],
                               "start_at": plan["start_at"], "end_at": plan["end_at"]})
    monkeypatch.setattr(bookings, "_pick_beautician", lambda *a, **k: (plan["beautician"], None))  # 检查刚好没看到
    result = bookings.commit_reschedule(plan)
    assert not result["success"] and "刚刚有人订走了" in result["error"]
    assert rows(db)[0]["start_at"] != plan["start_at"] and calendar.updated == []   # 原本的预约没被改
