# Layer 5 vectors — PARKED, not merged

**Status:** parked 2026-09-21 by the project owner's decision. Branch
`layer-5-vectors`, 6 commits, 171 tests passing, 94% coverage, one known bug
(`router._parse` raises on `{"facts": null}`).

## Why it is parked

The reference architecture this project is modelled on (waku-agent) uses
**keyword top-k with no embedding** for semantic memory — `state.db` with
SQLite FTS5. `main` already does exactly that. This branch replaced it with
local embeddings, which moves *away* from architectural parity rather than
toward it.

## What the work proved, which is worth keeping either way

Probing during the rewrite established a result that argues *for* the
keyword-plus-judgement design:

- Embeddings **rank** facts well: 5/5 top-1 correct with a bi-encoder, 6/6 with
  a cross-encoder reranker, including the vocabulary-mismatch case FTS5 cannot
  reach ("tell me about my testing background" → a QA fact sharing no terms).
- Embeddings **cannot gate**. No cosine floor separates a relevant query from an
  irrelevant one, at any model size tried:

  | Model | dim | lowest RELEVANT | highest IRRELEVANT | separation |
  |---|---|---|---|---|
  | bge-small-en-v1.5 | 384 | 0.512 | 0.651 | −0.139 |
  | bge-base-en-v1.5 | 768 | 0.464 | 0.543 | −0.079 |
  | gte-base | 768 | 0.749 | 0.776 | −0.027 |
  | mxbai-embed-large-v1 | 1024 | 0.412 | 0.519 | −0.108 |

  Margin and z-score separate worse, not better. Cross-encoders rank better and
  gate no better.

**So: ranking is cheap and solvable; judging relevance is not a similarity
problem.** That is the likely reason the reference design pairs cheap keyword
retrieval with an LLM judgement rather than a vector store with a threshold.

A second finding, independent of the above: a fact-to-fact dedup threshold at
0.85 **silently destroys distinct facts** — "standup at 9am" vs "standup at
10am" score 0.947. The only safe window measured was 0.947–0.981. If dedup is
ever revived, on any backend, it needs that calibration.

## What to do with it

Decide after the architecture review. The three options are: abandon and keep
this note as the design record; finish task 6 and merge deliberately; or
re-target the ranking work as an *addition* to keyword retrieval rather than a
replacement for it.
