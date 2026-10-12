"""Evaluation harness:  python -m evaluation.run [--llm ollama|extractive] [--embedding ollama|hash]

Runs every case in dataset.jsonl against an isolated, freshly seeded database and vector store
(your real data is never touched), then writes evaluation/reports/latest.{json,md}.

Metric definitions (all computed from observed behaviour, none estimated):
  retrieval_precision@k   per knowledge case: share of the chunks that cleared the relevance threshold
                          (max k) that come from an expected source document; 0 if none cleared it.
  retrieval_source_recall per knowledge case: share of expected source documents present in those chunks.
  evidence_hit_rate       share of knowledge cases whose expected facts appear in the retrieved chunks.
  answer_correctness      share of knowledge/tool cases whose answer contains ALL expected phrases.
                          This is a lexical proxy: a correct paraphrase can be marked wrong.
  citation_correctness    among answered knowledge cases: >=1 citation and every cited file is expected.
  hallucination_rate      (answers given to unanswerable questions + answered knowledge responses with
                          numbers/terms unsupported by the *full expected source documents*) divided by
                          (unanswerable cases + answered knowledge responses).
  false_fallback_rate     answerable knowledge questions the system refused to answer.
  routing_accuracy        share of all cases whose detected intent matches the expected intent.
  tool_call_accuracy      share of tool cases where the set of tools called equals the expected set and
                          no forbidden tool was called.
"""
import argparse
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import evaluation  # noqa: F401  (adds backend/ to sys.path)
from app.agents.validator import grounding_ratio  # noqa: E402
from app.config.settings import REPO_ROOT, Settings  # noqa: E402
from app.rag.loaders import load_document  # noqa: E402
from app.services.bootstrap import bootstrap  # noqa: E402
from app.services.container import build_container  # noqa: E402
from app.services.orchestrator import handle_chat  # noqa: E402

HERE = Path(__file__).resolve().parent
DOCS = REPO_ROOT / "data" / "documents"


def _mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def _has_all(text: str, phrases: list[str]) -> bool:
    low = text.lower()
    return all(p.lower() in low for p in phrases)


