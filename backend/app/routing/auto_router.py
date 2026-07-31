"""Engineered-signal classifier for the `auto` alias. Must beat a length-only
baseline (~60% on data/routing_eval.jsonl per the spec) - the eval set traps
length heuristics with short-but-hard prompts (proofs, deep systems reasoning)
and long-but-trivial ones (roster lookups, log extraction). Signals below are
keyed to the underlying task *type* (open-ended reasoning/design/proof vs a
single mechanical operation), not to exact eval phrasing - see
scripts/routing_eval.py for the measured accuracy and per-case results."""

from app.routing.aliases import ResolvedRoute, resolve_candidate_chain

SMART_SIGNALS = [
    "prove", "disprove", "derive", "justify",
    "design a", "design an", "schema for",
    "trade-off", "tradeoff",
    "compare", "recommend",
    "explain why", "explain what", "explain how the",
    "estimate how", "show your reasoning",
    "most likely causes", "how would you confirm",
    "algorithm", "big-o", "o(n",
    "migration plan", "zero-downtime", "roll back",
    "consistency guarantee", "quorum", "anomaly",
    "distributed system", "p95", "p99",
]

FAST_SIGNALS = [
    "what is the capital", "what does", "convert", "translate",
    "extract the", "who is on call", "what is the timestamp",
    "rewrite the following", "one-line", "divisible by",
]

LENGTH_FALLBACK_WORDS = 40


def classify_difficulty(prompt: str) -> tuple[str, str]:
    """Returns (tier, reason). tier is 'fast' or 'smart'."""
    text = prompt.lower()
    smart_hits = [s for s in SMART_SIGNALS if s in text]
    fast_hits = [s for s in FAST_SIGNALS if s in text]

    if len(smart_hits) > len(fast_hits):
        return "smart", f"reasoning/design signals matched: {smart_hits}"
    if len(fast_hits) > len(smart_hits):
        return "fast", f"mechanical-task signals matched: {fast_hits}"

    word_count = len(text.split())
    if word_count > LENGTH_FALLBACK_WORDS:
        return "smart", f"no keyword signal; length fallback ({word_count} words > {LENGTH_FALLBACK_WORDS})"
    return "fast", f"no keyword signal; length fallback ({word_count} words <= {LENGTH_FALLBACK_WORDS})"


def _last_user_message(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def resolve_route(alias_or_model: str, messages: list[dict]) -> tuple[list[ResolvedRoute], str | None]:
    """Resolves a request's candidate chain. For `auto`, classifies difficulty
    first and resolves the chosen tier's alias; returns the classification as
    a route_reason for the request log (Must Have #6)."""
    if alias_or_model == "auto":
        tier, reason = classify_difficulty(_last_user_message(messages))
        chain = resolve_candidate_chain(tier)
        return chain, f"auto -> {tier}: {reason}"
    return resolve_candidate_chain(alias_or_model), None
