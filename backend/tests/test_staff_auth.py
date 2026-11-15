"""员工后台登录（staff_auth.py）：没登录、不是员工、是员工 三种情况

用假的 Supabase Auth，不连网络。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import staff_auth


def fake_user(role=None, name=None, email="amy@glow.test"):
    return SimpleNamespace(email=email, app_metadata={"role": role} if role else {},
                           user_metadata={"name": name} if name else {})


@pytest.fixture
def fake_supabase(monkeypatch):
    """token "good-staff" 是员工，"good-customer" 是一般帐号，其他都是无效 token。"""
    users = {"good-staff": fake_user("staff", "前台 Amy"), "good-customer": fake_user()}

    def get_user(token):
        if token not in users:
            raise RuntimeError("invalid JWT")
        return SimpleNamespace(user=users[token])

    client = SimpleNamespace(auth=SimpleNamespace(get_user=get_user))
    monkeypatch.setattr(staff_auth, "_auth_client", lambda: client)


def status_of(authorization):
    with pytest.raises(HTTPException) as error:
        staff_auth.require_staff(authorization)
    return error.value.status_code


def test_no_token_is_401(fake_supabase):
    assert status_of(None) == 401


def test_invalid_or_expired_token_is_401(fake_supabase):
    assert status_of("Bearer not-a-real-token") == 401


def test_logged_in_but_not_staff_is_403(fake_supabase):
    assert status_of("Bearer good-customer") == 403


def test_staff_can_enter(fake_supabase):
    staff = staff_auth.require_staff("Bearer good-staff")
    assert staff == {"email": "amy@glow.test", "name": "前台 Amy", "role": "staff"}
