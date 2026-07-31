#!/usr/bin/env python3
"""One-command routing eval for the `auto` classifier (Must Have #6).

Runs every case in data/routing_eval.jsonl through app.routing.auto_router's
classifier directly (no running gateway needed) and reports accuracy with
per-case expected vs actual.

Usage (from backend/, with the venv active):
    python3 scripts/routing_eval.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routing.auto_router import classify_difficulty  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_FILE = REPO_ROOT / "data" / "routing_eval.jsonl"


def main() -> int:
    cases = [json.loads(line) for line in EVAL_FILE.read_text().splitlines() if line.strip()]

    correct = 0
    print(f"{'id':<12} {'expected':<8} {'actual':<8} {'ok':<4} reason")
    for case in cases:
        tier, reason = classify_difficulty(case["prompt"])
        ok = tier == case["expected_tier"]
        correct += ok
        print(f"{case['id']:<12} {case['expected_tier']:<8} {tier:<8} {'PASS' if ok else 'FAIL':<4} {reason}")

    total = len(cases)
    accuracy = correct / total if total else 0.0
    print(f"\nAccuracy: {correct}/{total} ({accuracy:.0%})")
    return 0 if correct == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
