from pathlib import Path

import pytest

from core.models import Note
from core.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "test.db")


def test_add_and_get_roundtrip(storage: Storage):
    note = Note(content="weekly review automation idea", tag="work")
    note_id = storage.add(note)
    fetched = storage.get(note_id)

    assert fetched is not None
    assert fetched.id == note_id
    assert fetched.content == note.content
    assert fetched.tag == "work"


def test_recent_orders_newest_first(storage: Storage):
    storage.add(Note(content="oldest"))
    storage.add(Note(content="middle"))
    storage.add(Note(content="newest"))

    recent = storage.recent(days=1)
    assert len(recent) == 3
    assert recent[0].content == "newest"
    assert recent[-1].content == "oldest"


def test_fts_search_finds_keyword(storage: Storage):
    storage.add(Note(content="run a marathon in fall"))
    storage.add(Note(content="learn rust async patterns"))
    storage.add(Note(content="weekly review of marathon training"))

    hits = storage.search("marathon")
    contents = {h.content for h in hits}

    assert "run a marathon in fall" in contents
    assert "weekly review of marathon training" in contents
    assert "learn rust async patterns" not in contents


@pytest.mark.parametrize(
    "query",
    ["C++", '"unclosed', "AND", "(plan)", "rust*"],
    ids=["plus", "stray-quote", "reserved-AND", "parens", "star"],
)
def test_search_tolerates_fts_special_chars(storage: Storage, query: str):
    storage.add(Note(content="C++ async patterns"))
    storage.add(Note(content="plan a marathon"))
    # Should not raise — user-supplied syntax is escaped to literal phrases.
    storage.search(query)


def test_search_empty_query_returns_empty(storage: Storage):
    storage.add(Note(content="anything"))
    assert storage.search("") == []
    assert storage.search("   ") == []


def test_delete_removes_note_and_fts_entry(storage: Storage):
    note_id = storage.add(Note(content="ephemeral thought about kafka"))
    assert storage.delete(note_id) is True
    assert storage.get(note_id) is None
    assert storage.search("kafka") == []


def test_delete_missing_returns_false(storage: Storage):
    assert storage.delete("01nonexistent") is False


def test_find_by_prefix(storage: Storage):
    a = storage.add(Note(content="alpha"))
    b = storage.add(Note(content="beta"))
    # Full id always uniquely matches.
    assert [n.id for n in storage.find_by_prefix(a)] == [a]
    # Empty prefix returns everything (LIKE '%' matches all).
    assert {n.id for n in storage.find_by_prefix("")} == {a, b}


def test_search_multitoken_implicit_and(storage: Storage):
    storage.add(Note(content="plan a marathon"))
    storage.add(Note(content="plan a vacation"))
    storage.add(Note(content="marathon training notes"))

    hits = storage.search("plan marathon")
    contents = {h.content for h in hits}
    assert contents == {"plan a marathon"}


def test_embedding_columns_present_on_fresh_db(storage: Storage):
    cols = {c.name for c in storage.db["notes"].columns}
    assert "embedding_model" in cols
    assert "embedded_at" in cols


def test_migration_is_idempotent(storage: Storage):
    # Re-running _init_schema (e.g. across processes) must not raise.
    storage._init_schema()
    storage._init_schema()
    cols = {c.name for c in storage.db["notes"].columns}
    assert "embedding_model" in cols


def test_migration_adds_columns_to_pre_existing_db(tmp_path):
    import sqlite_utils

    # Simulate a v0.1 database that pre-dates the embedding columns.
    db_path = tmp_path / "legacy.db"
    legacy = sqlite_utils.Database(str(db_path))
    legacy["notes"].create(
        {"id": str, "content": str, "tag": str, "created_at": str}, pk="id"
    )
    legacy["notes"].enable_fts(
        ["content", "tag"], create_triggers=True, tokenize="porter unicode61"
    )
    cols_before = {c.name for c in legacy["notes"].columns}
    assert "embedding_model" not in cols_before

    storage = Storage(db_path=db_path)
    cols_after = {c.name for c in storage.db["notes"].columns}
    assert "embedding_model" in cols_after
    assert "embedded_at" in cols_after


def test_pending_embed_count_treats_null_as_pending(storage: Storage):
    storage.add(Note(content="a"))
    storage.add(Note(content="b"))
    assert storage.pending_embed_count("multilingual-e5-small") == 2


