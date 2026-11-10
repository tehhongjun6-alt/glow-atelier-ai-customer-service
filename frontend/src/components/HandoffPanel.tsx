import { useEffect, useState } from "react";
import { api, type Handoff, type HandoffStatus } from "../api";

const FILTERS: { id: HandoffStatus | "all"; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "processing", label: "待处理" },
  { id: "accepted", label: "处理中" },
  { id: "resolved", label: "处理完毕" },
];

const STEPS: { id: HandoffStatus; label: string }[] = [
  { id: "processing", label: "后台收到" },
  { id: "accepted", label: "同事接手" },
  { id: "resolved", label: "处理完毕" },
];

function Stepper({ status }: { status: HandoffStatus }) {
  const current = STEPS.findIndex((s) => s.id === status);
  return (
    <ol className="stepper" aria-label="工单进度">
      {STEPS.map((s, i) => (
        <li key={s.id} className={i < current ? "is-done" : i === current ? "is-current" : ""}>
          <span className="step-index">{i + 1}</span>
          {s.label}
        </li>
      ))}
    </ol>
  );
}

function Detail({ code, onChanged }: { code: string; onChanged: () => void }) {
  const [ticket, setTicket] = useState<Handoff | null>(null);
  const [staff, setStaff] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTicket(null);
    setError("");
    api.handoff(code).then(setTicket).catch((e) => setError((e as Error).message));
  }, [code]);

  async function act(run: () => Promise<Handoff>) {
    setSaving(true);
    setError("");
    try {
      await run();
      setTicket(await api.handoff(code));
      setNote("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!ticket) return <div className="ticket-detail">{error ? <p className="panel-error">{error}</p> : <p className="panel-loading">载入中……</p>}</div>;

  return (
    <div className="ticket-detail">
      <header className="ticket-head">
        <h3>{ticket.ticket_code}</h3>
        <span className={`status status-${ticket.status}`}>{ticket.status_name}</span>
      </header>
      <Stepper status={ticket.status} />

      <dl className="facts">
        <dt>原因</dt><dd>{ticket.reason_name}</dd>
        <dt>顾客</dt><dd>{ticket.customer_name ?? "（没有留名字）"}</dd>
        <dt>联系方式</dt><dd>{ticket.contact}</dd>
        <dt>来自</dt><dd>{ticket.channel === "email" ? "电邮" : "网页聊天"}，{ticket.created_at_local}</dd>
        {ticket.booking_code && (<><dt>相关预约</dt><dd>{ticket.booking_code}</dd></>)}
        {ticket.assigned_to && (<><dt>接手同事</dt><dd>{ticket.assigned_to}</dd></>)}
      </dl>

      <p className="summary">{ticket.summary}</p>

      {ticket.status === "processing" && (
        <form className="ticket-action" onSubmit={(e) => { e.preventDefault(); act(() => api.accept(code, staff)); }}>
          <label htmlFor="staff">接手的同事</label>
          <div className="row">
            <input id="staff" value={staff} onChange={(e) => setStaff(e.target.value)} placeholder="例如 美玲" />
            <button className="primary" disabled={saving || !staff.trim()}>接受工单</button>
          </div>
        </form>
      )}
      {ticket.status === "accepted" && (
        <form className="ticket-action" onSubmit={(e) => { e.preventDefault(); act(() => api.resolve(code, note)); }}>
          <label htmlFor="note">处理记录</label>
          <textarea id="note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：已致电顾客，安排免费复诊" />
          <button className="primary" disabled={saving}>标记处理完毕</button>
        </form>
      )}
      {ticket.status === "resolved" && ticket.staff_note && <p className="resolved-note">处理记录：{ticket.staff_note}</p>}
      {error && <p className="panel-error" role="alert">{error}</p>}

      <h4 className="transcript-title">转人工前的对话</h4>
      <ul className="transcript">
        {(ticket.conversation ?? []).map((line, i) => (
          <li key={i} className={`line line-${line.role}`}>
            <span className="line-who">{line.role === "customer" ? "顾客" : "AI"}</span>
            <span className="line-text">{line.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function HandoffPanel({ refreshKey }: { refreshKey: number }) {
  const [tickets, setTickets] = useState<Handoff[] | null>(null);
  const [filter, setFilter] = useState<HandoffStatus | "all">("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");

  function load() {
    api
      .handoffs()
      .then((r) => {
        setTickets(r.handoffs);
        setSelected((s) => s ?? r.handoffs.find((h) => h.status !== "resolved")?.ticket_code ?? r.handoffs[0]?.ticket_code ?? null);
      })
      .catch((e) => setError((e as Error).message));
  }
  useEffect(load, [refreshKey]);

  if (error) return <p className="panel-error">工单载入失败：{error}</p>;
  if (!tickets) return <p className="panel-loading">正在载入工单……</p>;

  const count = (id: HandoffStatus | "all") => tickets.filter((t) => id === "all" || t.status === id).length;
  const visible = tickets.filter((t) => filter === "all" || t.status === filter);

  return (
    <div className="handoffs">
      <p className="demo-note" role="note">
        示范用后台：为了让大家体验，这里不需要登录，任何人都能接受或处理工单。正式上线时，只有登录的门市同事才能操作。
      </p>
      <div className="filters" role="group" aria-label="按状态筛选">
        {FILTERS.map((f) => (
          <button key={f.id} className={filter === f.id ? "filter is-active" : "filter"} onClick={() => setFilter(f.id)}>
            {f.label} <span className="count">{count(f.id)}</span>
          </button>
        ))}
        <button className="ghost" onClick={load}>重新整理</button>
      </div>

      <div className="handoff-body">
        <ul className="ticket-list">
          {visible.map((t) => (
            <li key={t.ticket_code}>
              <button
                className={selected === t.ticket_code ? "ticket is-selected" : "ticket"}
                onClick={() => setSelected(t.ticket_code)}
              >
                <span className="ticket-top">
                  <span className={`status status-${t.status}`}>{t.status_name}</span>
                  <span className="ticket-reason">{t.reason_name}</span>
                </span>
                <span className="ticket-who">{t.customer_name ?? t.contact}</span>
                <span className="ticket-summary">{t.summary}</span>
                <span className="ticket-meta">{t.ticket_code}，{t.channel === "email" ? "电邮" : "聊天"}，{t.created_at_local}</span>
              </button>
            </li>
          ))}
          {visible.length === 0 && <li className="toc-empty">这个状态没有工单</li>}
        </ul>
        {selected ? <Detail code={selected} onChanged={load} /> : <p className="panel-loading">还没有工单。试着在左边说"我要找真人客服"。</p>}
      </div>
    </div>
  );
}
