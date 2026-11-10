"""第 6 步：转人工后台（终端版）

第 7 步会做网页版的后台，按钮背后调用的也是 handoff/handoffs.py 这几个函数。

用法（在 backend 资料夹里）：
    python admin.py list                        列出所有工单
    python admin.py list --status processing    只看待处理的
    python admin.py show HO-XXXXXX              看一张工单的详情和对话记录
    python admin.py accept HO-XXXXXX 美玲       接受（processing → accepted）
    python admin.py resolve HO-XXXXXX "已致电顾客，安排免费重做"   处理完毕（accepted → resolved）
"""

import argparse

from handoff import handoffs

ICONS = {"processing": "🟠", "accepted": "🔵", "resolved": "🟢"}


def show_list(status):
    rows = handoffs.list_handoffs(status)
    if not rows:
        print("没有工单")
        return
    for h in rows:
        who = f"｜{h['assigned_to']} 处理中" if h["assigned_to"] and h["status"] == "accepted" else ""
        print(f"{ICONS[h['status']]} {h['ticket_code']}  {h['status_name']:<4}  {h['created_at_local']}  "
              f"[{h['channel']}] {h['reason_name']}｜{h['customer_name'] or '-'}｜{h['contact']}{who}")
        print(f"      {h['summary']}")


def show_one(ticket):
    row = handoffs.get_handoff(ticket)
    if not row:
        print(f"找不到工单 {ticket}")
        return
    h = handoffs.describe(row)
    print(f"{ICONS[h['status']]} {h['ticket_code']}｜{h['status_name']}｜{h['reason_name']}｜渠道：{h['channel']}")
    print(f"顾客：{h['customer_name'] or '-'}｜联系：{h['contact']}｜相关预约：{h['booking_code'] or '-'}")
    print(f"摘要：{h['summary']}")
    if h["assigned_to"]:
        print(f"接手同事：{h['assigned_to']}")
    if h["staff_note"]:
        print(f"处理记录：{h['staff_note']}")
    print("\n—— 转人工前的对话 ——")
    for line in row["conversation"]:
        who = "顾客" if line["role"] == "customer" else "AI  "
        print(f"{who}：{line['text']}")


def report(result):
    if result["success"]:
        h = result["handoff"]
        print(f"✅ {h['ticket_code']} → {h['status_name']}")
    else:
        print(f"❌ {result['error']}")


def main():
    parser = argparse.ArgumentParser(description="转人工后台")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list"); p.add_argument("--status", choices=list(handoffs.STATUS_NAMES))
    p = sub.add_parser("show"); p.add_argument("ticket")
    p = sub.add_parser("accept"); p.add_argument("ticket"); p.add_argument("staff")
    p = sub.add_parser("resolve"); p.add_argument("ticket"); p.add_argument("note", nargs="?", default="")
    args = parser.parse_args()

    if args.command == "list":
        show_list(args.status)
    elif args.command == "show":
        show_one(args.ticket)
    elif args.command == "accept":
        report(handoffs.accept(args.ticket, args.staff))
    elif args.command == "resolve":
        report(handoffs.resolve(args.ticket, args.note))


if __name__ == "__main__":
    main()
