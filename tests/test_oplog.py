"""Unit tests for the pure op-log data shaping (core/oplog.py).

Storage-level emission and atomicity live in test_storage.py; here we only
exercise payload construction, encoding determinism, and replay.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.models import Note
from core.oplog import (
    decode_payload,
    delete_payload,
    encode_payload,
    insert_payload,
    payload_to_note,
)


def test_insert_payload_carries_the_full_row():
    note = Note(content="hello", tag="work")
    p = insert_payload(note)
    assert p == {
        "id": note.id,
        "content": "hello",
        "tag": "work",
        "created_at": note.created_at.isoformat(),
    }


def test_insert_payload_excludes_local_derived_embedding_fields():
    # Embedding columns are non-synced (SPEC §7.6) — they must never appear in
    # the op payload even though they live on the same notes row.
    note = Note(content="x")
    p = insert_payload(note)
    assert "embedding_model" not in p
    assert "embedded_at" not in p


def test_delete_payload_is_just_the_id():
    assert delete_payload("01ABC") == {"id": "01ABC"}


def test_encode_is_deterministic_regardless_of_key_order():
    a = encode_payload({"id": "1", "content": "c", "tag": None})
    b = encode_payload({"tag": None, "content": "c", "id": "1"})
    assert a == b  # sort_keys makes a signed blob stable across dict ordering


def test_encode_preserves_non_ascii_readably():
    raw = encode_payload({"content": "今天写了 marbles"})
    assert "今天写了" in raw  # not \uXXXX-escaped


def test_encode_decode_roundtrip():
    payload = {"id": "01ABC", "content": "c", "tag": "t", "created_at": "2026-06-21T00:00:00+00:00"}
    assert decode_payload(encode_payload(payload)) == payload


def test_payload_to_note_reconstructs_an_equivalent_note():
    original = Note(
        content="run a marathon",
        tag="health",
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    restored = payload_to_note(insert_payload(original))
    assert restored.id == original.id
    assert restored.content == original.content
    assert restored.tag == original.tag
    assert restored.created_at == original.created_at


def test_payload_to_note_handles_absent_tag():
    note = Note(content="no tag here")
    restored = payload_to_note(insert_payload(note))
    assert restored.tag is None
