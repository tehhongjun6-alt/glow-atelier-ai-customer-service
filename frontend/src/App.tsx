import { useState } from "react";
import ChatPanel from "./components/ChatPanel";
import KnowledgePanel from "./components/KnowledgePanel";
import BookingsPanel from "./components/BookingsPanel";
import HandoffPanel from "./components/HandoffPanel";

type Tab = "knowledge" | "bookings" | "handoffs";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "knowledge", label: "知识库", hint: "AI 回答时读的资料" },
  { id: "bookings", label: "预约日历", hint: "AI 帮顾客订的时段" },
  { id: "handoffs", label: "转人工后台", hint: "同事接手的工单" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("knowledge");
  const [cited, setCited] = useState<{ title: string; nonce: number } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

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

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <span className="brand-name">Glow Atelier 光研美学</span>
          <span className="brand-sub">AI 客服作品集 demo</span>
        </div>
        <p className="masthead-note">
          左边扮演顾客和 AI 聊天；右边看 AI 读了哪段资料、订了哪个时段、转了哪张工单。
        </p>
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
                <span className="tab-hint">{t.hint}</span>
              </button>
            ))}
          </nav>
          <div className="tab-body">
            {tab === "knowledge" && <KnowledgePanel cited={cited} />}
            {tab === "bookings" && <BookingsPanel refreshKey={refreshKey} />}
            {tab === "handoffs" && <HandoffPanel refreshKey={refreshKey} />}
          </div>
        </section>
      </main>
    </div>
  );
}
