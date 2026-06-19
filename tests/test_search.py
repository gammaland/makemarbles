"""Unit tests for core.search hybrid retrieval.

Storage and an in-memory fake embedder are wired together so RRF behavior
can be verified directly, without going through the CLI surface.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.models import Note
from core.search import hybrid_search, vector_only_search
from core.storage import Storage


class FakeEmbedder:
    """Returns a vector aligned with whatever direction a fixture pins to a
    given keyword. Tests set up the mapping explicitly; queries fall back to
    a zero-ish vector that ties with nothing."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.mapping: dict[str, np.ndarray] = {}

    def pin(self, text: str, direction: list[float]) -> np.ndarray:
        v = np.array(direction, dtype=np.float32)
        v = v / np.linalg.norm(v)
        self.mapping[text] = v
        return v

    def embed_query(self, text: str) -> np.ndarray:
        return self.mapping.get(
            text, np.zeros(self.dim, dtype=np.float32)
        )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "search.db")


# ---------- FTS-only path (no embedder) ----------


def test_hybrid_search_without_embedder_returns_fts_results(storage: Storage):
    a = storage.add(Note(content="kafka consumer rebalance"))
    storage.add(Note(content="weekend sourdough plan"))
    hits = hybrid_search(storage, "kafka", embedder=None, model_name=None)
    assert [n.id for n in hits] == [a]


def test_hybrid_search_empty_query_returns_nothing(storage: Storage):
    storage.add(Note(content="anything"))
    assert hybrid_search(storage, "", embedder=None, model_name=None) == []
    assert hybrid_search(storage, "   ", embedder=None, model_name=None) == []


# ---------- Pure hybrid: both rankers contribute ----------


def test_hybrid_search_fuses_fts_and_vector_when_both_exist(storage: Storage):
    """A note that mentions the query verbatim should appear; a paraphrase
    note that the embedder considers similar should appear too."""
    direct_hit = storage.add(Note(content="kafka topic offset commit failure"))
    paraphrase = storage.add(Note(content="we keep losing acks on the broker"))
    unrelated = storage.add(Note(content="banana bread is rising slow"))

    emb = FakeEmbedder()
    # Vector mapping: query and paraphrase land in the same direction; the
    # direct hit and unrelated each get an orthogonal direction.
    query_dir = [1.0, 0.0, 0.0, 0.0]
    emb.pin("kafka", query_dir)
    storage.upsert_vector(direct_hit, emb.pin("direct_hit_text", [0.0, 1.0, 0.0, 0.0]), "m")
    storage.upsert_vector(paraphrase, emb.pin("paraphrase_text", query_dir), "m")
    storage.upsert_vector(unrelated, emb.pin("unrelated_text", [0.0, 0.0, 1.0, 0.0]), "m")

    hits = hybrid_search(storage, "kafka", embedder=emb, model_name="m", limit=2)
    hit_ids = {n.id for n in hits}
    assert direct_hit in hit_ids  # lexical contributes
    assert paraphrase in hit_ids  # vector contributes
    assert unrelated not in hit_ids


def test_hybrid_search_falls_back_to_fts_when_no_vec_table(storage: Storage):
    """Embedder is present but no vectors stored yet. Must not crash; must
    return FTS results."""
    a = storage.add(Note(content="kafka rebalance debug"))
    emb = FakeEmbedder()
    emb.pin("kafka", [1.0, 0.0, 0.0, 0.0])
    hits = hybrid_search(storage, "kafka", embedder=emb, model_name="m")
    assert [n.id for n in hits] == [a]


def test_hybrid_search_respects_limit(storage: Storage):
    for i in range(15):
        storage.add(Note(content=f"kafka note number {i}"))
    hits = hybrid_search(storage, "kafka", embedder=None, model_name=None, limit=5)
    assert len(hits) == 5


def test_hybrid_search_dedups_when_same_note_ranks_in_both_lists(storage: Storage):
    """If FTS and vector both rank the same note in their top results, it
    should appear exactly once in the fused output."""
    only_note = storage.add(Note(content="kafka issues again"))

    emb = FakeEmbedder()
    direction = [1.0, 0.0, 0.0, 0.0]
    emb.pin("kafka", direction)
    storage.upsert_vector(only_note, emb.pin("only_text", direction), "m")

    hits = hybrid_search(storage, "kafka", embedder=emb, model_name="m", limit=10)
    assert [n.id for n in hits] == [only_note]


# ---------- vector_only_search ----------


def test_vector_only_search_returns_nothing_when_no_vec_table(storage: Storage):
    storage.add(Note(content="anything"))
    emb = FakeEmbedder()
    assert vector_only_search(storage, "query", emb, "m") == []


def test_vector_only_search_finds_semantic_match(storage: Storage):
    direct = storage.add(Note(content="kafka literal mention"))
    para = storage.add(Note(content="some unrelated text on the surface"))
    unrelated = storage.add(Note(content="another unrelated row"))

    emb = FakeEmbedder()
    query_dir = [1.0, 0.0, 0.0, 0.0]
    emb.pin("kafka", query_dir)
    storage.upsert_vector(direct, emb.pin("direct_text", [0.0, 1.0, 0.0, 0.0]), "m")
    storage.upsert_vector(para, emb.pin("para_text", query_dir), "m")
    storage.upsert_vector(unrelated, emb.pin("unrelated_text", [0.0, 0.0, 1.0, 0.0]), "m")

    hits = vector_only_search(storage, "kafka", emb, "m", limit=1)
    # Pure vector path: the paraphrase wins even though the direct hit
    # contains the literal word, because the vector model says so.
    assert [n.id for n in hits] == [para]


def test_vector_only_search_empty_query_returns_nothing(storage: Storage):
    storage.add(Note(content="x"))
    emb = FakeEmbedder()
    assert vector_only_search(storage, "", emb, "m") == []
