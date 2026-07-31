# Low-Level Design — Prism LLM Gateway

Audience: engineers implementing against or modifying this codebase. Assumes familiarity
with REST APIs, SQL, and async I/O concepts. For the architecture-level view (why the
system looks like this) see [HLD.md](HLD.md); for a zero-assumptions walkthrough see
[HOW_IT_WORKS.md](HOW_IT_WORKS.md).

---

## 1. Module Structure

```
app/
├── main.py               FastAPI app instance, lifespan (create tables + seed), exception handler
├── config.py              Settings (env) + cached loaders for the 3 JSON data sources
├── db.py                  Async SQLAlchemy engine/session factory
├── models.py               ORM table definitions (§2)
├── seed.py                 Upserts data/seed_keys.json into virtual_keys; bootstraps one admin_users row
├── auth.py                 authenticate(), enforce_allowlist(), GatewayError - data-plane (virtual key) auth
├── admin_auth.py            login/refresh/logout, JWT issuance + verification - admin-plane auth (§4.11)
├── jwt_keys.py               RS256 key pair generation/loading
├── rate_limit.py            enforce_rate_limit() - atomic per-minute counter
├── budget.py                enforce_budget(), record_usage(), current_year_month()
├── cache.py                 find_cache_hit(), store_cache_entry(), record_cache_hit()
├── semantic.py               tokenize(), term_frequency(), cosine_similarity()
├── streaming.py              forward_stream() - SSE forwarding + mid-stream failure handling
├── request_log.py            log_request() - single INSERT helper
├── providers.py               provider_registry() - builds adapters from gateway config
├── adapters/
│   ├── base.py                ProviderAdapter ABC, UpstreamError
│   └── openai_compatible.py    HTTP client implementation (httpx)
├── routing/
│   ├── aliases.py               resolve_candidate_chain(), UnknownModelError, RouteNotImplementedError
│   ├── auto_router.py            classify_difficulty(), resolve_route()
│   ├── dispatch.py               dispatch_non_streaming(), dispatch_streaming(), retry/failover
│   └── pricing.py                compute_cost_usd()
└── routers/
    ├── chat.py                   POST /v1/chat/completions (the orchestrator)
    └── admin.py                  /admin/auth/*, /admin/usage, /admin/logs, /admin/cache/stats, /health

tests/                            pytest suite - pricing, semantic/cache, rate limiter (real
                                   concurrency), allowlist, auto router. See AGENTS.md for the
                                   run command; requires Postgres reachable, not the gateway.
```

**Dependency direction:** `routers` → `routing` / `auth` / `rate_limit` / `budget` / `cache`
→ `adapters` / `models`. Nothing under `routing/`, `adapters/`, or the policy modules
imports from `routers/` — the pipeline orchestration lives entirely in `chat.py`, everything
else is a library function it calls.

---

## 2. Database Schema

