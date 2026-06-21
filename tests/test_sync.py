"""Tests for the client push loop (core/sync.py).

A FakeTransport stands in for the server + a peer: it verifies and decrypts
each envelope exactly as a real peer would, assigns a monotonic op_id, and
records the recovered ops. This proves the whole push pipeline end to end —
storage backlog -> wire seal -> transport -> mark synced — with no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import crypto
from core.models import Note
from core.storage import Storage
from core.sync import push_backlog
from core.wire import DecodedOp, Identity, unpack_and_verify

ACCOUNT = "acct_test"
DEVICE = "dev_test"
ENC_KEY = bytes(range(32))


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "test.db")


@pytest.fixture
def keypair():
    return crypto.generate_device_keypair()


@pytest.fixture
def identity(keypair) -> Identity:
    return Identity(
        account_id=ACCOUNT,
        device_id=DEVICE,
        enc_key=ENC_KEY,
        device_seed=keypair.private_seed,
    )


class FakeTransport:
    """Verifies + decrypts like a peer; assigns op_ids like the server."""

    def __init__(self, account_id: str, device_pubkey: bytes, enc_key: bytes):
        self.account_id = account_id
        self.device_pubkey = device_pubkey
        self.enc_key = enc_key
        self.received: list[DecodedOp] = []
        self._next_op_id = 1000

    def push(self, envelope: dict) -> int:
        decoded = unpack_and_verify(
            envelope, self.account_id, self.device_pubkey, self.enc_key
        )
        self.received.append(decoded)
        op_id = self._next_op_id
        self._next_op_id += 1
        return op_id


@pytest.fixture
def transport(keypair) -> FakeTransport:
    return FakeTransport(ACCOUNT, keypair.public_key, ENC_KEY)


def test_push_backlog_delivers_all_ops_in_order(storage, identity, transport):
    a = storage.add(Note(content="first", tag="x"))
    b = storage.add(Note(content="second"))
    storage.delete(a)

    pushed = push_backlog(storage, identity, transport)

    assert pushed == 3
    types = [d.op_type for d in transport.received]
    assert types == ["insert", "insert", "delete"]
    # Content survives the round trip through the transport.
    assert transport.received[0].payload["content"] == "first"
    assert transport.received[2].payload == {"id": a}
    assert b  # silence unused-var linters; b is the second note id


def test_push_backlog_marks_ops_synced(storage, identity, transport):
    storage.add(Note(content="a"))
    storage.add(Note(content="b"))
    assert len(storage.unsynced_ops()) == 2

    push_backlog(storage, identity, transport)

    assert storage.unsynced_ops() == []  # backlog drained
    assert storage.ops_count() == 2      # but ops remain in the log, acknowledged


def test_second_push_is_a_noop(storage, identity, transport):
    storage.add(Note(content="a"))
    assert push_backlog(storage, identity, transport) == 1
    # Nothing new to send; we must not re-push an already-acknowledged op.
    assert push_backlog(storage, identity, transport) == 0
    assert len(transport.received) == 1


def test_new_ops_after_a_sync_form_the_next_backlog(storage, identity, transport):
    storage.add(Note(content="a"))
    push_backlog(storage, identity, transport)
    storage.add(Note(content="b"))

    pushed = push_backlog(storage, identity, transport)

    assert pushed == 1
    assert transport.received[-1].payload["content"] == "b"


def test_batch_limits_how_many_are_pushed(storage, identity, transport):
    for i in range(5):
        storage.add(Note(content=f"n{i}"))

    pushed = push_backlog(storage, identity, transport, batch=2)

    assert pushed == 2
    assert len(storage.unsynced_ops()) == 3  # remainder still pending
