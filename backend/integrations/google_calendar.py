"""Google Calendar：把预约放进日历

用一个专用的日历"Glow Atelier 预约"，不会和你自己的行程混在一起。

用法（在 backend 资料夹里，登录授权之后）：
    python -m integrations.google_calendar --setup    建立专用日历，日历 ID 自动写进 .env
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build

from integrations.google_auth import get_credentials

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

CALENDAR_NAME = "Glow Atelier 预约"
TIMEZONE = "Asia/Kuala_Lumpur"

_service = None


def get_service():
    global _service
    if _service is None:
        _service = build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)
    return _service


def calendar_id():
    value = os.getenv("GOOGLE_CALENDAR_ID")
    if not value:
        raise RuntimeError("还没有设定 GOOGLE_CALENDAR_ID，请先运行：python -m integrations.google_calendar --setup")
    return value


def _event_body(booking):
    """把一笔预约变成 Google Calendar 活动的格式。"""
    lines = [
        f"预约编号：{booking['booking_code']}",
        f"顾客：{booking['customer_name']}",
        f"电话：{booking['phone']}",
        f"电邮：{booking['email']}",
        f"服务：{booking['service']}",
    ]
    if booking.get("beautician"):
        lines.append(f"指定美容师：{booking['beautician']}")
    if booking.get("notes"):
        lines.append(f"备注：{booking['notes']}")

    return {
        "summary": f"{booking['service']}｜{booking['customer_name']}",
        "description": "\n".join(lines),
        "start": {"dateTime": booking["start_at"], "timeZone": TIMEZONE},
        "end": {"dateTime": booking["end_at"], "timeZone": TIMEZONE},
    }


def create_event(booking):
    """新增活动，返回活动 ID。不邀请顾客（确认信由第 5 步用 Gmail 发）。"""
    event = get_service().events().insert(
        calendarId=calendar_id(), body=_event_body(booking), sendUpdates="none"
    ).execute()
    return event["id"]


def update_event_time(event_id, start_at, end_at):
    """改期：只改开始和结束时间。"""
    get_service().events().patch(
        calendarId=calendar_id(),
        eventId=event_id,
        body={
            "start": {"dateTime": start_at, "timeZone": TIMEZONE},
            "end": {"dateTime": end_at, "timeZone": TIMEZONE},
        },
        sendUpdates="none",
    ).execute()


def delete_event(event_id):
    """取消：从日历删除活动（数据库里的记录会保留，状态改成 cancelled）。"""
    get_service().events().delete(calendarId=calendar_id(), eventId=event_id, sendUpdates="none").execute()


def setup_calendar():
    """建立专用日历，并把日历 ID 写进 .env。已经有同名日历就直接用。"""
    service = get_service()
    for item in service.calendarList().list().execute().get("items", []):
        if item.get("summary") == CALENDAR_NAME:
            cal_id = item["id"]
            print(f"已经有日历「{CALENDAR_NAME}」")
            break
    else:
        cal_id = service.calendars().insert(body={"summary": CALENDAR_NAME, "timeZone": TIMEZONE}).execute()["id"]
        print(f"✅ 建立了日历「{CALENDAR_NAME}」")

    env_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    if "GOOGLE_CALENDAR_ID=" not in env_text:
        with ENV_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\nGOOGLE_CALENDAR_ID={cal_id}\n")
        print("✅ 日历 ID 已写进 backend/.env")
    print(f"日历 ID：{cal_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="建立专用日历")
    if parser.parse_args().setup:
        setup_calendar()
