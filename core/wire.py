"""Wire codec for sync push/pull (SPEC §7.4 + §7.5) — the client side.

This is where the local op log (core/oplog.py) first meets the cryptographic
primitives (core/crypto.py). One local Op becomes an encrypted, signed
envelope the server relays blindly, and back again on receipt:

    Op  --pack_push-->  {account_id, device_id, client_ts, blob, signature}
        <--unpack_and_verify--

The op payload AND its type are AES-256-GCM encrypted under the account key, so
the server sees only ciphertext plus routing metadata (SPEC §7.6: it cannot
learn the op type, the note_id, the content, or the count). Each envelope is
Ed25519-signed by the originating device; pulling peers verify the signature
before trusting a relayed op (we do not trust the server to deliver an op no
device ever signed).

Pure codec: no I/O, no storage, no network. The push loop that drains the
backlog over a transport lives in core/sync.py.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from core import crypto, oplog
from core.crypto import EncryptedBlob
from core.oplog import Op, OpType


class BadSignature(Exception):
    """A relayed envelope's Ed25519 signature did not verify against the
    registered device public key. The op is rejected before any attempt to
    decrypt it."""


@dataclass(frozen=True)
class Identity:
    """The in-memory credential bundle a logged-in device holds.

    Produced by the login handshake (SPEC §7.3, not yet built); tests and the
    push loop construct it directly. `enc_key` and `device_seed` are secret —
    they are used to seal/sign envelopes but are never themselves serialized
    onto the wire.
    """

    account_id: str
    device_id: str
    enc_key: bytes      # 32-byte AES-256 key (Argon2id-derived); never leaves device
    device_seed: bytes  # 32-byte Ed25519 seed; never leaves device


@dataclass(frozen=True)
class DecodedOp:
    """A verified, decrypted op recovered from an envelope, ready to replay."""

    op_type: OpType
    payload: dict


def _aad(account_id: str, device_id: str, client_ts: str) -> bytes:
    """Additional authenticated data bound into the GCM tag (SPEC §7.4):
    `account_id || device_id || client_ts` as UTF-8. AAD is authenticated but
    not encrypted, so a malicious server that re-points an envelope at a
    different account or device breaks the tag and decryption fails.

    The three fields are server-generated, fixed-shape tokens (ULID-ish ids and
    an ISO-8601 timestamp), so plain concatenation is unambiguous here.
    Length-prefixing is a noted v0.3 hardening, not a present vulnerability.
    """
    return (account_id + device_id + client_ts).encode("utf-8")


def _signing_input(device_id: str, client_ts: str, blob: bytes) -> bytes:
    """Bytes the device signs (SPEC §7.5): `device_id || client_ts || blob`,
    where `blob` is the raw `nonce || ciphertext || gcm_tag`. Signing the raw
    blob rather than its base64 text avoids transport-encoding ambiguity."""
    return device_id.encode("utf-8") + client_ts.encode("utf-8") + blob


def _plaintext(op: Op) -> bytes:
    """The bytes that actually get encrypted: the op type and its payload.

    Both live inside the ciphertext, never in metadata, because the server must
    not be able to observe the op type or the note_id (SPEC §7.6).
    """
    obj = {"type": op.op_type, "payload": op.payload}
    return oplog.encode_payload(obj).encode("utf-8")


def pack_push(op: Op, identity: Identity) -> dict:
    """Seal one local op into a push envelope (SPEC §7.5).

    Encrypt under the account key with AAD-bound metadata, then sign the
    resulting blob with the device key. The returned dict is JSON-ready; `blob`
    and `signature` are base64.
    """
    client_ts = op.client_ts
    aad = _aad(identity.account_id, identity.device_id, client_ts)
    eb = crypto.encrypt(identity.enc_key, _plaintext(op), aad)
    blob = eb.nonce + eb.ciphertext  # ciphertext already carries the GCM tag
    sig = crypto.sign(
        identity.device_seed, _signing_input(identity.device_id, client_ts, blob)
    )
    return {
        "account_id": identity.account_id,
        "device_id": identity.device_id,
        "client_ts": client_ts,
        "blob": base64.b64encode(blob).decode("ascii"),
        "signature": base64.b64encode(sig).decode("ascii"),
    }


def unpack_and_verify(
    envelope: dict, account_id: str, device_pubkey: bytes, enc_key: bytes
) -> DecodedOp:
    """Verify and decrypt a relayed envelope (SPEC §7.4/§7.5).

    `account_id` is the receiving client's own account (the pull wire format
    omits it; the client supplies it to rebuild the AAD). `device_pubkey` is
    the originating device's registered Ed25519 key.

    Order matters: verify the signature first (cheap, and we refuse to feed an
    unauthenticated blob to the decryptor), then GCM-decrypt. A swapped
    `account_id` passes signature check but fails the GCM tag, because AAD binds
    the account; a tampered blob/client_ts/device_id fails the signature.
    """
    device_id = envelope["device_id"]
    client_ts = envelope["client_ts"]
    blob = base64.b64decode(envelope["blob"])
    sig = base64.b64decode(envelope["signature"])

    if not crypto.verify(device_pubkey, _signing_input(device_id, client_ts, blob), sig):
        raise BadSignature(
            f"signature does not verify for device {device_id!r}"
        )

    nonce, ciphertext = blob[: crypto.NONCE_LEN], blob[crypto.NONCE_LEN :]
    aad = _aad(account_id, device_id, client_ts)
    plaintext = crypto.decrypt(
        enc_key, EncryptedBlob(nonce=nonce, ciphertext=ciphertext), aad
    )
    obj = oplog.decode_payload(plaintext.decode("utf-8"))
    return DecodedOp(op_type=obj["type"], payload=obj["payload"])
