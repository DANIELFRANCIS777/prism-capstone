# Prism — Verification Report

Run against the Docker Compose stack (`docker compose up -d --build`) on `http://localhost:8080`,
using the seeded demo keys from `data/seed_keys.json`. Raw commands to reproduce every result
below are in [AGENTS.md](AGENTS.md#verification-either-option-or-use-the-prism-verify-skill).

---

## 1. Smoke test (`scripts/smoke_test.py`)

```
python3 scripts/smoke_test.py --url http://localhost:8080 --key prism-sk-search-1a2b3c --model fast
```

```
[1] Non-streaming completion + header contract
  PASS  returns 200  (got 200)
  PASS  body has choices and usage
  PASS  header x-prism-provider present  (alpha/alpha-small)
  PASS  header x-prism-cache present  (miss)
  PASS  header x-prism-cost-usd present  (1.5e-05)
  PASS  first request is a cache miss  (miss)

[2] Streaming
  PASS  returns 200  (got 200)
  PASS  content-type is text/event-stream  (text/event-stream; charset=utf-8)
  PASS  received multiple SSE chunks  (19 data lines)
  PASS  stream ends with [DONE]

[3] Auth
  PASS  invalid key rejected with 401/403  (got 401)

[4] Unknown model
  PASS  unknown model rejected with 4xx  (got 404)
  PASS  error body explains the problem

[5] Semantic cache: exact repeat
  PASS  identical repeat is a cache hit  (hit)

[6] Semantic cache: paraphrases (each pair soft; at least one pair must hit)
  PASS  pair A paraphrase is a cache hit  (hit)
  WARN  pair B paraphrase is a cache hit  (miss)
  PASS  matching is semantic, not exact-only (at least one pair hit)

[7] Semantic cache: unrelated prompt
  PASS  unrelated prompt is a cache miss  (miss)

==================================================
  17 passed, 1 warnings, 0 failed
```

**The one WARN, explained:** pair B (`"refund policy"` vs `"can I get my money back"`) shares
almost no vocabulary between the two phrasings. The cache uses bag-of-words cosine similarity
(a documented, zero-dependency substitute for a real embedding model — see
[backend/HLD.md](backend/HLD.md) §4), which can only match paraphrases that reuse similar
words; it cannot bridge two phrasings built from entirely different vocabulary. Pair A
(`"reset my password"` vs `"steps to reset my dashboard password"`) shares enough vocabulary
to score a perfect 1.0 match and clears even the strictest configured threshold (0.92). This
is exactly what the smoke test's design anticipates — it only requires *at least one* pair to
hit, precisely because not every technique catches every paraphrase.

## 2. Routing eval — `auto` alias accuracy (`backend/scripts/routing_eval.py`)

```
cd backend && python3 scripts/routing_eval.py
```

**Result: 20/20 (100%)** against `data/routing_eval.jsonl` — including every short-but-hard
trap (proofs, estimation, deep systems reasoning) and every long-but-trivial trap (roster
lookup, log extraction, email rewrite) that the eval set specifically designs to fool a
length-only heuristic (which the spec notes scores ~60%).

| id | expected | actual | signals matched |
|---|---|---|---|
| route_001 | fast | fast | `what is the capital` |
| route_002 | fast | fast | `convert` |
| route_003 | fast | fast | `one-line` |
| route_004 | fast | fast | `what does` |
| route_005 | fast | fast | `translate` |
| route_006 | fast | fast | `who is on call` *(long roster dump — trap)* |
| route_007 | fast | fast | `what is the timestamp` *(long log excerpt — trap)* |
| route_008 | fast | fast | `extract the` |
| route_009 | fast | fast | `divisible by` |
| route_010 | fast | fast | `rewrite the following` *(longer input — trap)* |
| route_011 | smart | smart | `prove` *(short proof — trap)* |
| route_012 | smart | smart | `estimate how`, `show your reasoning` *(short — trap)* |
| route_013 | smart | smart | `distributed system`, `p99` *(short — trap)* |
| route_014 | smart | smart | `design a`, `schema for`, `trade-off` |
| route_015 | smart | smart | `compare`, `recommend` |
| route_016 | smart | smart | `explain how the`, `algorithm`, `o(n` |
| route_017 | smart | smart | `explain what`, `consistency guarantee`, `quorum`, `anomaly` |
| route_018 | smart | smart | `migration plan`, `zero-downtime`, `roll back` |
| route_019 | smart | smart | `most likely causes`, `p95` *(shortish — trap)* |
| route_020 | smart | smart | `prove`, `disprove` *(shares "divisible by" with route_009 — resolved by signal count, not a single keyword)* |

No misses to explain this run. Classifier design and full signal lists:
[backend/LLD.md §4.7](backend/LLD.md).

## 3. Load test — rate-limit over-admission (`scripts/load_test.py`)

Free-tier key (`prism-sk-free-7g8h9i`, limit 10 requests/minute), 30 requests at concurrency 10:

```
Burst finished in 0.4s
  accepted (200):      10
  rate limited (429):  20
  latency avg/p95 ms:  213 / 263

Over-admission check OK: 10 accepted <= limit 10.
```

Exactly 10 admitted under real concurrent load against a limit of 10 — not 9, not 11. This is
what the atomic `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING` rate limiter is designed
to guarantee (see [backend/LLD.md §4.4](backend/LLD.md)); this result was reproduced
identically across every run this session, including this one.

## 4. Accounting reconciliation

Search key (`prism-sk-search-1a2b3c`), isolated before/after `/admin/usage` snapshot around a
30-request/concurrency-10 burst:

| | Load test client-side total | `/admin/usage` delta |
|---|---|---|
| Accepted requests | 30 | 30 |
| Prompt tokens | 360 | 360 |
| Completion tokens | 781 | 781 |
| Cost (USD) | 0.000521 | 0.000521 |

Exact match. This isn't a coincidence to verify — `chat.py` writes the same computed
`cost_usd` value to both `usage_records` (what `/admin/usage` sums) and `request_logs` in the
same request, so they're structurally guaranteed to agree; this run confirms that guarantee
holds under concurrent load, not just sequential requests.

## 5. Automated tests (`backend/tests/`, pytest)

```bash
cd backend && pip install -r requirements-dev.txt && pytest -v
```

```
tests/test_allowlist.py ....                                             [ 13%]
tests/test_cache.py ...                                                  [ 24%]
tests/test_pricing.py .....                                              [ 41%]
tests/test_rate_limit.py ..                                              [ 48%]
tests/test_routing.py .........                                          [ 79%]
tests/test_semantic.py ......                                            [100%]

======================== 29 passed in 0.70s ========================
```

The one test worth calling out specifically:
`test_rate_limiter_admits_exactly_the_limit_under_real_concurrency` fires 20 genuinely
concurrent `asyncio` tasks (each with its own DB session, mirroring 20 real concurrent
requests) against a limit of 5, and asserts exactly 5 are admitted — this is the same
no-read-then-write-gap claim §3 demonstrates externally via `load_test.py`, proven here at
the unit-test level so it's checked on every run, not just when someone remembers to run the
load test. `test_cache_is_never_shared_across_virtual_keys` is the direct automated check for
the "cache hit returning another team's response is a data leak" requirement.

## 6. Pack validation (`scripts/validate_pack.py`)

```
Pack OK:
  4 priced models, 2 providers, 3 aliases
  4 seed tenants, 14 sample requests, 20 routing eval cases
```

## 7. Manual verification (this session, not scripted)

These aren't covered by the provided scripts, so they were exercised directly:

| Scenario | Result |
|---|---|
| Kill `alpha` mid-response (`kill -9` on the process while streaming) | Client received its partial content, then a clean `data: {"error": ...}` SSE event and `[DONE]` — no hang, no silent splice from `beta`. |
| Take `alpha` down (`POST /admin/config {"mode":"down"}`), send `fast` request | Served by `beta` with `x-prism-fallback: true`; traffic returned to `alpha` once restored. |
| Make `alpha` respond after 15s (timeout is 10s) | Request failed over to `beta` and still succeeded — no client-side hang. |
| Make `alpha` 50% flaky (`fail_rate: 0.5`), burst 10 requests | All 10 succeeded via retry-with-backoff. |
| Same prompt sent under two different keys with caching enabled | Cache miss on the second key — confirmed no cross-tenant cache leak. |
| Budget-demo key (`$0.00001` budget) | First request succeeds and exhausts it; second returns `402 budget_exceeded`. |
| Replay an already-rotated admin refresh token | Rejected, and every other active token for that account was revoked as a theft-response side effect (see [backend/LLD.md §4.11](backend/LLD.md)). |
| Restart the gateway container, reuse a pre-restart admin access token | Still valid — confirms the RS256 key pair persists via the `prism_jwt_keys` Docker volume. |

---

## Known, documented limitations (not gaps in testing — accepted trade-offs)

See [backend/LLD.md §7](backend/LLD.md#7-known-limitations-by-design-at-must-have-scope) for
the full list and reasoning. Summary: `rate_limit_windows` rows are never pruned; the semantic
cache does a linear scan rather than an indexed vector search; refresh-token rotation isn't
fully atomic the way the rate limiter is; there's no admin account management API beyond the
single bootstrap account.
