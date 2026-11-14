"""逐字显示（agent/agent.py 的 _stream、api.py 的 /api/chat/stream）

用假的 Gemini 和假的 Agent，不花额度、不连网络。
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

import api
from agent import agent as agent_module


def text_chunk(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]))])


def events_of(response):
    return [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "chat_limiter", type("NoLimit", (), {"check": lambda self, key: None})())
    return TestClient(api.app)


def fake_agent(chat):
    return type("FakeAgent", (), {"chat": chat})()


# ---------- Gemini 逐字 ----------
def test_stream_passes_each_piece_and_rebuilds_full_answer(monkeypatch):
    monkeypatch.setattr(agent_module.client.models, "generate_content_stream",
                        lambda **kwargs: iter([text_chunk("星期一"), text_chunk("休息，"), text_chunk("其他天 10 点开。")]))
    pieces = []
    response = agent_module._stream([], "", pieces.append, {"sent": False})
    assert pieces == ["星期一", "休息，", "其他天 10 点开。"]          # 一段一段送出去
    assert response.text == "星期一休息，其他天 10 点开。"             # 拼回完整答案，后面的代码照旧使用


def test_no_retry_after_text_was_shown(monkeypatch):
    """已经有字显示在顾客画面上，中途出错就不能重试（重试会把同一段话再显示一次）。"""
    calls = []

    def half_then_fail(**kwargs):
        calls.append(1)
        yield text_chunk("您好，")
        raise errors.APIError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})

    monkeypatch.setattr(agent_module.client.models, "generate_content_stream", half_then_fail)
    monkeypatch.setattr(agent_module.time, "sleep", lambda s: None)
    with pytest.raises(errors.APIError):
        agent_module.CustomerServiceAgent._generate([], on_text=lambda t: None)
    assert len(calls) == 1


# ---------- /api/chat/stream ----------
def test_stream_endpoint_sends_start_tool_delta_done(client, monkeypatch):
    def chat(self, message, on_event=None):
        step = {"tool": "search_knowledge_base", "args": {"query": "营业时间"}, "results": ["营业时间"]}
        on_event({"type": "tool", "step": step})
        on_event({"type": "delta", "text": "星期一"})
        on_event({"type": "delta", "text": "休息。"})
        return {"answer": "星期一休息。", "trace": [step], "tokens": {"input": 1, "output": 2}}

    monkeypatch.setattr(api, "_get_agent", lambda sid: fake_agent(chat))
    response = client.post("/api/chat/stream", json={"message": "星期一有开吗"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = events_of(response)
    assert [e["type"] for e in events] == ["start", "tool", "delta", "delta", "done"]
    assert events[-1]["answer"] == "星期一休息。" and events[-1]["session_id"] == events[0]["session_id"]


def test_stream_endpoint_times_out_instead_of_hanging(client, monkeypatch):
    def slow_chat(self, message, on_event=None):
        time.sleep(1)                              # 一直没有进度
        return {"answer": "太晚了", "trace": [], "tokens": {}}

    monkeypatch.setattr(api, "STREAM_IDLE_SECONDS", 0.2)
    monkeypatch.setattr(api, "_get_agent", lambda sid: fake_agent(slow_chat))
    events = events_of(client.post("/api/chat/stream", json={"message": "hi"}))
    assert [e["type"] for e in events] == ["start", "error"] and events[-1]["message"] == api.SLOW_ANSWER


def test_stream_endpoint_hides_internal_errors(client, monkeypatch):
    def broken_chat(self, message, on_event=None):
        raise RuntimeError("token.json 授权过期")

    monkeypatch.setattr(api, "_get_agent", lambda sid: fake_agent(broken_chat))
    events = events_of(client.post("/api/chat/stream", json={"message": "hi"}))
    assert events[-1] == {"type": "error", "message": api.FALLBACK_ANSWER}      # 不带内部细节


def test_empty_message_is_rejected_before_streaming(client):
    assert client.post("/api/chat/stream", json={"message": "   "}).status_code == 400
