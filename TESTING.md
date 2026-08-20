# Prism — Test Procedure

Manual + automated test procedure for the post-capstone build: self-serve org accounts, BYOK
provider credentials, and everything from the original capstone (routing, failover, caching,
rate limits, budgets, streaming, admin console). Written for a tester with no prior context on
the codebase.

## 0. Prerequisites

- Docker Desktop running.
- A free API key from at least one real provider, to exercise BYOK for real (optional but
  recommended — Section 5 covers this):
  - Groq: https://console.groq.com/keys
  - Gemini: https://aistudio.google.com/apikey

## 1. Start the stack

```bash
cd prism-capstone
docker compose up -d --build
```

Wait ~10s, then confirm everything is healthy:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
curl -s http://localhost:8080/health        # {"status":"ok"}          - liveness only
curl -s http://localhost:8080/ready         # {"status":"ready",...}   - also checks Postgres
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173   # 200
```

`/health` answers "is the process up" and deliberately checks nothing else — a dependency outage
shouldn't get the container killed and restarted. `/ready` answers "can this replica actually
serve a request" by pinging Postgres, and is what a load balancer should watch.

| Service | URL |
|---|---|
| Tenant console (self-serve) | http://localhost:5173 |
| Gateway API | http://localhost:8080 |
| Mock providers | http://localhost:9001 (alpha), http://localhost:9002 (beta) |

To stop: `docker compose down` (add `-v` to also wipe the database and start from empty).

## 2. Automated regression suite

Run this first — if it doesn't pass, nothing below will either. Takes under a minute.

```bash
# Backend unit/integration tests (needs Postgres reachable; docker compose already provides it)
cd backend
pip install -r requirements-dev.txt   # first time only
pytest -v
```
Expect: **all tests pass** (currently 59). A handful of `DeprecationWarning`s from
`pytest-asyncio` are expected and harmless.

```bash
cd ..   # repo root
python3 scripts/validate_pack.py
python3 scripts/smoke_test.py --url http://localhost:8080 --key prism-sk-search-1a2b3c --model fast
python3 scripts/load_test.py  --url http://localhost:8080 --key prism-sk-free-7g8h9i --model fast \
  --requests 30 --concurrency 10 --rpm-limit 10
