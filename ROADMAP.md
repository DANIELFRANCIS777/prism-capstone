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
- [ ] **Hashed API keys at rest** — `virtual_keys.virtual_key` currently stores the raw bearer
      token ([models.py:20](backend/app/models.py#L20)). Move to SHA-256 hash + a display prefix
      (`prism-sk-…3c`); the plaintext key becomes unrecoverable after creation.
- [ ] **Org / user tenancy** — today one bootstrap admin sees every tenant's data
      ([models.py:103](backend/app/models.py#L103)). Add `Organization` → `User` (owner/admin/
      viewer) → `VirtualKey`, and scope every admin query by org.
- [ ] **Observability** — structured JSON logs with request-ID propagation, Prometheus
      `/metrics` (RED metrics per provider/model/tenant), `/health` vs `/ready` split,
      Sentry hook.
- [ ] **Real provider adapters** — OpenAI, Anthropic, Gemini behind the existing adapter
      interface, with a real pricing catalog. Provider credentials encrypted at rest, not
      plaintext in `config/gateway_config.json`.
- [ ] **CI** — GitHub Actions: lint, pytest against a Postgres service, Docker build, and the
      smoke/load/routing-eval verification pass.

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

- [ ] Fly.io deploy (staging + prod), Neon, Upstash, Cloudflare Pages, domain + TLS.
- [ ] One-command self-host: `docker compose up`, a Fly launch template, and a Helm chart.
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
