import { useEffect, useState } from "react";
import { api, type EmailStatus } from "../api";

// 「邮箱」分页：Gmail 自动回复的开关（预设关闭）
export default function EmailPanel() {
  const [status, setStatus] = useState<EmailStatus | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  // 打开这个分页时，每 10 秒更新一次"上次检查"时间
  useEffect(() => {
    const load = () =>
      api.emailStatus().then((s) => { setStatus(s); setError(""); }).catch((e) => setError((e as Error).message));
    load();
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, []);

  async function toggle() {
    if (!status) return;
    setSaving(true);
    setError("");
    try {
      setStatus(await (status.running ? api.emailStop() : api.emailStart()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!status) return error ? <p className="panel-error">{error}</p> : <p className="panel-loading">载入中……</p>;

  return (
    <div className="email-panel">
      <div className="email-switch-row">
        <div>
          <h3 className="email-title">Gmail 自动回复</h3>
          <p className="panel-intro">
            打开后，每 {status.interval_seconds} 秒检查一次寄到公司信箱的新信，AI 会<strong>真的回信</strong>。
            后端重启后会自动回到「关」。
          </p>
        </div>
        <button
          role="switch"
          aria-checked={status.running}
          aria-label="Gmail 自动回复"
          className={status.running ? "switch is-on" : "switch"}
          disabled={saving || !status.address}
          onClick={toggle}
        >
          <span className="switch-knob" />
          <span className="switch-text">{status.running ? "开" : "关"}</span>
        </button>
      </div>

      <dl className="facts">
        <dt>状态</dt>
        <dd>{status.running ? "运行中" : "已关闭"}</dd>
        <dt>公司信箱</dt>
        <dd>{status.address ?? "（还没设定）"}</dd>
        <dt>上次检查</dt>
        <dd>{status.last_check ?? "还没检查过"}</dd>
      </dl>

      {status.last_error && <p className="panel-error email-error">上一轮出错：{status.last_error}</p>}
      {error && <p className="panel-error email-error">{error}</p>}
    </div>
  );
}
