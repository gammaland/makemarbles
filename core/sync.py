"""Client sync orchestration (SPEC §7.5) — the push loop.

Drains the local op backlog over a transport: each unsynced op is packed into
an encrypted, signed envelope (core/wire.py) and handed to the transport, which
returns the server-assigned op_id. That op_id is recorded locally so the op
drops out of the backlog and is never pushed twice.

The transport is an interface, not a concrete client. The real implementation
(WebSocket while online, `GET /ops?after=` for catch-up) lands with the
Cloudflare Durable Objects server; tests drive a fake that verifies and
decrypts exactly as a peer would. This keeps the whole push pipeline testable
with no network and no server.
"""

from __future__ import annotations

from typing import Protocol

from core import wire
from core.storage import Storage
from core.wire import Identity


class Transport(Protocol):
    """Whatever can ship a push envelope to the server and report the
    server-assigned op_id back. Synchronous by design — there is no background
    daemon (CLAUDE.md design principle 5)."""

    def push(self, envelope: dict) -> int:
        """Send one envelope; return the monotonic server op_id assigned to it."""
        ...


def push_backlog(
    storage: Storage,
    identity: Identity,
    transport: Transport,
    *,
    batch: int | None = None,
) -> int:
    """Push every unsynced op in local emission order; return the count pushed.

    Ops go out oldest-first (`local_seq` ascending) so the server assigns
    `op_id`s in the same order the device produced them. Each op is marked
    synced immediately after the transport accepts it, so an interruption
    mid-loop simply leaves the remaining ops in the backlog for next time —
    the operation is naturally resumable and at-least-once safe (a duplicate
    delivery is harmless: the server's op_id is authoritative and replay is
    idempotent under last-write-wins, SPEC §7.2).
    """
    pushed = 0
    for op in storage.unsynced_ops(limit=batch):
        envelope = wire.pack_push(op, identity)
        server_op_id = transport.push(envelope)
        storage.mark_op_synced(op.local_seq, server_op_id)
        pushed += 1
    return pushed
