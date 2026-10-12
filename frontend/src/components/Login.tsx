import { useEffect, useState } from "react";
import { api } from "../api";
import type { DemoAccount, LoginResponse } from "../types";

interface Props { notice: string; onLogin: (r: LoginResponse) => void; onGuest: () => void }

export default function Login({ notice, onLogin, onGuest }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<DemoAccount[]>([]);

  // Only exists when the server runs in development mode with demo users; a 404 simply hides the shortcuts.
  useEffect(() => { api<DemoAccount[]>("/api/demo/accounts").then(setDemo).catch(() => setDemo([])); }, []);

  const submit = async (e?: React.FormEvent, creds?: { email: string; password: string }) => {
    e?.preventDefault();
    const body = creds ?? { email: email.trim(), password };
    if (!body.email || !body.password) return;
    setBusy(true); setError("");
    try { onLogin(await api<LoginResponse>("/api/auth/login", { body })); }
    catch (err) { setError((err as Error).message); setPassword(""); }
    finally { setBusy(false); }
  };

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="brand dark"><span className="logo">S</span><div><b>SupportPilot</b><small>Support Console</small></div></div>
        <h2>Sign in</h2>
        {notice && <div className="banner warn">{notice}</div>}
        <label>E-mail<input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="banner bad">{error}</div>}
        <button className="btn primary wide" disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <button type="button" className="btn wide" onClick={onGuest}>Continue as guest (policy questions only)</button>

        {demo.length > 0 && (
          <div className="demo-box">
            <small>Development demo accounts. Click to sign in:</small>
            <div className="chips">
              {demo.map((d) => (
                <button type="button" key={d.email} className="chip" disabled={busy} onClick={() => submit(undefined, { email: d.email, password: d.password })}>
                  <b>{d.role}</b> · {d.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </form>
    </div>
  );
}
