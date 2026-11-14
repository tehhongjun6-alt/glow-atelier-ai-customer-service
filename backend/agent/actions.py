"""两段式确认（Human-in-the-loop）：不可逆的动作，要顾客在"下一则讯息"确认后才执行

流程：
    顾客："我叫陈小明，电话……"
    Agent → request_booking(...)    只检查、暂存，什么都不写，返回摘要
    Agent："帮您预约 11/18 15:00 的 Aqua Peel……确认吗？"
    ─────────────── 等顾客回覆（新的一轮）───────────────
    顾客："确认"
    Agent → confirm_action()        这时才真正写进 Supabase + Google Calendar

关键：confirm_action 会检查"暂存的动作是不是在之前的轮次建立的"。
改期和取消还要多一关：顾客要提供寄到登记电邮的验证码（booking/otp.py）。
如果模型在同一轮里 request 完马上 confirm（等于没问顾客），代码会拒绝。
为什么不只靠 prompt？测试时模型真的跳过了确认步骤，所以要用代码挡。
"""

import logging

from booking import bookings, notifications, otp

COMMITS = {
    "booking": bookings.commit_booking,
    "cancel": bookings.commit_cancel,
    "reschedule": bookings.commit_reschedule,
}


class ActionGate:
    """每位顾客（每个 Agent）一个：记录现在是第几轮，以及等待确认的动作。"""

    def __init__(self):
        self.turn = 0
        self.pending = None   # {"kind", "payload", "summary", "turn"}

    def new_turn(self):
        self.turn += 1

    def stage(self, kind, payload, summary, otp_record=None, otp_sent_to=None):
        """暂存一个动作（新的会取代旧的，例如顾客改了时间）。
        otp_record：改期／取消要验证码时，存验证码的记录（只有哈希值）。"""
        self.pending = {"kind": kind, "payload": payload, "summary": summary, "turn": self.turn, "otp": otp_record}
        result = {
            "success": True,
            "status": "waiting_for_customer_confirmation",
            "summary": summary,
            "next_step": "把 summary 整理给顾客确认。顾客在下一则讯息明确同意后，才调用 confirm_action。",
        }
        if otp_record:
            result["otp_sent_to"] = otp_sent_to
            result["next_step"] = (
                f"验证码已寄到 {otp_sent_to}（10 分钟内有效）。把 summary 整理给顾客确认，并请顾客提供信里的 6 位数验证码。"
                "顾客在下一则讯息同意并给了验证码后，才调用 confirm_action(otp_code=...)。"
            )
        return result

    def confirm(self, otp_code=None):
        if not self.pending:
            return {"success": False, "error": "没有等待确认的动作，请先用 request_booking／request_cancel／request_reschedule"}
        if self.pending["turn"] == self.turn:
            return {"success": False,
                    "error": "顾客还没有回覆确认。先把摘要给顾客看，等顾客下一则讯息说确认后，才能调用 confirm_action。"}

        if self.pending.get("otp"):
            problem = otp.check(self.pending["otp"], otp_code)
            if problem == "wrong_code":
                # 把"还剩几次"直接告诉 AI：AI 看不到代码，不给数字它只能自己猜（测试时真的猜错、提早转人工）
                left = otp.MAX_ATTEMPTS - self.pending["otp"]["attempts"]
                return {"success": False, "attempts_left": left,
                        "error": f"验证码不对，还可以再试 {left} 次。请顾客再看一次信里的 6 位数验证码；如果收到好几封，要用最新一封里的。"}
            if problem:   # 过期或错太多次：作废，要重新申请
                self.pending = None
                why = "验证码已经过期" if problem == "expired" else "验证码错太多次"
                return {"success": False, "error": f"{why}，这次的申请已作废。要继续的话，重新 request_cancel／request_reschedule 寄新的验证码。"}

        pending, self.pending = self.pending, None
        result = COMMITS[pending["kind"]](pending["payload"])
        result["action"] = pending["kind"]

        # 第 5 步：预约／改期／取消成功后，寄通知信给顾客
        result["confirmation_email_sent"] = False
        if result.get("success"):
            sent, error = notifications.send_for(pending["kind"], result)
            result["confirmation_email_sent"] = sent
            if error:
                # 寄信失败的原因只写进日志；AI 只需要知道"没寄出"（confirmation_email_sent=False）
                logging.getLogger("glow.actions").error("通知信寄送失败：%s", error)
        return result
