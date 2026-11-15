"""第 7 步：给前端用的 API（FastAPI）

把前面做好的功能包成网址，让 React 前端可以调用：
    POST /api/chat                     和客服 Agent 聊天
    POST /api/chat/stream              同上，但逐字回传（SSE），网页聊天用这个
    GET  /api/knowledge                知识库原文（Markdown）＋ 切块清单
    GET  /api/bookings                 预约清单（日历用）
    GET  /api/handoffs                 转人工工单清单
    GET  /api/handoffs/{code}          一张工单的详情（含对话）
    POST /api/handoffs/{code}/accept   接受
    POST /api/handoffs/{code}/resolve  处理完毕
    GET  /api/email/status             邮箱自动回复开关的状态（预设关闭）
    POST /api/email/start              打开：每 60 秒检查一次 Gmail，真的会回信
    POST /api/email/stop               关闭

用法（在 backend 资料夹里）：
    uvicorn api:app --reload --port 8000
然后打开 http://localhost:8000/docs 可以直接试每一个 API。

后台 API（预约清单、转人工、邮箱开关）要员工登录才能用（Supabase Auth，见 staff_auth.py）：
    POST /api/auth/login               员工登录，拿到 token
    GET  /api/auth/me                  目前登录的是哪位员工
聊天和知识库是给顾客用的，不用登录。
"""

import json
import logging
import os
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langfuse import propagate_attributes
from pydantic import BaseModel

from agent.agent import FALLBACK_ANSWER, MODEL, CustomerServiceAgent
from alerts import telegram
from booking import bookings, calendar_sync, rules
from email_poller import poller
from handoff import handoffs
from integrations import google_auth
from log_setup import setup_logging
from ratelimit import chat_limiter, client_ip
from staff_auth import login, require_staff
from rag.chunker import chunk_file
from rag.store import supabase

KNOWLEDGE_FILE = Path(__file__).resolve().parent.parent / "knowledge_base" / "glow_atelier.md"
MAX_SESSIONS = 200
SESSION_TTL_SECONDS = 2 * 60 * 60

setup_logging("api.log")   # 统一 log 格式，同时写进 backend/logs/api.log

@asynccontextmanager
async def lifespan(_app):
    calendar_sync.start()   # 后端启动时：日历自动补做（每 60 秒把"日历待补做"的预约补到 Google Calendar）
    yield


app = FastAPI(title="Glow Atelier AI 客服 API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],   # Vite 开发服务器
    allow_methods=["*"],
    allow_headers=["*"],
)

# 每位顾客（每个浏览器分页）一个 Agent，记住对话和等待确认的动作
sessions = {}   # session_id → {"agent": CustomerServiceAgent, "last_used": 时间}


def _get_agent(session_id):
    now = time.time()
    for sid in [s for s, v in sessions.items() if now - v["last_used"] > SESSION_TTL_SECONDS]:
        del sessions[sid]                     # 超过 2 小时没说话的对话，清掉
    if session_id not in sessions:
        if len(sessions) >= MAX_SESSIONS:
            oldest = min(sessions, key=lambda s: sessions[s]["last_used"])
            del sessions[oldest]
        sessions[session_id] = {"agent": CustomerServiceAgent(channel="chat")}
    sessions[session_id]["last_used"] = now
    return sessions[session_id]["agent"]


# ---------- 聊天 ----------
class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


def _check_chat(req: ChatRequest, request: Request):
    """聊天的共同检查：讯息不能空、限流。返回 (讯息, session_id)。"""
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "讯息不能是空的")
    limited = chat_limiter.check(client_ip(request))   # 保护 Gemini 免费额度
    if limited:
        raise HTTPException(429, limited)
    return message[:2000], req.session_id or uuid.uuid4().hex


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    """一次拿到完整答案（旧的做法，留着给测试和其他程序用）。网页聊天改用下面的 /api/chat/stream。"""
    message, session_id = _check_chat(req, request)
    # Observability：这一题的所有记录都贴上 session_id，Langfuse 就能把同一位顾客的多轮对话串起来
    with propagate_attributes(session_id=session_id, tags=["chat"]):
        result = _get_agent(session_id).chat(message)
    return {"session_id": session_id, "model": MODEL, **result}


# ---------- 逐字显示（Streaming） ----------
# 以前：AI 全部想完、写完才回传，顾客要盯着"思考中"等 5–15 秒。
# 现在：用 SSE（Server-Sent Events）一边生成一边送：
#   start → tool（每用一个工具）→ delta（一小段文字，很多次）→ done（完整答案和过程）
# AI 在另一个线程里跑，产生的事件放进 queue；这里从 queue 拿出来送给浏览器。
STREAM_IDLE_SECONDS = 60   # 逾时处理：超过这么久没有任何新进度，就告诉顾客系统太慢，不要让画面一直转
SLOW_ANSWER = "不好意思，系统现在回应比较慢，请稍后再试，或直接联系我们的门市同事。"
log = logging.getLogger("glow.api")


