"""Normalized bag-of-words cosine similarity - the documented local substitute
for a real embedding model (see docs/IMPLEMENTATION_GUIDE.md FAQ). Used by both
the semantic cache and, indirectly, nothing else - the `auto` router uses its
own keyword signals (app/routing/auto_router.py), not this similarity metric."""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Stopwords plus generic question-scaffolding words ("steps", "ways", ...) that
# carry no topic content - two prompts that only differ by this kind of framing
# should still be treated as the same underlying question.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "and", "or", "that", "this", "it", "its",
    "what", "how", "why", "who", "when", "where", "which",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    "i", "my", "me", "you", "your", "we", "our", "us",
    "please", "kindly",
    "step", "steps", "way", "ways", "method", "methods", "process",
    # _TOKEN_RE has no apostrophe in its character class, so a contraction
    # like "what's"/"don't"/"you're"/"I've"/"I'll"/"I'd"/"I'm" splits into two
    # tokens at the apostrophe - the first half is usually already a stopword
    # above (what/do/you/i/...), but the second half ("s"/"t"/"re"/"ve"/"ll"/
    # "d"/"m") would otherwise survive as a spurious one-off token and dilute
    # the similarity score against an uncontracted phrasing of the same
    # question (e.g. "What's a message queue?" vs "What is a message queue?"
    # scored 0.816 instead of 1.0 before this was added - below every
    # configured cache threshold, a real cost-bearing miss on what should be
    # an obvious match).
    "s", "t", "re", "ve", "ll", "d", "m",
}

_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def term_frequency(text: str) -> Counter:
    return Counter(tokenize(text))


def cosine_similarity(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
