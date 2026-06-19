"""Hybrid retrieval over FTS5 keyword and vector cosine.

Combines two rankers with Reciprocal Rank Fusion (RRF). FTS5 contributes
lexical precision ("the marble that mentions kafka by name"); the embedding
model contributes paraphrase recall ("the marble about the rebalance bug,
even though it never said kafka"). RRF needs only the rank of each
candidate in each ranker's output, not a comparable score, which is what
makes BM25 plus cosine combinable at all.

When no embedder is available, or no vectors have been stored yet, hybrid
search degrades gracefully to FTS5 only. The caller does not have to think
about it; users get whatever quality the database can offer right now.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from core.models import Note
from core.storage import Storage

# RRF constant from the original paper. Larger values smooth the curve;
# smaller values reward top ranks more aggressively. 60 is the de facto
# default across the literature and has no tunable downside at our scale.
RRF_K = 60


class _Embedder(Protocol):
    def embed_query(self, text: str) -> np.ndarray: ...


def _rrf_accumulate(
    scores: dict[str, float],
    notes_by_id: dict[str, Note],
    ranked: list[Note],
    k: int,
) -> None:
    for rank, note in enumerate(ranked):
        notes_by_id[note.id] = note
        scores[note.id] = scores.get(note.id, 0.0) + 1.0 / (k + rank + 1)


def hybrid_search(
    storage: Storage,
    query: str,
    embedder: _Embedder | None,
    model_name: str | None,
    *,
    limit: int = 10,
    over_fetch: int = 50,
    rrf_k: int = RRF_K,
) -> list[Note]:
    """Run FTS5 and (when available) vector search, fuse via RRF.

    `embedder` and `model_name` may both be None, in which case this
    function reduces to FTS5 only. The same happens when the embedder is
    provided but the database holds no vectors yet (e.g. user has not run
    `marbles reembed`).
    """
    if not query.strip():
        return []

    scores: dict[str, float] = {}
    notes_by_id: dict[str, Note] = {}

    fts_hits = storage.search(query, limit=over_fetch)
    _rrf_accumulate(scores, notes_by_id, fts_hits, rrf_k)

    if (
        embedder is not None
        and model_name is not None
        and storage.vec_table_dim() is not None
    ):
        qvec = embedder.embed_query(query)
        vec_hits = [n for n, _dist in storage.vector_search(
            qvec, model_name, limit=over_fetch
        )]
        _rrf_accumulate(scores, notes_by_id, vec_hits, rrf_k)

    return [
        notes_by_id[nid]
        for nid in sorted(scores, key=lambda i: -scores[i])[:limit]
    ]


def vector_only_search(
    storage: Storage,
    query: str,
    embedder: _Embedder,
    model_name: str,
    *,
    limit: int = 10,
) -> list[Note]:
    """Pure vector search; returns [] if no vectors are present.

    Used by `marbles search --semantic` when the user wants to bypass the
    lexical channel entirely. The empty-result case is the caller's job to
    surface; this function does not warn.
    """
    if not query.strip() or storage.vec_table_dim() is None:
        return []
    qvec = embedder.embed_query(query)
    return [n for n, _ in storage.vector_search(qvec, model_name, limit=limit)]