def _sse(event):
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, request: Request):
    message, session_id = _check_chat(req, request)
    agent = _get_agent(session_id)
    events = queue.Queue()

    def work():
        try:
            # Observability：线程不会继承外面的设定，所以在线程里面贴 session_id
            with propagate_attributes(session_id=session_id, tags=["chat"]):
                result = agent.chat(message, on_event=events.put)
            events.put({"type": "done", "session_id": session_id, "model": MODEL, **result})
        except Exception:
            log.exception("逐字回答出错（session %s）", session_id)
            events.put({"type": "error", "message": FALLBACK_ANSWER})

    threading.Thread(target=work, name="chat-stream", daemon=True).start()

    def stream():
        yield _sse({"type": "start", "session_id": session_id})
        while True:
            try:
                event = events.get(timeout=STREAM_IDLE_SECONDS)
            except queue.Empty:
                log.warning("逐字回答逾时：%d 秒没有进度（session %s）", STREAM_IDLE_SECONDS, session_id)
                yield _sse({"type": "error", "message": SLOW_ANSWER})
                return
            yield _sse(event)
            if event["type"] in ("done", "error"):
                return

    # no-cache / X-Accel-Buffering：叫浏览器和代理服务器不要先存起来，收到一段就马上送出去
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/reset")
def reset_chat(req: ChatRequest):
    sessions.pop(req.session_id or "", None)
    return {"ok": True}


# ---------- 员工登录 ----------
class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def staff_login(req: LoginRequest):
    return login(req.email, req.password)


@app.get("/api/auth/me")
def staff_me(staff: dict = Depends(require_staff)):
    return {"staff": staff}


# ---------- 知识库 ----------
@app.get("/api/knowledge")
def knowledge():
    chunks = chunk_file(KNOWLEDGE_FILE)
    return {
        "file": KNOWLEDGE_FILE.name,
        "markdown": KNOWLEDGE_FILE.read_text(encoding="utf-8"),
        "chunks": [{"title": c["title"], "category": c["category"], "length": len(c["content"])} for c in chunks],
    }


# ---------- 预约（日历） ----------
@app.get("/api/bookings", dependencies=[Depends(require_staff)])   # 有顾客电话、邮箱 → 只给员工看
def list_bookings(days: int = 30, include_cancelled: bool = True):
    start = rules.now().replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        supabase.table("bookings").select("*")
        .gte("start_at", start.isoformat())
        .lt("start_at", (start + timedelta(days=days)).isoformat())
        .order("start_at")
    )
    if not include_cancelled:
        query = query.eq("status", "confirmed")
    return {
        "bookings": [bookings.describe(r) for r in query.execute().data],
        # 前端用来嵌入 Google Calendar（日历要在 Google 设成公开，别人才看得到）
        "google_calendar_id": os.getenv("GOOGLE_CALENDAR_ID"),
    }


# ---------- 转人工后台 ----------
class AcceptRequest(BaseModel):
    staff: str


class ResolveRequest(BaseModel):
    note: str = ""


def _check(result):
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result["handoff"]


@app.get("/api/handoffs", dependencies=[Depends(require_staff)])
def list_handoffs(status: str | None = None):
    rows = handoffs.list_handoffs(status)
    return {"handoffs": sorted(rows, key=lambda h: h["created_at"], reverse=True)}


@app.get("/api/handoffs/{ticket_code}", dependencies=[Depends(require_staff)])
def get_handoff(ticket_code: str):
    row = handoffs.get_handoff(ticket_code)
    if not row:
        raise HTTPException(404, f"找不到工单 {ticket_code}")
    return {**handoffs.describe(row), "conversation": row["conversation"]}


@app.post("/api/handoffs/{ticket_code}/accept", dependencies=[Depends(require_staff)])
def accept_handoff(ticket_code: str, req: AcceptRequest):
    return _check(handoffs.accept(ticket_code, req.staff))


@app.post("/api/handoffs/{ticket_code}/resolve", dependencies=[Depends(require_staff)])
def resolve_handoff(ticket_code: str, req: ResolveRequest):
    return _check(handoffs.resolve(ticket_code, req.note))


# ---------- 邮箱（自动回复开关，预设关闭） ----------
@app.get("/api/email/status", dependencies=[Depends(require_staff)])
def email_status():
    return poller.status()


@app.post("/api/email/start", dependencies=[Depends(require_staff)])
def email_start():
    poller.start()
    return poller.status()


@app.post("/api/email/stop", dependencies=[Depends(require_staff)])
def email_stop():
    poller.stop()
    return poller.status()


def _database_ok():
    try:
        supabase.table("bookings").select("id").limit(1).execute()
        return True
    except Exception:
        log.exception("健康检查：数据库连不上")
        return False


@app.get("/api/health")
def health():
    """健康检查：给 uptime 监控（例如 UptimeRobot）每分钟来问一次。
    数据库连不上 → 回 503，监控就会通知"网站出问题了"；只有 Google 授权过期 → 还是 200（聊天照样能用），但写出原因。"""
    database = _database_ok()
    body = {
        "ok": database,
        "database": database,
        "model": MODEL,
        "time": datetime.now(rules.TIMEZONE).isoformat(),
        "google": google_auth.status(),   # 授权过期时，这里会写出原因和修法
        "alerts": telegram.configured(),  # 有没有接上 Telegram 警报
    }
    return body if database else JSONResponse(body, status_code=503)
