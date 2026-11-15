import { useEffect, useState } from "react";
import { api, auth, type Staff } from "./api";
import ChatPanel from "./components/ChatPanel";
import KnowledgePanel from "./components/KnowledgePanel";
import BookingsPanel from "./components/BookingsPanel";
import HandoffPanel from "./components/HandoffPanel";
import EmailPanel from "./components/EmailPanel";
import LoginPanel from "./components/LoginPanel";

type Tab = "knowledge" | "bookings" | "handoffs" | "email";

// staffOnly：有顾客资料，要员工登录才看得到（后端也会检查，前端只是先挡一下画面）
const TABS: { id: Tab; label: string; staffOnly?: boolean }[] = [
  { id: "knowledge", label: "知识库" },
  { id: "bookings", label: "预约日历", staffOnly: true },
  { id: "handoffs", label: "转人工后台", staffOnly: true },
  { id: "email", label: "邮箱", staffOnly: true },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("knowledge");
  const [cited, setCited] = useState<{ title: string; nonce: number } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [staff, setStaff] = useState<Staff | null>(null);

  // 打开网页时：之前登录过（token 还在）→ 问后端这个 token 还有没有效
  useEffect(() => {
    auth.onUnauthorized(() => setStaff(null));   // 之后任何请求回 401（过期）→ 回到登录画面
    if (auth.token()) api.me().then((r) => setStaff(r.staff)).catch(() => setStaff(null));
  }, []);

  function logout() {
    auth.clear();
    setStaff(null);
  }

  // 点了 AI 回答下面的资料名称 → 切到知识库，跳到那一段
  function showSource(title: string) {
    setTab("knowledge");
    setCited({ title, nonce: Date.now() });
  }

  // AI 订了预约或开了工单 → 右边的面板重新载入；顺便切过去让人看到变化
  function onToolsUsed(tools: string[]) {
    if (tools.some((t) => t === "confirm_action" || t === "request_human_handoff")) {
      setRefreshKey((k) => k + 1);
      if (tools.includes("request_human_handoff")) setTab("handoffs");
      else if (tools.includes("confirm_action")) setTab("bookings");
    }
  }

  const current = TABS.find((t) => t.id === tab)!;

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <span className="brand-name">Glow Atelier 光研美学</span>
        </div>
        <div className="masthead-side">
          <p className="masthead-note">
            左边扮演顾客和 AI 聊天；右边看 AI 读了哪段资料、订了哪个时段、转了哪张工单。
          </p>
          {staff && (
            <p className="staff-badge">
              员工：{staff.name}
              <button className="link-button" onClick={logout}>登出</button>
            </p>
          )}
        </div>
      </header>

      <main className="workspace">
        <section className="pane pane-chat" aria-label="顾客聊天">
          <ChatPanel onCite={showSource} onToolsUsed={onToolsUsed} />
        </section>

        <section className="pane pane-side" aria-label="后台">
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                className={tab === t.id ? "tab is-active" : "tab"}
                onClick={() => setTab(t.id)}
              >
                <span className="tab-label">{t.label}</span>
              </button>
            ))}
          </nav>
          <div className="tab-body">
            {tab === "knowledge" && <KnowledgePanel cited={cited} />}
            {current.staffOnly && !staff && <LoginPanel onLogin={setStaff} />}
            {staff && tab === "bookings" && <BookingsPanel refreshKey={refreshKey} />}
            {staff && tab === "handoffs" && <HandoffPanel refreshKey={refreshKey} />}
            {staff && tab === "email" && <EmailPanel />}
          </div>
        </section>
      </main>
    </div>
  );
}
