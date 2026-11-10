"""使用次数限制：保护 Gemini 免费额度，避免 demo 被人狂按发送弄坏

三道限制（任何一道超过就拒绝，返回 429）：
1. 每个 IP 每分钟最多 PER_MINUTE 则讯息      → 挡住狂按
2. 每个 IP 每天最多 PER_DAY 则讯息            → 挡住同一个人玩一整天
3. 全站每天最多 GLOBAL_PER_DAY 则讯息         → 不管多少人来，Gemini 的每日额度都不会被用完

数字可以在 .env 调整：CHAT_LIMIT_PER_MINUTE、CHAT_LIMIT_PER_DAY、CHAT_LIMIT_GLOBAL_PER_DAY。
计数存在内存里：服务器重启会归零（demo 够用；多台服务器时要改用 Redis 之类的共用储存）。
"""

import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from booking import rules


class RateLimiter:
    def __init__(self, per_minute, per_day, global_per_day, clock=time.time):
        self.per_minute = per_minute
        self.per_day = per_day
        self.global_per_day = global_per_day
        self.clock = clock                       # 测试时可以换成假的时钟
        self.recent = defaultdict(deque)         # IP → 最近一分钟内每则讯息的时间
        self.daily = defaultdict(int)            # (日期, IP) → 今天几则
        self.global_daily = defaultdict(int)     # 日期 → 全站今天几则
        self.lock = threading.Lock()             # FastAPI 会同时处理好几个请求

    def _today(self):
        return datetime.fromtimestamp(self.clock(), rules.TIMEZONE).strftime("%Y-%m-%d")

    def check(self, key):
        """可以就记一次并返回 None；超过限制就返回原因（中文，直接给顾客看）。"""
        with self.lock:
            now = self.clock()
            today = self._today()
            window = self.recent[key]
            while window and now - window[0] >= 60:
                window.popleft()

            if self.global_daily[today] >= self.global_per_day:
                return "今天的 demo 体验人数已经满了，请明天再来试试。"
            if self.daily[(today, key)] >= self.per_day:
                return f"今天已经发了 {self.per_day} 则讯息，达到每人每日上限，请明天再来试试。"
            if len(window) >= self.per_minute:
                wait = int(60 - (now - window[0])) + 1
                return f"讯息发得太快了，请等 {wait} 秒再试。"

            window.append(now)
            self.daily[(today, key)] += 1
            self.global_daily[today] += 1
            return None


chat_limiter = RateLimiter(
    per_minute=int(os.getenv("CHAT_LIMIT_PER_MINUTE", "8")),
    per_day=int(os.getenv("CHAT_LIMIT_PER_DAY", "60")),
    global_per_day=int(os.getenv("CHAT_LIMIT_GLOBAL_PER_DAY", "300")),
)


def client_ip(request):
    """部署后请求会经过代理，真正的 IP 在 X-Forwarded-For 的第一个。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
