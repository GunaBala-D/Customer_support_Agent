import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { KbDocument } from "../types";

const kb = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export default function Knowledge() {
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const load = () => api<KbDocument[]>("/api/documents").then((d) => { setDocs(d); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setNotice(""); setError("");
    for (const f of Array.from(files)) {
      const form = new FormData();
      form.append("file", f);
      try { const d = await api<KbDocument>("/api/documents/upload", { form }); setNotice((n) => `${n}${d.filename}: ${d.chunks} chunks indexed. `); }
      catch (e) { setError(`${f.name}: ${(e as Error).message}`); }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    load();
  };

  const remove = async (d: KbDocument) => {
    if (!confirm(`Delete ${d.filename} and its ${d.chunks} chunks from the knowledge base?`)) return;
    try { await api(`/api/documents/${d.id}`, { method: "DELETE" }); load(); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="stack">
      <section className="panel pad dropzone"
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files); }}>
        <div>
          <h3>Add knowledge</h3>
          <p className="muted">PDF, DOCX, Markdown or TXT up to 10 MB. Documents are chunked, embedded locally and become searchable immediately; cached answers are invalidated.</p>
        </div>
        <input ref={input} type="file" multiple hidden accept=".pdf,.docx,.md,.markdown,.txt" onChange={(e) => upload(e.target.files)} />
        <button className="btn primary" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Indexing…" : "Upload documents"}</button>
      </section>
      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner bad">{error}</div>}
      <section className="panel">
        <table>
          <thead><tr><th>Document</th><th>Source</th><th>Pages</th><th>Chunks</th><th>Size</th><th>Embedding model</th><th>Uploaded</th><th /></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td><b>{d.filename}</b></td><td><i className="pill neutral">{d.source}</i></td><td>{d.pages ?? "–"}</td><td>{d.chunks}</td>
                <td>{kb(d.size_bytes)}</td><td><code>{d.embedding_model}</code></td><td>{new Date(d.uploaded_at + "Z").toLocaleString()}</td>
                <td><button className="btn small danger" onClick={() => remove(d)}>Delete</button></td>
              </tr>
            ))}
            {docs.length === 0 && !error && <tr><td colSpan={8} className="muted">No documents indexed.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}
