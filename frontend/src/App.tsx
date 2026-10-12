import { useCallback, useEffect, useState } from "react";
import { api, auth } from "./api";
import Chat from "./components/Chat";
import Dashboard from "./components/Dashboard";
import Evaluation from "./components/Evaluation";
import Inbox from "./components/Inbox";
import Knowledge from "./components/Knowledge";
import Login from "./components/Login";
import Settings from "./components/Settings";
import type { AuthUser, LoginResponse, Role } from "./types";

type Page = "chat" | "inbox" | "dashboard" | "knowledge" | "evaluation" | "settings";
const NAV: { id: Page; label: string; icon: string; roles: Role[] | "all" }[] = [
  { id: "chat", label: "Support chat", icon: "💬", roles: "all" },
  { id: "inbox", label: "Agent inbox", icon: "🎫", roles: ["agent", "admin"] },
  { id: "dashboard", label: "Operations", icon: "📊", roles: ["admin"] },
  { id: "knowledge", label: "Knowledge base", icon: "📚", roles: ["admin"] },
  { id: "evaluation", label: "Evaluation", icon: "🧪", roles: ["admin"] },
  { id: "settings", label: "Settings", icon: "⚙️", roles: ["admin"] },
];

interface Health { status: string; llm: { ok: boolean; provider: string; model: string; error?: string | null }; vector_store: { chunks: number }; cache: { backend: string } }

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState<boolean>(() => !!auth.get());
  const [guest, setGuest] = useState(false);
  const [notice, setNotice] = useState("");
  const [health, setHealth] = useState<Health | null>(null);

  const signOut = useCallback((why = "") => { auth.clear(); setUser(null); setGuest(false); setPage("chat"); setNotice(why); }, []);

  // Restore the session after a page reload, if the stored token is still good.
  useEffect(() => {
    if (!auth.get()) return;
    api<AuthUser>("/api/auth/me").then(setUser).catch(() => auth.clear()).finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    const onExpired = (e: Event) => signOut((e as CustomEvent<string>).detail || "Your session ended. Please sign in again.");
    window.addEventListener("auth-expired", onExpired);
    return () => window.removeEventListener("auth-expired", onExpired);
  }, [signOut]);

  const signedIn = user !== null || guest;
  useEffect(() => {
    if (!signedIn) return;
    const load = () => api<Health>("/api/health").then(setHealth).catch(() => setHealth(null));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [signedIn]);

  const onLogin = (r: LoginResponse) => { auth.set(r.access_token); setUser(r.user); setNotice(""); setPage(r.user.role === "agent" ? "inbox" : "chat"); };

  if (checking) return <div className="login-shell"><p className="muted">Loading…</p></div>;
  if (!signedIn) return <Login notice={notice} onLogin={onLogin} onGuest={() => { setGuest(true); setNotice(""); }} />;

  const role: Role | null = user?.role ?? null;
  const visible = NAV.filter((n) => n.roles === "all" || (role !== null && n.roles.includes(role)));
  const current = visible.find((n) => n.id === page) ?? visible[0];
  const initials = (user?.name ?? "Guest").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="logo">S</span><div><b>SupportPilot</b><small>Support Console</small></div></div>
        <nav>
          {visible.map((n) => (
            <button key={n.id} className={current.id === n.id ? "nav active" : "nav"} onClick={() => setPage(n.id)}>
              <span>{n.icon}</span>{n.label}{n.roles !== "all" && <em>{n.roles.length === 1 ? n.roles[0] : "staff"}</em>}
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <div className="status-row"><i className={health?.llm.ok ? "dot ok" : "dot bad"} />
            {health ? `${health.llm.provider} · ${health.llm.model}` : "API offline"}</div>
          {health && !health.llm.ok && <div className="warn-text">{health.llm.error}</div>}
          {health && <div className="muted">{health.vector_store.chunks} chunks indexed · cache: {health.cache.backend}</div>}
        </div>
      </aside>
      <main>
        <header className="topbar">
          <h1>{current.label}</h1>
          <div className="identity">
            <div className="usercard" title={user?.email ?? "Not signed in"}>
              <span className="avatar">{initials}</span>
              <div><b>{user?.name ?? "Guest"}</b><small>{role ?? "guest: policy questions only"}</small></div>
            </div>
            {role === "admin" && (
              <button className={current.id === "settings" ? "iconbtn active" : "iconbtn"} aria-label="Settings" title="Settings: language model, database, users" onClick={() => setPage("settings")}>⚙️</button>
            )}
            <button className="btn small" onClick={() => signOut()}>{user ? "Sign out" : "Sign in"}</button>
          </div>
        </header>
        <div className="content">
          {current.id === "chat" && <Chat customer={role === "customer" ? user?.customer_id ?? null : null} />}
          {current.id === "inbox" && <Inbox me={user?.name ?? "agent"} />}
          {current.id === "dashboard" && <Dashboard />}
          {current.id === "knowledge" && <Knowledge />}
          {current.id === "evaluation" && <Evaluation />}
          {current.id === "settings" && <Settings onRelogin={(why) => signOut(why)} />}
        </div>
      </main>
    </div>
  );
}
