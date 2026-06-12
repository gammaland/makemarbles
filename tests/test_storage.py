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
