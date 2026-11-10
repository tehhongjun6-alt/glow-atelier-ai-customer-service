"""第 7 步：给前端用的 API（FastAPI）

把前面做好的功能包成网址，让 React 前端可以调用：
    POST /api/chat                     和客服 Agent 聊天
    GET  /api/knowledge                知识库原文（Markdown）＋ 切块清单
    GET  /api/bookings                 预约清单（日历用）
    GET  /api/handoffs                 转人工工单清单
    GET  /api/handoffs/{code}          一张工单的详情（含对话）
    POST /api/handoffs/{code}/accept   接受
    POST /api/handoffs/{code}/resolve  处理完毕

用法（在 backend 资料夹里）：
    uvicorn api:app --reload --port 8000
然后打开 http://localhost:8000/docs 可以直接试每一个 API。

⚠️ 这是作品集 demo：后台 API 没有登录保护。正式上线要加员工登录（例如 Supabase Auth）。
"""

import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.agent import MODEL, CustomerServiceAgent
from booking import bookings, rules
from handoff import handoffs
from rag.chunker import chunk_file
from rag.store import supabase

KNOWLEDGE_FILE = Path(__file__).resolve().parent.parent / "knowledge_base" / "glow_atelier.md"
MAX_SESSIONS = 200
SESSION_TTL_SECONDS = 2 * 60 * 60

app = FastAPI(title="Glow Atelier AI 客服 API")
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


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "讯息不能是空的")
    session_id = req.session_id or uuid.uuid4().hex
    result = _get_agent(session_id).chat(message[:2000])
    return {"session_id": session_id, "model": MODEL, **result}


@app.post("/api/chat/reset")
def reset_chat(req: ChatRequest):
    sessions.pop(req.session_id or "", None)
    return {"ok": True}


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
@app.get("/api/bookings")
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


@app.get("/api/handoffs")
def list_handoffs(status: str | None = None):
    rows = handoffs.list_handoffs(status)
    return {"handoffs": sorted(rows, key=lambda h: h["created_at"], reverse=True)}


@app.get("/api/handoffs/{ticket_code}")
def get_handoff(ticket_code: str):
    row = handoffs.get_handoff(ticket_code)
    if not row:
        raise HTTPException(404, f"找不到工单 {ticket_code}")
    return {**handoffs.describe(row), "conversation": row["conversation"]}


@app.post("/api/handoffs/{ticket_code}/accept")
def accept_handoff(ticket_code: str, req: AcceptRequest):
    return _check(handoffs.accept(ticket_code, req.staff))


@app.post("/api/handoffs/{ticket_code}/resolve")
def resolve_handoff(ticket_code: str, req: ResolveRequest):
    return _check(handoffs.resolve(ticket_code, req.note))


@app.get("/api/health")
def health():
    return {"ok": True, "model": MODEL, "time": datetime.now(rules.TIMEZONE).isoformat()}
