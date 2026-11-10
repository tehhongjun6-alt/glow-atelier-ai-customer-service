"""测试共用的"假环境"：不连网络、不花钱，每次结果都一样

- FakeSupabase：在内存里模拟 Supabase 的表（只做到我们用到的查询）
- fake_calendar：假的 Google Calendar，记录被调用了什么，也可以故意让它失败
- fixed_now：把"现在"固定在 2026-11-10（星期二）09:00 吉隆坡时间

用法（在 backend 资料夹里）：
    python -m pytest            跑全部测试
    python -m pytest -v         显示每一个测试的名字
"""

import itertools
from datetime import datetime
from types import SimpleNamespace

import pytest

from booking import bookings, notifications, rules
from handoff import handoffs

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=rules.TIMEZONE)   # 星期二


# ---------- 假的 Supabase ----------
def _value(v):
    """ISO 时间字串转成 datetime 才能正确比大小（不同时区的字串直接比会错）。"""
    if isinstance(v, str) and "T" in v:
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return v
    return v


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self.op = "select"
        self.payload = None
        self.filters = []
        self.sort = None

    def select(self, *_):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, changes):
        self.op, self.payload = "update", changes
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _filter(self, key, test):
        self.filters.append(lambda r: r.get(key) is not None and test(_value(r.get(key))))
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def neq(self, k, v):
        self.filters.append(lambda r: r.get(k) != v)
        return self

    def lt(self, k, v):
        return self._filter(k, lambda x: x < _value(v))

    def gt(self, k, v):
        return self._filter(k, lambda x: x > _value(v))

    def gte(self, k, v):
        return self._filter(k, lambda x: x >= _value(v))

    def order(self, k, desc=False):
        self.sort = (k, desc)
        return self

    def execute(self):
        matched = [r for r in self.table.rows if all(f(r) for f in self.filters)]
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            new = [self.table.add(r) for r in rows]
            return SimpleNamespace(data=[dict(r) for r in new])
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in matched])
        if self.op == "delete":
            self.table.rows = [r for r in self.table.rows if r not in matched]
            return SimpleNamespace(data=[dict(r) for r in matched])
        if self.sort:
            key, desc = self.sort
            matched = sorted(matched, key=lambda r: _value(r[key]), reverse=desc)
        return SimpleNamespace(data=[dict(r) for r in matched])


class FakeTable:
    def __init__(self, defaults):
        self.rows = []
        self.defaults = defaults
        self.ids = itertools.count(1)

    def add(self, row):
        full = {"id": next(self.ids), "created_at": NOW.isoformat(), **self.defaults, **row}
        self.rows.append(full)
        return full


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "bookings": FakeTable({"status": "confirmed", "google_event_id": None, "updated_at": NOW.isoformat()}),
            "handoffs": FakeTable({"status": "processing", "conversation": [], "assigned_to": None,
                                   "staff_note": None, "accepted_at": None, "resolved_at": None}),
        }

    def table(self, name):
        return FakeQuery(self.tables[name])


# ---------- fixtures ----------
@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    """所有测试的"现在"都是 2026-11-10 星期二 09:00。"""
    monkeypatch.setattr(rules, "now", lambda: NOW)
    return NOW


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(bookings, "supabase", fake)
    monkeypatch.setattr(handoffs, "supabase", fake)
    return fake


@pytest.fixture
def calendar(monkeypatch):
    """假的 Google Calendar。calendar.fail = True 会让下一次调用失败。"""
    log = SimpleNamespace(created=[], updated=[], deleted=[], fail=False)

    def create_event(row):
        if log.fail:
            raise RuntimeError("Google Calendar 连不上")
        log.created.append(row)
        return f"event-{len(log.created)}"

    def update_event_time(event_id, start_at, end_at):
        log.updated.append((event_id, start_at, end_at))

    def delete_event(event_id):
        log.deleted.append(event_id)

    monkeypatch.setattr(bookings.google_calendar, "create_event", create_event)
    monkeypatch.setattr(bookings.google_calendar, "update_event_time", update_event_time)
    monkeypatch.setattr(bookings.google_calendar, "delete_event", delete_event)
    return log


@pytest.fixture
def emails(monkeypatch):
    """假的寄信。emails.fail = True 会让寄信失败。"""
    log = SimpleNamespace(sent=[], fail=False)

    def send_for(kind, result):
        if log.fail:
            return False, "Gmail 连不上"
        log.sent.append((kind, result["booking"]["booking_code"]))
        return True, None

    monkeypatch.setattr(notifications, "send_for", send_for)
    return log


def at(day, time):
    """测试里写时间的小工具：at("2026-11-11", "15:00")"""
    return rules.parse_datetime(day, time)


WEDNESDAY = "2026-11-11"
MONDAY = "2026-11-16"
