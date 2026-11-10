"""使用次数限制（ratelimit.py）和 Google 授权失效时的处理（integrations/google_auth.py）

用假的时钟，不用真的等一分钟。
"""

import pytest
from fastapi.testclient import TestClient

from ratelimit import RateLimiter


class FakeClock:
    def __init__(self, start=1_794_300_000.0):    # 2026-11-10 某个时间
        self.now = start

    def __call__(self):
        return self.now


def limiter(per_minute=3, per_day=5, global_per_day=100):
    clock = FakeClock()
    return RateLimiter(per_minute, per_day, global_per_day, clock=clock), clock


def test_per_minute_limit_and_recovery():
    rl, clock = limiter(per_minute=3)
    assert [rl.check("1.1.1.1") for _ in range(3)] == [None, None, None]
    blocked = rl.check("1.1.1.1")
    assert blocked and "太快" in blocked
    clock.now += 61                                 # 过了一分钟
    assert rl.check("1.1.1.1") is None


def test_each_ip_has_its_own_limit():
    rl, _ = limiter(per_minute=2)
    rl.check("A"), rl.check("A")
    assert rl.check("A") is not None
    assert rl.check("B") is None                    # 别人不受影响


def test_per_day_limit():
    rl, clock = limiter(per_minute=100, per_day=5)
    for _ in range(5):
        assert rl.check("A") is None
        clock.now += 1
    blocked = rl.check("A")
    assert blocked and "每人每日上限" in blocked
    clock.now += 24 * 3600                          # 隔天归零
    assert rl.check("A") is None


def test_global_daily_limit_protects_quota():
    rl, _ = limiter(per_minute=100, per_day=100, global_per_day=3)
    assert rl.check("A") is None and rl.check("B") is None and rl.check("C") is None
    blocked = rl.check("D")                         # 第 4 个人，就算他自己一则都没发
    assert blocked and "满了" in blocked


def test_blocked_requests_do_not_count():
    """被挡下的那一次不算数，不然狂按会让等待时间越拖越长。"""
    rl, clock = limiter(per_minute=1, per_day=2)
    assert rl.check("A") is None
    assert rl.check("A") is not None and rl.check("A") is not None
    clock.now += 61
    assert rl.check("A") is None                    # 每日 2 则：只算了真的发出的 2 则


def test_chat_api_returns_429(monkeypatch):
    """API 层：超过限制返回 429，而且不会调用 Gemini。"""
    import api

    rl, _ = limiter(per_minute=1)
    monkeypatch.setattr(api, "chat_limiter", rl)
    called = []
    monkeypatch.setattr(api, "_get_agent", lambda sid: called.append(sid) or type(
        "A", (), {"chat": lambda self, m: {"answer": "ok", "trace": [], "tokens": {"input": 0, "output": 0}}})())
    client = TestClient(api.app)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    second = client.post("/api/chat", json={"message": "hi"})
    assert second.status_code == 429 and "太快" in second.json()["detail"]
    assert len(called) == 1


# ---------- Google 授权失效 ----------
def test_google_auth_never_waits_for_browser_on_server(monkeypatch):
    """服务器上没有人能登录：授权有问题要马上报错（并说明怎么修），不能卡住等浏览器。"""
    from integrations import google_auth

    missing = google_auth.SECRETS_DIR / "does-not-exist" / "token.json"
    monkeypatch.setattr(google_auth, "TOKEN_FILE", missing)   # 假装没有 token
    with pytest.raises(google_auth.GoogleAuthError) as error:
        google_auth.get_credentials()
    assert "重新登录" in str(error.value)
    assert google_auth.status()["ok"] is False
