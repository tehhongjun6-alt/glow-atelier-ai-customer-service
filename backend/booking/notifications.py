"""第 5 步：预约通知信（预约成功、改期、取消）

由 agent/actions.py 在 confirm_action 成功之后调用。
信件内容用固定模板（不是 AI 写的）：通知信要准确、格式一致，不需要创意。
"""

import threading
from collections import defaultdict

from langfuse import get_client, observe

from booking import rules
from integrations import gmail

# 防邮件轰炸：电邮不用验证，坏人可以填别人的电邮，订了又取消，用你的 Gmail 一直寄信给对方。
# 所以每个电邮每天最多收 MAX_EMAILS_PER_ADDRESS_PER_DAY 封系统信；超过就不寄（预约照样成立）。
# 计数存在内存里，服务器重启会归零。
MAX_EMAILS_PER_ADDRESS_PER_DAY = 5
_sent_today = defaultdict(int)     # (日期, 电邮) → 今天寄了几封
_sent_lock = threading.Lock()


def _allow(address):
    key = (rules.now().strftime("%Y-%m-%d"), address.strip().lower())
    with _sent_lock:
        if _sent_today[key] >= MAX_EMAILS_PER_ADDRESS_PER_DAY:
            return False
        _sent_today[key] += 1
        return True

ADDRESS = "No. 23-1, Jalan SS 2/64, SS2, 47300 Petaling Jaya, Selangor"
FOOTER = (
    "\n\n——\nGlow Atelier 光研美学\n"
    f"{ADDRESS}\n"
    "营业时间：星期二至星期日 10:00–20:00（星期一休息）\n"
    "如需改期或取消，直接回复这封信，或在网站聊天客服提供预约编号和手机号码即可。\n"
    "（这是系统自动寄出的通知信）"
)


def _details(b):
    lines = [
        f"预约编号：{b['booking_code']}",
        f"服务项目：{b['service']}",
        f"日期：{b['date']}（{b['weekday']}）",
        f"时间：{b['time']} – {b['end_time']}",
    ]
    if b.get("beautician"):
        lines.append(f"指定美容师：{b['beautician']}")
    if b.get("notes"):
        lines.append(f"备注：{b['notes']}")
    return "\n".join(lines)


def booking_confirmed(b):
    subject = f"【Glow Atelier】预约确认 {b['booking_code']}｜{b['date']} {b['time']}"
    body = (
        f"{b['customer_name']} 您好，\n\n"
        "谢谢您的预约！以下是您的预约资料：\n\n"
        f"{_details(b)}\n\n"
        "温馨提醒：\n"
        "- 请提早 10 分钟到，第一次来需要填写简单的健康表格\n"
        "- 预约时间 24 小时前改期或取消不收费\n"
        "- 迟到超过 30 分钟会当作缺席处理"
        f"{FOOTER}"
    )
    return subject, body


def booking_rescheduled(old, b):
    subject = f"【Glow Atelier】改期确认 {b['booking_code']}｜{b['date']} {b['time']}"
    body = (
        f"{b['customer_name']} 您好，\n\n"
        "您的预约已经改期：\n\n"
        f"原本：{old['date']}（{old['weekday']}）{old['time']}\n"
        f"改为：{b['date']}（{b['weekday']}）{b['time']} – {b['end_time']}\n\n"
        f"{_details(b)}"
        f"{FOOTER}"
    )
    return subject, body


def booking_cancelled(b):
    subject = f"【Glow Atelier】取消确认 {b['booking_code']}"
    body = (
        f"{b['customer_name']} 您好，\n\n"
        "您的以下预约已经取消：\n\n"
        f"{_details(b)}\n\n"
        "期待下次为您服务，欢迎随时再预约。"
        f"{FOOTER}"
    )
    return subject, body


def build(kind, result):
    b = result["booking"]
    if kind == "booking":
        return booking_confirmed(b)
    if kind == "reschedule":
        return booking_rescheduled(result["old"], b)
    return booking_cancelled(b)


@observe(name="confirmation-email")
def send_for(kind, result):
    """寄通知信。成功返回 (True, None)；失败返回 (False, 原因)。寄信失败不影响预约本身。"""
    address = result["booking"]["email"]
    if not _allow(address):
        reason = f"{address} 今天已经收到 {MAX_EMAILS_PER_ADDRESS_PER_DAY} 封系统信，不再寄送（防止邮件轰炸）"
        get_client().update_current_span(level="WARNING", status_message=reason)
        return False, reason
    try:
        subject, body = build(kind, result)
        gmail.send_email(address, subject, body, kind=f"booking-{kind}")
        return True, None
    except Exception as error:
        get_client().update_current_span(level="ERROR", status_message=f"通知信没寄出：{error}")
        return False, str(error)
