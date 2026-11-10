"""第 5 步：自动读信、自动回信（Polling：每分钟检查一次）

⚠️ 这个程序在你的个人邮箱里运行，所以安全规则很严格，每一封信都要通过全部检查才会回复：
1. 只看寄到 GMAIL_COMPANY_ADDRESS（+glow 别名）的信，Gmail 搜索和代码各检查一次
2. 只看程序第一次启动之后才收到的信（时间存在 secrets/gmail_state.json），旧信一律不碰
3. 不回复我们自己寄出的信（有 X-Glow-AI 标头）
4. 不回复自动寄出的信：no-reply、退信、电子报、自动回复（避免两个机器人互相回信、无限循环）
5. 处理过的信加标签（GlowAI-Replied／GlowAI-Skipped／GlowAI-Handoff），不会处理第二次
7. 第 6 步：已经转人工、工单还没处理完的对话，AI 不再自动回复（交给同事），信件加 GlowAI-Handoff 标签
6. 每次最多处理 MAX_PER_ROUND 封（Gemini 免费方案有限速）

用法（在 backend 资料夹里）：
    python email_worker.py --once --dry-run   只检查一次，显示"会回什么"，不寄信、不加标签（先用这个！）
    python email_worker.py --once             只检查一次，真的回信
    python email_worker.py                    每 60 秒检查一次，一直运行（Ctrl + C 停止）
"""

import argparse
import json
import re
import time
from pathlib import Path

from agent.agent import FALLBACK_ANSWER, CustomerServiceAgent
from handoff import handoffs
from integrations import gmail

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
    print(f"第一次启动：只会处理 {time.strftime('%Y-%m-%d %H:%M', time.localtime(epoch))} 之后收到的信")
    return epoch


def search_query(company, since):
    # {A B} 是 Gmail 搜索的"或"。
    # 别人寄来的信看 Delivered-To；自己寄给自己的信没有 Delivered-To，只能看 To（测试时踩到的坑）
    return (f"{{to:{company} deliveredto:{company}}} in:inbox after:{since} "
            f"-label:{REPLIED_LABEL} -label:{SKIPPED_LABEL} -label:{HANDOFF_LABEL}")


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


# ---------- 处理一封信 ----------
def handle(msg, dry_run):
    agent = agents_by_thread.setdefault(msg["thread_id"], CustomerServiceAgent(
        channel="email", email_thread_id=msg["thread_id"], customer_email=msg["from_address"]))
    sender = msg["from_name"] or msg["from_address"]
    text = f"寄件人：{sender} <{msg['from_address']}>\n主旨：{msg['subject']}\n\n{msg['body']}"

    result = agent.chat(text)
    if result["answer"] == FALLBACK_ANSWER or any(step["tool"] == "error" for step in result["trace"]):
        print("   ❌ Gemini 出错或没有答案，这封信先不回，下一轮再试")
        return False

    for step in result["trace"]:
        print(f"   🔧 {step['tool']} → {', '.join(step['results'][:2])}")
    print("   ✉️ 回信内容：\n" + "\n".join("      " + line for line in result["answer"].splitlines()))

    if dry_run:
        return True
    original = msg["subject"].strip() or "您的询问"   # 没有主旨的信
    subject = original if original.lower().startswith("re:") else f"Re: {original}"
    gmail.send_email(msg["from_address"], subject, result["answer"], thread_id=msg["thread_id"],
                     in_reply_to=msg["message_id"], references=msg["references"], kind="auto-reply")
    gmail.add_label(msg["id"], REPLIED_LABEL, mark_read=True)
    return True


def run_once(dry_run):
    company = gmail.company_address()
    ids = gmail.search(search_query(company, start_epoch()), max_results=MAX_PER_ROUND)
    print(f"[{time.strftime('%H:%M:%S')}] 找到 {len(ids)} 封新信" + ("（dry-run：不寄信、不加标签）" if dry_run else ""))

    for message_id in reversed(ids):   # 旧的先处理
        msg = gmail.get_message(message_id)
        print(f"\n📩 {msg['from_address']}｜{msg['subject']}")
        ticket = handoffs.open_ticket_for_thread(msg["thread_id"])
        if ticket:
            print(f"   🙋 这个对话已经转人工（工单 {ticket['ticket_code']}，{handoffs.STATUS_NAMES[ticket['status']]}），AI 不回复，留给同事")
            if not dry_run:
                gmail.add_label(message_id, HANDOFF_LABEL)
            continue
        reason = skip_reason(msg, company)
        if reason:
            print(f"   ⏭️ 略过：{reason}")
            if not dry_run:
                gmail.add_label(message_id, SKIPPED_LABEL)
            continue
        handle(msg, dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--dry-run", action="store_true", help="只显示会回什么，不寄信、不加标签")
    args = parser.parse_args()

    if args.once:
        run_once(args.dry_run)
        return
    print(f"开始监听 {gmail.company_address()}，每 {POLL_SECONDS} 秒检查一次（Ctrl + C 停止）")
    while True:
        try:
            run_once(args.dry_run)
        except Exception as error:   # 网络断了之类的，等下一轮再试，不要整个停掉
            print(f"⚠️ 这一轮出错：{error}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
