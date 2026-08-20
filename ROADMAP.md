# Prism — Post-Capstone Product Roadmap

The capstone is submitted. This document tracks turning it into a production-grade,
self-hostable open-source LLM gateway, and getting it in front of real users.

**Positioning:** the LLM-gateway category is crowded (LiteLLM, Portkey, OpenRouter, Helicone,
Cloudflare AI Gateway). Prism's wedge is not "unified API" — everyone has that. It is
**measured cost reduction**: a semantic cache and a difficulty router that are actually good,
with a dashboard that shows the dollars they saved. Every feature below is justified against
that wedge or against the reliability floor a gateway needs to be trusted in a request path.

**Decisions (2026-08-11):**

| | |
|---|---|
| Distribution | Open-source core first, self-hostable. Hosted cloud tier later. |
| Providers | Real OpenAI / Anthropic / Gemini adapters, cheap tiers by default. Mocks stay as the CI path. |
| Hosting | Fly.io (gateway) · Neon Postgres + pgvector · Upstash Redis · Cloudflare Pages (console) |

---

## Phase 1 — Production hardening *(in progress)*

Nothing built on top survives without this. No new user-facing features in this phase.

- [ ] **Alembic migrations** — replace `Base.metadata.create_all` at startup
      ([main.py:19](backend/app/main.py#L19)). Required before any schema change ships to a
      database with real data in it.
- [x] **Hashed API keys at rest** — SHA-256 `key_hash` + display `key_prefix`; the four dependent
      tables now reference `virtual_keys.id` instead of copying the secret.
- [x] **Org / user tenancy** — `Organization` → `User` → `VirtualKey`, self-serve signup/login on
      a separate JWT scope from the platform operator, every `/me/*` query scoped by org.
- [x] **BYOK provider credentials** — Fernet-encrypted per org, consulted per candidate during
      dispatch, falling back to the platform's static config when absent.
- [x] **Auth hardening** — per-IP/per-email throttling and consecutive-failure lockout on
      `/auth/login`, `/auth/signup`, `/admin/auth/login`; production refuses to boot with the
      default admin password.
- [x] **Structured logging** — JSON logs with `request_id` correlation to the `request_logs` row,
      covering rejections, dispatch retries, and failovers. Catch-all exception handler.
- [x] **`/health` vs `/ready` split** — `/ready` verifies Postgres connectivity for load-balancer
      routing decisions.
- [x] **Table pruning** — in-process maintenance loop (advisory-lock serialized across replicas)
      pruning stale rate-limit windows and expired refresh tokens. Same mechanism will host the
      provider catalog sync.
- [x] **Model discovery** — `GET /v1/models`, OpenAI-compatible, served from the registered
      catalog and scoped to the calling key's allowlist.
- [ ] **Prometheus `/metrics`** — RED metrics per provider/model/tenant, cache hit rate,
      rejection counters. Sentry hook (optional `SENTRY_DSN`, no-op when unset).
- [ ] **TTFT (time to first token)** — providers report it; Prism records total latency only.
      `app/streaming.py` already knows when the first line arrives, so this is a column plus one
      assignment. Worth having before the savings dashboard, since "cached response in 3ms vs
      320ms to first token upstream" is a sharper claim than total latency alone.
- [ ] **Real provider adapters** — OpenAI and Anthropic alongside the current Groq/Gemini
      registration.
- [x] **CI** — GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)): pytest
      against a Postgres service + `alembic check`, frontend lint/build, a full end-to-end
      verification pass against the Compose stack, production image builds (asserting the backend
      doesn't run as root), and an advisory dependency audit.
- [x] **Production container hardening** — backend runs as an unprivileged user and honours
      `$PORT`; `frontend/Dockerfile.prod` builds the static bundle and serves it via nginx with
      SPA fallback, hashed-asset caching, and security headers.
- [x] **Deployable key material** — JWT and credential-encryption keys load from env secrets, so
      they survive redeploys on ephemeral filesystems. Production refuses to boot without them,
      since silently regenerating the credential key destroys every stored BYOK credential.
- [ ] **Account lifecycle** — change-own-password (cheap, no email infra), then password reset
      (needs an email dependency + a single-use token table). Today a user who forgets their
      password has no recovery path.

### Provider catalog sync *(after first deploy)*

`GET /v1/models` reads a catalog that's hand-maintained in `config/gateway_config.json`. Groq
deprecated `llama-3.3-70b-versatile` mid-development and requests started 404ing with no warning —
a scheduled job polling each provider's own `/models` endpoint and reconciling against the
registered catalog would catch that. Belongs in `app/background.py` alongside the pruning loop.

## Phase 2 — Sharpen the wedge

The differentiator. Phase 1 makes it safe to build; this makes it worth buying.

- [ ] **Real semantic cache** — embeddings + pgvector with an ANN index, replacing the
      bag-of-words full-table scan ([cache.py:39-50](backend/app/cache.py#L39-L50)). Plus TTL,
      eviction, and per-tenant cache size caps.
- [ ] **Trained difficulty router** — replace the hardcoded keyword lists
      ([auto_router.py:11-29](backend/app/routing/auto_router.py#L11-L29)) with an embedding
      classifier, an expanded labelled eval set, and offline accuracy/cost regression tracking.
- [ ] **Savings dashboard** — "you spent $X, you would have spent $Y" broken down by cache hits
      and downtier routing. This is the screenshot that sells the product.
- [ ] **Redis rate limiting** — move off the per-request Postgres row write
      ([rate_limit.py:17-27](backend/app/rate_limit.py#L17-L27)), add the unenforced
      `tokens_per_minute` limit, and prune old windows.

## Phase 3 — Product surface

- [ ] Console rebuild: cost analytics, request-log explorer with search/filter, API-key CRUD,
      provider health, budget alerts.
- [ ] Runtime configuration — providers, aliases, and routing rules editable via API/UI instead
      of a JSON file read once at boot.
- [ ] Drop-in guides + SDK snippets (point any OpenAI SDK at Prism by changing `base_url`).
- [ ] Docs site.

## Phase 4 — Deploy & launch

- [x] Deployment path documented and scripted: [DEPLOYMENT.md](DEPLOYMENT.md) +
      [render.yaml](render.yaml). **Revised off Fly.io** — Fly removed its free allowance for new
      accounts in Oct 2024 (trial only, then pay-as-you-go). Free stack is now Render (gateway +
      console, 750 instance-hours/month) and Neon (Postgres, permanent free tier). Nothing is
      host-specific: a standard Docker image, env vars, and `/ready`, so Fly/Cloud Run/a VPS are
      the same artifacts.
- [ ] Actually deploy it and put the URL somewhere.
- [ ] Custom domain + TLS.
- [ ] One-command self-host is already `docker compose up`; add a Helm chart if anyone asks.
- [ ] Landing page built on the savings numbers, not on a feature list.
- [ ] Launch: GitHub, Hacker News / Product Hunt / r/LocalLLaMA, a teardown post comparing real
      measured cache-hit savings.

## Phase 5 — Hosted tier *(only with traction)*

- [ ] Signup, org onboarding, Stripe metered billing.
- [ ] SSO, audit logs, SOC2-track controls.

---

## Non-negotiables carried forward from the capstone

`scripts/*.py` and `data/*` are the regression harness now. Every change above must leave these
intact:

- `POST /v1/chat/completions` stays OpenAI-compatible.
- `x-prism-provider`, `x-prism-cache`, `x-prism-fallback`, `x-prism-cost-usd` headers unchanged.
- Seeded keys in `data/seed_keys.json` and the `fast` / `smart` / `auto` aliases keep working.
- Streaming stays true pass-through; cost always computed from provider-reported usage.
- Cache scoped per tenant; limits and budgets race-safe.

Run the pass with the `prism-verify` skill before marking any phase done.
