import { useState, type FormEvent } from "react";
import { api, auth, type Staff } from "../api";

// 员工登录：预约日历、转人工后台、邮箱这三个分页要登录才看得到
export default function LoginPanel({ onLogin }: { onLogin: (staff: Staff) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await api.login(email, password);
      auth.save(result.access_token);
      onLogin(result.staff);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form className="login-panel" onSubmit={submit}>
      <h3 className="login-title">员工登录</h3>
      <p className="panel-intro">预约日历、转人工后台、邮箱有顾客资料，只有员工登录后才看得到。</p>
      <label className="login-field">
        邮箱
        <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="login-field">
        密码
        <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      {error && <p className="panel-error login-error">{error}</p>}
      <button type="submit" className="primary" disabled={loading}>{loading ? "登录中……" : "登录"}</button>
    </form>
  );
}
