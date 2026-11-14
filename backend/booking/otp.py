"""改期、取消前的验证码（OTP，One-Time Password）

为什么要有？以前改期、取消只要"预约编号 + 手机号码"。
知道这两样的人（例如看过对方确认信的人）就能改别人的预约。
现在多一关：验证码寄到预约时登记的电邮，只有那个信箱的主人收得到。

规则：
- 6 位数字，10 分钟内有效
- 最多输错 5 次，超过就作废，要重新申请
- 只存验证码的"哈希值"（hash），不存原本的数字：就算记录外流，也看不出验证码
- 寄信次数和通知信共用"每个电邮每天最多 5 封"的上限（防止被拿来轰炸别人的信箱）
"""

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import timedelta

from booking import notifications, rules
from integrations import gmail

VALID_MINUTES = 10
MAX_ATTEMPTS = 5


def normalize(code):
    """把顾客打的验证码整理成标准的 6 位数，避免"明明对却被判错"：
    - 全角数字（中文输入法）：４８２９１３ → 482913
    - 中间的空格、横线：482 913、482-913 → 482913
    - 开头的 0 被吃掉（AI 当成数字传进来）：12345 → 012345
    整理后还是要和原本的 6 位数完全一样才算对，所以不会降低安全性。
    """
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(code or "")))
    return digits.zfill(6) if 0 < len(digits) <= 6 else digits


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def _send(address, code):
    subject = f"【Glow Atelier】验证码 {code}"
    body = (
        f"您好，\n\n您正在申请改期或取消预约，验证码是：\n\n    {code}\n\n"
        f"请在 {VALID_MINUTES} 分钟内把验证码告诉客服。\n"
        "如果这不是您本人操作，请忽略这封信，您的预约不会被改动。"
        + notifications.FOOTER
    )
    gmail.send_email(address, subject, body, kind="otp")


def issue(address):
    """产生验证码并寄出。成功返回 (记录, None)；失败返回 (None, 原因)。"""
    if not notifications._allow(address):
        return None, "这个电邮今天收到的系统信太多了，暂时不能再寄验证码"
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        _send(address, code)
    except Exception as error:
        return None, f"验证码寄送失败：{error}"
    return {"hash": _hash(code), "expires_at": rules.now() + timedelta(minutes=VALID_MINUTES), "attempts": 0}, None


def check(record, code):
    """核对验证码。正确返回 None；错误返回原因（会累计错误次数）。"""
    if rules.now() > record["expires_at"]:
        return "expired"
    if record["attempts"] >= MAX_ATTEMPTS:
        return "too_many_attempts"
    record["attempts"] += 1
    # compare_digest：比较时间不会因为"前几位对了"而变长，避免被一位一位猜出来
    if not hmac.compare_digest(_hash(normalize(code)), record["hash"]):
        return "too_many_attempts" if record["attempts"] >= MAX_ATTEMPTS else "wrong_code"
    return None
