"""换了 Google 帐号之后：把"未来的有效预约"搬到新帐号的日历

为什么要搬？每笔预约在数据库里记着 google_event_id（日历活动的编号）。
换帐号后，旧编号在新日历里找不到，改期／取消时就更新不到日历。
所以要在新日历重新建立活动，并把新的编号写回数据库。

- 只搬未来、状态是 confirmed 的预约（已取消、已过去的不用搬）
- 旧帐号日历上的活动不会被删除（程序已经没有旧帐号的授权），可以自己去旧日历删掉
- 再跑一次也安全：已经在新日历上的预约会跳过

用法（在 backend 资料夹里，先完成新帐号授权和 --setup 之后）：
    python migrate_calendar.py
"""

from booking import rules
from integrations import google_calendar
from rag.store import supabase


def already_in_new_calendar(event_id):
    try:
        google_calendar.get_service().events().get(
            calendarId=google_calendar.calendar_id(), eventId=event_id).execute()
        return True
    except Exception:
        return False


def main():
    rows = (supabase.table("bookings").select("*").eq("status", "confirmed")
            .gte("start_at", rules.now().isoformat()).order("start_at").execute().data)
    print(f"未来的有效预约：{len(rows)} 笔")
    moved = 0
    for row in rows:
        if row.get("google_event_id") and already_in_new_calendar(row["google_event_id"]):
            print(f"⏭️ {row['booking_code']} 已经在新日历上，跳过")
            continue
        event_id = google_calendar.create_event(row)
        supabase.table("bookings").update({"google_event_id": event_id}).eq("id", row["id"]).execute()
        moved += 1
        print(f"✅ {row['booking_code']} {row['service']} {row['start_at'][:16]} → 新日历")
    print(f"完成：搬了 {moved} 笔")


if __name__ == "__main__":
    main()
