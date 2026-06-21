"""Local op log: change-data-capture for sync (SPEC §7.1).

Every content mutation (note insert / delete) appends exactly one op to a
local, append-only log. Ops are stored in PLAINTEXT locally — the same data
already lives in `notes` on the same disk, so the op log adds no new exposure.
The payload is encrypted only at push time (SPEC §7.4), which means a free /
offline user accumulates ops without ever needing the master encryption key.

This module is pure data shaping: the Op record, payload (de)serialization,
and replay back into a Note. The table schema and the atomic emission live in
core/storage.py, which owns the database handle and the transaction.

Local sequencing vs. server ordering:
- `local_seq` is the client-side emission order (the ops table rowid). It only
  ever means "what order did THIS device produce ops in".
- `server_op_id` is assigned by the server on accept (SPEC §7.2) and stays
  None until then. Push reads `server_op_id IS NULL` to find the backlog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from core.models import Note

OpType = Literal["insert", "update", "delete"]


@dataclass(frozen=True)
class Op:
    """One row of the local op log.

    `payload` is the decoded JSON dict: the complete resulting note row for
    insert/update, or just `{"id": ...}` for delete (SPEC §7.1). `client_ts`
    is the wall-clock UTC moment the op was produced and is the only signal
    the server's last-write-wins rule uses to break ties (SPEC §7.2).
    """

    local_seq: int
    op_type: OpType
    note_id: str
    client_ts: str
    payload: dict
    server_op_id: int | None


def insert_payload(note: Note) -> dict:
    """The complete resulting row for an INSERT op (SPEC §7.1).

    Embedding columns (`embedding_model`, `embedded_at`) are deliberately
    excluded: they are local-derived, recomputable per device, and the server
    must not see them (SPEC §7.6). Each device re-embeds replayed notes itself.
    """
    return {
        "id": note.id,
        "content": note.content,
        "tag": note.tag,
        "created_at": note.created_at.isoformat(),
    }


def delete_payload(note_id: str) -> dict:
    """A DELETE op carries only the target id; client_ts travels in its own
    column (SPEC §7.1)."""
    return {"id": note_id}


def payload_to_note(payload: dict) -> Note:
    """Reconstruct a Note from an insert/update payload, for replay on pull."""
    return Note(
        id=payload["id"],
        content=payload["content"],
        tag=payload.get("tag"),
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def encode_payload(payload: dict) -> str:
    """Serialize a payload for storage / future signing.

    `sort_keys` makes the encoding deterministic so a byte-for-byte identical
    op produces an identical blob (matters once payloads are signed, SPEC
    §7.4). `ensure_ascii=False` keeps non-ASCII content (e.g. 中文) readable
    in the local db and compact on the wire.
    """
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def decode_payload(raw: str) -> dict:
    return json.loads(raw)
