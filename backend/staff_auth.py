"""员工后台登录（Supabase Auth）

流程：
    1. 员工在网页输入邮箱 + 密码 → POST /api/auth/login
    2. 后端请 Supabase Auth 验证密码，成功就拿到一个 access token（有效 1 小时）
    3. 前端把 token 存起来，之后调用后台 API 都在 Authorization 标头带上它
    4. 后台 API 用 require_staff 检查：token 是真的？这个人是员工吗？

为什么要检查"是不是员工"？Supabase 帐号不一定都是员工，
所以要在帐号的 app_metadata 设 role = "staff"（只有后台 SQL 能改，用户自己改不了）。
帐号和角色由我们在 Supabase 后台建立（见 sql/006_staff_roles.sql）。

前端完全不需要 Supabase 的 key：登录和验证都在后端做。
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Header, HTTPException
from supabase import ClientOptions, create_client

load_dotenv(Path(__file__).resolve().parent / ".env")

STAFF_ROLES = {"staff", "admin"}
log = logging.getLogger("glow.auth")


def _auth_client():
    """登录专用的 client：不保存登录状态，不会影响其他地方查数据库用的 client。"""
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"),
                         options=ClientOptions(auto_refresh_token=False, persist_session=False))


def _staff_info(user):
    role = (user.app_metadata or {}).get("role")
    if role not in STAFF_ROLES:
        return None
    name = (user.user_metadata or {}).get("name") or user.email
    return {"email": user.email, "name": name, "role": role}


def login(email, password):
    """邮箱 + 密码登录。成功返回 {"access_token", "staff"}；失败抛出 HTTPException。"""
    try:
        result = _auth_client().auth.sign_in_with_password({"email": email.strip(), "password": password})
    except Exception:
        log.info("登录失败：%s", email)
        raise HTTPException(401, "邮箱或密码不对")
    staff = _staff_info(result.user)
    if staff is None:
        log.warning("不是员工的帐号尝试登录后台：%s", email)
        raise HTTPException(403, "这个帐号不是员工帐号")
    log.info("员工登录：%s", staff["email"])
    return {"access_token": result.session.access_token, "staff": staff}


def staff_from_token(token):
    """用 token 找出是哪位员工。token 无效或过期 → 401；不是员工 → 403。"""
    if not token:
        raise HTTPException(401, "请先登录员工帐号")
    try:
        user = _auth_client().auth.get_user(token).user
    except Exception:
        raise HTTPException(401, "登录已过期，请重新登录")
    staff = _staff_info(user)
    if staff is None:
        raise HTTPException(403, "这个帐号不是员工帐号")
    return staff


def require_staff(authorization: str | None = Header(default=None)):
    """FastAPI 依赖：放在后台 API 上，没登录的人就进不来。"""
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    return staff_from_token(token)
