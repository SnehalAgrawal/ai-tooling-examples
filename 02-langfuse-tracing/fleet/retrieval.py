"""Keyword retrieval over the snippets: deterministic, no model, no index."""

import re

from fleet.knowledge import SNIPPETS

STOPWORDS = frozenset(
    "a an and are at be by can do does for from how i if in is it its long many may me much must of on or "
    "our should the their them there they this to was we what when where which who why will with you your".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens without stopwords; a trailing plural 's' is dropped so 'vans' matches 'van'."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def score(query_tokens: list[str], text: str) -> int:
    """Number of distinct query tokens that appear in the text."""
    doc = set(tokenize(text))
    return len(set(query_tokens) & doc)


def retrieve(question: str, k: int = 2, snippets: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """Top-k snippets by keyword overlap (title counts as text); ties keep knowledge-base order. Zero-score
    snippets are never returned."""
    snippets = SNIPPETS if snippets is None else snippets
    q = tokenize(question)
    scored = [(score(q, s["title"] + " " + s["text"]), i, s) for i, s in enumerate(snippets)]
    scored = [t for t in scored if t[0] > 0]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, _, s in scored[:k]]
