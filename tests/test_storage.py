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
