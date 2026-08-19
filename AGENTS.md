# Prism — LLM Gateway (AI-First Capstone)

An AI-first LLM gateway: OpenAI-compatible chat completions across multiple providers, with
difficulty-based routing (`auto`), failover, streaming, per-tenant metering/budgets, and a
semantic response cache. Full spec: `PRISM_PROBLEM_STATEMENT.md` (source of truth for scope).

**Stack:** Python (FastAPI) backend, React frontend (ops console).

## Repo layout

- `PRISM_PROBLEM_STATEMENT.md` — capstone spec: Must Have / Good To Have / Stretch, grading weights.
- `docs/IMPLEMENTATION_GUIDE.md` — suggested build order, demo scenarios, FAQ.
- `docs/API_CONTRACT.md` — API shape, header contract, error shapes.
- `docs/DATA_MODEL.md` — suggested entities/relationships.
- `docs/EVALUATION_GUIDE.md` — how the gateway gets verified.
- `data/` — provided seed data: `model_pricing.json`, `seed_keys.json`,
  `gateway_config.sample.json`, `sample_requests.jsonl`, `routing_eval.jsonl`.
- `scripts/` — `mock_provider.py`, `smoke_test.py`, `load_test.py`, `validate_pack.py`.
  These define the grading contract — **do not edit them.**
- `backend/` — FastAPI gateway. Must Have complete (core pass-through, keys/limits/budgets,
  streaming/retries/failover, semantic cache, `auto` router), plus a JWT-based admin login
  (access + rotating/revocable refresh tokens). See `backend/HOW_IT_WORKS.md` for a
  zero-assumptions walkthrough, `backend/HLD.md` for architecture, `backend/LLD.md` for
  implementation-level detail (schemas, algorithms, sequence diagrams).
- `frontend/` — React + Vite ops console (login, usage by key, recent requests, cache hit rate).
- `docker-compose.yml` / `backend/Dockerfile` / `scripts/Dockerfile` — runs the whole backend
  (Postgres + both mock providers + the gateway) in containers; see below.

## Commands

### Option A — everything in Docker (fastest way to just run it)

```bash
docker compose up -d --build
```

Brings up Postgres, both mock providers, and the gateway on `http://localhost:8080`, fully
networked together — no local Python setup needed. Data plane, streaming, cache, and admin
routes all work identically to local dev (verified against `scripts/smoke_test.py`). Provider
URLs for this mode live in `backend/config/gateway_config.docker.json` (service names, not
`localhost`) - selected via the `GATEWAY_CONFIG_FILE` env var set in `docker-compose.yml`.
`docker compose down` to stop; add `-v` to also drop the Postgres volume.

### Option B — local dev (hot reload, debugger access)

Postgres via Docker, everything else on the host:

```bash
docker compose up -d postgres
python3 scripts/mock_provider.py --port 9001 --name alpha &
python3 scripts/mock_provider.py --port 9002 --name beta &
cd backend
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # first time only
uvicorn app.main:app --reload --port 8080
```

Frontend:

```bash
cd frontend && npm install && cp .env.example .env && npm run dev
```

Requires the gateway running and reachable at `VITE_API_BASE_URL` (`frontend/.env`, default
`http://localhost:8080`) — and the gateway's `CORS_ALLOW_ORIGINS` must include the console's
origin (default already covers `http://localhost:5173`).

### Admin login

`/admin/*` (except `/admin/auth/*` and `/health`) requires a JWT access token from a real
login, not a static token:

```bash
curl -X POST http://localhost:8080/admin/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "prism-admin-dev-password"}'
```

Bootstrap credentials come from `ADMIN_BOOTSTRAP_USERNAME` / `ADMIN_BOOTSTRAP_PASSWORD`
(`.env` locally, `docker-compose.yml` for the container). See `backend/HOW_IT_WORKS.md` §6.9
or `backend/LLD.md` §4.11 for the full login/refresh/logout design.

### Verification (either option, or use the `prism-verify` skill)

```bash
python3 scripts/validate_pack.py
python3 scripts/smoke_test.py --url http://localhost:8080 --key prism-sk-search-1a2b3c --model fast
python3 scripts/load_test.py  --url http://localhost:8080 --key prism-sk-free-7g8h9i --model fast --requests 30 --concurrency 10 --rpm-limit 10
cd backend && python3 scripts/routing_eval.py   # auto-router accuracy against data/routing_eval.jsonl
```

### Database migrations (Alembic)

The schema is owned by Alembic revisions in `backend/migrations/versions/`, not by
`Base.metadata.create_all`. The gateway applies pending revisions on startup by default
(`RUN_MIGRATIONS_ON_STARTUP`), holding a Postgres advisory lock so concurrent replicas can't
race. A database created by the old `create_all` path is detected and stamped rather than
rebuilt, so upgrading in place is safe.

```bash
cd backend
alembic revision --autogenerate -m "what changed"   # after editing app/models.py
alembic upgrade head                                 # apply
alembic downgrade -1                                 # roll back one revision
alembic check                                        # models vs migrations drift
```

**Any change to `app/models.py` needs a revision in the same commit** —
`tests/test_migrations.py` fails the build otherwise.

### Automated tests (pytest)

```bash
cd backend
pip install -r requirements-dev.txt
pytest -v
```

Needs Postgres reachable at `DATABASE_URL` (`docker compose up -d postgres` is enough - the
gateway itself doesn't need to be running). Covers cost computation, the rate limiter under
real concurrency (20 concurrent tasks against a limit of 5 - proves no read-then-write gap,
not just sequential correctness), the semantic cache's similarity decision and its per-key/
per-alias isolation, allowlist enforcement, and the `auto` router (including a regression
check against the full `data/routing_eval.jsonl`).

## Non-negotiable contract

The provided scripts depend on these — changing them breaks verification:

- Data plane stays `POST /v1/chat/completions`, OpenAI-compatible.
- Every data-plane response carries: `x-prism-provider`, `x-prism-cache` (`hit`/`miss`),
  `x-prism-fallback` (`true` only on fallback), `x-prism-cost-usd` (non-streaming only).
- Seed key values (`data/seed_keys.json`) and alias names (`fast`, `smart`, `auto` from
  `data/gateway_config.sample.json`) are preserved as-is.
- Streaming is true pass-through — never buffer the full upstream response before forwarding.
- Cost is computed from provider-reported `usage`, never client-declared token counts.
- The semantic cache is scoped per virtual key by default — never shared across tenants.
- Rate limits and budgets are race-safe under concurrency — no read-then-write.
- Every upstream call has a timeout; a hung provider must never hang the client.

## Conventions

- Keep provider integration behind an adapter so it can be pointed at the mock providers (or an
  in-process fake) in tests.
- Log every request, including rejections: key, requested/resolved model, provider, status,
  tokens, cost, cache result, fallback flag, latency.
- Log every `auto` routing decision with its reason (score, matched signal, or judge output).
- Automated tests are required for: cost computation, the rate limiter, the cache decision, and
  the allowlist/routing logic.

## Where to look for detail

- Full requirements, simplifications, and grading weights: `PRISM_PROBLEM_STATEMENT.md`.
- Build order, demo scenarios, FAQ: `docs/IMPLEMENTATION_GUIDE.md`.
- API/header/error detail: `docs/API_CONTRACT.md`.
- Suggested entities and storage: `docs/DATA_MODEL.md`.
- What gets verified and how: `docs/EVALUATION_GUIDE.md`.
