"""第 5 步：自动读信、自动回信（Polling：每分钟检查一次）

⚠️ 自动回信寄出去就收不回来，所以安全规则很严格，每一封信都要通过全部检查才会回复：
1. 只看寄到 GMAIL_COMPANY_ADDRESS（公司专用信箱）的信，Gmail 搜索和代码各检查一次
2. 只看程序第一次启动之后才收到的信（时间存在 secrets/gmail_state.json），旧信一律不碰
3. 不回复我们自己寄出的信（有 X-Glow-AI 标头）
4. 不回复自动寄出的信：no-reply、退信、电子报、自动回复（避免两个机器人互相回信、无限循环）
5. 处理过的信加标签（GlowAI-Replied／GlowAI-Skipped／GlowAI-Handoff），不会处理第二次
7. 第 6 步：已经转人工、工单还没处理完的对话，AI 不再自动回复（交给同事），信件加 GlowAI-Handoff 标签
6. 每次最多处理 MAX_PER_ROUND 封（Gemini 免费方案有限速）
8. 垃圾邮件：AI 不读、不回（可能是诈骗或提示词注入）；看起来像顾客的信开转人工工单，免得顾客被漏掉
9. AI 回信说「已转人工／已建立工单」却没有真的开工单 → 寄出前由代码补开，工单编号写进回信

用法（在 backend 资料夹里）：
    python email_worker.py --once --dry-run   只检查一次，显示"会回什么"，不寄信、不加标签（先用这个！）
    python email_worker.py --once             只检查一次，真的回信
    python email_worker.py                    每 60 秒检查一次，一直运行（Ctrl + C 停止）
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

from langfuse import get_client, observe, propagate_attributes

from agent.agent import FALLBACK_ANSWER, CustomerServiceAgent
from handoff import handoffs
from integrations import gmail
from log_setup import setup_logging

log = logging.getLogger("glow.email")

STATE_FILE = Path(__file__).resolve().parent / "secrets" / "gmail_state.json"
REPLIED_LABEL = "GlowAI-Replied"
SKIPPED_LABEL = "GlowAI-Skipped"
HANDOFF_LABEL = "GlowAI-Handoff"
POLL_SECONDS = 60
MAX_PER_ROUND = 5

AUTOMATED_SENDERS = re.compile(r"(no-?reply|mailer-daemon|postmaster|bounce|notification)", re.I)

# 每个电邮对话（thread）一个 Agent，记住来回的内容和等待确认的动作。
# 注意：存在内存里，程序重启就会忘记（之后可以改存数据库）。
agents_by_thread = {}


# ---------- 安全检查 ----------
def start_epoch():
    """程序第一次启动的时间。只处理这个时间之后的信。"""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))["start_epoch"]
    epoch = int(time.time())
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({"start_epoch": epoch}), encoding="utf-8")
    log.info("第一次启动：只会处理 %s 之后收到的信", time.strftime('%Y-%m-%d %H:%M', time.localtime(epoch)))
    return epoch


def search_query(company, since):
    # {A B} 是 Gmail 搜索的"或"。
    # 别人寄来的信看 Delivered-To；自己寄给自己的信没有 Delivered-To，只能看 To（测试时踩到的坑）
    return (f"{{to:{company} deliveredto:{company}}} in:inbox after:{since} "
            f"-label:{REPLIED_LABEL} -label:{SKIPPED_LABEL} -label:{HANDOFF_LABEL}")


def spam_query(company, since):
    """垃圾邮件里、寄到公司地址、还没处理过的信（条件和收件箱一样，只是 in:spam）。"""
    return search_query(company, since).replace("in:inbox", "in:spam")


def skip_reason(msg, company):
    """不应该自动回复的信，返回原因；可以回复就返回 None。"""
    h = msg["headers"]
    if company.lower() not in (msg["delivered_to"] + " " + msg["to"]).lower():
        return "不是寄到公司地址"
    if gmail.AI_HEADER.lower() in h:
        return "是我们自己寄出的信"
    if AUTOMATED_SENDERS.search(msg["from_address"]):
        return "自动寄件地址（no-reply／退信）"
    if h.get("auto-submitted", "no").lower() != "no":
        return "自动回复的信"
    if h.get("precedence", "").lower() in {"bulk", "list", "junk"} or "list-unsubscribe" in h:
        return "电子报／群发信"
    if not msg["body"].strip():
        return "没有内文"
    return None


# ---------- 代码保护：AI 说已转人工，就一定要真的有工单 ----------
# 实测发生过：AI 回信说「我已为您安排转接」「已经为您建立了一份工单」，却没有调用 request_human_handoff，
# 后台根本没有工单，顾客以为有人会联络他。Prompt 规则改了也只有 1/3 照做，所以用代码保证。
# 只抓「已…」的说法（已经做了）；「收到资料后会帮您转给同事」这种还没做的说法不算。
CLAIMS_HANDOFF = re.compile(r"已(经)?[^。！？\n]{0,10}(工单|转接|转交|转给|安排[^。！？\n]{0,6}同事)")
SIGNATURE = "Glow Atelier 光研美学 客服团队"


def handoff_succeeded(trace):
    return any(step["tool"] == "request_human_handoff" and step["results"][:1]
               and step["results"][0].startswith("🙋") for step in trace)


def ensure_handoff_is_real(msg, agent, answer, trace, dry_run):
    """回信说已转人工、但这一轮没有成功开工单 → 由代码补开工单，并把工单编号写进回信。返回要寄出的回信。"""
    if handoff_succeeded(trace) or not CLAIMS_HANDOFF.search(answer):
        return answer
    log.warning("AI 回信说已转人工，但没有真的开工单（thread %s）%s", msg["thread_id"],
                "，dry-run 不补开" if dry_run else "，由系统补开")
    get_client().update_current_span(level="WARNING", status_message="AI 说已转人工但没开工单，系统补开")
    if dry_run:
        return answer
    result = handoffs.create_handoff(
        channel="email", reason="other", contact=msg["from_address"],
        customer_name=msg["from_name"] or None, email_thread_id=msg["thread_id"],
        summary="AI 回信告诉顾客已转人工，但没有真的开工单，由系统自动补开。请看对话内容判断原因。",
        conversation=agent.transcript(),
    )
    code = result["handoff"]["ticket_code"]
    log.info("系统补开工单 %s", code)
    note = f"您的工单编号是 {code}，门市同事会尽快联络您。\n\n"
    if SIGNATURE in answer:   # 放在署名前面
        head, _, tail = answer.rpartition(SIGNATURE)
        return f"{head.rstrip()}\n\n{note}{SIGNATURE}{tail}"
    return f"{answer}\n\n{note}".rstrip()


# ---------- 处理一封信 ----------
def handle(msg, dry_run):
    agent = agents_by_thread.setdefault(msg["thread_id"], CustomerServiceAgent(
        channel="email", email_thread_id=msg["thread_id"], customer_email=msg["from_address"]))
    sender = msg["from_name"] or msg["from_address"]
    text = f"寄件人：{sender} <{msg['from_address']}>\n主旨：{msg['subject']}\n\n{msg['body']}"

    result = agent.chat(text)
    if result["answer"] == FALLBACK_ANSWER or any(step["tool"] == "error" for step in result["trace"]):
        log.warning("Gemini 出错或没有答案，这封信先不回，下一轮再试（thread %s）", msg["thread_id"])
        return False

    for step in result["trace"]:
        log.info("工具 %s → %s", step["tool"], ", ".join(step["results"][:2]))
    answer = ensure_handoff_is_real(msg, agent, result["answer"], result["trace"], dry_run)
    log.info("回信内容：\n%s", "\n".join("      " + line for line in answer.splitlines()))

    if dry_run:
        return True
    original = msg["subject"].strip() or "您的询问"   # 没有主旨的信
    subject = original if original.lower().startswith("re:") else f"Re: {original}"
    gmail.send_email(msg["from_address"], subject, answer, thread_id=msg["thread_id"],
                     in_reply_to=msg["message_id"], references=msg["references"], kind="auto-reply")
    gmail.add_label(msg["id"], REPLIED_LABEL, mark_read=True)
    return True


@observe(name="email-message", capture_input=False)
def process_message(msg, company, dry_run):
    """处理一封信，返回处理结果。
    Observability：一封信 = Langfuse 里的一条 trace，连被略过、转人工的信也看得到原因。
    """
    langfuse = get_client()
    # 只送寄件人和主旨（信的内文已经在 customer-chat 那一步里，不用重复送）
    langfuse.update_current_span(input={"from": msg["from_address"], "subject": msg["subject"]})

    ticket = handoffs.open_ticket_for_thread(msg["thread_id"])
    if ticket:
        log.info("这个对话已经转人工（工单 %s，%s），AI 不回复，留给同事",
                 ticket["ticket_code"], handoffs.STATUS_NAMES[ticket["status"]])
        if not dry_run:
            gmail.add_label(msg["id"], HANDOFF_LABEL)
        return f"已转人工（工单 {ticket['ticket_code']}），AI 不回复"

    reason = skip_reason(msg, company)
    if reason:
        log.info("略过：%s", reason)
        if not dry_run:
            gmail.add_label(msg["id"], SKIPPED_LABEL)
        return f"略过：{reason}"

    if handle(msg, dry_run):
        return "dry-run：回信已写好，没有寄出" if dry_run else "已回信"
    outcome = "Gemini 出错或没有答案，这封信先不回，下一轮再试"
    langfuse.update_current_span(level="WARNING", status_message=outcome)
    return outcome


SPAM_SUMMARY = "这封信被 Gmail 判成垃圾邮件，AI 没有读也没有回。请同事确认是不是顾客，是的话请在 Gmail 的垃圾邮件里找到这封信，亲自回复。"


@observe(name="email-spam-message", capture_input=False)
def process_spam_message(msg, company, dry_run):
    """垃圾邮件里的信：AI 不读、不回（可能是诈骗或提示词注入）。
    明显是机器寄的 → 略过；看起来像顾客 → 开转人工工单，交给同事判断。
    """
    get_client().update_current_span(input={"from": msg["from_address"], "subject": msg["subject"]})

    reason = skip_reason(msg, company)
    if reason:
        log.info("垃圾邮件，略过：%s", reason)
        if not dry_run:
            gmail.add_label(msg["id"], SKIPPED_LABEL)
        return f"垃圾邮件，略过：{reason}"

    if dry_run:
        log.info("垃圾邮件里疑似顾客的信：会开转人工工单（dry-run 没有开）")
        return "dry-run：会开转人工工单"
    # 信的内容放进工单，同事在「转人工后台」就看得到；AI 完全没有碰这封信
    result = handoffs.create_handoff(
        channel="email", reason="other", summary=SPAM_SUMMARY, contact=msg["from_address"],
        customer_name=msg["from_name"] or None, email_thread_id=msg["thread_id"],
        conversation=[{"role": "customer", "text": f"主旨：{msg['subject'] or '（无主旨）'}\n\n{msg['body']}"}],
    )
    gmail.add_label(msg["id"], HANDOFF_LABEL)
    ticket = result["handoff"]["ticket_code"]
    log.info("垃圾邮件里疑似顾客的信：已转人工（工单 %s），AI 不回复", ticket)
    return f"垃圾邮件，已转人工（工单 {ticket}）"


def run_once(dry_run):
    company = gmail.company_address()
    since = start_epoch()
    ids = gmail.search(search_query(company, since), max_results=MAX_PER_ROUND)
    log.info("找到 %d 封新信%s", len(ids), "（dry-run：不寄信、不加标签）" if dry_run else "")

    for message_id in reversed(ids):   # 旧的先处理
        msg = gmail.get_message(message_id)
        log.info("新信：%s｜%s", msg["from_address"], msg["subject"])
        # Observability：同一个 Gmail 对话串成一个 session，标记为电邮渠道
        with propagate_attributes(session_id=f"email-{msg['thread_id']}", tags=["email"]):
            process_message(msg, company, dry_run)

    # 垃圾邮件：顾客的信也可能被误判，不能就这样漏掉（但 AI 不碰，交给同事）
    spam_ids = gmail.search(spam_query(company, since), max_results=MAX_PER_ROUND, include_spam=True)
    if spam_ids:
        log.info("垃圾邮件里有 %d 封寄到公司地址的信", len(spam_ids))
    for message_id in reversed(spam_ids):
        msg = gmail.get_message(message_id)
        log.info("垃圾邮件：%s｜%s", msg["from_address"], msg["subject"])
        with propagate_attributes(session_id=f"email-{msg['thread_id']}", tags=["email", "spam"]):
            process_spam_message(msg, company, dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--dry-run", action="store_true", help="只显示会回什么，不寄信、不加标签")
    args = parser.parse_args()
    setup_logging("email_worker.log")   # 统一 log 格式，同时写进 backend/logs/email_worker.log

    if args.once:
        run_once(args.dry_run)
        return
    log.info("开始监听 %s，每 %d 秒检查一次（Ctrl + C 停止）", gmail.company_address(), POLL_SECONDS)
    while True:
        try:
            run_once(args.dry_run)
        except Exception:   # 网络断了之类的，等下一轮再试，不要整个停掉
            log.exception("这一轮出错，等下一轮再试")   # exception = ERROR 等级 + 完整出错位置
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
