from pathlib import Path

import pytest

from core.storage import Storage
from marbles_mcp import server as srv


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "mcp.db"
    monkeypatch.setattr(srv, "_storage", lambda: Storage(db_path=db))


def test_tools_are_registered():
    names = {t.name for t in srv.mcp._tool_manager.list_tools()}
    assert names == {"add_note", "search_notes"}


def test_add_note_persists_and_returns_dict():
    out = srv.add_note("learn FastMCP tools API", tag="mcp")
    assert out["content"] == "learn FastMCP tools API"
    assert out["tag"] == "mcp"
    assert "id" in out and "created_at" in out


def test_add_note_rejects_empty():
    with pytest.raises(ValueError):
        srv.add_note("   ")


def test_search_notes_round_trip():
    srv.add_note("hybrid search RRF tradeoffs")
    srv.add_note("kafka consumer group rebalance")
    hits = srv.search_notes("hybrid")
    assert len(hits) == 1
    assert hits[0]["content"] == "hybrid search RRF tradeoffs"


def test_search_handles_fts_special_chars():
    srv.add_note("C++ async patterns")
    # Must not raise — escaping is shared with CLI via core.storage.
    hits = srv.search_notes("C++")
    assert any("C++" in h["content"] for h in hits)


def test_search_empty_query_returns_empty():
    srv.add_note("anything")
    assert srv.search_notes("") == []
