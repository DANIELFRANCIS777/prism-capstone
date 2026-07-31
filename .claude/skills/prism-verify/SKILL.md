---
name: prism-verify
description: Run Prism's full verification pass - starts the two mock providers, then runs validate_pack.py, smoke_test.py, load_test.py, and the routing eval against the running gateway, and summarizes pass/fail. Use whenever asked to verify, test, or check the gateway end-to-end, or before claiming Must Have work is done.
---

# Prism Verification

Runs the checks the capstone is graded on: data validity, the smoke test (API + header
contract), the load test (rate-limit over-admission and accounting accuracy), and the `auto`
routing eval.

## Prerequisites

The gateway must be implemented and running (default assumed at `http://localhost:8080`). If
`backend/` doesn't exist yet or the gateway isn't reachable, say so and stop — there's nothing to
verify yet.

## Steps

1. **Validate the data pack** (safe to run any time, no gateway needed):

   ```bash
   python3 scripts/validate_pack.py
   ```

2. **Start both mock providers** if they aren't already running:

   ```bash
   python3 scripts/mock_provider.py --port 9001 --name alpha &
   python3 scripts/mock_provider.py --port 9002 --name beta &
   ```

   Confirm both respond before continuing, e.g.:

   ```bash
   curl -s http://localhost:9001/v1/chat/completions \
     -H "Authorization: Bearer anything" -H "Content-Type: application/json" \
     -d '{"model": "alpha-small", "messages": [{"role": "user", "content": "hello"}]}'
   ```

3. **Confirm the gateway is up**: `GET http://localhost:8080/health` (ask the user for the
   correct URL/port if it differs).

4. **Smoke test**:

   ```bash
   python3 scripts/smoke_test.py --url http://localhost:8080 --key prism-sk-search-1a2b3c --model fast
   ```

5. **Load test**:

   ```bash
   python3 scripts/load_test.py --url http://localhost:8080 --key prism-sk-free-7g8h9i --model fast --requests 30 --concurrency 10 --rpm-limit 10
   ```

   Confirm the report shows zero rate-limit over-admission and that accounting totals reconcile.

6. **Routing eval**: run the gateway's own routing-eval command over `data/routing_eval.jsonl`
   (this is implemented by the gateway itself, not a provided script — check the backend
   README/Makefile for the exact command) and report accuracy with per-case results.

7. **Summarize**: report pass/fail for each of the four checks above, and surface the key
   numbers (smoke test result, load test over-admission count + accounting delta, routing eval
   accuracy) so they can go straight into the verification report the capstone deliverables
   require.

## Notes

- Never edit `scripts/*.py` or `data/*` to make a check pass — they define the grading contract
  as-is.
- On failure, diagnose against `docs/API_CONTRACT.md` (header contract), `docs/EVALUATION_GUIDE.md`
  (what's being verified and why), and the "Avoid These Mistakes" section of
  `PRISM_PROBLEM_STATEMENT.md` before changing gateway code.
