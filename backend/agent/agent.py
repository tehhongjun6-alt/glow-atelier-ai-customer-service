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

import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from agent.actions import ActionGate
from agent.prompts import CHANNEL_NOTES, SYSTEM_PROMPT
from agent.tools import TOOL_FUNCTIONS, TOOLS
from booking import rules

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
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

    def chat(self, message):
        """处理顾客的一则讯息，返回答案和过程记录。"""
        self.gate.new_turn()   # 顾客每发一则讯息，就是新的一轮
        contents = self.history + [types.Content(role="user", parts=[types.Part(text=message)])]
        self.current_contents = contents
        trace = []          # 这一题用了哪些工具、查到什么
        tokens = {"input": 0, "output": 0}
        answer = None

        for _ in range(MAX_STEPS):
            try:
                response = self._generate(contents, self.channel_note)
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
                result_parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=result_parts))

        if not answer:
            answer = FALLBACK_ANSWER

        self.history = contents
        return {"answer": answer.strip(), "trace": trace, "tokens": tokens}

    @staticmethod
    def _generate(contents, channel_note="", max_attempts=3, max_wait=40):
        """调用 Gemini，遇到暂时性的错误就等一下再试：
        - 429：免费方案每分钟 15 次的限速 → 照 Gemini 建议的秒数等
        - 500／503：Gemini 服务器太忙 → 等 3 秒、6 秒
        其他错误（例如 400 参数错）重试也没用，直接抛出。
        """
        for attempt in range(1, max_attempts + 1):
            try:
                return client.models.generate_content(model=MODEL, contents=contents, config=build_config(channel_note))
            except errors.APIError as error:
                if error.code not in (429, 500, 503) or attempt == max_attempts:
                    raise
                if error.code == 429:
                    match = re.search(r"retry in ([\d.]+)s", str(error))
                    wait = min(float(match.group(1)) + 1 if match else 30, max_wait)
                else:
                    wait = 3 * attempt
                print(f"   ⏳ Gemini 返回 {error.code}，等 {wait:.0f} 秒再试（第 {attempt} 次）……")
                time.sleep(wait)

    def _run_tool(self, call):
        function = TOOL_FUNCTIONS.get(call.name)
        if function is None:
            return {"error": f"没有这个工具：{call.name}"}, []
        try:
            return function(self, **(call.args or {}))
        except Exception as error:   # 工具出错（例如网络断了），告诉模型，让它向顾客说明
            return {"error": f"工具执行失败：{error}"}, []

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
