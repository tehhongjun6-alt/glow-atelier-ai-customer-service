"""第 3 步：客服 Agent（Gemini + 工具调用）

Agent 循环（Agent Loop）是这样运作的：

    顾客讯息
       ↓
    ┌─► 交给 Gemini（附上对话历史 + 工具清单）
    │      ↓
    │   Gemini 决定：直接回答？还是先用工具？
    │      ├─ 直接回答 → 结束，把答案给顾客
    │      └─ 要用工具 → 我们执行工具（例如查知识库）
    │                      ↓
    └────────────── 把工具结果交回给 Gemini

模型自己决定什么时候查、查什么（可以改写搜索句、找不到再搜一次），
这叫 Agentic RAG。第 4–6 步只要在 tools.py 加工具，这个循环不用改。
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from langfuse import get_client, observe
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from agent.actions import ActionGate
from agent.prompts import CHANNEL_NOTES, SYSTEM_PROMPT
from agent.tools import TOOL_FUNCTIONS, TOOLS
from booking import bookings
from booking import rules

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------- 模型清单：免费额度用完就自动换下一个 ----------
# 排列顺序 = 又快又会正确用工具的放前面。某个模型额度用完或太忙，就跳到下一个；之后自动回到第一个。
# 想改顺序：在 .env 设定 GEMINI_MODELS=模型A,模型B,...（逗号分开），不设定就用下面这个。
# 2026-11-15 实测（同一题"星期一有开吗？"，同样的客服设定）：
#   3.6-flash 1.5 秒、3.5-flash 1.4 秒、3.5-flash-lite 1.5 秒、3.1-flash-lite 1.0 秒 → 都正确先查知识库
#   3.8-flash、3.7-flash → 503 太忙（之前一题等了 163 秒），放最后当备用
#   2.5-flash、2.5-flash-lite → 404，这个 key 不能用，不列进来
DEFAULT_MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    "gemini-3.7-flash", "gemini-3.8-flash",
]
BUSY_SKIP_SECONDS = 5 * 60   # 模型一直回 503（太忙）→ 5 分钟内先用别的，不让顾客一直等
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",") if m.strip()] or DEFAULT_MODELS
MODEL = MODELS[0]   # 第一选择（显示用）；实际这一次用哪个，看 current_model()
MAX_STEPS = 5   # 一个问题最多来回几次，避免模型一直调用工具停不下来

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def build_config(channel_note=""):
    """每次调用都附上"现在时间"：模型自己不知道今天几号，"明天""下星期三"会算错。
    channel_note：渠道说明（网页聊天 or 电邮），见 prompts.py。"""
    now = rules.now()
    clock = f"\n## 现在时间\n{now:%Y-%m-%d} {rules.WEEKDAY_NAMES[now.weekday()]} {now:%H:%M}（吉隆坡时间）\n"
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT + channel_note + clock,
        tools=TOOLS,
        # 关掉 SDK 的"自动执行工具"，由我们自己跑循环：
        # 这样每一次工具调用都看得到、记得下来（之后前端要显示，评估也要用）
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

FALLBACK_ANSWER = "不好意思，系统暂时没办法回答这个问题，请稍后再试，或直接联系我们的门市同事。"


_unavailable = {}   # 模型 → 到什么时候之前先不要用（time.time() 秒数）
QUOTA_RESET_ZONE = ZoneInfo("America/Los_Angeles")   # Gemini 免费额度在美国太平洋时间午夜重算
log = logging.getLogger("glow.agent")


def _available_models():
    now = time.time()
    return [m for m in MODELS if _unavailable.get(m, 0) <= now]


def current_model():
    """现在会先用哪个模型（健康检查、画面显示用）。"""
    available = _available_models()
    return available[0] if available else MODELS[-1]


def _next_quota_reset():
    now = datetime.now(QUOTA_RESET_ZONE)
    return (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0).timestamp()


def _is_daily_quota(text):
    return "PerDay" in text or "per day" in text.lower()


def _skip_model(model, error):
    """这个模型暂时不能用：记下来，先换下一个。返回 True = 可以换下一个；False = 不是额度问题，不该换。"""
    text = str(error)
    # Google 的错误里写着是哪一种额度：quotaId "GenerateRequestsPerDay..." 和说明 "...per day limit exceeded"
    # （每分钟的是 "PerMinute" / "per minute"）。两个记号任一个出现就算"今天用完"，Google 改一边的写法也不会认错
    if error.code == 429 and _is_daily_quota(text):
        until, reason = _next_quota_reset(), "今天的免费额度用完"
    elif error.code == 429:
        until, reason = time.time() + 60, "每分钟次数到上限"
    elif error.code in (403, 404):
        until, reason = time.time() + 24 * 3600, f"这个 key 不能用（{error.code}）"
    elif error.code in (500, 503):
        until, reason = time.time() + BUSY_SKIP_SECONDS, f"服务器太忙（{error.code}）"
    else:
        return False
    _unavailable[model] = until
    nxt = next((m for m in MODELS if m != model and _unavailable.get(m, 0) <= time.time()), None)
    log.warning("模型 %s %s，改用 %s", model, reason, nxt or "（没有可用的模型了）")
    return True


def _stream(contents, channel_note, on_text, state, model=None):
    """逐字模式：Gemini 一边生成，一边把文字片段交给 on_text。
    全部收完后，拼回和一般模式一样的 response（工具调用、对话历史的写法都不用改）。
    state["sent"]：有没有文字已经送到顾客画面上（中途出错时，_generate 靠它决定能不能重试）。"""
    parts, usage = [], None
    for chunk in client.models.generate_content_stream(model=model or MODEL, contents=contents,
                                                      config=build_config(channel_note)):
        usage = chunk.usage_metadata or usage
        candidate = chunk.candidates[0] if chunk.candidates else None
        for part in (candidate.content.parts or []) if candidate and candidate.content else []:
            parts.append(part)               # 原封不动保留（工具调用的签名 thought_signature 也在里面）
            if part.text and not part.thought:
                state["sent"] = True
                on_text(part.text)
    candidates = [types.Candidate(content=types.Content(role="model", parts=parts))] if parts else []
    return types.GenerateContentResponse(candidates=candidates, usage_metadata=usage)


class CustomerServiceAgent:
    """一个顾客 = 一个 Agent，记住这位顾客的对话历史。"""

    def __init__(self, channel="chat", email_thread_id=None, customer_email=None):
        self.history = []          # 对话历史（types.Content 列表）
        self.gate = ActionGate()   # 两段式确认：记录轮次和等待确认的动作
        self.channel = channel     # "chat" 或 "email"
        self.channel_note = CHANNEL_NOTES[channel]
        self.email_thread_id = email_thread_id   # 电邮渠道：Gmail 对话 ID（转人工用）
        self.customer_email = customer_email     # 电邮渠道：寄件人地址
        self.current_contents = []               # 这一轮进行中的对话（转人工时存进工单）

    @observe(name="customer-chat", as_type="agent")
    def chat(self, message, on_event=None):
        """处理顾客的一则讯息，返回答案和过程记录。

        on_event：网页聊天的逐字显示（streaming）用。有传进来的话：
        - Gemini 每产生一小段文字，就调用 on_event({"type": "delta", "text": ...})
        - 每用完一个工具，就调用 on_event({"type": "tool", "step": ...})
        电邮和评估不传，照旧一次拿到完整答案。
        """
        on_text = (lambda text: on_event({"type": "delta", "text": text})) if on_event else None
        self.gate.new_turn()   # 顾客每发一则讯息，就是新的一轮
        contents = self.history + [types.Content(role="user", parts=[types.Part(text=message)])]
        self.current_contents = contents
        trace = []          # 这一题用了哪些工具、查到什么
        tokens = {"input": 0, "output": 0}
        answer = None

        for _ in range(MAX_STEPS):
            try:
                response = self._generate(contents, self.channel_note, on_text=on_text)
            except errors.APIError as error:
                trace.append({"tool": "error", "args": {}, "results": [f"{error.code} {error.status}"]})
                break
            self._count_tokens(response, tokens)

            if not response.candidates or response.candidates[0].content is None:
                break   # 被安全过滤挡下，或模型没有输出

            # 模型这一轮的输出（可能是答案，也可能是"我要用工具"）原封不动放回历史
            contents.append(response.candidates[0].content)

            calls = response.function_calls
            if not calls:
                answer = response.text   # 没有要用工具 → 这就是最后的答案
                break

            # 执行模型要求的每一个工具，把结果一起交回给模型
            result_parts = []
            for call in calls:
                result, info = self._run_tool(call)
                trace.append({"tool": call.name, "args": dict(call.args or {}), "results": info})
                if on_event:
                    on_event({"type": "tool", "step": trace[-1]})
                result_parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=result_parts))

        if not answer:
            answer = FALLBACK_ANSWER

        self.history = contents
        return {"answer": answer.strip(), "trace": trace, "tokens": tokens}
    @staticmethod
    @observe(name="gemini-call", as_type="generation")
    def _generate(contents, channel_note="", max_attempts=2, max_wait=40, on_text=None):
        """调用 Gemini，从模型清单的第一个开始试：
        - 429 今天额度用完 → 这个模型今天先不用，换下一个（明天自动恢复）
        - 429 每分钟上限 → 这个模型 60 秒内先不用，马上换下一个（顾客不用等）
        - 403／404 这个 key 不能用这个模型 → 一天内不用，换下一个
        - 500／503 Gemini 服务器太忙 → 同一个模型等 2 秒再试一次；还是太忙就 5 分钟内换别的
          （实测：3.8-flash 一直 503，原本一直等同一个模型，一题等了 163 秒）
        - 全部模型都只是"每分钟上限" → 照 Gemini 建议的秒数等一次，再从头试
        其他错误（例如 400 参数错）换模型也没用，直接抛出。
        on_text：有的话改用逐字模式（stream），每收到一段文字就交给 on_text。
        """
        last_error = None
        for round_ in (1, 2):
            # 全部都暂停时，还是试最后一个：拿到正常的 Gemini 错误，聊天会显示"系统暂时没办法回答"，不会当机
            for model in _available_models() or [MODELS[-1]]:
                for attempt in range(1, max_attempts + 1):
                    state = {"sent": False}   # 已经有文字送到顾客画面上了 → 不能重试也不能换模型（会重复显示）
                    try:
                        if on_text:
                            response = _stream(contents, channel_note, on_text, state, model)
                        else:
                            response = client.models.generate_content(model=model, contents=contents,
                                                                      config=build_config(channel_note))
                        # Observability：告诉 Langfuse 这次实际用了哪个模型、几个 token（@observe 自己不知道）
                        usage = response.usage_metadata
                        get_client().update_current_generation(model=model, usage_details={
                            "input": (usage.prompt_token_count or 0) if usage else 0,
                            "output": (usage.candidates_token_count or 0) if usage else 0,
                        })
                        return response
                    except errors.APIError as error:
                        last_error = error
                        if state["sent"]:
                            raise
                        if error.code in (500, 503) and attempt < max_attempts:
                            time.sleep(2)
                            continue
                        if _skip_model(model, error):
                            break        # 换下一个模型
                        raise
            # 全部模型都试过了。如果只是"每分钟上限"，等一下再从头试一次；今天额度全部用完就放弃
            if round_ == 2 or last_error is None or last_error.code != 429 or _is_daily_quota(str(last_error)):
                break
            match = re.search(r"retry in ([\d.]+)s", str(last_error))
            wait = min(float(match.group(1)) + 1 if match else 30, max_wait)
            log.warning("所有模型都到每分钟上限，等 %.0f 秒再试", wait)
            time.sleep(wait)
            for m, until in list(_unavailable.items()):
                if until - time.time() <= 60:   # 只清掉"每分钟上限"的，今天额度用完、太忙的照样跳过
                    _unavailable.pop(m)
        raise last_error or RuntimeError("没有可用的 Gemini 模型")

    @observe(name="run-tool", as_type="tool")
    def _run_tool(self, call):
        function = TOOL_FUNCTIONS.get(call.name)
        if function is None:
            return {"error": f"没有这个工具：{call.name}"}, []
        try:
            return function(self, **(call.args or {}))
        except Exception as error:
            # 工具出错（例如网络断了）：真正的原因只写进后端日志；
            # 交给模型（也就是会被念给顾客）的，只有一句客服该说的话，不带任何内部细节
            logging.getLogger("glow.agent").exception("工具 %s 执行失败", call.name)
            # Observability：在 Langfuse 把这一步标成 ERROR，方便筛选出所有出错的工具
            get_client().update_current_span(level="ERROR", status_message=f"{type(error).__name__}: {error}")
            return {"error": bookings.SYSTEM_BUSY}, ["❌ 系统出错（详情见后端日志）"]

    def transcript(self):
        """把对话整理成文字记录（只留顾客和客服说的话，不含工具调用），转人工时存进工单。"""
        lines = []
        for content in self.current_contents or self.history:
            text = "".join(p.text for p in (content.parts or []) if getattr(p, "text", None))
            if text.strip():
                lines.append({"role": "customer" if content.role == "user" else "ai", "text": text.strip()})
        return lines

    @staticmethod
    def _count_tokens(response, tokens):
        usage = getattr(response, "usage_metadata", None)
        if usage:
            tokens["input"] += usage.prompt_token_count or 0
            tokens["output"] += usage.candidates_token_count or 0
