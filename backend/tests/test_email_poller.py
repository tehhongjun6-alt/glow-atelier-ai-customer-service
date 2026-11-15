"""「邮箱」分页的自动回复开关（email_poller.py）

用假的 run_once 代替真的读信／回信，不连 Gmail、不花 Gemini 额度。
"""

import threading
import time

import pytest

import email_poller
from email_poller import EmailPoller


class FakeRunOnce:
    """记录被调用了几次；可以故意让它出错。"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.called = threading.Event()

    def __call__(self, dry_run):
        self.calls.append(dry_run)
        self.called.set()
        if self.fail:
            raise ConnectionError("网络断了")


def wait_until(condition, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def poller_threads():
    return [t for t in threading.enumerate() if t.name == "email-poller" and t.is_alive()]


@pytest.fixture
def make_poller():
    created = []

    def make(**kwargs):
        p = EmailPoller(**{"interval": 0.02, **kwargs})
        created.append(p)
        return p
    yield make
    for p in created:          # 测试结束一定要关掉，不要留下背景线程
        p.stop()
    assert wait_until(lambda: not poller_threads())


def test_default_is_off():
    assert email_poller.poller.running is False


def test_start_really_replies_and_stop_stops(make_poller):
    run = FakeRunOnce()
    p = make_poller(run_once=run)
    p.start()
    assert p.running
    assert wait_until(lambda: len(run.calls) >= 2)        # 一轮一轮地检查
    assert all(dry_run is False for dry_run in run.calls)   # 开关打开就是真的回信
    assert p.last_check is not None

    p.stop()
    assert not p.running
    assert wait_until(lambda: not poller_threads())
    count = len(run.calls)
    time.sleep(0.1)
    assert len(run.calls) == count                          # 关了之后不再检查


def test_pressing_start_twice_keeps_one_thread(make_poller):
    p = make_poller(run_once=FakeRunOnce())
    p.start()
    p.start()
    p.stop()
    p.start()                                               # 关了马上再开
    assert p.running
    time.sleep(0.05)
    assert len(poller_threads()) == 1                       # 不会有两个线程抢着回同一封信


def test_error_is_shown_and_polling_continues(make_poller):
    run = FakeRunOnce(fail=True)
    p = make_poller(run_once=run)
    p.start()
    assert wait_until(lambda: len(run.calls) >= 2)          # 出错之后下一轮照样检查
    assert p.running
    assert "网络断了" in p.last_error


def test_status_for_frontend(make_poller, monkeypatch):
    monkeypatch.setattr(email_poller.email_worker.gmail, "company_address", lambda: "tehhongjun6+glow@gmail.com")
    p = make_poller(run_once=FakeRunOnce())
    status = p.status()
    assert status == {"running": False, "address": "tehhongjun6+glow@gmail.com",
                      "interval_seconds": 0.02, "last_check": None, "last_error": None}
