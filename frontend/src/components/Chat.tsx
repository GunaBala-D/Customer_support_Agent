import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { ChatMessage, ConversationDetail, ConversationSummary, ResponseStatus } from "../types";
import Inspector from "./Inspector";

const EXAMPLES = [
  "How can I return my product?",
  "Where is order ORD-1024?",
  "Please cancel order ORD-1001",
  "Can I return ORD-1002?",
  "My payment was successful but my order wasn't created.",
  "What is your refund policy?",
];

export const STATUS_LABEL: Record<ResponseStatus, { text: string; cls: string }> = {
  answered: { text: "Answered", cls: "ok" },
  completed: { text: "Action completed", cls: "ok" },
  needs_confirmation: { text: "Awaiting confirmation", cls: "warn" },
  clarification: { text: "Needs info", cls: "info" },
  fallback: { text: "No reliable answer", cls: "warn" },
  escalated: { text: "Escalated to human", cls: "bad" },
  error: { text: "Error", cls: "bad" },
};

interface ChatResponse {
  conversation_id: string; message_id: number; answer: string; intent: string; status: ResponseStatus;
  escalated: boolean; ticket: string | null; citations: ChatMessage["citations"]; tool_calls: ChatMessage["tool_calls"];
  trace: ChatMessage["trace"]; validation: ChatMessage["validation"]; metrics: ChatMessage["metrics"];
}

export default function Chat({ customer }: { customer: string | null }) {
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [ticket, setTicket] = useState<string | null>(null);
  const [escalated, setEscalated] = useState(false);
  const [selected, setSelected] = useState<ChatMessage["id"] | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const loadConvs = useCallback(() => {
    if (!customer) { setConvs([]); return; }
    api<ConversationSummary[]>("/api/conversations").then(setConvs).catch(() => setConvs([]));
  }, [customer]);

  const reset = useCallback(() => {
    setActiveId(null); setMessages([]); setTicket(null); setEscalated(false); setSelected(null); setError("");
  }, []);

  useEffect(() => { reset(); loadConvs(); }, [customer, reset, loadConvs]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  const openConv = async (id: string) => {
    try {
      const d = await api<ConversationDetail>(`/api/conversations/${id}`);
      setActiveId(id);
      setMessages(d.messages);
      setEscalated(d.status === "escalated");
      setTicket(d.memory.ticket_ref ?? null);
      setSelected(null);
      setError("");
    } catch (e) { setError((e as Error).message); }
  };

  // Pick up replies a human agent posts to this conversation while the customer has it open.
  useEffect(() => {
    if (!activeId || !customer) return;
    const t = setInterval(async () => {
      if (sending) return;
      try {
        const d = await api<ConversationDetail>(`/api/conversations/${activeId}`);
        setMessages((cur) => (d.messages.length > cur.length ? d.messages : cur));
        setEscalated(d.status === "escalated");
      } catch { /* transient; try again next tick */ }
    }, 8000);
    return () => clearInterval(t);
  }, [activeId, customer, sending]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || sending) return;
    setInput(""); setError(""); setSending(true);
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: "user", content: msg }]);
    try {
      const r = await api<ChatResponse>("/api/chat", { body: { message: msg, conversation_id: activeId ?? undefined } });
      setActiveId(r.conversation_id);
      setEscalated(r.escalated);
      setTicket(r.ticket);
      const reply: ChatMessage = {
        id: r.message_id, role: "assistant", content: r.answer, intent: r.intent, status: r.status,
        citations: r.citations, tool_calls: r.tool_calls, trace: r.trace, metrics: r.metrics, validation: r.validation,
      };
      setMessages((m) => [...m, reply]);
      setSelected(reply.id);
      loadConvs();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 429 ? "You're sending messages too quickly. Please wait a moment." : (e as Error).message);
    } finally { setSending(false); }
  };

  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const inspected = messages.find((m) => m.id === selected) ?? last;
  const awaiting = last && (last.status === "needs_confirmation" || last.status === "fallback") && !sending;

  return (
    <div className="chat-layout">
      <section className="panel convlist">
        <div className="panel-head">
          <b>Conversations</b>
          <button className="btn small" onClick={reset}>+ New</button>
        </div>
        {!customer && <p className="muted pad">Sign in as a customer (top right) to keep a history and use order tools.</p>}
        {customer && convs.length === 0 && <p className="muted pad">No conversations yet.</p>}
        {convs.map((c) => (
          <button key={c.id} className={c.id === activeId ? "conv active" : "conv"} onClick={() => openConv(c.id)}>
            <span className="conv-title">{c.title ?? "Conversation"}</span>
            <span className="conv-meta"><i className={`pill ${c.status}`}>{c.status}</i>{c.message_count} msgs</span>
          </button>
        ))}
      </section>

      <section className="panel thread">
        {escalated && (
          <div className="banner bad">
            <b>Escalated to a human agent</b>
            {ticket ? <span> · ticket <code>{ticket}</code>. The agent can see this conversation.</span> : <span>.</span>}
          </div>
        )}
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <h2>How can we help?</h2>
              <p className="muted">Ask about policies, or look up and manage your orders. Answers from our knowledge base always show their sources.</p>
              <div className="chips">{EXAMPLES.map((e) => <button key={e} className="chip" onClick={() => send(e)}>{e}</button>)}</div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.role === "agent" && <small className="agent-label">👤 Human support agent</small>}
              <div className={`bubble ${m.id === inspected?.id ? "selected" : ""}`} onClick={() => m.role === "assistant" && setSelected(m.id)}>
                {m.content.split("\n").map((l, i) => <p key={i}>{l}</p>)}
                {m.role === "assistant" && m.citations && m.citations.length > 0 && (
                  <div className="sources">
                    <small>Sources</small>
                    {m.citations.map((c) => <span key={c.index} className="source" title={c.snippet}>[{c.index}] 📄 {c.label}</span>)}
                  </div>
                )}
              </div>
              {m.role === "assistant" && m.status && (
                <div className="meta"><i className={`pill ${STATUS_LABEL[m.status].cls}`}>{STATUS_LABEL[m.status].text}</i>
                  {m.tool_calls && m.tool_calls.length > 0 && <i className="pill neutral">🔧 {m.tool_calls.map((t) => t.tool).join(", ")}</i>}
                </div>
              )}
            </div>
          ))}
          {sending && <div className="msg assistant"><div className="bubble typing"><span /><span /><span /></div></div>}
          <div ref={endRef} />
        </div>
        {awaiting && (
          <div className="quick">
            <button className="btn primary" onClick={() => send("yes")}>Yes</button>
            <button className="btn" onClick={() => send("no")}>No</button>
          </div>
        )}
        {error && <div className="banner bad">{error}</div>}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input value={input} maxLength={2000} placeholder="Type your message…" onChange={(e) => setInput(e.target.value)} disabled={sending} />
          <button className="btn primary" disabled={sending || !input.trim()}>Send</button>
        </form>
      </section>

      <Inspector message={inspected} />
    </div>
  );
}
