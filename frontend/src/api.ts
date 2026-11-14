// 和后端 FastAPI（backend/api.py）沟通的函数

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

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

export const api = {
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
