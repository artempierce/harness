"""Layer 5: durable facts, and the gate in front of them.

Episodic memory answers "what were we just saying". This answers "what is true
about this person" — searched by relevance, not recency, so a fact from two
hundred messages ago can surface for the right question.

The gate is the more important half. Most turns need no facts at all, and
retrieving anyway spends tokens and pushes irrelevant context at the model,
which makes answers worse rather than merely slower.
"""

import re
from datetime import UTC, datetime

from ninja.trace import connect

TOP_K = 3


def remember(
    content: str, source: str = "told", trace_id: int | None = None, conn=None
) -> int:
    # A caller that passes its own connection is inside a transaction of its
    # own and owns the commit. Committing or closing here would end that
    # transaction halfway through.
    own = conn is None
    if own:
        conn = connect()
    cur = conn.execute(
        "INSERT INTO facts (content, source, trace_id, created_at) VALUES (?, ?, ?, ?)",
        (content, source, trace_id, datetime.now(UTC).isoformat(timespec="seconds")),
    )
    if own:
        conn.commit()
        conn.close()
    return cur.lastrowid


# Words that appear in almost every fact and almost every question. Without
# this list, "tell me about the deployment problem" matches any fact
# containing "the" — a confident retrieval built on a stopword.
STOPWORDS = frozenset({
    "the", "and", "you", "your", "for", "are", "was", "with", "that", "this",
    "have", "from", "can", "does", "did", "how", "why", "when", "who", "what",
    "about", "tell", "has", "had", "its", "our", "their", "them", "they",
    "been", "will", "would", "should", "could", "there", "here", "than",
    "then", "some", "any", "not", "but", "out", "very", "just", "get", "know",
})


def words(text: str) -> list[str]:
    """Lowercased, meaningful tokens — 3+ letters/digits, minus stopwords.

    Shared with ninja.skills so the fact gate and skill matching cannot drift
    apart on what counts as a word.
    """
    return [w for w in re.findall(r"[A-Za-z0-9]{3,}", text.lower())
            if w not in STOPWORDS]


def _query(text: str) -> str:
    """FTS5 has its own syntax, and raw user input is not valid in it.

    OR the meaningful words together. A term with an apostrophe or a bare '-'
    is a syntax error, not a bad search.
    """
    return " OR ".join(words(text))


def search(text: str, k: int = TOP_K) -> list[tuple[float, str]]:
    query = _query(text)
    if not query:
        return []
    conn = connect()
    rows = conn.execute(
        "SELECT bm25(facts), content FROM facts WHERE facts MATCH ?"
        " ORDER BY bm25(facts) LIMIT ?",
        (query, k),
    ).fetchall()
    conn.close()
    return [(score, content) for score, content in rows]


def gate(text: str) -> tuple[bool, str, list[tuple[float, str]]]:
    """Decide whether this turn is worth retrieving for.

    Returns (retrieve?, why, hits). The reason is recorded in the trace, so a
    skip is a visible decision rather than an absence.

    Note the ordering: the search runs *before* the decision. With a local
    index that costs microseconds, so what the gate protects is context, not
    latency. Backed by a vector API the order would have to flip.

    There is deliberately no relevance floor. bm25 weights a term by how rare
    it is across the corpus, and with a handful of facts that weight collapses
    toward zero — so any fixed threshold is measuring corpus size, not
    relevance, and rejects correct matches. Term overlap after stopword removal
    is the honest signal at this scale. Add a floor when there are thousands of
    facts and bm25 means something.
    """
    if count() == 0:
        return False, "nothing remembered yet", []
    hits = search(text)
    if not hits:
        return False, "no fact matched", []
    return True, f"{len(hits)} fact(s) matched", hits


def as_context(hits: list[tuple[float, str]]) -> str:
    return "\n".join(f"- {content}" for _, content in hits)


def count() -> int:
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    conn.close()
    return n


def all_facts(limit: int = 50) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT rowid, content, source, trace_id, created_at FROM facts"
        " ORDER BY rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    keys = ("id", "content", "source", "trace_id", "created_at")
    return [dict(zip(keys, row, strict=True)) for row in rows]