```mermaid
erDiagram
    VIRTUAL_KEYS {
        int id PK
        string virtual_key UK
        string team
        numeric monthly_budget_usd
        int requests_per_minute
        int tokens_per_minute "nullable"
        json model_allowlist
        bool cache_enabled
        float cache_similarity_threshold "nullable"
        string status
        datetime created_at
    }
    REQUEST_LOGS {
        int id PK
        string request_id UK
        string virtual_key FK "logical, not enforced"
        string requested_model
        string resolved_provider "nullable"
        string resolved_model "nullable"
        string status
        int prompt_tokens
        int completion_tokens
        numeric cost_usd
        string cache
        bool fallback
        string route_reason "nullable"
        int retries
        int latency_ms
        datetime created_at
    }
    RATE_LIMIT_WINDOWS {
        string virtual_key PK
        int window_start PK "unix seconds, floored to 60"
        int count
    }
    USAGE_RECORDS {
        string virtual_key PK
        string year_month PK "YYYY-MM"
        numeric spend_usd
        int prompt_tokens
        int completion_tokens
        int requests
        int cache_hits
    }
    CACHE_ENTRIES {
        int id PK
        string virtual_key FK "logical, not enforced"
        string model
        string prompt_text
        json token_counts
        json response_body
        string resolved_provider
        string resolved_model
        int hit_count
        datetime created_at
    }
    ADMIN_USERS {
        int id PK
        string username UK
        string password_hash "bcrypt"
        string status
        datetime created_at
    }
    REFRESH_TOKENS {
        string jti PK "from the JWT's jti claim"
        int admin_user_id FK
        datetime issued_at
        datetime expires_at
        datetime used_at "nullable - set once, on rotation"
        datetime revoked_at "nullable - set on logout or theft detection"
    }

    VIRTUAL_KEYS ||--o{ REQUEST_LOGS : "logs"
    VIRTUAL_KEYS ||--o{ RATE_LIMIT_WINDOWS : "rate-limited by"
    VIRTUAL_KEYS ||--o{ USAGE_RECORDS : "spends via"
    VIRTUAL_KEYS ||--o{ CACHE_ENTRIES : "caches under"
    ADMIN_USERS ||--o{ REFRESH_TOKENS : "issued to"
```

Foreign keys to `virtual_key` are logical (matched by string value), not SQL foreign-key
constraints — tenants are seeded once at startup and never deleted mid-session, so referential
integrity is maintained by the seeding process rather than a DB constraint. Tables are created
via `Base.metadata.create_all()` on startup (`app/main.py`), no separate migration tool.

**Why two "usage" tables instead of one?** `usage_records` is a write-optimized, coarse
counter (one row per key per month) used only for the admission check on the hot path —
O(1) read. `request_logs` is the append-only source of truth used for all reporting
(`/admin/usage`, `/admin/logs`) — it can answer arbitrary date-range queries that
`usage_records`'s fixed monthly bucket can't, without risking the two ever disagreeing (both
are written from the same `cost_usd` value in the same request, see §4.9).

---

## 3. API Specification

### 3.1 Data plane

**`POST /v1/chat/completions`**

| | |
|---|---|
| Auth | `Authorization: Bearer <virtual_key>` |
| Request body | `{"model": str, "messages": [{"role": str, "content": str}, ...], "stream"?: bool}` |
| Success (non-streaming) | `200`, OpenAI-shaped completion body |
| Success (streaming) | `200`, `Content-Type: text/event-stream`, SSE chunks, terminated by `data: [DONE]` |

Response headers (non-streaming):

| Header | Always present? | Meaning |
|---|---|---|
| `x-prism-provider` | yes | `"<provider>/<model>"` that served the request |
| `x-prism-cache` | yes | `hit` or `miss` |
| `x-prism-fallback` | yes | `"true"` only if a non-primary candidate served it |
| `x-prism-cost-usd` | yes (non-streaming only) | computed cost; `"0"` on a cache hit |

