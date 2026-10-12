import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvalReport } from "../types";

const pct = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

function Score({ label, value, good, hint }: { label: string; value: number | null | undefined; good: "high" | "low"; hint: string }) {
  const tone = value == null ? "" : (good === "high" ? value >= 0.85 : value <= 0.05) ? "ok" : (good === "high" ? value >= 0.6 : value <= 0.15) ? "warn" : "bad";
  return <div className={`kpi ${tone}`} title={hint}><span>{label}</span><b>{pct(value)}</b><small>{hint}</small></div>;
}

export default function Evaluation() {
  const [r, setR] = useState<EvalReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<EvalReport>("/api/evaluation/latest").then(setR).catch((e) => setError(e.message)); }, []);

  if (error) return <div className="banner warn">{error}</div>;
  if (!r) return <p className="muted">Loading…</p>;
  const m = r.metrics, a = m.answers;
  return (
    <div className="stack">
      {r.warnings.map((w) => <div className="banner warn" key={w}><b>Not representative:</b> {w}</div>)}
      <section className="panel pad">
        <h3>Run configuration</h3>
        <div className="kv"><span>Generated</span><b>{new Date(r.meta.generated_at).toLocaleString()}</b></div>
        <div className="kv"><span>LLM</span><b>{r.meta.llm_provider} / {r.meta.llm_model}</b></div>
        <div className="kv"><span>Embeddings</span><b>{r.meta.embedding_provider} / {r.meta.embedding_model}</b></div>
        <div className="kv"><span>Retrieval</span><b>top-{r.meta.top_k}, min score {r.meta.min_score}</b></div>
        <div className="kv"><span>Cases</span><b>{m.counts.total} ({m.counts.knowledge} knowledge · {m.counts.unanswerable} unanswerable · {m.counts.tool} tool flows)</b></div>
      </section>

      <div className="kpis">
        <Score label="Retrieval precision@k" value={m.retrieval.precision_at_k} good="high" hint="chunks from expected sources" />
        <Score label="Source recall" value={m.retrieval.source_recall} good="high" hint="expected docs retrieved" />
        <Score label="Answer correctness" value={a.correctness_knowledge} good="high" hint="all expected facts present" />
        <Score label="Citation correctness" value={a.citation_correctness} good="high" hint="cited the right documents" />
        <Score label="Hallucination rate" value={a.hallucination_rate} good="low" hint="lower is better" />
        <Score label="Tool-call accuracy" value={m.tool_call_accuracy} good="high" hint="right tools, none forbidden" />
        <Score label="Routing accuracy" value={m.routing_accuracy} good="high" hint="intent matches label" />
        <Score label="Correct refusals" value={a.correct_refusal_rate} good="high" hint="unanswerable → fallback" />
      </div>

      <section className="panel pad">
        <h3>Latency</h3>
        <div className="kv"><span>Mean</span><b>{m.latency_ms.mean} ms</b></div>
        <div className="kv"><span>p50 / p95</span><b>{m.latency_ms.p50} / {m.latency_ms.p95} ms</b></div>
      </section>

      <section className="panel">
        <div className="pad"><h3>Cases needing attention ({r.failures.length})</h3></div>
        <table><thead><tr><th>Case</th><th>Question</th><th>Problem</th></tr></thead>
          <tbody>{r.failures.map((f) => <tr key={f.id}><td>{f.id}</td><td>{f.question}</td><td>{f.problems.join("; ")}</td></tr>)}
            {r.failures.length === 0 && <tr><td colSpan={3} className="muted">None.</td></tr>}</tbody></table>
      </section>
      <p className="muted small">Regenerate with <code>make evaluate</code>. Metric definitions are in <code>evaluation/run.py</code>.</p>
    </div>
  );
}
