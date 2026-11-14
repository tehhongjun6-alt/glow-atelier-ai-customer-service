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


# ---------- 垃圾邮件：AI 不碰，像顾客的信转人工 ----------
@pytest.fixture
def labels(monkeypatch):
    """假的加标签，记录 (信件 id, 标签)；如果 AI 被叫到就直接让测试失败。"""
    added = []
    monkeypatch.setattr(gmail, "add_label", lambda message_id, label, mark_read=False: added.append((message_id, label)))
    monkeypatch.setattr(w, "handle", lambda *a, **k: pytest.fail("垃圾邮件不可以交给 AI"))
    return added


SPAM = {**NORMAL, "id": "m1", "thread_id": "t1", "from_name": "Amy", "subject": "星期一有开吗", "body": "请问星期一有营业吗？"}


def test_spam_query_only_looks_in_spam_with_same_filters():
    q = w.spam_query(COMPANY, 1794312662)
    assert "in:spam" in q and "in:inbox" not in q
    assert f"to:{COMPANY}" in q and "after:1794312662" in q and f"-label:{w.HANDOFF_LABEL}" in q


def test_customer_mail_in_spam_becomes_handoff_ticket(db, labels):
    outcome = w.process_spam_message(SPAM, COMPANY, dry_run=False)
    [ticket] = db.tables["handoffs"].rows
    assert "已转人工" in outcome and ticket["ticket_code"] in outcome
    assert ticket["channel"] == "email" and ticket["contact"] == "amy@gmail.com" and ticket["email_thread_id"] == "t1"
    assert "请问星期一有营业吗" in ticket["conversation"][0]["text"]     # 同事在后台看得到信的内容
    assert labels == [("m1", w.HANDOFF_LABEL)]                           # 下一轮不会再处理


def test_junk_in_spam_is_skipped_without_ticket(db, labels):
    outcome = w.process_spam_message({**SPAM, "from_address": "no-reply@shopee.com"}, COMPANY, dry_run=False)
    assert "略过" in outcome and db.tables["handoffs"].rows == []
    assert labels == [("m1", w.SKIPPED_LABEL)]


def test_spam_dry_run_changes_nothing(db, labels):
    assert "dry-run" in w.process_spam_message(SPAM, COMPANY, dry_run=True)
    assert db.tables["handoffs"].rows == [] and labels == []


def test_same_spam_thread_does_not_open_second_ticket(db, labels):
    w.process_spam_message(SPAM, COMPANY, dry_run=False)
    w.process_spam_message({**SPAM, "id": "m2"}, COMPANY, dry_run=False)
    assert len(db.tables["handoffs"].rows) == 1


# ---------- 代码保护：AI 说已转人工，就一定要真的有工单 ----------
class TranscriptAgent:
    def transcript(self):
        return [{"role": "customer", "text": "上次做的护理效果很差，我很生气"}]


SIGN = "\n\nGlow Atelier 光研美学 客服团队"
OK_TRACE = [{"tool": "request_human_handoff", "args": {}, "results": ["🙋 工单 HO-AAAAAA（投诉／不满意，待处理）"]}]


@pytest.mark.parametrize("claim", [
    "我已为您安排转接至我们的门市同事。",          # 实测时 AI 真的这样写（没开工单）
    "我已经为您建立了一份工单，并会转交给管理团队。",  # 评估 H6 第 3 次
])
def test_false_handoff_claim_opens_ticket_and_adds_code(db, claim):
    answer = w.ensure_handoff_is_real(SPAM, TranscriptAgent(), "Jun 您好，\n\n" + claim + SIGN, [], dry_run=False)
    [ticket] = db.tables["handoffs"].rows
    assert ticket["email_thread_id"] == "t1" and ticket["contact"] == "amy@gmail.com"
    assert f"工单编号是 {ticket['ticket_code']}" in answer
    assert answer.endswith("Glow Atelier 光研美学 客服团队")          # 编号放在署名前面


def test_real_handoff_is_left_alone(db):
    text = "已为您建立工单 HO-AAAAAA。" + SIGN
    assert w.ensure_handoff_is_real(SPAM, TranscriptAgent(), text, OK_TRACE, dry_run=False) == text
    assert db.tables["handoffs"].rows == []


@pytest.mark.parametrize("text", [
    "我们的营业时间是星期二至星期日。",
    "收到您的回复后，我会为您建立工单。",      # 还没做的说法，不算说谎（评估 H6 第 1 次）
])
def test_normal_reply_does_not_open_ticket(db, text):
    assert w.ensure_handoff_is_real(SPAM, TranscriptAgent(), text, [], dry_run=False) == text
    assert db.tables["handoffs"].rows == []


def test_false_claim_in_dry_run_opens_nothing(db):
    w.ensure_handoff_is_real(SPAM, TranscriptAgent(), "我已为您安排转接。", [], dry_run=True)
    assert db.tables["handoffs"].rows == []
