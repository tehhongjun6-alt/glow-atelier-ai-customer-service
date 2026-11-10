"""自动回信的安全检查（email_worker.py）

这个程序在个人邮箱里运行（500+ 封未读），所以要确保它只回"寄到 +glow 的顾客来信"。
"""

import pytest

import email_worker as w
from integrations import gmail

COMPANY = "tehhongjun6+glow@gmail.com"
NORMAL = {"delivered_to": COMPANY, "to": COMPANY, "from_address": "amy@gmail.com", "headers": {}, "body": "雾眉几钱？"}


@pytest.mark.parametrize("case, changes, expected", [
    ("寄到个人地址（没有 +glow）", {"delivered_to": "tehhongjun6@gmail.com", "to": "tehhongjun6@gmail.com"}, "不是寄到公司地址"),
    ("我们自己寄出的信", {"headers": {gmail.AI_HEADER.lower(): "auto-reply"}}, "自己寄出"),
    ("no-reply 寄件人", {"from_address": "no-reply@shopee.com"}, "自动寄件"),
    ("退信", {"from_address": "mailer-daemon@googlemail.com"}, "自动寄件"),
    ("自动回复（例如休假通知）", {"headers": {"auto-submitted": "auto-replied"}}, "自动回复"),
    ("电子报", {"headers": {"list-unsubscribe": "<mailto:x>"}}, "电子报"),
    ("群发信", {"headers": {"precedence": "bulk"}}, "电子报"),
    ("空白内文", {"body": "   "}, "没有内文"),
])
def test_skips_mail_that_must_not_be_answered(case, changes, expected):
    reason = w.skip_reason({**NORMAL, **changes}, COMPANY)
    assert reason and expected in reason, case


def test_answers_normal_customer_mail():
    assert w.skip_reason(NORMAL, COMPANY) is None


def test_self_sent_test_mail_without_delivered_to_is_answered():
    """自己寄给自己的信没有 Delivered-To（测试时踩过的坑），只要 To 有 +glow 就要处理。"""
    assert w.skip_reason({**NORMAL, "delivered_to": ""}, COMPANY) is None


def test_search_query_has_all_filters():
    q = w.search_query(COMPANY, 1794312662)
    assert f"to:{COMPANY}" in q and f"deliveredto:{COMPANY}" in q        # 只找 +glow
    assert "after:1794312662" in q                                       # 只找启动后的新信
    for label in (w.REPLIED_LABEL, w.SKIPPED_LABEL, w.HANDOFF_LABEL):
        assert f"-label:{label}" in q                                    # 处理过的不再处理