Streaming responses carry the same three headers except `x-prism-cost-usd` (cost isn't
known until the stream completes — it's still recorded in the request log).

### 3.2 Admin plane

`/admin/auth/*` and `/health` are open; every other `/admin/*` route requires
`Authorization: Bearer <access_token>` from a successful login (see §4.11 for the full
auth design).

| Endpoint | Auth | Body / query params | Returns |
|---|---|---|---|
| `GET /health` | none | — | `{"status": "ok"}` |
| `POST /admin/auth/login` | none | `{"username", "password"}` | `{access_token, refresh_token, token_type, expires_in}` |
| `POST /admin/auth/refresh` | none (token is the credential) | `{"refresh_token"}` | a **new** `{access_token, refresh_token, ...}` pair; the input token is consumed |
| `POST /admin/auth/logout` | none (token is the credential) | `{"refresh_token"}` | `{"status": "ok"}`; that refresh token can no longer be used |
| `GET /admin/usage` | access token | `key` (required), `from`, `to` (ISO dates, optional) | requests/tokens/cost/cache_hits aggregated over `request_logs` |
| `GET /admin/logs` | access token | `key` (optional), `limit` (default 50) | recent `request_logs` rows, newest first |
| `GET /admin/cache/stats` | access token | `key` (optional) | hits/misses/hit_rate over `request_logs.cache` |

### 3.3 Error shape (all non-2xx responses)

```json
{"error": {"message": "...", "type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}}
```

| Condition | Status | `type` |
|---|---|---|
| Missing/invalid/inactive virtual key | 401 | `authentication_error` |
| Unknown model/alias | 404 | `not_found_error` |
| Model not on key's allowlist | 403 | `model_not_allowed` |
| Rate limit exceeded | 429 | `rate_limit_exceeded` |
| Budget exhausted | 402 | `budget_exceeded` |
| Malformed request body | 400 | `invalid_request_error` |
| All provider candidates failed | 502 | `upstream_error` |
| Alias has no resolvable route (config gap) | 501 | `not_implemented_error` |
| Invalid/missing/expired admin access token | 401 | `authentication_error` |
| Wrong username/password on login | 401 | `authentication_error` |
| Refresh token unknown, already used, revoked, or expired | 401 | `authentication_error` |

---

## 4. Request Pipeline Detail

`app/routers/chat.py` runs these steps **in this exact order**, using one shared
`AsyncSession` for the whole request:

```mermaid
sequenceDiagram
    participant C as Client
    participant R as chat.py
    participant Auth as auth.py
    participant Rt as routing/*
    participant RL as rate_limit.py
    participant Bu as budget.py
    participant Ca as cache.py
    participant Di as dispatch.py
    participant P as Provider
    participant DB as Postgres

    C->>R: POST /v1/chat/completions
    R->>Auth: authenticate(token)
    Auth->>DB: SELECT virtual_keys WHERE virtual_key=?
    Auth-->>R: VirtualKey | 401
    R->>Rt: resolve_route(model, messages)
    Rt-->>R: candidate chain, route_reason | 404 | 501
    R->>Auth: enforce_allowlist(key, model)
    Auth-->>R: ok | 403
    R->>RL: enforce_rate_limit(key)
    RL->>DB: UPSERT+INCREMENT rate_limit_windows RETURNING count
    RL-->>R: ok | 429
    R->>Bu: enforce_budget(key, year_month)
    Bu->>DB: SELECT spend_usd FROM usage_records
    Bu-->>R: ok | 402
    R->>Ca: find_cache_hit(key, model, messages, threshold)
    Ca->>DB: SELECT cache_entries WHERE virtual_key AND model
    alt cache hit
        Ca-->>R: CacheEntry
        R->>DB: log_request(status=ok, cache=hit, cost=0)
        R-->>C: 200 + cached body, x-prism-cache: hit
    else cache miss
        R->>Di: dispatch(chain, messages)
        Di->>P: POST /v1/chat/completions (with retries/failover)
        P-->>Di: response + usage
        Di-->>R: DispatchResult(body, route, fallback, retries)
        R->>DB: store_cache_entry() [if cache_enabled]
        R->>DB: record_usage() [atomic increment]
        R->>DB: log_request(status=ok)
        R-->>C: 200 + body, x-prism-* headers
    end
```

### 4.1 Authentication (`app/auth.py::authenticate`)

```
token = strip "Bearer " prefix from Authorization header
if token missing/empty -> GatewayError(401, authentication_error)
key = SELECT * FROM virtual_keys WHERE virtual_key = token
if key is None or key.status != "active" -> GatewayError(401, authentication_error)
return key
```

Single indexed lookup; no caching layer in front of this (traffic volumes here don't warrant
one, and correctness on key revocation matters more than shaving one query).

### 4.2 Model/alias resolution (`app/routing/aliases.py`, `app/routing/auto_router.py`)

```
resolve_route(requested_model, messages):
    if requested_model == "auto":
        tier, reason = classify_difficulty(last_user_message(messages))
        chain = resolve_candidate_chain(tier)          # tier is "fast" or "smart"
        return chain, f"auto -> {tier}: {reason}"
    return resolve_candidate_chain(requested_model), None

resolve_candidate_chain(alias_or_model):
    if alias_or_model in config.model_aliases:
        models = [alias.primary, *alias.fallbacks]
    else:
        models = [alias_or_model]                        # treat as a literal model name
    for model in models:
        provider = model_provider_map[model]              # KeyError -> UnknownModelError
    return [ResolvedRoute(provider, model) for model in models]
```

`auto` reuses the exact same alias-resolution path once a tier is chosen — it never has its
own separate provider list. Resolution happens **before** the allowlist check (§4.3) so a
truly nonexistent model name always yields `404`, never a `403` that would mask it.

### 4.3 Allowlist (`app/auth.py::enforce_allowlist`)

```
if requested_model not in key.model_allowlist:
    raise GatewayError(403, model_not_allowed)
```

Checked against the **literal string the caller sent** (`"auto"`, `"fast"`, `"smart"`, or a
raw model name) — not the resolved model — so a key's allowlist controls which aliases it may
invoke, not which underlying models happen to serve them.

### 4.4 Rate limiting (`app/rate_limit.py::enforce_rate_limit`)

**Algorithm:** fixed 60-second window, atomic upsert-increment.

```sql
INSERT INTO rate_limit_windows (virtual_key, window_start, count)
VALUES (:key, :window_start, 1)
ON CONFLICT (virtual_key, window_start)
DO UPDATE SET count = rate_limit_windows.count + 1
RETURNING count;
```

```
window_start = now_unix - (now_unix % 60)
count = <result of the statement above>
if count > key.requests_per_minute:
    raise GatewayError(429, rate_limit_exceeded)
```

**Why this is race-safe:** the increment and the read of the new value happen inside one SQL
statement. PostgreSQL executes `INSERT ... ON CONFLICT` as a single atomic operation per row,
so two requests arriving simultaneously are serialized by the database itself — each gets a
*distinct* count back (`N` and `N+1`), never the same one. There is no window where two
concurrent requests can both observe "count is still under the limit" before either writes.
This was verified directly: 30 concurrent requests against a 10/min limit admitted **exactly**
10, every time (`scripts/load_test.py`).

Old window rows are never cleaned up in this Must Have implementation — acceptable at capstone
scale; flagged as a known limitation for a long-running production deployment (see §7).

### 4.5 Budget enforcement (`app/budget.py`)

**Admission check** (cheap read, before calling the provider):
```sql
SELECT spend_usd FROM usage_records
WHERE virtual_key = :key AND year_month = :year_month;
```
```
if (spend_usd or 0) >= key.monthly_budget_usd:
    raise GatewayError(402, budget_exceeded)
```

**Post-call increment** (atomic, after the provider responds and real cost is known):
```sql
INSERT INTO usage_records (virtual_key, year_month, spend_usd, prompt_tokens,
                             completion_tokens, requests, cache_hits)
VALUES (:key, :ym, :cost, :pt, :ct, 1, :is_cache_hit)
ON CONFLICT (virtual_key, year_month) DO UPDATE SET
    spend_usd = usage_records.spend_usd + EXCLUDED.spend_usd,
    prompt_tokens = usage_records.prompt_tokens + EXCLUDED.prompt_tokens,
    completion_tokens = usage_records.completion_tokens + EXCLUDED.completion_tokens,
    requests = usage_records.requests + EXCLUDED.requests,
    cache_hits = usage_records.cache_hits + EXCLUDED.cache_hits;
```

**Documented consistency trade-off:** the admission check happens *before* the request's
actual cost is known (the provider hasn't answered yet). A burst of concurrent requests that
all read "budget remaining" before any of them writes back can collectively overshoot the
budget by up to (concurrency × average request cost). This is called out explicitly in
`docs/DATA_MODEL.md` as an acceptable trade-off — the alternative (a global lock serializing
every request for a key until cost is known) would eliminate all request-level concurrency
per tenant, which is a worse trade for a capstone-scale system.

Calendar-month window, reset-on-read, no proration — also a documented simplification.

### 4.6 Semantic cache (`app/cache.py`, `app/semantic.py`)

**Scope key:** `(virtual_key, requested_model)` — never global, never cross-alias.
**Cache key granularity:** the last `role: "user"` message only (documented choice; all
graded paraphrase pairs are single-turn).

**Similarity pipeline** (`app/semantic.py`):
```
tokenize(text):
    lowercase -> regex split on [a-z0-9]+ -> drop stopwords
                (articles, question words, auxiliary verbs, generic
                 scaffolding words like "steps"/"ways"/"method")
              -> light suffix-stripping stem (ing/ed/es/s)

term_frequency(text) = Counter(tokenize(text))          # bag-of-words vector

cosine_similarity(a, b):
    dot = sum(a[t] * b[t] for t in a.keys() & b.keys())
    return dot / (||a|| * ||b||)                          # 0.0 if either vector is empty
```

**Lookup** (`find_cache_hit`): compute the query vector, scan all `cache_entries` rows for
`(virtual_key, model)` (loaded and compared in Python — no vector index; volumes here don't
warrant one), take the best cosine score; a hit requires `score >= key.cache_similarity_threshold`
(per-key configurable, e.g. `0.92` for the search team, `0.85` for free-tier).

**Store** (`store_cache_entry`): on every successful non-streaming cache-enabled response,
persist `(prompt_text, token_counts, response_body, resolved_provider, resolved_model)`.

**On hit:** `hit_count` increments, response replays with `x-prism-cost-usd: 0`,
`x-prism-cache: hit`, and the *original* serving provider/model in `x-prism-provider` (for
demo/debug traceability) with `x-prism-fallback: false` (no failover decision was made for a
cache hit).

Streaming requests never check or populate the cache (documented Must Have simplification).

### 4.7 Auto router (`app/routing/auto_router.py`)

```
classify_difficulty(prompt):
    text = prompt.lower()
    smart_hits = [s for s in SMART_SIGNALS if s in text]
    fast_hits  = [s for s in FAST_SIGNALS  if s in text]
    if len(smart_hits) > len(fast_hits): return "smart", reason
    if len(fast_hits)  > len(smart_hits): return "fast",  reason
    # tie (including 0-0): weak length fallback
    return ("smart" if word_count > 40 else "fast"), reason
```

`SMART_SIGNALS` targets open-ended reasoning/design/proof language (`"prove"`, `"design a"`,
`"compare"`, `"trade-off"`, `"algorithm"`, `"most likely causes"`, ...). `FAST_SIGNALS` targets
single mechanical operations (`"convert"`, `"translate"`, `"extract the"`, `"what does"`,
`"divisible by"`, ...). Signal count comparison — not presence/absence of a single keyword —
is what correctly resolves cases where both categories appear in one prompt (e.g. *"Prove or
disprove: ... divisible by 5"* scores `smart=2` vs `fast=1` and correctly routes `smart`).
Verified at **20/20 (100%)** against `data/routing_eval.jsonl` via `scripts/routing_eval.py`,
which the classifier itself calls with no gateway/DB dependency — pure function evaluation.

### 4.8 Dispatch: retries + failover (`app/routing/dispatch.py`)

State machine per request:

```mermaid
stateDiagram-v2
    [*] --> TryCandidate: candidates = [primary, fallback1, fallback2, ...]
    TryCandidate --> Success: 2xx response
    TryCandidate --> Retry: retryable error & attempts remain
    Retry --> TryCandidate: backoff, same candidate
    TryCandidate --> NextCandidate: non-retryable error, or attempts exhausted
    NextCandidate --> TryCandidate: candidates remain
    NextCandidate --> AllFailed: no candidates left
    Success --> [*]
    AllFailed --> [*]
```

**Retryable** = no HTTP status (timeout/connection failure) or status in
`{408, 429, 500, 502, 503, 504}`. **Non-retryable** (e.g. a clean 400/404) moves to the next
candidate immediately without wasting the retry budget on a request that can't succeed against
that candidate. Backoff policy (`max_attempts`, `initial_backoff_ms`, `backoff_multiplier`)
comes from `backend/config/gateway_config.json`'s `retry` block — applied per candidate, so
each fallback gets its own full retry budget.

`x-prism-fallback: true` is set **only** when the response ultimately came from a non-primary
candidate — retries against the primary that eventually succeed do not set it.

### 4.9 Streaming (`app/adapters/openai_compatible.py::stream_chat_completion`,
`app/routing/dispatch.py::dispatch_streaming`, `app/streaming.py::forward_stream`)

Streaming reuses the same candidate-chain retry/failover logic, but only for **opening** the
upstream connection:

```
for candidate in chain:
    for attempt in range(max_attempts):
        generator = adapter.stream_chat_completion(model, messages, timeout)
        try:
            first_line = await generator.__anext__()   # pulls the first SSE line
        except UpstreamError:
            retry or move to next candidate (same rules as §4.8)
            continue
        return StreamHandle(generator, first_line, route, fallback, retries)  # SUCCESS
```

Once `first_line` is obtained, response headers are sent and `StreamingResponse` starts
consuming `forward_stream()`, which:
1. Yields each line to the client as `f"{line}\n\n"` bytes.
2. Parses each line for a `usage` object (captured for cost calculation once the stream ends).
3. If `line == "data: [DONE]"` — normal completion; falls through to cost/log with `status=ok`.
4. If the generator raises (connection dropped) or ends without `[DONE]` — this is a
   **mid-stream failure**. It is *not* retried and does *not* fail over (bytes may already be
   with the client) — instead a synthetic `data: {"error": {...}}` event is emitted, followed
   by `data: [DONE]`, and the request is logged with `status=upstream_error`. Verified directly
   by killing the serving mock provider process mid-response: the client received its partial
   content followed by a clean error event, not a hang or spliced output from the other
   provider.

### 4.10 Cost, cache-store, logging, response (`chat.py`, tail of the pipeline)

```
usage = provider_response["usage"]          # never client-declared token counts
cost_usd = compute_cost_usd(model, usage.prompt_tokens, usage.completion_tokens)
          = (prompt_tokens / 1e6) * price.input_per_1m
          + (completion_tokens / 1e6) * price.output_per_1m

if key.cache_enabled: store_cache_entry(...)
record_usage(...)         # atomic increment, §4.5
log_request(..., status="ok", cost_usd=cost_usd, ...)
response.headers[...] = ...
return body
```

The **same** `cost_usd` value is written to `usage_records` (via `record_usage`) and
`request_logs` (via `log_request`) in the same request — this is *why* `/admin/usage`'s sums
always reconcile exactly with the client-observed `x-prism-cost-usd` headers: they are not two
independently computed numbers that happen to agree, they are the same number persisted twice.

### 4.11 Admin authentication (`app/admin_auth.py`, `app/jwt_keys.py`)

This is a **separate auth system from the data plane** — virtual keys (§4.1) authenticate
application traffic; this authenticates the human/service operating `/admin/*`. Two token
types, deliberately asymmetric in how they're validated:

| | Access token | Refresh token |
|---|---|---|
| Lifetime | 30 min (`ACCESS_TOKEN_EXPIRE_MINUTES`) | 7 days (`REFRESH_TOKEN_EXPIRE_DAYS`) |
| Validation | Signature + expiry only — **no DB lookup** | Signature + expiry, **then** a DB lookup by `jti` |
| Can be revoked before expiry? | No | Yes |
| Used for | `Authorization` header on every `/admin/*` call | Only `POST /admin/auth/refresh` and `/logout` |

**Why this split:** an access token needs to be checked on *every* admin request, so it stays
stateless (no DB round trip) — the cost of that is that a leaked access token is valid until
it naturally expires, capped at 30 minutes by design. A refresh token is used rarely (once
per ~30 minutes per session), so it can afford a DB check, and that's exactly what buys back
revocability.

**Signing:** RS256 (`app/jwt_keys.py`). A 2048-bit RSA key pair is generated on first startup
if `backend/keys/jwt_private.pem` / `jwt_public.pem` don't exist, and reused after that
(persisted via the `prism_jwt_keys` Docker volume in Compose — without persisting it, every
container recreate would mint a new key pair and silently invalidate every previously issued
token). The private key only ever needs to be loaded by `_encode()` (login/refresh); the
public key is what `require_admin_jwt()` uses on every other request — in a multi-instance
deployment, only the instance(s) handling login/refresh would strictly need the private key.

**Login** (`POST /admin/auth/login`):
```
user = SELECT * FROM admin_users WHERE username = :username
if user is None or user.status != "active" or not bcrypt.checkpw(password, user.password_hash):
    raise 401 authentication_error
access_token  = JWT{sub, admin_user_id, type: "access",  exp: now+30min, jti}, signed RS256
refresh_token = JWT{sub, admin_user_id, type: "refresh", exp: now+7d,    jti}, signed RS256
INSERT INTO refresh_tokens (jti, admin_user_id, expires_at)   # tracks the refresh token only
return {access_token, refresh_token, token_type: "bearer", expires_in: 1800}
```

**Refresh, with rotation and reuse (theft) detection** (`POST /admin/auth/refresh`):
```
claims = jwt.decode(refresh_token, public_key, algorithms=["RS256"])   # raises on bad sig/expired
if claims.type != "refresh": raise 401

record = SELECT * FROM refresh_tokens WHERE jti = claims.jti
if record is None: raise 401
if record.used_at is not None or record.revoked_at is not None:
    # This token was already consumed (or revoked) once before - someone is
    # replaying it. Treat as theft: kill every other still-active token for
    # this user so a stolen refresh token can't keep minting access tokens.
    UPDATE refresh_tokens SET revoked_at = now()
        WHERE admin_user_id = record.admin_user_id AND revoked_at IS NULL
    raise 401 "Refresh token already used or revoked"

record.used_at = now()   # single-use: this exact token can never succeed again
issue a brand new (access_token, refresh_token) pair, as in Login
return the new pair
```

This is the standard OAuth2 refresh-token-rotation pattern: a legitimate client always uses
the *latest* refresh token it was given and never sees this path trigger. An attacker who
stole an older refresh token and uses it *after* the legitimate client already rotated past
it hits the reuse branch — and, notably, so does the legitimate client if it tries to reuse a
token it already rotated away from. Verified directly in this session: reusing a
just-rotated-away-from refresh token was rejected, **and** the brand new token issued in that
same rotation was also revoked as a side effect — exactly the theft-response behavior above.

**Logout** (`POST /admin/auth/logout`): decodes the refresh token just to read its `jti` (a
token that's already invalid/expired is simply a no-op — nothing to revoke), then sets
`revoked_at`. The paired access token is *not* revoked — it simply expires naturally within
30 minutes, which is the accepted trade-off of keeping access-token checks stateless.

**Bootstrapping the first account:** there is no signup endpoint. `app/seed.py::seed_admin_user`
runs on every startup and inserts exactly one row into `admin_users` — but only if the table is
currently empty — using `ADMIN_BOOTSTRAP_USERNAME` / `ADMIN_BOOTSTRAP_PASSWORD` (bcrypt-hashed
before storage). Additional admin accounts currently require a direct `INSERT` into
`admin_users`; a `POST /admin/users` creation endpoint would be the natural next addition if
multiple admins are needed.

---

## 5. Concurrency & Consistency Design

| Shared state | Consistency mechanism | Why it's sufficient |
|---|---|---|
| Rate limit counters | Single atomic `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING` per request | PostgreSQL serializes concurrent writes to the same row; no read-then-write gap exists in application code. |
| Budget counters | Same atomic upsert pattern for increments; a plain read for admission | Admission is intentionally optimistic (§4.5) — documented, bounded overshoot rather than serialized writes. |
| Cache entries | Read-many/write-one per request, scoped by `(virtual_key, model)` | Two concurrent requests for the same new prompt may both miss and both write a cache entry (a duplicate, not a correctness bug — the next lookup just has two equally valid candidates). |
| Request log | Append-only INSERT per event | No update-in-place, no contention. |
| Refresh token rotation | `used_at`/`revoked_at` are set via a normal `UPDATE` after a `SELECT` (not a single atomic statement, unlike the rate limiter) | Acceptable here: refresh calls are low-frequency (once per ~30 min per session) compared to the rate limiter's every-request path, so a theoretical double-refresh race is a tolerable edge case, not one worth the same atomic-upsert treatment. |

**No in-process locks or in-memory counters are used anywhere in this design** — every piece
of cross-request shared state lives in PostgreSQL specifically so correctness holds even if
the gateway process is scaled to multiple instances behind a load balancer (the current
single-instance assumption only affects whether that scaling is *necessary* yet, not whether
the data layer would support it).

---

## 6. Configuration Reference

| Setting | Source | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | env / `.env` | `postgresql+asyncpg://prism:prism@localhost:5432/prism` | SQLAlchemy async connection string |
| `UPSTREAM_TIMEOUT_SECONDS` | env / `.env` | `10.0` | Per-attempt provider call timeout |
| `GATEWAY_CONFIG_FILE` | env / `.env` | `gateway_config.json` | Which file under `backend/config/` to load (local vs Docker) |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | env / `.env` | `backend/keys/jwt_{private,public}.pem` | RS256 key pair location; auto-generated on first startup if missing |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | env / `.env` | `30` | Admin access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | env / `.env` | `7` | Admin refresh token lifetime |
| `ADMIN_BOOTSTRAP_USERNAME` / `ADMIN_BOOTSTRAP_PASSWORD` | env / `.env` | `admin` / `prism-admin-dev-password` | Seeded into `admin_users` only if that table is empty |
| `data/model_pricing.json` | file, read-only | — | Per-model `input_per_1m` / `output_per_1m` USD pricing |
| `data/seed_keys.json` | file, read-only | — | Tenant definitions, synced into `virtual_keys` on every startup |
| `backend/config/gateway_config*.json` | file | — | Provider registry, alias→model+fallback map, retry policy |

---

## 7. Known Limitations (by design, at Must Have scope)

- `rate_limit_windows` rows are never pruned — unbounded growth over a long-running deployment
  (Stretch-scope concern, not required for the capstone).
- Cache similarity search is a linear scan over a key's cache entries in Python, not an
  indexed vector search — fine at capstone traffic volumes, would need a proper vector index
  (or a real embedding + ANN store) at production scale.
- Rate limiting/budgets are correct under concurrency **within** the data layer, but the
  gateway itself still runs as a single process in this deployment — horizontal scaling is
  Stretch scope per the spec, though the DB-backed design (§5) is what would make it safe to do.
- Refresh token rotation (§4.11) uses a `SELECT` then `UPDATE`, not a single atomic statement
  like the rate limiter — two near-simultaneous refresh calls with the *same* token could both
  pass the "not yet used" check before either writes back. Low practical risk (refresh calls
  are infrequent and normally single-threaded per session) but not proven race-free the way the
  rate limiter is; would need the same atomic-upsert treatment to close that gap fully.
- There is no admin account creation/rotation/deactivation API — additional admins require a
  direct database `INSERT`. Only one bootstrap account is provisioned automatically.