def _full_text(filenames: list[str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(text="\n".join(p.text for p in load_document(DOCS / f))) for f in filenames if (DOCS / f).exists()]


def run_case(c, case, session, retriever_top_k):
    ctype = case["type"]
    out = {"id": case["id"], "type": ctype, "question": case.get("question") or " / ".join(case["turns"])}
    if ctype == "tool":
        turns, conv, tools, last = case.get("turns") or [case["question"]], None, [], None
        start = time.perf_counter()
        for t in turns:
            last = handle_chat(c, session, message=t, conversation_id=conv, customer_id=case.get("customer_id"))
            conv = last["conversation_id"]
            tools += [x["tool"] for x in last["tool_calls"]]
        out["latency_ms"] = (time.perf_counter() - start) * 1000 / len(turns)
        exp = set(case["expected_tools"])
        out.update(
            intent=last["intent"], routing_ok=last["intent"] == case["expected_intent"], answer=last["answer"], tools=tools,
            tool_ok=set(tools) == exp and not (set(tools) & set(case.get("forbidden_tools", []))),
            correct=_has_all(last["answer"], case["expected_phrases"]),
        )
        return out

    question = case["question"]
    retrieval = c.rag.retrieve(question)
    relevant = retrieval.relevant[:retriever_top_k]
    start = time.perf_counter()
    resp = handle_chat(c, session, message=question, conversation_id=None, customer_id=None)
    out["latency_ms"] = (time.perf_counter() - start) * 1000
    c.cache.clear() if hasattr(c.cache, "clear") else None  # every case measured cold
    out.update(intent=resp["intent"], status=resp["status"], answer=resp["answer"],
               cited=[x["filename"] for x in resp["citations"]], routing_ok=resp["intent"] in {"knowledge_question", "refund_question"},
               retrieved=[x.filename for x in relevant], top_score=retrieval.top_score)
    if ctype == "unanswerable":
        out["fell_back"] = resp["status"] in {"fallback", "escalated"}
        return out
    exp_src = set(case["expected_sources"])
    hits = [x for x in relevant if x.filename in exp_src]
    out["precision"] = len(hits) / len(relevant) if relevant else 0.0
    out["source_recall"] = len({x.filename for x in hits}) / len(exp_src)
    out["evidence_hit"] = _has_all(" ".join(x.text for x in relevant), case["expected_phrases"])
    out["answered"] = resp["status"] == "answered"
    out["validation_reasons"] = (resp.get("validation") or {}).get("reasons", [])
    out["correct"] = out["answered"] and _has_all(resp["answer"], case["expected_phrases"])
    out["citation_ok"] = out["answered"] and bool(out["cited"]) and set(out["cited"]) <= exp_src
    if out["answered"]:
        ratio, bad = grounding_ratio(resp["answer"], _full_text(sorted(exp_src)))
        out["hallucinated"] = ratio < 0.6 or bool(bad)
    return out


def summarise(results):
    know = [r for r in results if r["type"] == "knowledge"]
    unans = [r for r in results if r["type"] == "unanswerable"]
    tool = [r for r in results if r["type"] == "tool"]
    answered = [r for r in know if r["answered"]]
    halluc = sum(1 for r in unans if not r["fell_back"]) + sum(1 for r in answered if r.get("hallucinated"))
    denom = len(unans) + len(answered)
    lat = [r["latency_ms"] for r in results]
    return {
        "counts": {"total": len(results), "knowledge": len(know), "unanswerable": len(unans), "tool": len(tool)},
        "retrieval": {
            "precision_at_k": _mean([r["precision"] for r in know]),
            "source_recall": _mean([r["source_recall"] for r in know]),
            "evidence_hit_rate": _mean([1.0 if r["evidence_hit"] else 0.0 for r in know]),
        },
        "answers": {
            "correctness_knowledge": _mean([1.0 if r["correct"] else 0.0 for r in know]),
            "correctness_tool": _mean([1.0 if r["correct"] else 0.0 for r in tool]),
            "citation_correctness": _mean([1.0 if r["citation_ok"] else 0.0 for r in answered]),
            "false_fallback_rate": _mean([0.0 if r["answered"] else 1.0 for r in know]),
            "hallucination_rate": round(halluc / denom, 4) if denom else None,
            "correct_refusal_rate": _mean([1.0 if r["fell_back"] else 0.0 for r in unans]),
        },
        "routing_accuracy": _mean([1.0 if r["routing_ok"] else 0.0 for r in results]),
        "tool_call_accuracy": _mean([1.0 if r["tool_ok"] else 0.0 for r in tool]),
        "latency_ms": {"mean": round(statistics.fmean(lat), 1), "p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
    }


def failures(results):
    bad = []
    for r in results:
        why = []
        if not r["routing_ok"]:
            why.append(f"intent={r['intent']}")
        if r["type"] == "knowledge":
            if r["precision"] < 1:
                why.append(f"precision={r['precision']:.2f}")
            if not r["correct"]:
                why.append("answer missing expected facts" if r["answered"] else "fell back: " + "; ".join(r.get("validation_reasons") or ["model said insufficient context"]))
            elif not r["citation_ok"]:
                why.append(f"citations={r['cited']}")
        elif r["type"] == "unanswerable" and not r["fell_back"]:
            why.append("answered an unanswerable question")
        elif r["type"] == "tool":
            if not r["tool_ok"]:
                why.append(f"tools={r['tools']}")
            if not r["correct"]:
                why.append("answer missing expected text")
        if why:
            bad.append({"id": r["id"], "question": r["question"], "problems": why})
    return bad


def markdown(report) -> str:
    m, meta = report["metrics"], report["meta"]
    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    lines = ["# Evaluation report", "", f"Generated {meta['generated_at']}", ""]
    if report["warnings"]:
        lines += [f"> **Warning:** {w}" for w in report["warnings"]] + [""]
    lines += [
        "## Configuration", "", f"- LLM: `{meta['llm_provider']}` / `{meta['llm_model']}`",
        f"- Embeddings: `{meta['embedding_provider']}` / `{meta['embedding_model']}`",
        f"- Retrieval: top_k={meta['top_k']}, min_score={meta['min_score']}",
        f"- Cases: {m['counts']['total']} ({m['counts']['knowledge']} knowledge, {m['counts']['unanswerable']} unanswerable, {m['counts']['tool']} tool/flow)", "",
        "## Results", "", "| Metric | Value |", "|---|---|",
        f"| Retrieval precision@k | {pct(m['retrieval']['precision_at_k'])} |",
        f"| Retrieval source recall | {pct(m['retrieval']['source_recall'])} |",
        f"| Evidence hit rate | {pct(m['retrieval']['evidence_hit_rate'])} |",
        f"| Answer correctness (knowledge) | {pct(m['answers']['correctness_knowledge'])} |",
        f"| Answer correctness (tool flows) | {pct(m['answers']['correctness_tool'])} |",
        f"| Citation correctness | {pct(m['answers']['citation_correctness'])} |",
        f"| Hallucination rate | {pct(m['answers']['hallucination_rate'])} |",
        f"| Correct refusal on unanswerable | {pct(m['answers']['correct_refusal_rate'])} |",
        f"| False fallback rate | {pct(m['answers']['false_fallback_rate'])} |",
        f"| Routing accuracy | {pct(m['routing_accuracy'])} |",
        f"| Tool-call accuracy | {pct(m['tool_call_accuracy'])} |",
        f"| Latency mean / p50 / p95 | {m['latency_ms']['mean']} / {m['latency_ms']['p50']} / {m['latency_ms']['p95']} ms |", "",
        "## Cases needing attention", "",
    ]
    if report["failures"]:
        lines += ["| Case | Question | Problem |", "|---|---|---|"]
        lines += [f"| {f['id']} | {f['question']} | {'; '.join(f['problems'])} |" for f in report["failures"]]
    else:
        lines.append("None.")
    lines += ["", "See the docstring of `evaluation/run.py` for exact metric definitions."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(HERE / "dataset.jsonl"))
    ap.add_argument("--llm", help="override LLM_PROVIDER (ollama|extractive)")
    ap.add_argument("--embedding", help="override EMBEDDING_PROVIDER (ollama|hash)")
    ap.add_argument("--out-dir", default=str(HERE / "reports"))
    args = ap.parse_args()

    overrides = {k: v for k, v in {"llm_provider": args.llm, "embedding_provider": args.embedding}.items() if v}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows keeps Chroma files locked
        tmp = Path(tmp)
        settings = Settings(**overrides).model_copy(update={
            "database_url": f"sqlite:///{(tmp / 'eval.db').as_posix()}", "chroma_path": (tmp / "chroma").as_posix(),
            "redis_url": "", "documents_dir": DOCS.as_posix()})
        container = build_container(settings)
        print(f"LLM={container.llm.name}/{container.llm.model}  embeddings={container.embedder.model}")
        health = container.llm.health()
        if not health["ok"]:
            raise SystemExit(f"LLM backend not ready: {health['error']}")
        bootstrap(container)
        cases = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
        results = []
        with container.session_factory() as session:
            for case in cases:
                results.append(run_case(container, case, session, settings.rag_top_k))
                print(f"  {case['id']:<4} {'ok ' if not failures([results[-1]]) else 'BAD'}  {results[-1]['latency_ms']:.0f} ms")

    warnings = []
    if container.llm.name == "extractive":
        warnings.append("LLM is the offline extractive baseline, not a language model. Answer metrics do NOT represent Ollama quality.")
    if settings.embedding_provider == "hash":
        warnings.append("Embeddings are the lexical hash baseline, not a semantic model. Retrieval metrics do NOT represent nomic-embed-text.")
    report = {
        "meta": {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "llm_provider": container.llm.name,
                 "llm_model": container.llm.model, "embedding_provider": settings.embedding_provider,
                 "embedding_model": container.embedder.model, "top_k": settings.rag_top_k, "min_score": settings.rag_min_score},
        "warnings": warnings, "metrics": summarise(results), "failures": failures(results),
        "cases": [{k: v for k, v in r.items() if k != "answer"} | {"answer": r.get("answer", "")[:400]} for r in results],
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "latest.md").write_text(markdown(report), encoding="utf-8")
    print("\n" + markdown(report))
    print(f"Report written to {out / 'latest.md'}")


if __name__ == "__main__":
    main()
