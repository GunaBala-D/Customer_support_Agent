# SupportPilot

**A local-first AI customer-support & operations agent.** Answers from your own documents with citations, looks up and manages orders through safe tools, and hands off to a human when it should.

> **About the names.** *SupportPilot* is the project/console. *Acme Electronics* is the fictional online store used for the sample documents, products and orders, a placeholder to replace with your own company's documents.

A production-style support agent that runs **entirely on your machine**: a local LLM served by
[Ollama](https://ollama.com), retrieval-augmented answers with citations, permission-checked tool calling
against a real database, human escalation, observability, an evaluation harness and an admin console.

No paid API is required anywhere. Ollama is the primary LLM and embedding backend.

> **Status of verification.** Tested end to end on a Windows PC (CPU only) with real Ollama models
> (`llama3.2:3b` + `nomic-embed-text`): 178 pytest tests, the evaluation harness, the full UI, and live outage/rate-limit
> checks. Docker images and PostgreSQL/Redis containers have **not** been run (Docker wasn't available).
> See [What was and wasn't verified](#17-what-was-and-wasnt-verified).

---

## 1. Problem statement

Support teams answer the same questions (returns, shipping, warranty) and run the same lookups (order status,
cancellation) all day. A plain chatbot fails here in three ways: it **invents policies**, it **can't act** on real data,
and it **never hands off** when it's out of its depth. This project addresses each:

| Failure mode | How this system handles it |
|---|---|
| Invented policies | Answers come only from retrieved chunks; a validator rejects unsupported answers and falls back |
| Can't act on data | Tool layer over SQL (orders, customers, tickets) with ownership checks and audit logging |
| Risky actions | Destructive tools require an explicit user confirmation, enforced in the tool layer |
| No hand-off | Complaints, payment mismatches, repeated failures and explicit requests create a support ticket |
| Black box | Every reply carries sources, tool activity, a high-level step trace and latency breakdown |

## 2. Architecture

```
React + TS console ──► FastAPI ──► rate limit / request-id middleware
                         │
                         ▼
                 Orchestrator service ──► persists conversation, messages, tool_calls, events
                         │
                         ▼
                 LangGraph agent
   START ─► router ─┬─► knowledge ──► (retrieve → rerank → context → Ollama) ─┐
                    ├─► tools ──────► tool executor (validate · authorise · confirm · audit) ─┤
                    ├─► confirm ────► executes a *pending, user-confirmed* action ┤
                    ├─► escalate ───► create_support_ticket ─────────────────────┤
                    └─► unknown ────► capability menu ───────────────────────────┤
                                                                                  ▼
                                                               validator ─► (escalate once if needed) ─► END
```

```
LLMProvider (interface) ── OllamaProvider │ ExtractiveProvider (offline test double)
EmbeddingProvider ──────── OllamaEmbeddings (nomic-embed-text) │ HashEmbeddings (offline lexical)
VectorStore (interface) ── ChromaVectorStore (swap for pgvector / Qdrant / Pinecone)
Cache ──────────────────── RedisCache │ InMemoryCache
```

Repository layout:

```
backend/app/{api,agents,rag,tools,models,services,database,middleware,config}
backend/tests/          178 pytest tests (unit + integration)
frontend/               React + TypeScript (Vite) console
data/documents/         sample knowledge base (md, txt, pdf, docx)
evaluation/             dataset.jsonl, run.py, reports/
scripts/  docker/  docker-compose.yml  Makefile  .env.example
```

## 3. Technology stack

Python 3.12 · FastAPI · SQLAlchemy 2 · LangGraph · ChromaDB · Ollama · SQLite or PostgreSQL ·
Redis (optional) · pypdf / python-docx · pytest · ruff · React 18 + TypeScript + Vite · Docker Compose.

## 4. How RAG works

`Documents → loader → cleaning → chunking → embeddings → vector store → retriever → rerank/filter → context builder → Ollama → answer + sources`

* **Loaders** (`rag/loaders.py`): PDF (page numbers kept), DOCX (headings and tables), Markdown, TXT.
* **Chunking** (`rag/chunking.py`): paragraph packing to ~800 chars with 120 chars of overlap; Markdown headings
  become a section path (`Refund Policy > Eligibility`) prepended to each chunk; chunks never cross page boundaries so
  page citations stay correct.
* **Metadata**: filename, document_id, page, section, chunk_id, upload timestamp, source (`upload` | `bootstrap`) are stored
  in both SQL (`documents`, `document_chunks`) and the vector store.
* **Embeddings**: `nomic-embed-text` via Ollama. Why: ~270 MB, 768 dims, 8k context, fast on CPU and Apple Silicon, and
  strong retrieval quality for its size. It needs task prefixes (`search_document:` / `search_query:`), which the client adds.
  Configurable via `EMBEDDING_MODEL`. The vector collection name includes the embedding model, so vectors from different
  models never mix.
* **Retriever + reranker** (`rag/retriever.py`): fetch 12 candidates, re-score with
  `0.5 × cosine similarity + 0.5 × query-term coverage`, keep those ≥ `RAG_MIN_SCORE`, return top-k (4). The lexical term stops
  dense retrieval's classic failure: an off-topic question still returning its "nearest" chunk.
* **Context builder** (`rag/context.py`): numbered blocks `[1] (refund_policy.md · Eligibility)` inside a character budget
  (3000) sized for small local context windows.
* **Citations are never written by the model.** The prompt asks for `[n]` markers; we map each marker back to the metadata of
  a chunk that was actually in the prompt. Markers pointing at non-existent chunks are dropped. If a small model forgets
  markers, the single best supplied chunk is cited and the response is tagged `citation_mode: retrieval_fallback`.
* **Ingestion**: re-uploading identical content is a no-op; a changed file with the same name replaces the old version.
  Embeddings are computed before anything is written, so a failed embedding backend leaves no partial state.

## 5. How agent routing works

Routing is **hybrid and deliberately conservative** (`agents/router.py`, `agents/graph.py`):

1. **Rules first**: regexes for high-precision cases (human request, payment/order mismatch, cancel wording, complaint,
   status wording + order reference, refund wording, account details, lost package). Fast, free and unit-tested.
2. **LLM classification** only when rules are inconclusive: a short JSON-mode prompt. Output is validated against the intent
   list; anything else becomes `unknown`.
3. **Identifiers are extracted by regex**, never by the model. A mis-classified intent can at worst trigger a harmless
   "which order?" question, because tools still need a valid ID, an owner match and (for destructive tools) a confirmation.

Intents: `knowledge_question`, `order_status`, `customer_information`, `refund_question`, `cancellation_request`,
`complaint`, `escalation`, `unknown`. A `refund_question` about *your order* ("Can I return ORD-1002?") goes to a tool; a general
one ("What is your refund policy?") goes to RAG. "How do I cancel my order?" is a knowledge question; "Please cancel my order" is an action.

**Memory** (`agents/deps.py:new_memory`): short-term, per conversation, stored in `conversations.state`: last order ID
(resolves "it"), a pending confirmation, counters. Long-term customer data stays in the database and is only reachable through
permission-checked tools, never copied into memory or prompts. The last 6 messages are loaded as history; short follow-ups
("And express?") borrow the previous question's topic for retrieval.

## 6. How tool calling works

Tools (`tools/functions.py`): `get_order_status`, `get_order_items`, `get_customer_details`, `check_refund_eligibility`,
`create_support_ticket`, `cancel_order`. Every call, from the agent **or** from a REST endpoint, goes through `run_tool`
(`tools/executor.py`):

1. **Argument validation**: strict patterns (`^ORD-\d{3,8}$`); SQL is only ever parameterised through SQLAlchemy.
2. **Ownership**: a signed-in customer can only touch their own orders/profile. "Not yours" and "doesn't exist" return the
   same message so IDs can't be probed.
3. **Confirmation gate**: `cancel_order` is `destructive`; without `confirmed=True` the executor refuses. The agent sets that flag
   only on the turn after the customer replies "yes" to an explicit prompt. A stray "yes" later does nothing, and any unrelated
   message discards the pending action. The REST endpoint requires `{"confirm": true}`.
4. **Audit**: one `tool_calls` row per call (arguments, result, success, duration, confirmed) plus a structured event.

Tool answers are **rendered from templates** (`agents/render.py`), not generated by the LLM, so dates, tracking numbers and totals
can't be paraphrased wrongly by a small model. The LLM is used where language understanding is needed: classification fallback
and grounded knowledge answers.

## 7. How hallucination is controlled

| Safeguard | Where | Behaviour |
|---|---|---|
| Similarity threshold | `retriever.py` | Chunks below `RAG_MIN_SCORE` are not evidence |
| No evidence → no generation | `graph.py` | If nothing passes, the LLM is not even called |
| Grounded prompt | `rag/pipeline.py` | "Use ONLY the context, else reply `INSUFFICIENT_CONTEXT`" |
| Citation requirement | `validator.py` | An answer without a citation fails validation |
| Grounding check | `validator.py` | ≥ 60% of the answer's content terms must occur in the context |
| Number check | `validator.py` | Every number in the answer must appear in the context (catches "14 days" vs "30 days") |
| Fallback | `graph.py` | "I couldn't find reliable information about that in the available knowledge base." |
| Escalation | `graph.py` | Offer a human after one failure; open a ticket automatically after the second in a row |

**Metrics we expose, and their definitions**: *retrieval relevance* (top blended retrieval score, 0–1) and *grounding ratio*
(share of answer terms found in the context, 0–1). They are measurements, not probabilities. There is no "AI confidence %".

Honest limitation: the grounding check is lexical. It catches invented numbers and off-context answers but cannot catch a
fluent answer that reuses context words to say something false. Evaluation tracks this (see below).

## 8. Database architecture

SQLAlchemy models (`models/db.py`), PostgreSQL in Docker, SQLite by default for development:
`customers`, `products`, `orders`, `order_items`, `support_tickets`, `conversations`, `messages`, `tool_calls`, `documents`,
`document_chunks`, plus `events` (observability) and `ticket_notes` (agent activity on tickets). Seed data (`database/seed.py`, deterministic): 120 customers, 56 products,
240 orders with internally consistent timelines (shipped after created, delivered after shipped, status matches dates), 45 tickets,
and hand-written demo scenarios (`ORD-1001` processing/cancellable, `ORD-1002` delivered 10 days ago, `ORD-1003` delivered 45 days
ago, `ORD-1024` shipped, `ORD-1004` belongs to another customer). The PostgreSQL DDL for all 11 tables was compiled in
tests; a live Postgres container was not run here.

## 9. Observability

Every chat turn stores on the assistant message: latency, LLM latency, retrieval latency, tool latency, retrieved chunk count,
top score, token usage (when the model reports it), model name, response status. Structured events
(`retrieval`, `llm_call`, `tool_call`, `validation_failed`, `chat_response`, `error`) are written as JSON log lines and to the
`events` table (`GET /api/events`); HTTP requests are logged with their latency. Every response carries `X-Request-ID`. **No chain-of-thought is stored or shown.** The
"How this was handled" panel contains short system-level steps ("Intent: order_status (rule); route: tools", "4 of 12 chunks passed the threshold").

## Human-agent inbox

Escalations become tickets that a human works in the console's **Agent inbox** page (admin key required):

* **Queue**: tabs with live counts (All / Open / In progress / Resolved), search (subject, customer, order) and a priority filter.
  Order: unclaimed *open* tickets first, then in progress, resolved last; inside each group high priority first, then longest-waiting.
* **Ticket view**: the customer's full chat transcript (so they never repeat themselves), customer and order details, why it was
  escalated, and an activity log.
* **Actions**: change status/priority, **assign to me** (an open ticket moves to in progress), add an **internal note**
  (never shown to the customer), or **reply to the customer**. A reply is appended to the customer's conversation as a message from a
  "Human support agent"; the customer's chat polls every 8 s, so it appears without a reload.
* Agent activity lives in its own `ticket_notes` table, so existing databases pick it up on startup with no migration.

Limits: the agent identity is just a name typed into the page (the admin key has no per-user identity); there is no per-agent login,
SLA timers, or email notification to the customer. The bot keeps answering the customer after a human has replied.

## Authentication and roles (JWT)

Real logins replace the old demo header. `POST /api/auth/login` returns a signed JWT (HS256, 60 min by default, `JWT_EXPIRE_MINUTES`).
The browser keeps it in `sessionStorage` and sends `Authorization: Bearer <token>`.

| Role | Can do |
|---|---|
| **customer** | chat with account tools for *their own* orders/profile; read their own conversations |
| **agent** | everything a customer can as a guest, plus the **Agent inbox** (work tickets, notes, replies) and read any order |
| **admin** | everything: dashboard, knowledge base, evaluation, **Settings**, user management |
| *guest* | policy questions only (no tools) |

Design points worth knowing (each has tests):
* **Passwords** are hashed with scrypt (per-user salt, stdlib, no extra dependency). Wrong e-mail and wrong password return the *same*
  error after the *same* work, so accounts can't be enumerated. Login is limited to 10 attempts/minute per IP.
* **The role is read from the database on every request**, not trusted from the token: disabling a user or changing a role takes effect
  immediately, and a forged `role: admin` claim gets a 403. Tokens with `alg=none`, a wrong signature, or an expired time are rejected.
* **Tokens are bound to a database fingerprint**, so after you switch databases a token can't be mistaken for a different person's account.
* Agents' names on notes/replies come from their login, never from the request body (no impersonation).
* `POST /api/users` / `PATCH /api/users/{id}` (admin) create, disable and re-role users; the last active admin can't be locked out.
* Development convenience, **off in production** (`APP_ENV=production`): demo logins on the sign-in screen (`/api/demo/accounts`), the
  `X-Customer-Id` header, and a built-in dev admin password. Production **refuses to start** without a 32+ character `JWT_SECRET`, and only
  creates an admin when `ADMIN_PASSWORD` (12+ chars) is set.
* `X-API-Key` still works as an optional static *service* credential acting as admin (for scripts); set `ALLOW_API_KEY=false` to remove it.

Development demo accounts (password for customers/agent: `demo-password`; admin: `admin-demo-1`): `admin@supportpilot.example`,
`agent@supportpilot.example`, and the first eight seeded customers (e.g. `aisha.khan@example.com`).

## Settings page (change the LLM and database from the UI)

Admins get a **⚙ Settings** button in the top bar. No file editing or restart:

* **Language model**: Ollama (pick from the models it has installed via *Load models*), any **OpenAI-compatible server** (LM Studio, vLLM,
  llama.cpp server, or a hosted API; optional, never required), or the offline test double. **Test model** sends a real request.
* **Embeddings**: provider and model, with a **Test embeddings** button. Changing the model re-indexes every document automatically from the
  chunk text already stored in the database.
* **Database**: SQLite or PostgreSQL, built from host/port/database/user/password, with **Test connection**, which tells you whether the target is
  empty, an older app database (it will be adopted and migrated, data kept), up to date, or something it will refuse to touch. Optionally load the
  demo data into it.
* **Users**: add, disable and enable logins.

How it stays safe: every change is **tested first**, a brand-new application container is built (connecting, migrating, loading models), and only
then swapped in. If any step fails nothing changes and the old configuration keeps serving. Secrets are masked in every response and never shown again;
passwords inside the database URL are masked too. **Reset to defaults** drops all UI overrides and returns to `.env`.

Things to know: overrides are saved to `data/runtime_settings.json` (gitignored, owner-only permissions where the OS supports it). It holds
the database password and API key in plain text, like `.env` does. Switching database does **not** copy data; each database keeps its own
knowledge base, and switching back restores everything. After a switch everyone must sign in again. Set `ALLOW_RUNTIME_CONFIG=false` on a
locked-down server to make the page read-only. Because an admin can point the app at any URL, only trusted admins should have the role.

## Database migrations (Alembic)

Schema changes are versioned in `backend/migrations/` and applied automatically at startup (`RUN_MIGRATIONS=false` to run them yourself).

```bash
cd backend
python -m alembic upgrade head                       # apply
python -m alembic revision --autogenerate -m "add x"  # after editing app/models/db.py
python -m alembic check                              # fails if models and migrations disagree
```

* Revisions: `0001` baseline (the 12 original tables), `0002` users.
* **Databases created before Alembic existed are adopted, not rebuilt**: if all the original tables are present with no version table, the app
  stamps `0001` and runs the rest, keeping your data (tested on SQLite and live on PostgreSQL).
* **It refuses to touch schemas it doesn't recognise**: a database with only *some* of the application's tables is rejected. Other applications'
  tables are never modified, and the version table has its own name (`support_alembic_version`) so it can't collide with another app's `alembic_version`.
* A test fails the build if someone edits the models without writing a migration.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: **backend tests on SQLite and on a PostgreSQL 16 service container**, ruff,
an Alembic upgrade → drift check → downgrade on both databases, a smoke run of the evaluation harness with the offline providers, the frontend
type-check and build, and finally `docker compose config` plus both Docker image builds and a boot test of the backend image.
*The workflow file is validated locally (its commands were run on SQLite and PostgreSQL), but it has not yet run on GitHub.*

## 10. Caching

* Cached: validated knowledge answers (key = normalised query terms + `kb_version`) and public product lookups.
* **Never cached**: orders, customer details, tickets, anything personal. A test asserts no `ORD-`/`CUST-` keys are cached.
* **Invalidation**: uploading or deleting a document bumps `kb_version`, making every older answer unreachable at once; TTL
  (`CACHE_TTL_SECONDS`) bounds everything else. Only answers that passed validation are cached.
* Redis when `REDIS_URL` is set (failures never break a request); otherwise an in-process cache (single worker only).

## 11. Security

Environment-based config, no secrets in code · JWT login with scrypt-hashed passwords and role checks (see *Authentication and roles*) ·
pydantic input validation (message ≤ 2000 chars, UUID conversation IDs, ID patterns) · SQLAlchemy parameterised queries (a test throws an
injection string at the ID path) · per-IP sliding-window rate limit on POST endpoints (10/min on login) · upload hardening (extension
allow-list, size cap, client file name never used as a path) · tool permission checks and a destructive-action gate · conversations are only
readable by their owner or staff (404 otherwise) · secrets masked in every settings response.

**Before a real deployment:** set `APP_ENV=production`, a random `JWT_SECRET`, and `ADMIN_PASSWORD`; serve over HTTPS behind a reverse proxy
(the app doesn't terminate TLS); rotate the database password away from any development value; consider `ALLOW_RUNTIME_CONFIG=false`. The in-memory
rate limiter is per process; use Redis or a gateway behind several workers. Not implemented: refresh tokens, password reset by e-mail, MFA,
account lockout beyond the login rate limit, and a revocation list (a disabled user is cut off immediately because the user row is checked on
each request, but a stolen token stays valid until it expires for an *active* user).

## 12. Evaluation methodology

`python -m evaluation.run` (or `make evaluate`) runs `evaluation/dataset.jsonl` (47 cases: 28 answerable knowledge
questions with expected source documents and facts, 7 unanswerable questions including hard negatives like "price match", and
12 tool/multi-turn flows) against a **fresh isolated database and vector store**, then writes `evaluation/reports/latest.{md,json}`
(shown in the console's *Evaluation* page).

| Metric | Definition |
|---|---|
| Retrieval precision@k | per knowledge case, share of above-threshold chunks from an expected source |
| Retrieval source recall | share of expected source documents present in those chunks |
| Evidence hit rate | expected facts appear in the retrieved chunks |
| Answer correctness | answer contains **all** expected phrases (lexical proxy; paraphrases can be marked wrong) |
| Citation correctness | answered cases: ≥ 1 citation and every cited file is an expected source |
| Hallucination rate | (answers to unanswerable questions + answered responses with numbers/terms unsupported by the *full* expected source documents) / (unanswerable + answered) |
| Tool-call accuracy | exact set of tools called, no forbidden tool (e.g. `cancel_order` after "no") |
| Routing accuracy, latency | intent matches label; mean / p50 / p95 per turn |

The report states which LLM and embeddings produced it and prints a warning when those are the offline baselines.

**Measured result** (`llama3.2:3b` + `nomic-embed-text`, 47 cases, `RAG_MIN_SCORE=0.30`, CPU-only Windows PC; LLM sampling is
not fully deterministic, so expect small run-to-run differences): source recall 100%, evidence hit 100%, citation correctness
100%, hallucination 0%, correct refusal of unanswerable questions 100%, routing 100%, tool-call accuracy 100%, answer correctness
93-96% (phrase match), false-fallback 0-3.6%, **retrieval precision@k 68%**, latency mean ~1.5 s / p95 ~3 s per turn.

Precision@k is the weak metric: `nomic-embed-text` scores unrelated chunks around 0.55 cosine, so a 0.30 blended threshold
lets 4 chunks through even when 1 is enough. Raising `RAG_MIN_SCORE` to 0.50 lifted precision to 76% but cost recall
(100% → 96%) and produced a false refusal, so 0.30 is kept: the validator, not the threshold, is the safety net.
Recalibrate with `make evaluate` whenever you change the embedding model.

> **On a Mac?** Follow [`SETUP_MAC.md`](SETUP_MAC.md) for a step-by-step guide.

## 13. Local Ollama setup (Mac, Apple Silicon recommended)

```bash
# 1. Install and start Ollama
brew install ollama            # or download from https://ollama.com/download
ollama serve                   # leave running (the desktop app starts it automatically)

# 2. Pull a small instruct model and the embedding model
ollama pull llama3.2:3b        # ~2 GB; good on 8-16 GB Macs. Alternatives: qwen2.5:3b, phi3.5, llama3.1:8b (needs ~16 GB)
ollama pull nomic-embed-text   # ~270 MB

# 3. Smoke test
ollama run llama3.2:3b "Reply with OK"
```

Sizing guidance for CPU/Apple Silicon: prefer 3B-class models; keep `OLLAMA_NUM_CTX=4096` (smaller = faster); the first request
after idle is slower while weights load (`keep_alive: 10m` is set). Change models with `OLLAMA_MODEL` only; nothing else in the code
depends on a specific model. `GET /api/health` tells you if the model isn't pulled and prints the `ollama pull` command to fix it.

## 14. Installation and running

```bash
git clone <this repo> && cd local-ai-support-agent
make install            # venv, pip install, npm install, copies .env.example -> .env
# optional: edit .env (database, models). A JWT secret is generated for you in development.
make seed               # demo data + indexes data/documents with nomic-embed-text (needs Ollama running)
make dev                # backend :8000 (docs at /docs) + frontend :5173
make test               # pytest
make evaluate           # evaluation report
make lint               # ruff + tsc
```

Open http://localhost:5173 and sign in with one of the development demo accounts shown on the login screen (click a chip), or continue as a
guest. The admin account unlocks Operations, Knowledge base, Evaluation and ⚙ Settings. (`AUTO_SEED=true` also seeds on first start.)

On Windows without `make`, use the commands in the Makefile directly, or `make PY=backend/.venv/Scripts/python <target>` from Git Bash.

**No Ollama yet?** `LLM_PROVIDER=extractive EMBEDDING_PROVIDER=hash` runs the whole system offline using the test doubles
(an extractive sentence picker and a lexical hashing embedder). It is for tests/CI/demos of the plumbing, **not** for judging answer quality.

### Using PostgreSQL locally (no Docker)

```bash
# once, as the postgres superuser: create an isolated database and user for the app
psql -U postgres -h 127.0.0.1 -c "CREATE USER support WITH PASSWORD 'choose-a-password';" -c "CREATE DATABASE support OWNER support;"
# .env
DATABASE_URL=postgresql+psycopg://support:choose-a-password@127.0.0.1:5432/support
python scripts/seed.py --reset     # creates the tables, seeds data, re-indexes the documents
```

Tables are created and migrated automatically on startup (Alembic; see above). You can also enter these details in ⚙ Settings → Database.
Run the test suite on PostgreSQL with a **separate, empty** database (its tables are dropped):
`TEST_DATABASE_URL=postgresql+psycopg://support:...@127.0.0.1:5432/support_test pytest`. Use a strong password outside local development.

### Docker

```bash
cp .env.example .env            # for anything beyond a local demo set APP_ENV=production, JWT_SECRET and ADMIN_PASSWORD
# A) Ollama on the host (recommended on a Mac: uses the GPU via Metal)
ollama serve &  ollama pull llama3.2:3b && ollama pull nomic-embed-text
docker compose up --build       # UI http://localhost:3000, API http://localhost:8000/docs
# B) Ollama in a container (CPU only on macOS)
OLLAMA_BASE_URL=http://ollama:11434 docker compose --profile ollama up --build
docker compose exec ollama ollama pull llama3.2:3b && docker compose exec ollama ollama pull nomic-embed-text
```

Compose runs PostgreSQL, Redis, the backend and the frontend (nginx, proxying `/api`). ChromaDB runs embedded in the backend and
persists to a volume; the vector store sits behind an interface, so a separate Chroma/Qdrant/pgvector service is a drop-in change.

## 15. API examples

```bash
# Knowledge question (no sign-in needed)
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"How long does standard shipping take?"}' | jq '{answer, citations: [.citations[].label], status}'

# Sign in (returns an access_token) and use it as a customer
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"aisha.khan@example.com","password":"demo-password"}' | jq -r .access_token)

# Order lookup as that customer, then a follow-up using memory
curl -s localhost:8000/api/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"My order number is ORD-1024."}'
# -> pass the returned conversation_id on the next call: {"message":"Where is it now?","conversation_id":"..."}

# Destructive action over REST requires explicit confirmation
curl -s -X POST localhost:8000/api/orders/ORD-1001/cancel -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"confirm":true}'

# Admin (sign in as an admin the same way, or use the optional service key)
curl -s localhost:8000/api/metrics   -H "Authorization: Bearer $ADMIN_TOKEN"
curl -s -X POST localhost:8000/api/documents/upload -H "Authorization: Bearer $ADMIN_TOKEN" -F file=@my_policy.pdf
curl -s localhost:8000/api/settings  -H "Authorization: Bearer $ADMIN_TOKEN"   # current LLM / database (secrets masked)
```

Endpoints: `POST /api/auth/login` · `GET /api/auth/me` · `POST /api/auth/change-password` · `GET|POST|PATCH /api/users` · `GET|PUT /api/settings` · `POST /api/settings/test/{llm,embedding,database}` · `POST /api/settings/{reindex,reset}` · `POST /api/chat` · `POST /api/documents/upload` · `GET|DELETE /api/documents` · `GET /api/conversations[/{id}]` ·
`GET /api/orders/{id}` · `POST /api/orders/{id}/cancel` · `POST|GET /api/support/tickets` · `GET /api/products/{sku}` ·
`GET /api/support/tickets` (inbox queue) · `GET|PATCH /api/support/tickets/{ref}` · `POST /api/support/tickets/{ref}/notes|reply` · `GET /api/metrics` · `GET /api/events` · `GET /api/evaluation/latest` · `GET /api/health`. OpenAPI at `/docs`.

## 16. Screenshots

_Add after your first run:_ `docs/chat.png` (chat + inspector) · `docs/dashboard.png` · `docs/knowledge.png` · `docs/evaluation.png`

## 17. What was and wasn't verified

| Verified (run in the author's environment) | Not verified |
|---|---|
| 178 pytest tests pass on SQLite **and** on PostgreSQL 16; the app, seed, inbox, login and Settings flows run live on PostgreSQL | `docker compose up`, image builds, the Redis/Postgres *containers* (Docker not installed) |
| Real Ollama (`llama3.2:3b`, `nomic-embed-text`): ingestion, RAG answers, JSON-mode classification, evaluation run (§12) | Live Redis; the GitHub Actions workflow itself (its commands were run locally) |
| Full chat, RAG, confirmation, escalation, document upload/delete, admin flows in the browser UI (desktop + 375px mobile) | Apple-Silicon speed (tested on a CPU-only Windows PC) |
| Live: rate limiter (31st request → 429), Ollama outage (graceful message + human offer; tools keep working) and recovery without restart | Larger models (`llama3.1:8b` etc.) |
| Login for all three roles, role-based navigation, and live Settings changes in the browser: switching the LLM provider (Ollama's own OpenAI-compatible endpoint), switching databases (PostgreSQL legacy-schema adoption, new SQLite with demo data, back to PostgreSQL), reset to defaults | Browsers other than the embedded one |
| Frontend type-check and production build | |

Bugs found and fixed during real-model testing: a numbered-list answer was wrongly rejected as "unsupported numbers";
marker-less answers were attributed to chunk 1 instead of the chunk that supports them; "what is in it?" follow-ups were
not routed to the order summary; an embedding-server outage produced a generic error instead of the graceful fallback.
Each has a regression test.

Practical notes: the first request after (re)starting Ollama cold-loads both models and took ~160 s on this CPU-only machine;
use `127.0.0.1` rather than `localhost` for `OLLAMA_BASE_URL` on Windows if `localhost` is slow. One intermittent failure of
`tests/test_api.py::test_document_upload_search_and_delete` was seen twice early on and was never root-caused (an HNSW-recall
hypothesis was tested and ruled out); it did not recur in 40 consecutive full runs afterwards.

## 18. Known limitations

* Routing rules are English-only regexes; the LLM fallback is only as good as your local model.
* Grounding validation is lexical (see §7); semantic faithfulness checking (NLI / LLM-judge) is not implemented.
* Answer correctness in evaluation is phrase matching, not semantic equivalence.
* "Resolved" is a heuristic (last reply answered/completed without escalation), not a satisfaction signal.
* Auth is JWT with roles but has no refresh tokens, MFA, password-reset e-mail or token revocation list (see *Security*). Rate limiting is per process.
* The Settings page stores secrets in a local JSON file in plain text (like `.env`). Switching database doesn't copy data.
* One knowledge base for all users; no per-tenant isolation. Documents are not re-ranked by a cross-encoder.
* No streaming responses; a 3B model on CPU can take several seconds per answer.

## 19. Future improvements

Cross-encoder reranking · LLM-as-judge faithfulness in evaluation · streaming responses · refresh tokens, MFA and password reset · tenant-aware retrieval ·
hybrid BM25 + vector search · pgvector/Qdrant adapter · background re-embedding when the embedding model changes ·
OpenTelemetry export · SLA timers and customer notifications for tickets.
