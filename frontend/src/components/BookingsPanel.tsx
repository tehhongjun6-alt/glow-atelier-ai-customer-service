import { useEffect, useState } from "react";
import { api, type Booking } from "../api";

const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
const DAYS_SHOWN = 14;

// Google Calendar 的嵌入网址（日历要在 Google 设成"公开"，别人才看得到）
function embedUrl(calendarId: string) {
  const params = new URLSearchParams({
    src: calendarId,
    ctz: "Asia/Kuala_Lumpur",
    mode: "WEEK",
    hl: "zh_CN",
    showTitle: "0",
    showPrint: "0",
    showCalendars: "0",
    showTz: "0",
  });
  return `https://calendar.google.com/calendar/embed?${params}`;
}

function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function BookingsPanel({ refreshKey }: { refreshKey: number }) {
  const [bookings, setBookings] = useState<Booking[] | null>(null);
  const [error, setError] = useState("");
  const [showCancelled, setShowCancelled] = useState(false);
  const [view, setView] = useState<"list" | "google">("list");
  const [calendarId, setCalendarId] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);

  function load() {
    api
      .bookings()
      .then((r) => {
        setBookings(r.bookings);
        setCalendarId(r.google_calendar_id);
        setFrameKey((k) => k + 1);   // Google 日历也重新载入
      })
      .catch((e) => setError((e as Error).message));
  }
  useEffect(load, [refreshKey]);

  if (error) return <p className="panel-error">预约载入失败：{error}</p>;
  if (!bookings) return <p className="panel-loading">正在载入预约……</p>;

  const today = new Date();
  const days = Array.from({ length: DAYS_SHOWN }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    return d;
  });
  const visible = bookings.filter((b) => showCancelled || b.status === "confirmed");
  const confirmedCount = bookings.filter((b) => b.status === "confirmed").length;

  return (
    <div className="bookings">
      <div className="panel-toolbar">
        <div className="view-switch" role="group" aria-label="显示方式">
          <button className={view === "list" ? "filter is-active" : "filter"} onClick={() => setView("list")}>
            列表
          </button>
          <button className={view === "google" ? "filter is-active" : "filter"} onClick={() => setView("google")}>
            Google 日历
          </button>
        </div>
        <div className="toolbar-actions">
          {view === "list" && (
            <label className="check">
              <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} />
              显示已取消
            </label>
          )}
          <button className="ghost" onClick={load}>重新整理</button>
        </div>
      </div>
      <p className="panel-intro panel-intro-wide">
        {view === "list"
          ? <>接下来 {DAYS_SHOWN} 天有 <strong>{confirmedCount}</strong> 个有效预约，资料来自数据库。</>
          : <>这是真的 Google Calendar「Glow Atelier 预约」日历。AI 订好预约后会同步过来，Google 有时要几秒钟才更新。</>}
      </p>

      {view === "google" && (
        calendarId ? (
          <iframe key={frameKey} className="gcal" title="Glow Atelier 预约（Google Calendar）" src={embedUrl(calendarId)} />
        ) : (
          <p className="panel-error">后端没有设定 GOOGLE_CALENDAR_ID。</p>
        )
      )}

      {view === "list" && (
      <div className="calendar">
        {days.map((d) => {
          const key = ymd(d);
          const closed = d.getDay() === 1;
          const items = visible.filter((b) => b.date === key);
          return (
            <section key={key} className={closed ? "day is-closed" : "day"} aria-label={`${key} ${WEEKDAYS[d.getDay()]}`}>
              <header className="day-head">
                <span className="day-date">{d.getMonth() + 1}/{d.getDate()}</span>
                <span className="day-week">{WEEKDAYS[d.getDay()]}</span>
              </header>
              {closed ? (
                <p className="day-note">休息</p>
              ) : items.length === 0 ? (
                <p className="day-note">没有预约</p>
              ) : (
                <ul className="day-list">
                  {items.map((b) => (
                    <li key={b.booking_code} className={b.status === "cancelled" ? "slot is-cancelled" : "slot"}>
                      <span className="slot-time">{b.time}–{b.end_time}</span>
                      <span className="slot-service">{b.service}</span>
                      <span className="slot-who">
                        {b.customer_name}｜{b.booking_code}
                        {b.status === "cancelled" && "｜已取消"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
      )}
    </div>
  );
}