cd backend && python3 scripts/routing_eval.py && cd ..
```
Expect:
- `validate_pack.py`: `Pack OK`
- `smoke_test.py`: **17 passed, 1 warning, 0 failed** (the 1 warning is a documented, expected
  soft-match case in the semantic-cache paraphrase test — not a failure)
- `load_test.py`: exactly **10 accepted / 20 rate-limited** out of 30, `Over-admission check OK`
- `routing_eval.py`: **20/20 (100%)**

## 3. Self-serve signup / login

All in the browser at http://localhost:5173.

| # | Step | Expected |
|---|---|---|
| 3.1 | Open the app | "Sign up" / "Log in" toggle, Sign up selected by default |
| 3.2 | Fill email + password (8+ chars), leave org name blank, submit | Lands on the tenant dashboard; header shows your email and an org name |
| 3.3 | Click **Log out** (top right) | Returns to the signup/login screen |
| 3.4 | Click the **Log in** tab specifically, enter the same email/password, submit | Lands back on the dashboard. **Note:** the form resets to the "Sign up" tab after logout — if login ever seems to fail, check you're actually on the Log in tab, not silently retrying signup with the same email (which correctly fails with "already exists") |
| 3.5 | Try signing up again with the same email (fresh browser / incognito) | `409` — "An account with this email already exists" |
| 3.6 | Enter a **wrong** password 5 times in a row, then the correct one | The 6th attempt returns `429`, and the correct password is *also* refused: "Too many failed attempts. Try again in 15 minutes." The lockout is per account, so a different account still logs in fine |
| 3.7 | Fail 3 times, then log in correctly, then fail 4 more times | Still logs in — a successful login clears the failure streak, so occasional typos never accumulate toward a lockout |

## 4. API keys and ceiling enforcement

On the tenant dashboard, **API keys** panel.

| # | Step | Expected |
|---|---|---|
| 4.1 | Create a key with Requests/min = 30, Budget = $20 | Key created; raw value shown once in a highlighted box (`prism-sk-…`) — copy it now, it won't be shown again |
| 4.2 | Try to create a **second** key | Rejected: "This account already has the maximum number of keys (1)" |
| 4.3 | Disable the key (**Disable** button) | Status flips to `disabled` |
| 4.4 | Try creating a key with Requests/min = 9999 | Rejected: "requests_per_minute may not exceed 60" (or whatever the operator has configured) |
| 4.5 | Try creating a key with Budget = $9999 | Rejected: "monthly_budget_usd may not exceed 50" |

Equivalent via curl (useful for scripting/CI):
```bash
TOKEN=<access_token from signup/login response>
curl -s -X POST http://localhost:8080/me/keys -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"requests_per_minute": 30, "monthly_budget_usd": 20}'
```

## 4b. Model discovery (`GET /v1/models`)

The standard OpenAI-compatible discovery endpoint, so an OpenAI SDK pointed at Prism can list
models the usual way. Served from Prism's own registered catalog (not a live call out to Groq or
Gemini on every request), and **scoped to the calling key's allowlist** — which is also what makes
a separate "is this model valid for this provider" check unnecessary: a key that can't reach a
provider never sees its models here.

```bash
curl -s http://localhost:8080/v1/models -H "Authorization: Bearer prism-sk-search-1a2b3c"
```

| # | Step | Expected |
|---|---|---|
| 4b.1 | Call with no `Authorization` header | `401` |
| 4b.2 | Call with the seeded `search` key (allowlist is `["fast"]`) | Exactly one entry: `fast`, with `"owned_by": "prism"` (aliases belong to Prism, not one provider — `fast` can fail over between providers) |
| 4b.3 | Call with the seeded `research` key | `auto`, `fast`, `smart` |
| 4b.4 | Call with your self-serve key | The full catalog, including `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `gemini-2.5-flash-lite`, `gemini-2.5-flash` — each with its real provider in `owned_by` |

## 5. BYOK — real provider credentials

This is the actual product differentiator: point your own traffic through your own provider
account and watch Prism's cache/routing reduce what you'd otherwise pay *that provider*.

| # | Step | Expected |
|---|---|---|
| 5.1 | Without adding any credential, call `model: "groq"` (or `"gemini"`) with your key from §4 | `502` with an `upstream_error` whose message is a **real 401 from Groq/Gemini's own API** (not a mock) — proves the plumbing reaches the real provider even before you've added a key |
| 5.2 | In the **Provider credentials (BYOK)** panel, pick `groq` (or `gemini`), paste your real key, save | Appears in the list with a masked prefix (e.g. `gsk_H3B6...`) — the plaintext is never shown again or returned by any endpoint |
| 5.3 | Call `model: "groq"` again with the same gateway key | `200 OK`. Response headers: `x-prism-provider: groq/llama-3.3-70b-versatile`, `x-prism-cache: miss`, a real non-zero `x-prism-cost-usd` |
| 5.4 | Repeat the identical (or a close paraphrase of the) request | `x-prism-cache: hit`, `x-prism-cost-usd: 0` — self-serve keys have caching on with a 0.85 similarity threshold by default. This is the savings the product exists to prove |
| 5.5 | Remove the credential (**Remove** button), call `model: "groq"` again | Back to a clean 401 from Groq, same as 5.1 — confirms removal actually takes effect, not just hides it in the UI |

curl version of 5.3:
```bash
RAW_KEY=<the prism-sk-... key from step 4.1>
curl -s -i http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $RAW_KEY" -H "Content-Type: application/json" \
  -d '{"model": "groq", "messages": [{"role": "user", "content": "hello"}]}' \
  | grep -E "^HTTP|^x-prism"
```

## 5b. Reconciling Prism's numbers against the provider's own console

Worth doing once with real traffic — and worth knowing which differences are *expected*, because
three of them look like bugs and aren't.

**Should match exactly:**

| Field | Why |
|---|---|
| Prompt / completion tokens | Taken verbatim from the provider's reported `usage`, never counted client-side |
| Cost | Computed from those tokens × `data/model_pricing.json`. If this drifts, the price entry is stale — check the provider's pricing page |

