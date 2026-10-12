import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ApplyResult, ManagedUser, Role, SettingsInfo, TestResult } from "../types";

type DbKind = "sqlite" | "postgresql";

function ResultLine({ r, busy }: { r?: TestResult | null; busy?: boolean }) {
  if (busy) return <span className="muted small">Testing…</span>;
  if (!r) return null;
  return r.ok
    ? <span className="result ok">✓ {r.message ?? (r.dimension ? `${r.dimension}-dimension vectors` : `replied "${r.sample ?? ""}"`)}{r.latency_ms != null ? ` · ${r.latency_ms} ms` : ""}{r.server ? ` · ${r.server}` : ""}</span>
    : <span className="result bad">✗ {r.error ?? "failed"}</span>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small className="muted">{hint}</small>}</label>;
}

export default function Settings({ onRelogin }: { onRelogin: (why: string) => void }) {
  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ cls: string; text: string } | null>(null);
  const [busy, setBusy] = useState<string>("");

  // language model
  const [llmProvider, setLlmProvider] = useState("ollama");
  const [ollamaUrl, setOllamaUrl] = useState("");
  const [ollamaModel, setOllamaModel] = useState("");
  const [ctx, setCtx] = useState(4096);
  const [models, setModels] = useState<string[]>([]);
  const [modelsMsg, setModelsMsg] = useState("");
  const [oaUrl, setOaUrl] = useState("");
  const [oaKey, setOaKey] = useState("");
  const [oaModel, setOaModel] = useState("");
  // embeddings
  const [embProvider, setEmbProvider] = useState("ollama");
  const [embModel, setEmbModel] = useState("");
  // database
  const [changeDb, setChangeDb] = useState(false);
  const [dbKind, setDbKind] = useState<DbKind>("postgresql");
  const [sqlitePath, setSqlitePath] = useState("app.db");
  const [pg, setPg] = useState({ host: "127.0.0.1", port: "5432", database: "support", user: "support", password: "" });
  const [seedDemo, setSeedDemo] = useState(false);

  const [tests, setTests] = useState<Record<string, TestResult | null>>({});

  const load = useCallback(() => {
    api<SettingsInfo>("/api/settings").then((s) => {
      setInfo(s); setError("");
      setLlmProvider(s.llm.provider); setOllamaUrl(s.llm.ollama_base_url); setOllamaModel(s.llm.ollama_model); setCtx(s.llm.ollama_num_ctx);
      setOaUrl(s.llm.openai_base_url); setOaModel(s.llm.openai_model); setOaKey("");
      setEmbProvider(s.embedding.provider); setEmbModel(s.embedding.model);
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const dbUrl = () => dbKind === "sqlite"
    ? `sqlite:///${sqlitePath.trim()}`
    : `postgresql+psycopg://${encodeURIComponent(pg.user)}:${encodeURIComponent(pg.password)}@${pg.host.trim()}:${pg.port.trim()}/${pg.database.trim()}`;

  // Only send the fields of the selected provider, so the unused blank ones can't cause a validation error.
  const llmPayload = () => (llmProvider === "ollama"
    ? { llm_provider: llmProvider, ollama_base_url: ollamaUrl, ollama_model: ollamaModel, ollama_num_ctx: ctx }
    : llmProvider === "openai_compatible"
      ? { llm_provider: llmProvider, openai_base_url: oaUrl, openai_model: oaModel, ...(oaKey ? { openai_api_key: oaKey } : {}) }
      : { llm_provider: llmProvider });
  const embPayload = () => ({ embedding_provider: embProvider, ...(embProvider === "hash" ? {} : { embedding_model: embModel }), ...llmPayload() });

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key); setNotice(null);
    try { await fn(); } catch (e) { setNotice({ cls: "bad", text: (e as Error).message }); } finally { setBusy(""); }
  };

  const loadModels = () => run("models", async () => {
    const r = await api<{ ok: boolean; models: string[]; error?: string }>(`/api/settings/ollama-models?base_url=${encodeURIComponent(ollamaUrl)}`);
    setModels(r.models);
    setModelsMsg(r.ok ? `${r.models.length} model(s) installed` : r.error ?? "");
    if (r.ok && r.models.length && !r.models.includes(ollamaModel)) setOllamaModel(r.models[0]);
  });
  const doTestLlm = () => run("llm", async () => {
    const r = await api<TestResult>("/api/settings/test/llm", { body: llmPayload() });
    setTests((t) => ({ ...t, llm: r }));
  });
  const doTestEmb = () => run("emb", async () => {
    const r = await api<TestResult>("/api/settings/test/embedding", { body: embPayload() });
    setTests((t) => ({ ...t, emb: r }));
  });
  const doTestDb = () => run("db", async () => {
    const r = await api<TestResult>("/api/settings/test/database", { body: { database_url: dbUrl() } });
    setTests((t) => ({ ...t, db: r }));
  });

  const save = () => run("save", async () => {
    const body: Record<string, unknown> = { ...embPayload() };
    if (changeDb) { body.database_url = dbUrl(); body.seed_demo_data = seedDemo; }
    const r = await api<ApplyResult>("/api/settings", { method: "PUT", body });
    const bits = Object.entries(r.tests).map(([k, v]) => `${k}: ${v.ok ? "ok" : "failed"}`);
    if (r.reindexed) bits.push(`re-indexed ${r.reindexed.chunks} chunks`);
    if (r.reindex_error) bits.push(`re-index failed: ${r.reindex_error}`);
    setNotice({ cls: r.reindex_error ? "warn" : "ok", text: `Applied. ${bits.join(" · ")}` });
    if (r.relogin_required) { setTimeout(() => onRelogin("The database was changed, so please sign in again with an account from the new database."), 1800); return; }
    setChangeDb(false); setTests({}); load();
  });

  const reset = () => run("reset", async () => {
    if (!confirm("Discard all settings made on this page and go back to the server's environment configuration?")) return;
    const r = await api<{ relogin_required: boolean }>("/api/settings/reset", { method: "POST" });
    if (r.relogin_required) { onRelogin("Settings were reset and the database changed. Please sign in again."); return; }
    setNotice({ cls: "ok", text: "Back to the environment configuration." }); setTests({}); load();
  });

  const reindex = () => run("reindex", async () => {
    const r = await api<{ chunks: number }>("/api/settings/reindex", { method: "POST" });
    setNotice({ cls: "ok", text: `Rebuilt the search index from ${r.chunks} stored chunks.` }); load();
  });

  if (error) return <div className="banner bad">{error}</div>;
  if (!info) return <p className="muted">Loading…</p>;
  const ro = !info.editable;
  const embChanged = embModel !== info.embedding.model || embProvider !== info.embedding.provider;

  return (
    <div className="stack settings">
      {ro && <div className="banner warn">Changing settings from the UI is disabled on this server (ALLOW_RUNTIME_CONFIG=false). You can still test connections.</div>}
      {notice && <div className={`banner ${notice.cls}`}>{notice.text}</div>}
      <p className="muted small">Changes are tested first, then applied immediately with no restart. If a test fails, nothing changes. Secrets are stored on the server and never shown again.
        {info.overridden_keys.length > 0 && <> Overridden here: <code>{info.overridden_keys.join(", ")}</code>.</>}</p>

      <section className="panel pad">
        <h3>Language model <i className={`pill ${info.llm.health.ok ? "ok" : "bad"}`}>{info.llm.health.ok ? "connected" : "not reachable"}</i></h3>
        <div className="form-grid">
          <Field label="Provider">
            <select value={llmProvider} disabled={ro} onChange={(e) => setLlmProvider(e.target.value)}>
              <option value="ollama">Ollama (local, default)</option>
              <option value="openai_compatible">OpenAI-compatible server (LM Studio, vLLM, llama.cpp, hosted API)</option>
              <option value="extractive">Offline test double (no model, for demos and tests)</option>
            </select>
          </Field>
          {llmProvider === "ollama" && (<>
            <Field label="Ollama URL"><input value={ollamaUrl} disabled={ro} onChange={(e) => setOllamaUrl(e.target.value)} placeholder="http://127.0.0.1:11434" /></Field>
            <Field label="Model" hint={modelsMsg || "Click “Load models” to list what is installed, or type a name."}>
              <div className="inline">
                <input list="ollama-models" value={ollamaModel} disabled={ro} onChange={(e) => setOllamaModel(e.target.value)} placeholder="llama3.2:3b" />
                <datalist id="ollama-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
                <button className="btn small" disabled={busy === "models"} onClick={loadModels}>{busy === "models" ? "…" : "Load models"}</button>
              </div>
            </Field>
            <Field label="Context window (tokens)" hint="Smaller is faster on CPU. 4096 is a good default."><input type="number" min={512} max={131072} value={ctx} disabled={ro} onChange={(e) => setCtx(Number(e.target.value))} /></Field>
          </>)}
          {llmProvider === "openai_compatible" && (<>
            <Field label="Base URL" hint="Must end in /v1, e.g. http://127.0.0.1:1234/v1"><input value={oaUrl} disabled={ro} onChange={(e) => setOaUrl(e.target.value)} placeholder="http://127.0.0.1:1234/v1" /></Field>
            <Field label="API key" hint={info.llm.openai_api_key ? `Stored (${info.llm.openai_api_key}). Leave blank to keep it.` : "Optional for local servers."}>
              <input type="password" autoComplete="off" value={oaKey} disabled={ro} onChange={(e) => setOaKey(e.target.value)} placeholder={info.llm.openai_api_key ? "unchanged" : ""} />
            </Field>
            <Field label="Model"><input value={oaModel} disabled={ro} onChange={(e) => setOaModel(e.target.value)} placeholder="model name as the server lists it" /></Field>
          </>)}
        </div>
        <div className="row-actions"><button className="btn" disabled={busy === "llm"} onClick={doTestLlm}>Test model</button><ResultLine r={tests.llm} busy={busy === "llm"} /></div>
      </section>

      <section className="panel pad">
        <h3>Embeddings <span className="muted small">(turn documents into searchable vectors)</span></h3>
        <div className="form-grid">
          <Field label="Provider">
            <select value={embProvider} disabled={ro} onChange={(e) => setEmbProvider(e.target.value)}>
              <option value="ollama">Ollama (local, default)</option>
              <option value="openai_compatible">OpenAI-compatible server</option>
              <option value="hash">Lexical hash baseline (tests/offline only)</option>
            </select>
          </Field>
          <Field label="Embedding model" hint="e.g. nomic-embed-text"><input value={embModel} disabled={ro} onChange={(e) => setEmbModel(e.target.value)} /></Field>
        </div>
        {embChanged && <div className="banner warn inline-banner">Changing the embedding model re-indexes every document automatically (from the text already stored). This can take a minute.</div>}
        <div className="row-actions"><button className="btn" disabled={busy === "emb"} onClick={doTestEmb}>Test embeddings</button><ResultLine r={tests.emb} busy={busy === "emb"} /></div>
        <div className="kb-line">
          <span>Search index: <b>{info.knowledge_base.indexed}</b> of <b>{info.knowledge_base.chunks}</b> chunks from {info.knowledge_base.documents} document(s)</span>
          {info.knowledge_base.needs_reindex && <><i className="pill warn">out of date</i><button className="btn small" disabled={ro || busy === "reindex"} onClick={reindex}>{busy === "reindex" ? "Rebuilding…" : "Rebuild index"}</button></>}
        </div>
      </section>

      <section className="panel pad">
        <h3>Database <i className="pill neutral">{info.database.dialect}</i></h3>
        <div className="kv"><span>Current</span><code>{info.database.url}</code></div>
        {!changeDb ? (
          <div className="row-actions"><button className="btn" disabled={ro} onClick={() => setChangeDb(true)}>Change database…</button></div>
        ) : (<>
          <div className="tabs small">
            <button className={dbKind === "postgresql" ? "tab active" : "tab"} onClick={() => setDbKind("postgresql")}>PostgreSQL</button>
            <button className={dbKind === "sqlite" ? "tab active" : "tab"} onClick={() => setDbKind("sqlite")}>SQLite</button>
          </div>
          {dbKind === "postgresql" ? (
            <div className="form-grid">
              <Field label="Host"><input value={pg.host} onChange={(e) => setPg({ ...pg, host: e.target.value })} /></Field>
              <Field label="Port"><input value={pg.port} onChange={(e) => setPg({ ...pg, port: e.target.value })} /></Field>
              <Field label="Database" hint="Must already exist (CREATE DATABASE …)."><input value={pg.database} onChange={(e) => setPg({ ...pg, database: e.target.value })} /></Field>
              <Field label="User"><input value={pg.user} onChange={(e) => setPg({ ...pg, user: e.target.value })} /></Field>
              <Field label="Password"><input type="password" autoComplete="off" value={pg.password} onChange={(e) => setPg({ ...pg, password: e.target.value })} /></Field>
            </div>
          ) : (
            <div className="form-grid"><Field label="File" hint="Relative names are stored under the project's data/ folder."><input value={sqlitePath} onChange={(e) => setSqlitePath(e.target.value)} /></Field></div>
          )}
          <label className="check"><input type="checkbox" checked={seedDemo} onChange={(e) => setSeedDemo(e.target.checked)} /> Load demo customers, orders and the sample documents into this database</label>
          <div className="banner warn inline-banner">Existing data is <b>not copied</b>. The app creates its tables in the target database (never touching other tables) and uses it from then on; switching back later brings your old data back. Everyone will need to sign in again.</div>
          <div className="row-actions">
            <button className="btn" disabled={busy === "db"} onClick={doTestDb}>Test connection</button><ResultLine r={tests.db} busy={busy === "db"} />
            <button className="btn small" onClick={() => { setChangeDb(false); setTests((t) => ({ ...t, db: null })); }}>Cancel</button>
          </div>
        </>)}
      </section>

      <div className="save-bar">
        <button className="btn danger" disabled={ro || busy === "reset"} onClick={reset}>Reset to defaults</button>
        <button className="btn primary" disabled={ro || busy === "save"} onClick={save}>{busy === "save" ? "Testing and applying…" : "Save & apply"}</button>
      </div>

      <Users />
    </div>
  );
}

function Users() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "agent" as Role, customer_id: "" });
  const load = () => api<ManagedUser[]>("/api/users").then(setUsers).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const add = async () => {
    setError("");
    try {
      await api("/api/users", { body: { ...form, customer_id: form.role === "customer" ? form.customer_id : undefined } });
      setForm({ email: "", name: "", password: "", role: "agent", customer_id: "" }); load();
    } catch (e) { setError((e as Error).message); }
  };
  const toggle = async (u: ManagedUser) => {
    setError("");
    try { await api(`/api/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } }); load(); } catch (e) { setError((e as Error).message); }
  };

  return (
    <section className="panel">
      <div className="pad"><h3>Users</h3>{error && <div className="banner bad inline-banner">{error}</div>}</div>
      <table>
        <thead><tr><th>Name</th><th>E-mail</th><th>Role</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.is_active ? "" : "dim"}>
              <td>{u.name}</td><td>{u.email}</td><td><i className={`pill ${u.role === "admin" ? "bad" : u.role === "agent" ? "info" : "neutral"}`}>{u.role}</i></td>
              <td className="muted small">{u.last_login_at ? new Date(u.last_login_at + "Z").toLocaleString() : "never"}</td>
              <td><button className="btn small" onClick={() => toggle(u)}>{u.is_active ? "Disable" : "Enable"}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="pad add-user">
        <input placeholder="E-mail" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input placeholder="Full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input type="password" autoComplete="new-password" placeholder="Password (8+ chars)" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
          <option value="agent">agent</option><option value="admin">admin</option><option value="customer">customer</option>
        </select>
        {form.role === "customer" && <input placeholder="CUST-1001" value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value })} />}
        <button className="btn primary" disabled={!form.email || !form.name || form.password.length < 8} onClick={add}>Add user</button>
      </div>
    </section>
  );
}
