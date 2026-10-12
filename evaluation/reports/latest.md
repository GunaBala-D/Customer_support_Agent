# Evaluation report

Generated 2026-10-07T11:00:55+00:00

## Configuration

- LLM: `ollama` / `llama3.2:3b`
- Embeddings: `ollama` / `nomic-embed-text`
- Retrieval: top_k=4, min_score=0.3
- Cases: 47 (28 knowledge, 7 unanswerable, 12 tool/flow)

## Results

| Metric | Value |
|---|---|
| Retrieval precision@k | 66.1% |
| Retrieval source recall | 100.0% |
| Evidence hit rate | 100.0% |
| Answer correctness (knowledge) | 96.4% |
| Answer correctness (tool flows) | 100.0% |
| Citation correctness | 100.0% |
| Hallucination rate | 0.0% |
| Correct refusal on unanswerable | 100.0% |
| False fallback rate | 0.0% |
| Routing accuracy | 100.0% |
| Tool-call accuracy | 100.0% |
| Latency mean / p50 / p95 | 2043.2 / 1913.1 / 3267.8 ms |

## Cases needing attention

| Case | Question | Problem |
|---|---|---|
| k01 | How many days do I have to return a product? | precision=0.75 |
| k02 | Which items cannot be returned? | precision=0.75 |
| k03 | How long do refunds take to show up? | precision=0.50 |
| k04 | How much does a return label cost? | precision=0.75 |
| k06 | What should I do if my item arrives damaged? | precision=0.50 |
| k08 | When can an order be cancelled? | precision=0.50 |
| k09 | How long do refunds for cancelled orders take? | precision=0.50 |
| k10 | Can I change my delivery address after the order ships? | precision=0.75 |
| k11 | How long does standard shipping take? | precision=0.50 |
| k12 | How much does express shipping cost? | precision=0.25 |
| k13 | What order value qualifies for free shipping? | precision=0.50 |
| k14 | Do you ship internationally and how long does it take? | precision=0.50 |
| k15 | Which carriers do you ship with? | precision=0.75 |
| k16 | My package says delivered but I cannot find it | precision=0.25 |
| k17 | How long is the warranty on products? | precision=0.75; answer missing expected facts |
| k20 | How long is the warranty on storage devices? | precision=0.75 |
| k21 | Which payment methods do you accept? | precision=0.25 |
| k23 | Where can I find my invoice? | precision=0.50 |
| k27 | What are your support hours on Saturday? | precision=0.25 |
| k28 | What is the support phone number? | precision=0.25 |

See the docstring of `evaluation/run.py` for exact metric definitions.
