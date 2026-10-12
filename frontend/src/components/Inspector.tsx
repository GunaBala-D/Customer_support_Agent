import type { ChatMessage } from "../types";

const fmt = (v: unknown, d = 0) => (typeof v === "number" ? v.toFixed(d) : "–");

export default function Inspector({ message }: { message?: ChatMessage }) {
  if (!message) {
    return <aside className="panel inspector"><div className="panel-head"><b>Response inspector</b></div>
      <p className="muted pad">Send a message to see sources, tool activity and the processing steps behind each answer.</p></aside>;
  }
  const m = message.metrics ?? {};
  const v = message.validation;
  return (
    <aside className="panel inspector">
      <div className="panel-head"><b>Response inspector</b>{message.intent && <i className="pill neutral">{message.intent}</i>}</div>

      <h4>Sources</h4>
      {message.citations && message.citations.length > 0 ? message.citations.map((c) => (
        <div className="card" key={c.index}>
          <div className="card-title">[{c.index}] 📄 {c.label}</div>
          {c.section && <div className="muted small">{c.section}</div>}
          <blockquote>{c.snippet}…</blockquote>
          <div className="muted small">relevance score {c.score.toFixed(2)}</div>
        </div>
      )) : <p className="muted small">No knowledge-base sources used for this reply.</p>}

      <h4>Tool activity</h4>
      {message.tool_calls && message.tool_calls.length > 0 ? message.tool_calls.map((t, i) => (
        <div className="card row" key={i}>
          <div><code>{t.tool}</code><div className="muted small">{Object.entries(t.arguments).map(([k, val]) => `${k}=${String(val).slice(0, 48)}${String(val).length > 48 ? "…" : ""}`).join(", ")}</div></div>
          <div className="right"><i className={`pill ${t.success ? "ok" : "warn"}`}>{t.success ? "ok" : t.error_code}</i><div className="muted small">{t.duration_ms} ms</div></div>
        </div>
      )) : <p className="muted small">No tools were called.</p>}

      <h4>How this was handled</h4>
      <ol className="steps">
        {(message.trace ?? []).map((s, i) => <li key={i}><b>{s.step}</b> {s.detail}</li>)}
      </ol>

      {v && (<>
        <h4>Quality checks</h4>
        <div className="card">
          <div className="kv"><span>Validation</span><i className={`pill ${v.passed ? "ok" : "warn"}`}>{v.passed ? "passed" : "failed"}</i></div>
          {v.relevance_score != null && <div className="kv" title="Top retrieval score: 0.5 × cosine similarity + 0.5 × query-term coverage"><span>Retrieval relevance</span><b>{v.relevance_score.toFixed(2)}</b></div>}
          {v.grounding_ratio != null && <div className="kv" title="Share of the answer's content terms found in the cited context"><span>Grounding ratio</span><b>{v.grounding_ratio.toFixed(2)}</b></div>}
          {v.reasons?.map((r, i) => <div className="muted small" key={i}>• {r}</div>)}
          <div className="muted small">These are retrieval/lexical measurements, not model confidence.</div>
        </div>
      </>)}

      {Object.keys(m).length > 0 && (<>
        <h4>Performance</h4>
        <div className="card">
          <div className="kv"><span>Total</span><b>{fmt(m.latency_ms)} ms</b></div>
          <div className="kv"><span>LLM</span><b>{fmt(m.llm_ms)} ms</b></div>
          <div className="kv"><span>Retrieval</span><b>{fmt(m.retrieval_ms)} ms</b></div>
          <div className="kv"><span>Tools</span><b>{fmt(m.tool_ms)} ms</b></div>
          <div className="kv"><span>Chunks used</span><b>{String(m.retrieved_chunks ?? "–")}</b></div>
          <div className="kv"><span>Model</span><b>{String(m.model ?? "–")}</b></div>
          {m.cache_hit === true && <div className="muted small">Served from cache</div>}
        </div>
      </>)}
    </aside>
  );
}