A real reconciliation against Groq's console: three calls reporting `(82,62)`, `(77,2136)`,
`(76,2140)` summed to 235 prompt / 4338 completion tokens and $0.002638 in Prism — matching Groq
to the token and the microdollar.

**Expected to differ:**

- **Prism shows more requests than the provider.** Cache hits never reach the provider — that's
  the entire point. 7 requests in Prism against 5 at Groq means 2 were served from cache. Compare
  Prism's *misses* to the provider's request count, not its total.
- **Prism's timestamps run slightly later.** The provider timestamps when a request *arrives*;
  Prism writes its log row after the response completes and its own accounting finishes. The gap
  equals that request's latency plus a fraction of a second — a 4.8s call logged at 10:20:11
  upstream appears at 10:20:16 here.
- **The provider may show requests Prism never made.** Anything sent to the provider directly,
  bypassing the gateway, appears in their console only.

**Not tracked yet:** TTFT (time to first token). The provider's console reports it; Prism records
total latency only. Meaningful mainly for streaming — noted in `ROADMAP.md`.

## 6. Usage dashboard

After a few requests from §5:

| # | Step | Expected |
|---|---|---|
| 6.1 | Click **Refresh** under Usage | Requests/tokens/cost counts increase to match what you actually sent |
| 6.2 | Check **Recent requests** table | Rows for each call, with correct model/provider/status/cost/latency |
| 6.3 | Check **Cache stats** card | Hit rate reflects §5.4 if you triggered a cache hit |

## 7. Core gateway behavior (seeded demo tenants)

These use the pre-seeded demo keys against the mock providers — no real API key needed.

| Key | Team | Notes |
|---|---|---|
| `prism-sk-search-1a2b3c` | search | `fast` only, 60/min, cache on (threshold 0.92) |
| `prism-sk-research-4d5e6f` | research | `fast`/`smart`/`auto`, 300/min, cache off |
| `prism-sk-free-7g8h9i` | free-tier | `fast` only, 10/min, cache on (threshold 0.85) |
| `prism-sk-budget-demo-0j1k2l` | budget-demo | `fast` only, $0.00001/mo budget |

```bash
# non-streaming + header contract
curl -s -i http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "What is a message queue?"}]}'
# expect: 200, x-prism-provider: alpha/alpha-small, x-prism-cache: miss, x-prism-cost-usd present

# streaming
curl -N http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "stream": true, "messages": [{"role": "user", "content": "Explain SSE briefly."}]}'
# expect: text/event-stream, multiple "data: ..." lines, ends with "data: [DONE]"

# auto routing: short-but-hard -> smart, long-but-trivial -> fast
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-research-4d5e6f" -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Prove that the square root of 2 is irrational."}]}'
# expect: x-prism-provider references alpha-large/beta-large (the "smart" tier)

# live failover: take alpha down, fast request still succeeds via beta
curl -s -X POST http://localhost:9001/admin/config -d '{"mode": "down"}'
curl -s -i http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer prism-sk-search-1a2b3c" -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "failover test"}]}'
# expect: 200, x-prism-provider: beta/beta-small, x-prism-fallback: true
curl -s -X POST http://localhost:9001/admin/config -d '{"mode": "ok"}'   # restore alpha

# budget exhaustion: first call consumes the $0.00001 budget, second is rejected
curl -s http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-budget-demo-0j1k2l" \
  -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"first"}]}'
curl -s http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-budget-demo-0j1k2l" \
  -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"second"}]}'
# expect: first succeeds, second returns 402 budget_exceeded

# tenant isolation: same exact prompt under two different keys must NOT share a cache hit
curl -s http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-search-1a2b3c" \
  -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"isolation probe"}]}'
curl -s -i http://localhost:8080/v1/chat/completions -H "Authorization: Bearer prism-sk-free-7g8h9i" \
  -H "Content-Type: application/json" -d '{"model":"fast","messages":[{"role":"user","content":"isolation probe"}]}' \
  | grep x-prism-cache
# expect: x-prism-cache: miss (not "hit") on the second key, even though the first key already cached it
```

## 8. Admin (operator) console

