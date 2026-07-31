# Prism — AI-First LLM Gateway

A production-grade LLM gateway: one OpenAI-compatible API in front of multiple providers,
with the gateway itself making two AI-adjacent decisions in the request path — how hard is
this prompt (route to a cheap or expensive model tier) and have we answered it before (serve
from a semantic cache instead of paying for it again). Built for the Airtribe AI-First
Software Engineering Capstone; full spec in [PRISM_PROBLEM_STATEMENT.md](PRISM_PROBLEM_STATEMENT.md).

**Stack:** Python + FastAPI + PostgreSQL (gateway), React + Vite (ops console), Docker Compose
(everything, including two mock OpenAI-compatible providers for demo/testing).

---

## Quick start

```bash
docker compose up -d --build
```

Brings up Postgres, two mock providers, the gateway, and the ops console — fully networked,
no local Python/Node install required.

| Service | URL |
|---|---|
| Ops console | http://localhost:5173 |
| Gateway API | http://localhost:8080 |
| Mock providers | http://localhost:9001, http://localhost:9002 |

Log into the console with the bootstrap admin account: **`admin` / `prism-admin-dev-password`**
(change this outside of local dev — see [`backend/.env.example`](backend/.env.example)).

For local dev with hot reload instead of Docker for the app layers, and the full command
reference, see [AGENTS.md](AGENTS.md).

## Demo flow

Seeded tenants (`data/seed_keys.json`), used throughout the console and the scripts below:

| Key | Team | Allowed models | Rate limit | Budget | Cache |
|---|---|---|---|---|---|
| `prism-sk-search-1a2b3c` | search | `fast` | 60/min | $50/mo | on, threshold 0.92 |
| `prism-sk-research-4d5e6f` | research | `fast`, `smart`, `auto` | 300/min | $500/mo | off |
| `prism-sk-free-7g8h9i` | free-tier | `fast` | 10/min | $5/mo | on, threshold 0.85 |
| `prism-sk-budget-demo-0j1k2l` | budget-demo | `fast` | 60/min | $0.00001/mo | off |

```bash
# non-streaming + header contract
curl -s -i http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "What is a message queue?"}]}'

# streaming, token by token
curl -N http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "stream": true, "messages": [{"role": "user", "content": "Explain SSE briefly."}]}'

# auto routing: short-but-hard -> smart, long-but-trivial -> fast
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-research-4d5e6f" -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Prove that the square root of 2 is irrational."}]}'

# live failover: take alpha down, fast request still succeeds via beta
curl -s -X POST http://localhost:9001/admin/config -d '{"mode": "down"}'
curl -s -i http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "failover test"}]}'
curl -s -X POST http://localhost:9001/admin/config -d '{"mode": "ok"}'

# budget exhaustion: first call consumes the $0.00001 budget, second is rejected
curl -s http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-budget-demo-0j1k2l" -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"first"}]}'
curl -s http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-budget-demo-0j1k2l" -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"second"}]}'
```

A ready-to-import Postman collection covering every endpoint, including the error cases above,
is at [`backend/postman_collection.json`](backend/postman_collection.json).

## Architecture & design decisions

Three levels of documentation, depending on what you need:

- **[backend/HLD.md](backend/HLD.md)** — architecture: system context, component diagram,
  tech-stack rationale, non-functional requirements, deployment topology, and the key
  trade-offs (why bag-of-words caching, why a keyword-based router, why PostgreSQL-backed
  rate limiting).
- **[backend/LLD.md](backend/LLD.md)** — implementation detail: full ER diagram, API spec,
  a step-by-step sequence diagram of the request pipeline, and per-subsystem deep dives with
  the actual SQL/pseudocode (rate limiter, budget accounting, retry/failover state machine,
  cache similarity math, `auto` router scoring, admin JWT auth).
- **[backend/HOW_IT_WORKS.md](backend/HOW_IT_WORKS.md)** — a zero-assumptions walkthrough,
  useful if you don't already know FastAPI/Python.

Two decisions the capstone spec specifically calls out:

- **Routing & failover** — each alias (`fast`/`smart`) resolves to a primary model plus an
  ordered fallback chain. Transient failures (timeouts, 429/5xx) retry with exponential
  backoff against the *same* candidate first; only after that candidate is exhausted does the
  gateway move to the next one. `x-prism-fallback: true` is set only when a non-primary
  candidate actually served the response. For streaming, retries/failover only happen before
  the first byte reaches the client — a failure after streaming has started terminates with a
  clean SSE error event instead of retrying or silently splicing in another provider's output.
  Full detail: [LLD §4.8–4.9](backend/LLD.md).
- **Cache scoping** — every cache entry and every cache lookup is filtered by `virtual_key`
  (plus the requested alias). There is no code path that queries across tenants. Verified
  directly: the same exact prompt sent under two different keys produced a cache miss on the
  second key even though the first had already cached it. Full detail:
  [LLD §4.6](backend/LLD.md).

## API

Full spec (all endpoints, headers, error codes): [backend/LLD.md §3](backend/LLD.md#3-api-specification).

| | |
|---|---|
| Data plane | `POST /v1/chat/completions` — OpenAI-compatible, streaming or not |
| Admin auth | `POST /admin/auth/{login,refresh,logout}` — JWT access (30 min) + rotating/revocable refresh (7 day) tokens |
| Admin (JWT-gated) | `GET /admin/usage`, `GET /admin/logs`, `GET /admin/cache/stats` |
| Health | `GET /health` |

## Verification

See [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) for full output. Headline results:

- Smoke test: 17 passed, 0 failed, 1 expected warning (documented).
- Routing eval: **20/20 (100%)** against `data/routing_eval.jsonl`, including every trap case.
- Load test: exactly 10 of 30 concurrent requests admitted against a 10/min limit — zero
  over-admission.
- Accounting reconciliation: load test's client-side cost/token totals matched
  `/admin/usage`'s totals **exactly**.
- Automated tests: **29/29 passed** (`cd backend && pytest -v`) — cost computation, the rate
  limiter under real concurrency, cache decision + tenant isolation, allowlist, and the `auto`
  router against the full eval set.

## Repository layout

```
PRISM_PROBLEM_STATEMENT.md, docs/, data/, scripts/   the original capstone pack (unmodified)
backend/                                              FastAPI gateway - see HOW_IT_WORKS/HLD/LLD.md
frontend/                                             React + Vite ops console
docker-compose.yml, backend/Dockerfile,
  scripts/Dockerfile, frontend/Dockerfile              one-command containerized stack
AGENTS.md, CLAUDE.md                                  agent/AI-assistant instructions for this repo
VERIFICATION_REPORT.md                                smoke/load/routing-eval results
```

## Known limitations

See [backend/LLD.md §7](backend/LLD.md#7-known-limitations-by-design-at-must-have-scope) for
the full, honest list (unpruned rate-limit rows, linear-scan cache search, refresh-token
rotation not fully atomic, no admin account management API beyond the bootstrap account) —
all documented trade-offs at Must Have scope, not oversights.
