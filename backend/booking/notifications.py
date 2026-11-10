"""第 5 步：预约通知信（预约成功、改期、取消）

由 agent/actions.py 在 confirm_action 成功之后调用。
信件内容用固定模板（不是 AI 写的）：通知信要准确、格式一致，不需要创意。
"""

from integrations import gmail

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


def send_for(kind, result):
    """寄通知信。成功返回 (True, None)；失败返回 (False, 原因)。寄信失败不影响预约本身。"""
    try:
        subject, body = build(kind, result)
        gmail.send_email(result["booking"]["email"], subject, body, kind=f"booking-{kind}")
        return True, None
    except Exception as error:
        return False, str(error)