| # | Step | Expected |
|---|---|---|
| 8.1 | From the tenant dashboard, click **Operator login** | Switches to username/password form |
| 8.2 | Log in with `admin` / `prism-admin-dev-password` | Lands on "Prism Ops Console" |
| 8.3 | Check the **Key** dropdown | Populated from a real `GET /admin/keys` call — includes both seeded ("operator-provisioned") and self-serve keys you created above |
| 8.4 | Pick a key, click **Refresh** | Usage/cache stats/recent requests populate for that specific key |
| 8.5 | Click **Log out**, then the **Operator login** link should be gone (you're back on tenant view) | Confirms the two auth sessions (`prism_admin_tokens` / `prism_user_tokens` in localStorage) are independent — logging out of one doesn't touch the other |

## 9. Things that should fail (negative tests)

| # | Request | Expected |
|---|---|---|
| 9.1 | `Authorization: Bearer not-a-real-key` | `401 authentication_error` |
| 9.2 | `model: "no-such-model-xyz"` | `404 not_found_error` |
| 9.3 | A user-scoped access token against `/admin/keys` | `401` — "Not an admin token" |
| 9.4 | An admin-scoped access token against `/me` | `401` — "Not a user token" |
| 9.5 | `POST /me/keys` with `requests_per_minute` above the ceiling | `422 invalid_request_error` |
| 9.6 | `POST /auth/signup` with an email already in use | `409 email_taken` |
| 9.7 | More than 10 login/signup attempts in a minute from one IP | `429 rate_limit_exceeded` |
| 9.8 | `GET /v1/models` with no key | `401 authentication_error` |

## 10. Operational checks

| # | Step | Expected |
|---|---|---|
| 10.1 | `docker logs prism-capstone-gateway-1` | One JSON object per line (`ts`/`level`/`logger`/`message`), not plain text — parseable by a log aggregator without a custom pattern |
| 10.2 | Send a request that gets rejected (bad key, over budget), then check the logs | A `"request rejected"` line whose `request_id` matches the corresponding `request_logs` row, so a log line and its DB row can be correlated |
| 10.3 | Stop Postgres (`docker stop prism-capstone-postgres-1`), then curl both health endpoints | `/health` still `200` (process is alive); `/ready` returns `503` with `"database": "unavailable"`. Restart Postgres to continue |
| 10.4 | Start the gateway with `ENVIRONMENT=production` and the default admin password | Refuses to boot with an explicit error. Boots normally once `ADMIN_BOOTSTRAP_PASSWORD` is set to something real |
| 10.5 | Take a provider down (`curl -X POST http://localhost:9001/admin/config -d '{"mode":"down"}'`) and send a `fast` request | Logs show `"upstream call failed; retrying"` then `"failing over to the next candidate"` before the request succeeds on beta. Restore with `{"mode":"ok"}` |

## Known, expected, non-failures

- One `WARN` (not `FAIL`) in `smoke_test.py`'s paraphrase-pair cache test — documented as an
  accepted soft-match case, not a bug.
- `pytest`'s output includes `DeprecationWarning`s about `pytest-asyncio`'s `event_loop` fixture
  and a handful of "non-checked-in connection" `SAWarning`s from test teardown — cosmetic,
  unrelated to correctness, pre-existing.
- The admin console's key dropdown may show a large number of `test (test-...)` entries — these
  are rows left behind by `pytest` runs against the same database `docker compose` uses; harmless,
  but if it's noisy, `docker compose down -v && docker compose up -d --build` resets to a clean
  database.

## Troubleshooting

- **Container fails to start / `validate_pricing_coverage` error at boot**: every model listed in
  any provider's `models[]` in `backend/config/gateway_config*.json` needs a matching entry in
  `data/model_pricing.json`. Check `docker logs prism-capstone-gateway-1`.
- **"Not logged in" errors right after signup**: this was a real bug (React effect-ordering race)
  fixed on 2026-08-19 — if you see it again, note the exact repro steps, it likely regressed.
- **BYOK request fails with a 401 that looks like a mock error, not a real provider error**: check
  `docker logs prism-capstone-gateway-1` for the literal upstream response body — Groq/Gemini
  error bodies look distinctly different from the mock providers' (`scripts/mock_provider.py`).
