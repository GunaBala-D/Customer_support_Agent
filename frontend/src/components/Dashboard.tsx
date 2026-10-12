import { useEffect, useState } from "react";
import { api } from "../api";
import type { ConversationSummary, Metrics } from "../types";
import { BarList, Donut, LineChart } from "./Charts";

const ms = (v: number | null) => (v == null ? "–" : `${v.toFixed(0)} ms`);
const pct = (v: number | null) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

function Kpi({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return <div className={`kpi ${tone ?? ""}`}><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>;
}

export default function Dashboard() {
  const [m, setM] = useState<Metrics | null>(null);
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    api<Metrics>("/api/metrics").then(setM).catch((e) => { setM(null); setError(e.message); });
    api<ConversationSummary[]>("/api/conversations?limit=12").then(setConvs).catch(() => setConvs([]));
  };
  useEffect(load, []);

  if (error) return <div className="banner bad">{error.replace(/\.$/, "")}. Enter a valid admin key in the top bar, then <button className="btn small" onClick={load}>retry</button></div>;
  if (!m) return <p className="muted">Loading…</p>;

  const c = m.conversations;
  const resolvedRate = c.total ? c.resolved / c.total : null;
  return (
    <div className="dash">
      <div className="kpis">
        <Kpi label="Conversations" value={c.total} sub={`last ${m.window_days} days`} />
        <Kpi label="Resolved" value={c.resolved} sub={resolvedRate == null ? "" : `${pct(resolvedRate)} of total`} tone="ok" />
        <Kpi label="Escalated" value={c.escalated} tone="bad" />
        <Kpi label="Avg latency" value={ms(m.latency_ms.avg)} sub={`p95 ${ms(m.latency_ms.p95)}`} />
        <Kpi label="Tool success" value={pct(m.tools.success_rate)} sub={`${m.tools.total_calls} calls`} />
        <Kpi label="No-evidence rate" value={pct(m.rag.no_evidence_rate)} sub={`${m.rag.queries} RAG queries`} />
      </div>

      <div className="grid2">
        <section className="panel pad"><h3>Conversations per day</h3>
          <LineChart data={m.conversations_per_day.map((d) => ({ label: d.date, value: d.conversations }))} /></section>
        <section className="panel pad"><h3>Outcomes</h3>
          <Donut parts={[{ label: "Resolved", value: c.resolved, cls: "c-ok" }, { label: "Open", value: c.open, cls: "c-info" }, { label: "Escalated", value: c.escalated, cls: "c-bad" }]} /></section>
      </div>

      <div className="grid3">
        <section className="panel pad"><h3>Intent mix</h3>
          <BarList data={Object.entries(m.intents).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} /></section>
        <section className="panel pad"><h3>Latency breakdown (avg)</h3>
          <BarList unit=" ms" data={[
            { label: "LLM", value: m.latency_ms.avg_llm ?? 0 }, { label: "Retrieval", value: m.latency_ms.avg_retrieval ?? 0 },
            { label: "Tools", value: m.latency_ms.avg_tool ?? 0 }, { label: "Total", value: m.latency_ms.avg ?? 0 }]} /></section>
        <section className="panel pad"><h3>RAG retrieval</h3>
          <div className="kv"><span>Avg chunks per query</span><b>{m.rag.avg_retrieved_chunks?.toFixed(1) ?? "–"}</b></div>
          <div className="kv"><span>Avg top relevance score</span><b>{m.rag.avg_top_score?.toFixed(2) ?? "–"}</b></div>
          <div className="kv"><span>Queries with no evidence</span><b>{pct(m.rag.no_evidence_rate)}</b></div>
          <div className="kv"><span>Prompt / completion tokens</span><b>{m.tokens.prompt} / {m.tokens.completion}</b></div>
        </section>
      </div>

      <div className="grid2">
        <section className="panel pad"><h3>Tool performance</h3>
          {m.tools.by_tool.length === 0 ? <p className="muted small">No tool calls yet.</p> : (
            <table><thead><tr><th>Tool</th><th>Calls</th><th>Success</th><th>Avg</th></tr></thead>
              <tbody>{m.tools.by_tool.map((t) => <tr key={t.tool}><td><code>{t.tool}</code></td><td>{t.calls}</td><td>{pct(t.success_rate)}</td><td>{t.avg_ms} ms</td></tr>)}</tbody></table>)}
        </section>
        <section className="panel pad"><h3>Frequently asked questions</h3>
          {m.frequent_questions.length === 0 ? <p className="muted small">No knowledge questions yet.</p> :
            <BarList data={m.frequent_questions.map((q) => ({ label: q.question, value: q.count }))} />}
        </section>
      </div>

      <div className="grid2">
        <section className="panel pad"><h3>Failed queries</h3>
          {m.failed_queries.length === 0 ? <p className="muted small">None 🎉</p> : (
            <table><thead><tr><th>Question</th><th>Result</th></tr></thead>
              <tbody>{m.failed_queries.map((f, i) => <tr key={i}><td>{f.question}</td><td><i className="pill warn">{f.status}</i></td></tr>)}</tbody></table>)}
        </section>
        <section className="panel pad"><h3>Recent conversations</h3>
          <table><thead><tr><th>Started with</th><th>Customer</th><th>Status</th></tr></thead>
            <tbody>{convs.map((x) => <tr key={x.id}><td>{x.title}</td><td>{x.customer_id ?? "guest"}</td><td><i className={`pill ${x.status}`}>{x.status}</i></td></tr>)}</tbody></table>
        </section>
      </div>
      <p className="muted small">"Resolved" = last reply was answered or completed with no escalation (a heuristic, not a customer satisfaction signal).</p>
    </div>
  );
}
