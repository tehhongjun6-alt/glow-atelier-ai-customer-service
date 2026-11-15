// 和后端 FastAPI（backend/api.py）沟通的函数

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8010";

export type TraceStep = { tool: string; args: Record<string, unknown>; results: string[] };

export type ChatResponse = {
  session_id: string;
  model: string;
  answer: string;
  trace: TraceStep[];
  tokens: { input: number; output: number };
};

export type Chunk = { title: string; category: string; length: number };

export type Booking = {
  booking_code: string;
  customer_name: string;
  phone: string;
  email: string;
  service: string;
  date: string;
  weekday: string;
  time: string;
  end_time: string;
  beautician: string | null;
  notes: string | null;
  status: "confirmed" | "cancelled";
};

export type HandoffStatus = "processing" | "accepted" | "resolved";

export type Handoff = {
  ticket_code: string;
  status: HandoffStatus;
  status_name: string;
  reason: string;
  reason_name: string;
  channel: "chat" | "email";
  customer_name: string | null;
  contact: string;
  summary: string;
  booking_code: string | null;
  assigned_to: string | null;
  staff_note: string | null;
  created_at: string;
  created_at_local: string;
  conversation?: { role: "customer" | "ai"; text: string }[];
};

export type EmailStatus = {
  running: boolean;
  address: string | null;
  interval_seconds: number;
  last_check: string | null;
  last_error: string | null;
};

// 后端有回应、但拒绝了请求（例如 429 使用次数超过上限）。和"连不上后端"分开处理。
export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export type Staff = { email: string; name: string; role: string };

// ---------- 员工登录 ----------
// token 存在 sessionStorage：关掉分页就要重新登录（比 localStorage 安全一点）
const TOKEN_KEY = "glow-staff-token";
let onUnauthorized: () => void = () => {};

export const auth = {
  token: () => sessionStorage.getItem(TOKEN_KEY),
  save: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
  // token 过期或无效（后端回 401）时要做什么：App 会设成"回到登录画面"
  onUnauthorized: (handler: () => void) => { onUnauthorized = handler; },
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = auth.token();
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && token) {
    auth.clear();
    onUnauthorized();
  }
  if (!res.ok) throw new ApiError(body.detail ?? `请求失败（${res.status}）`, res.status);
  return body as T;
}

// ---------- 逐字显示（Streaming） ----------
// 后端用 SSE 一段一段送过来，每段是一行 "data: {...}"，段和段之间空一行。
export type StreamEvent =
  | { type: "start"; session_id: string }
  | { type: "delta"; text: string }
  | { type: "tool"; step: TraceStep }
  | ({ type: "done" } & ChatResponse)
  | { type: "error"; message: string };

const STREAM_TIMEOUT_MS = 90_000; // 后端 60 秒没进度会自己回错误；这是最后的保险（例如后端整个卡住）

async function chatStream(message: string, session_id: string | null, onEvent: (e: StreamEvent) => void) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);
  try {
    const res = await fetch(BASE + "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id }),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(body.detail ?? `请求失败（${res.status}）`, res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 2);
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      }
    }
  } catch (err) {
    if (controller.signal.aborted) throw new ApiError("系统回应太慢，请稍后再试。", 504);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  chatStream,
  login: (email: string, password: string) =>
    request<{ access_token: string; staff: Staff }>("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),
  me: () => request<{ staff: Staff }>("/api/auth/me"),
  chat: (message: string, session_id: string | null) =>
    request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify({ message, session_id }) }),
  resetChat: (session_id: string | null) =>
    request("/api/chat/reset", { method: "POST", body: JSON.stringify({ message: "-", session_id }) }),
  knowledge: () => request<{ file: string; markdown: string; chunks: Chunk[] }>("/api/knowledge"),
  bookings: () => request<{ bookings: Booking[]; google_calendar_id: string | null }>("/api/bookings?days=30"),
  handoffs: () => request<{ handoffs: Handoff[] }>("/api/handoffs"),
  handoff: (code: string) => request<Handoff>(`/api/handoffs/${code}`),
  accept: (code: string, staff: string) =>
    request<Handoff>(`/api/handoffs/${code}/accept`, { method: "POST", body: JSON.stringify({ staff }) }),
  resolve: (code: string, note: string) =>
    request<Handoff>(`/api/handoffs/${code}/resolve`, { method: "POST", body: JSON.stringify({ note }) }),
  emailStatus: () => request<EmailStatus>("/api/email/status"),
  emailStart: () => request<EmailStatus>("/api/email/start", { method: "POST" }),
  emailStop: () => request<EmailStatus>("/api/email/stop", { method: "POST" }),
};
