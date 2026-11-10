"""Gmail：寄信、读信、回信、加标签

这个项目用的是个人邮箱，所以安全规则很严格（见 email_worker.py）：
只处理寄到 GMAIL_COMPANY_ADDRESS（例如 tehhongjun6+glow@gmail.com）的新信，其他信一律不碰。

Gmail 的"+ 别名"：寄到 名字+任何字@gmail.com 的信，都会收进 名字@gmail.com，
但信件的 Delivered-To 会保留 +glow，所以程序分得出哪些是"公司"的信。
"""

import base64
import html
import os
import re
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build

from integrations.google_auth import get_credentials

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

COMPANY_NAME = "Glow Atelier 光研美学"
AI_HEADER = "X-Glow-AI"          # 我们寄出的每一封信都加这个标头，读信时看到就跳过（避免回复自己）

_service = None


def get_service():
    global _service
    if _service is None:
        _service = build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)
    return _service


def company_address():
    """公司收信地址（+ 别名）。顾客回信会寄到这里。"""
    value = os.getenv("GMAIL_COMPANY_ADDRESS", "").strip()
    if "+" not in value:
        raise RuntimeError("请在 backend/.env 设定 GMAIL_COMPANY_ADDRESS，例如 tehhongjun6+glow@gmail.com")
    return value


# ---------- 寄信 ----------
def send_email(to, subject, body, thread_id=None, in_reply_to=None, references=None, kind="notice"):
    """寄一封纯文字信。回信时带上 thread_id 和 In-Reply-To，Gmail 才会把它放在同一个对话里。"""
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = formataddr((COMPANY_NAME, get_service().users().getProfile(userId="me").execute()["emailAddress"]))
    msg["Reply-To"] = company_address()       # 顾客按"回复"，信会寄到 +glow 地址，让 AI 处理
    msg["Subject"] = subject
    msg[AI_HEADER] = kind
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(body)

    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    sent = get_service().users().messages().send(userId="me", body=payload).execute()
    return sent["id"]


# ---------- 读信 ----------
def search(query, max_results=10):
    result = get_service().users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    return [m["id"] for m in result.get("messages", [])]


def _decode(data):
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _find_body(part, mime):
    if part.get("mimeType") == mime and part.get("body", {}).get("data"):
        return _decode(part["body"]["data"])
    for sub in part.get("parts", []) or []:
        found = _find_body(sub, mime)
        if found:
            return found
    return None


def _strip_quoted(text):
    """去掉回信里引用的旧内容（"> ……"、"On … wrote:"、"在 … 写道："之后的部分）。"""
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*(On .+wrote:|在.+写道[:：]|于.+写道[:：]|-----Original Message-----)", line):
            break
        if line.strip().startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def get_message(message_id):
    """读一封信，整理成 dict：寄件人、主旨、内文（去掉引用）、标头。"""
    msg = get_service().users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

    body = _find_body(msg["payload"], "text/plain")
    if body is None:
        html_body = _find_body(msg["payload"], "text/html") or ""
        body = html.unescape(re.sub(r"<[^>]+>", " ", html_body))

    name, address = parseaddr(headers.get("from", ""))
    return {
        "id": message_id,
        "thread_id": msg["threadId"],
        "from_name": name,
        "from_address": address.lower(),
        "to": headers.get("to", ""),
        "delivered_to": headers.get("delivered-to", ""),
        "subject": headers.get("subject", ""),
        "message_id": headers.get("message-id"),
        "references": headers.get("references"),
        "headers": headers,
        "body": _strip_quoted(body)[:4000],   # 太长的信只取前 4000 字
        "label_ids": msg.get("labelIds", []),
    }


# ---------- 标签 ----------
def label_id(name):
    """找标签，没有就建一个。"""
    service = get_service()
    for label in service.users().labels().list(userId="me").execute().get("labels", []):
        if label["name"] == name:
            return label["id"]
    return service.users().labels().create(userId="me", body={"name": name}).execute()["id"]


def add_label(message_id, label, mark_read=False):
    body = {"addLabelIds": [label_id(label)]}
    if mark_read:
        body["removeLabelIds"] = ["UNREAD"]
    get_service().users().messages().modify(userId="me", id=message_id, body=body).execute()
