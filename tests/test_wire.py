"""Unit tests for the sync wire codec (core/wire.py).

Exercises the full seal/open round trip plus the tamper axes that the GCM tag
and the Ed25519 signature are each responsible for. No storage, no network.
"""

from __future__ import annotations

import base64

import pytest

from core import crypto
from core.oplog import Op
from core.wire import (
    BadSignature,
    DecodedOp,
    Identity,
    pack_push,
    unpack_and_verify,
)

ACCOUNT = "acct_01HXYZ"
DEVICE = "dev_01ABCD"
ENC_KEY = bytes(range(32))  # fixed 32-byte AES-256 key for determinism


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


def _insert_op() -> Op:
    return Op(
        local_seq=1,
        op_type="insert",
        note_id="note_01",
        client_ts="2026-06-21T12:00:00+00:00",
        payload={"id": "note_01", "content": "买 marbles 的域名", "tag": "biz",
                 "created_at": "2026-06-21T12:00:00+00:00"},
        server_op_id=None,
    )


def _delete_op() -> Op:
    return Op(
        local_seq=2,
        op_type="delete",
        note_id="note_01",
        client_ts="2026-06-21T13:00:00+00:00",
        payload={"id": "note_01"},
        server_op_id=None,
    )


# ---------- round trip ----------


def test_insert_op_round_trips(identity, keypair):
    op = _insert_op()
    env = pack_push(op, identity)
    decoded = unpack_and_verify(env, ACCOUNT, keypair.public_key, ENC_KEY)
    assert decoded == DecodedOp(op_type="insert", payload=op.payload)


def test_delete_op_round_trips(identity, keypair):
    op = _delete_op()
    decoded = unpack_and_verify(
        pack_push(op, identity), ACCOUNT, keypair.public_key, ENC_KEY
    )
    assert decoded.op_type == "delete"
    assert decoded.payload == {"id": "note_01"}


# ---------- what the server can and cannot see (SPEC §7.6) ----------


def test_envelope_metadata_is_only_routing_fields(identity):
    env = pack_push(_insert_op(), identity)
    assert set(env) == {"account_id", "device_id", "client_ts", "blob", "signature"}
    # The op type is NOT in the clear — the server cannot tell insert from delete.
    assert "insert" not in {env["account_id"], env["device_id"], env["client_ts"]}


def test_ciphertext_hides_content_and_type(identity):
    env = pack_push(_insert_op(), identity)
    raw = base64.b64decode(env["blob"])
    # None of the secret material leaks into the blob in recoverable form.
    assert "买 marbles 的域名".encode("utf-8") not in raw
    assert b"insert" not in raw
    assert b"note_01" not in raw


# ---------- signature responsibility ----------


def test_tampered_blob_fails_signature(identity, keypair):
    env = pack_push(_insert_op(), identity)
    raw = bytearray(base64.b64decode(env["blob"]))
    raw[-1] ^= 0x01  # flip one bit of the ciphertext/tag
    env["blob"] = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(BadSignature):
        unpack_and_verify(env, ACCOUNT, keypair.public_key, ENC_KEY)


def test_wrong_device_pubkey_fails_signature(identity):
    env = pack_push(_insert_op(), identity)
    other = crypto.generate_device_keypair()
    with pytest.raises(BadSignature):
        unpack_and_verify(env, ACCOUNT, other.public_key, ENC_KEY)


def test_tampered_client_ts_fails_signature(identity, keypair):
    env = pack_push(_insert_op(), identity)
    env["client_ts"] = "2026-06-21T12:00:01+00:00"  # signature covers client_ts
    with pytest.raises(BadSignature):
        unpack_and_verify(env, ACCOUNT, keypair.public_key, ENC_KEY)


def test_tampered_device_id_fails_signature(identity, keypair):
    env = pack_push(_insert_op(), identity)
    env["device_id"] = "dev_evil"
    with pytest.raises(BadSignature):
        unpack_and_verify(env, ACCOUNT, keypair.public_key, ENC_KEY)


# ---------- AAD responsibility (signature passes, GCM tag catches it) ----------


def test_wrong_account_passes_signature_but_fails_gcm(identity, keypair):
    # account_id is NOT in the signed input, but it IS in the AAD. A server that
    # re-points the envelope at another account survives signature verification
    # and dies at the GCM tag — exactly the boundary AAD exists to defend.
    env = pack_push(_insert_op(), identity)
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        unpack_and_verify(env, "acct_OTHER", keypair.public_key, ENC_KEY)


def test_wrong_enc_key_fails_gcm(identity, keypair):
    env = pack_push(_insert_op(), identity)
    from cryptography.exceptions import InvalidTag

    wrong_key = bytes([(b ^ 0xFF) for b in ENC_KEY])
    with pytest.raises(InvalidTag):
        unpack_and_verify(env, ACCOUNT, keypair.public_key, wrong_key)


# ---------- nonce freshness ----------


def test_same_op_seals_to_different_blobs(identity):
    # Random 96-bit nonce per encrypt => two seals of the same op differ, so the
    # server cannot detect repeated content by comparing ciphertexts.
    op = _insert_op()
    a = pack_push(op, identity)["blob"]
    b = pack_push(op, identity)["blob"]
    assert a != b