def test_pending_embed_count_excludes_matching_model(storage: Storage):
    id_a = storage.add(Note(content="a"))
    storage.add(Note(content="b"))
    storage.db["notes"].update(id_a, {"embedding_model": "multilingual-e5-small"})

    assert storage.pending_embed_count("multilingual-e5-small") == 1
    # Different target model => the already-embedded row counts as pending again.
    assert storage.pending_embed_count("bge-m3") == 2


def test_pending_embed_count_empty_db_is_zero(storage: Storage):
    assert storage.pending_embed_count("multilingual-e5-small") == 0


# ---------- vector index (sqlite-vec) ----------

import numpy as np

from core.storage import VectorDimMismatch


def _unit_vec(values: list[float]) -> np.ndarray:
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_vec_table_dim_is_none_before_first_upsert(storage: Storage):
    assert storage.vec_table_dim() is None


def test_upsert_creates_vec_table_on_first_call(storage: Storage):
    note_id = storage.add(Note(content="hello"))
    storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    assert storage.vec_table_dim() == 3


def test_upsert_stamps_embedding_bookkeeping(storage: Storage):
    note_id = storage.add(Note(content="hello"))
    storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    row = next(storage.db.query(
        "SELECT embedding_model, embedded_at FROM notes WHERE id = ?", [note_id]
    ))
    assert row["embedding_model"] == "model-a"
    assert row["embedded_at"] is not None


def test_upsert_replaces_existing_vector(storage: Storage):
    note_id = storage.add(Note(content="hello"))
    storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    storage.upsert_vector(note_id, _unit_vec([0.0, 1.0, 0.0]), "model-a")
    hits = storage.vector_search(_unit_vec([0.0, 1.0, 0.0]), "model-a", limit=1)
    assert len(hits) == 1
    assert hits[0][0].id == note_id


def test_upsert_dim_mismatch_raises(storage: Storage):
    note_id = storage.add(Note(content="hello"))
    storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    with pytest.raises(VectorDimMismatch):
        storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0, 0.0]), "model-b")


