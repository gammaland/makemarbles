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
