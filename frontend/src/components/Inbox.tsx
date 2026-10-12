import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { TicketDetail, TicketQueue, TicketSummary } from "../types";

type StatusFilter = "" | "open" | "in_progress" | "resolved";
const STATUS_LABEL = { open: "Open", in_progress: "In progress", resolved: "Resolved" } as const;
const PRIORITY_CLS = { high: "bad", normal: "info", low: "neutral" } as const;

const ago = (iso: string) => {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso + "Z").getTime()) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 1440) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
};

/** `me` is the signed-in agent's name; the server takes the author from the login, not from what we send. */
export default function Inbox({ me }: { me: string }) {
  const [queue, setQueue] = useState<TicketQueue | null>(null);
  const [status, setStatus] = useState<StatusFilter>("");
  const [priority, setPriority] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<"reply" | "note">("reply");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadQueue = useCallback(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (priority) params.set("priority", priority);
    if (q.trim()) params.set("q", q.trim());
    api<TicketQueue>(`/api/support/tickets?${params}`)
      .then((d) => { setQueue(d); setError(""); })
      .catch((e) => { setQueue(null); setError(e.message); });
  }, [status, priority, q]);

  const loadDetail = useCallback((ref: string) => {
    api<TicketDetail>(`/api/support/tickets/${ref}`).then(setDetail).catch((e) => setError(e.message));
  }, []);

  useEffect(() => { const t = setTimeout(loadQueue, 200); return () => clearTimeout(t); }, [loadQueue]);
  useEffect(() => { const t = setInterval(loadQueue, 20000); return () => clearInterval(t); }, [loadQueue]);
  useEffect(() => { if (selected) loadDetail(selected); else setDetail(null); }, [selected, loadDetail]);

  const refresh = () => { loadQueue(); if (selected) loadDetail(selected); };

  const patch = async (body: Record<string, string>) => {
    if (!selected) return;
    setBusy(true);
    try { await api(`/api/support/tickets/${selected}`, { method: "PATCH", body: body }); refresh(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const submit = async () => {
    if (!selected || !draft.trim()) return;
    setBusy(true);
    try {
      await api(`/api/support/tickets/${selected}/${mode === "reply" ? "reply" : "notes"}`, { body: { body: draft.trim() } });
      setDraft(""); setError(""); refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const counts = queue?.counts;
  const tabs: { id: StatusFilter; label: string; n?: number }[] = [
    { id: "", label: "All", n: counts ? counts.open + counts.in_progress + counts.resolved : undefined },
    { id: "open", label: "Open", n: counts?.open }, { id: "in_progress", label: "In progress", n: counts?.in_progress },
    { id: "resolved", label: "Resolved", n: counts?.resolved },
  ];

  return (
    <div className="inbox">
      <section className="panel ticketlist">
        <div className="tabs">
          {tabs.map((t) => (
            <button key={t.id} className={status === t.id ? "tab active" : "tab"} onClick={() => setStatus(t.id)}>
              {t.label}{t.n != null && <em>{t.n}</em>}
            </button>
          ))}
        </div>
        <div className="filters">
          <input placeholder="Search subject, customer, order…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority">
            <option value="">Any priority</option><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
          </select>
        </div>
        {error && !detail && <div className="banner bad">{error}</div>}
        <div className="tickets">
          {queue?.items.map((t: TicketSummary) => (
            <button key={t.reference} className={t.reference === selected ? "ticket active" : "ticket"} onClick={() => setSelected(t.reference)}>
              <div className="ticket-top"><code>{t.reference}</code><i className={`pill ${PRIORITY_CLS[t.priority]}`}>{t.priority}</i><span className="muted small">{ago(t.created_at)}</span></div>
              <div className="ticket-subject">{t.subject}</div>
              <div className="ticket-meta muted small">
                {t.customer_name ?? t.customer_id ?? "guest"} · {STATUS_LABEL[t.status]}{t.assignee ? ` · ${t.assignee}` : " · unassigned"}
              </div>
            </button>
          ))}
          {queue && queue.items.length === 0 && <p className="muted pad">No tickets match.</p>}
        </div>
      </section>

      <section className="panel ticketdetail">
        {!detail ? (
          <div className="empty"><h2>Select a ticket</h2><p className="muted">Escalated conversations appear here with the full chat history, so customers never repeat themselves.</p></div>
        ) : (
          <>
            <div className="detail-head">
              <div>
                <h3>{detail.subject}</h3>
                <div className="muted small"><code>{detail.reference}</code> · {detail.category} · opened {ago(detail.created_at)} ago</div>
              </div>
              <div className="detail-actions">
                <label>Status
                  <select disabled={busy} value={detail.status} onChange={(e) => patch({ status: e.target.value })}>
                    <option value="open">Open</option><option value="in_progress">In progress</option><option value="resolved">Resolved</option>
                  </select>
                </label>
                <label>Priority
                  <select disabled={busy} value={detail.priority} onChange={(e) => patch({ priority: e.target.value })}>
                    <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
                  </select>
                </label>
                <label>Assignee
                  <span className="assign">
                    <button className="btn small" disabled={busy} onClick={() => patch({ assignee: me })}>
                      {detail.assignee === me ? "Assigned to you" : "Assign to me"}
                    </button>
                  </span>
                </label>
              </div>
            </div>
            {error && <div className="banner bad">{error}</div>}

            <div className="detail-body">
              <div className="thread-col">
                <h4>Customer conversation</h4>
                <div className="transcript">
                  {detail.transcript.length === 0 && <p className="muted small">No linked conversation.</p>}
                  {detail.transcript.map((m, i) => (
                    <div key={i} className={`tmsg ${m.role}`}>
                      <b>{m.role === "user" ? (detail.customer?.name ?? "Customer") : m.role === "agent" ? "Human agent" : "Assistant"}</b>
                      <span>{m.content}</span>
                    </div>
                  ))}
                </div>
                <h4>Activity</h4>
                <div className="activity">
                  {detail.activity.length === 0 && <p className="muted small">No agent activity yet.</p>}
                  {detail.activity.map((a) => (
                    <div key={a.id} className={`act ${a.kind}`}>
                      <div className="muted small">{a.author} · {a.kind === "note" ? "internal note" : a.kind === "reply" ? "replied to customer" : a.kind === "assign" ? "assigned" : "updated"} · {ago(a.created_at)} ago</div>
                      <div>{a.body || "(unassigned)"}</div>
                    </div>
                  ))}
                </div>
              </div>
              <aside className="side-col">
                <h4>Customer</h4>
                {detail.customer ? (
                  <div className="card"><b>{detail.customer.name}</b><div className="muted small">{detail.customer.id} · {detail.customer.tier}</div><div className="small">{detail.customer.email}</div></div>
                ) : <p className="muted small">Guest (not signed in)</p>}
                {detail.order && (
                  <><h4>Order</h4><div className="card"><b>{detail.order.id}</b><div className="muted small">{detail.order.status} · payment {detail.order.payment_status.replace("_", " ")} · {detail.order.total.toFixed(2)} USD</div></div></>
                )}
                <h4>Why it was escalated</h4>
                <div className="card small pre">{detail.description}</div>
              </aside>
            </div>

            <div className="composer-box">
              <div className="tabs small">
                <button className={mode === "reply" ? "tab active" : "tab"} onClick={() => setMode("reply")}>Reply to customer</button>
                <button className={mode === "note" ? "tab active" : "tab"} onClick={() => setMode("note")}>Internal note</button>
              </div>
              <textarea rows={3} maxLength={4000} value={draft} onChange={(e) => setDraft(e.target.value)}
                placeholder={mode === "reply" ? "Visible to the customer in their chat…" : "Only visible to agents…"} />
              <div className="composer-foot">
                <span className="muted small">{mode === "reply" ? "The customer sees this when they open the conversation." : "Never shown to the customer."}</span>
                <button className="btn primary" disabled={busy || !draft.trim()} onClick={submit}>{mode === "reply" ? "Send reply" : "Add note"}</button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