def test_vector_search_returns_nearest_first(storage: Storage):
    a = storage.add(Note(content="east"))
    b = storage.add(Note(content="north"))
    c = storage.add(Note(content="up"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "m")
    storage.upsert_vector(b, _unit_vec([0.0, 1.0, 0.0]), "m")
    storage.upsert_vector(c, _unit_vec([0.0, 0.0, 1.0]), "m")
    hits = storage.vector_search(_unit_vec([0.9, 0.1, 0.0]), "m", limit=3)
    ids = [n.id for n, _ in hits]
    assert ids[0] == a
    # Distances must be sorted ascending.
    distances = [d for _, d in hits]
    assert distances == sorted(distances)


def test_vector_search_filters_by_model(storage: Storage):
    a = storage.add(Note(content="under model-a"))
    b = storage.add(Note(content="under model-b"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    # model-b vector lives in the same vec table since dims match, but the
    # note row records a different model. vector_search must skip it.
    storage.upsert_vector(b, _unit_vec([1.0, 0.0, 0.0]), "model-b")
    hits = storage.vector_search(_unit_vec([1.0, 0.0, 0.0]), "model-a", limit=10)
    assert [n.id for n, _ in hits] == [a]


def test_vector_search_returns_empty_when_no_index(storage: Storage):
    hits = storage.vector_search(_unit_vec([1.0, 0.0, 0.0]), "m", limit=10)
    assert hits == []


def test_iter_pending_for_embed_yields_unembedded_notes(storage: Storage):
    a = storage.add(Note(content="x"))
    b = storage.add(Note(content="y"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "m")
    pending = list(storage.iter_pending_for_embed("m"))
    assert {n.id for n in pending} == {b}


def test_iter_pending_for_embed_yields_stale_model(storage: Storage):
    a = storage.add(Note(content="x"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "old-model")
    pending = list(storage.iter_pending_for_embed("new-model"))
    assert [n.id for n in pending] == [a]


def test_iter_pending_for_embed_respects_limit(storage: Storage):
    for i in range(5):
        storage.add(Note(content=f"n{i}"))
    pending = list(storage.iter_pending_for_embed("m", limit=2))
    assert len(pending) == 2


def test_reset_vector_index_drops_table_and_clears_metadata(storage: Storage):
    a = storage.add(Note(content="x"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "model-a")
    assert storage.vec_table_dim() == 3

    storage.reset_vector_index()
    assert storage.vec_table_dim() is None
    row = next(storage.db.query(
        "SELECT embedding_model, embedded_at FROM notes WHERE id = ?", [a]
    ))
    assert row["embedding_model"] is None
    assert row["embedded_at"] is None


def test_delete_removes_vector_row(storage: Storage):
    a = storage.add(Note(content="x"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "m")
    storage.delete(a)
    hits = storage.vector_search(_unit_vec([1.0, 0.0, 0.0]), "m", limit=10)
    assert hits == []


def test_reset_then_recreate_with_different_dim(storage: Storage):
    a = storage.add(Note(content="x"))
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0]), "small-model")
    storage.reset_vector_index()
    # After reset, a fresh upsert may use any dim.
    storage.upsert_vector(a, _unit_vec([1.0, 0.0, 0.0, 0.0, 0.0]), "big-model")
    assert storage.vec_table_dim() == 5


# ---------- op log (SPEC §7.1) ----------


def test_add_emits_one_insert_op(storage: Storage):
    note = Note(content="capture this", tag="work")
    storage.add(note)
    ops = storage.unsynced_ops()
    assert len(ops) == 1
    op = ops[0]
    assert op.op_type == "insert"
    assert op.note_id == note.id
    assert op.server_op_id is None
    # client_ts equals the creation moment for a fresh insert.
    assert op.client_ts == note.created_at.isoformat()
    # Payload is the full row, minus local-derived embedding fields.
    assert op.payload == {
        "id": note.id,
        "content": "capture this",
        "tag": "work",
        "created_at": note.created_at.isoformat(),
    }


def test_delete_emits_a_delete_op_that_outlives_the_note(storage: Storage):
    note_id = storage.add(Note(content="ephemeral"))
    storage.delete(note_id)
    assert storage.get(note_id) is None  # note row gone
    ops = storage.unsynced_ops()
    assert [o.op_type for o in ops] == ["insert", "delete"]
    delete_op = ops[-1]
    assert delete_op.note_id == note_id
    assert delete_op.payload == {"id": note_id}


def test_delete_missing_note_emits_no_op(storage: Storage):
    assert storage.delete("01nonexistent") is False
    assert storage.ops_count() == 0


def test_local_seq_is_monotonic_in_emission_order(storage: Storage):
    a = storage.add(Note(content="first"))
    b = storage.add(Note(content="second"))
    ops = storage.unsynced_ops()
    assert [o.note_id for o in ops] == [a, b]
    assert ops[0].local_seq < ops[1].local_seq


def test_upsert_vector_emits_no_op(storage: Storage):
    # Embeddings are local-derived, non-synced state (SPEC §7.6); they must
    # not generate ops or every reembed would flood the sync log.
    note_id = storage.add(Note(content="x"))
    storage.upsert_vector(note_id, _unit_vec([1.0, 0.0, 0.0]), "m")
    assert storage.ops_count() == 1  # the insert only


def test_mark_op_synced_clears_it_from_the_backlog(storage: Storage):
    storage.add(Note(content="a"))
    storage.add(Note(content="b"))
    backlog = storage.unsynced_ops()
    assert len(backlog) == 2

    storage.mark_op_synced(backlog[0].local_seq, server_op_id=1001)
    remaining = storage.unsynced_ops()
    assert [o.local_seq for o in remaining] == [backlog[1].local_seq]
    assert storage.ops_count() == 2  # still in the log, just acknowledged


def test_unsynced_ops_respects_limit(storage: Storage):
    for i in range(5):
        storage.add(Note(content=f"n{i}"))
    assert len(storage.unsynced_ops(limit=2)) == 2


def test_add_is_atomic_note_and_op_roll_back_together(storage: Storage, monkeypatch):
    # If op emission fails mid-add, the note insert must roll back too, so we
    # never persist a note without its op. Force encode_payload to blow up.
    from core import oplog

    monkeypatch.setattr(
        oplog, "encode_payload", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    note = Note(content="should not survive")
    with pytest.raises(RuntimeError):
        storage.add(note)
    assert storage.get(note.id) is None
    assert storage.ops_count() == 0


def test_ops_survive_reopen(storage: Storage, tmp_path):
    storage.add(Note(content="durable"))
    # Reopen the same db file: ops table and rows persist.
    reopened = Storage(db_path=storage.db_path)
    assert reopened.ops_count() == 1
    assert reopened.unsynced_ops()[0].op_type == "insert"
