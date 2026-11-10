import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ApiError, type TraceStep } from "../api";

type Message =
  | { role: "customer"; text: string }
  | { role: "ai"; text: string; trace: TraceStep[]; tokens?: { input: number; output: number } }
  | { role: "error"; text: string };

const SUGGESTIONS = [
  "雾眉多少钱？",
  "星期一有开吗？",
  "我想 book 下星期三下午的 Aqua Peel",
  "can pay using touch n go?",
  "我做完 facial 整张脸红肿发痒",
];

const TOOL_NAMES: Record<string, string> = {
  search_knowledge_base: "查知识库",
  check_availability: "查空档",
  find_bookings: "查顾客的预约",
  request_booking: "准备预约",
  request_cancel: "准备取消",
  request_reschedule: "准备改期",
  confirm_action: "执行",
  request_human_handoff: "转人工",
  error: "出错",
};

function describeArgs(step: TraceStep) {
  const a = step.args as Record<string, string>;
  if (step.tool === "search_knowledge_base") return `「${a.query}」`;
  if (step.tool === "check_availability") return `${a.service}，${a.date}`;
  if (step.tool === "request_reschedule") return `${a.booking_code} → ${a.new_date} ${a.new_time}`;
  if (step.tool === "request_booking") return `${a.service}，${a.date} ${a.time}`;
  if (step.tool === "request_cancel") return a.booking_code;
  return "";
}

// 从答案最后一行"（参考：A、B（英文名））"取出 AI 真正引用的资料标题。
// 标题里本身可能有括号和顿号，所以只在括号外面的"、"切开。
function citedTitles(answer: string): string[] {
  const match = answer.match(/（参考：(.*)）\s*$/s);
  if (!match) return [];
  const titles: string[] = [];
  let depth = 0;
  let current = "";
  for (const ch of match[1]) {
    if (ch === "（" || ch === "(") depth++;
    if (ch === "）" || ch === ")") depth--;
    if (ch === "、" && depth === 0) {
      titles.push(current.trim());
      current = "";
    } else current += ch;
  }
  titles.push(current.trim());
  return titles.filter(Boolean);
}

// 回答里不显示最后那行"（参考：……）"：上面的资料格子已经列出来了
function stripCitation(answer: string): string {
  const i = answer.lastIndexOf("（参考：");
  if (i === -1 || !answer.trimEnd().endsWith("）")) return answer;
  return answer.slice(0, i).trimEnd();
}

// AI 写的标题有时会省略括号里的英文，所以"开头一样"也算同一块
const sameTitle = (a: string, b: string) => a === b || a.startsWith(b) || b.startsWith(a);

function Sources({ results, cited, onCite }: { results: string[]; cited: string[]; onCite: (title: string) => void }) {
  const [open, setOpen] = useState(false);
  const used = results.filter((t) => cited.some((c) => sameTitle(t, c)));
  const unused = results.filter((t) => !used.includes(t));
  const chip = (title: string, quiet = false) => (
    <button key={title} className={quiet ? "source-chip is-unused" : "source-chip"} onClick={() => onCite(title)} title="在知识库里查看这一段">
      {title}
    </button>
  );
  return (
    <span className="trace-sources">
      {used.map((t) => chip(t))}
      {unused.length > 0 && !open && (
        <button className="unused-toggle" onClick={() => setOpen(true)}>
          {used.length ? `还有 ${unused.length} 块没用到` : `查到 ${unused.length} 块，都没用到`}
        </button>
      )}
      {open && unused.map((t) => chip(t, true))}
    </span>
  );
}

function Trace({ steps, answer, onCite }: { steps: TraceStep[]; answer: string; onCite: (title: string) => void }) {
  if (!steps.length) return null;
  const cited = citedTitles(answer);
  return (
    <ol className="trace" aria-label="AI 的处理过程">
      {steps.map((step, i) => (
        <li key={i} className={`trace-step tool-${step.tool}`}>
          <span className="trace-tool">{TOOL_NAMES[step.tool] ?? step.tool}</span>
          <span className="trace-args">{describeArgs(step)}</span>
          {step.tool === "search_knowledge_base" ? (
            <Sources results={step.results} cited={cited} onCite={onCite} />
          ) : (
            <span className="trace-result">{step.results.join("；")}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

export default function ChatPanel({
  onCite,
  onToolsUsed,
}: {
  onCite: (title: string) => void;
  onToolsUsed: (tools: string[]) => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem("glow-session"));
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setDraft("");
    setMessages((m) => [...m, { role: "customer", text: message }]);
    setBusy(true);
    try {
      const res = await api.chat(message, sessionId);
      setSessionId(res.session_id);
      sessionStorage.setItem("glow-session", res.session_id);
      setMessages((m) => [...m, { role: "ai", text: res.answer, trace: res.trace, tokens: res.tokens }]);
      onToolsUsed(res.trace.map((s) => s.tool));
    } catch (err) {
      const text =
        err instanceof ApiError
          ? err.message // 后端说明的原因，例如"讯息发得太快了，请等 30 秒再试"
          : `没有连上后端：${(err as Error).message}。请确认 backend 的 API 正在运行。`;
      setMessages((m) => [...m, { role: "error", text }]);
    } finally {
      setBusy(false);
    }
  }

  async function startOver() {
    await api.resetChat(sessionId).catch(() => {});
    sessionStorage.removeItem("glow-session");
    setSessionId(null);
    setMessages([]);
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <div>
          <h2 className="pane-title">线上客服</h2>
          <p className="pane-sub">中文、英文、混着讲都可以</p>
        </div>
        {messages.length > 0 && (
          <button className="link-button" onClick={startOver}>
            开新对话
          </button>
        )}
      </div>

      <div className="chat-log" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="greeting">您好，这里是 Glow Atelier 光研美学。想问价格、预约时段，或者护理后的问题，都可以直接问。</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "customer" ? (
            <div key={i} className="bubble bubble-customer">{m.text}</div>
          ) : m.role === "ai" ? (
            <div key={i} className="ai-turn">
              <Trace steps={m.trace} answer={m.text} onCite={onCite} />
              <div className="bubble bubble-ai">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripCitation(m.text)}</ReactMarkdown>
              </div>
              {m.tokens && (
                <span className="tokens">
                  输入 {m.tokens.input.toLocaleString()} tokens，输出 {m.tokens.output.toLocaleString()}
                </span>
              )}
            </div>
          ) : (
            <div key={i} className="bubble bubble-error" role="alert">{m.text}</div>
          ),
        )}

        {busy && (
          <div className="thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="sr-only">AI 正在回复</span>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <label htmlFor="chat-input" className="sr-only">输入讯息</label>
        <input
          id="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="例如：这个 facial 几钱？可以 book 明天吗？"
          autoComplete="off"
          disabled={busy}
        />
        <button type="submit" className="primary" disabled={busy || !draft.trim()}>
          发送
        </button>
      </form>
    </div>
  );
}
