"""Tests for core.crypto.

Coverage strategy:
- Round-trip behavior (encrypt/decrypt, sign/verify, derive deterministically).
- Tamper detection on every input axis (key, nonce, ciphertext, AAD,
  message, signature, public key).
- Parameter sensitivity (different salt or password => different key).
- Length / shape validation at module boundaries.

Argon2id uses 64 MiB of memory by spec; the tests use a reduced parameter
set where the parameter values themselves are not under test, so the suite
stays fast. Where the actual values matter (the locked SPEC parameters),
we exercise them in slow-marked tests.
"""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from core import crypto


# ---------- salt ----------


def test_generate_salt_length_matches_constant():
    assert len(crypto.generate_salt()) == crypto.SALT_LEN


def test_generate_salt_returns_different_values():
    salts = {crypto.generate_salt() for _ in range(8)}
    assert len(salts) == 8


# ---------- PBKDF2 auth credential ----------


def test_derive_auth_credential_is_deterministic():
    salt = b"\x00" * crypto.SALT_LEN
    a = crypto.derive_auth_credential("hunter2", salt)
    b = crypto.derive_auth_credential("hunter2", salt)
    assert a == b
    assert len(a) == crypto.KEY_LEN


def test_derive_auth_credential_changes_with_salt():
    a = crypto.derive_auth_credential("pw", b"\x00" * crypto.SALT_LEN)
    b = crypto.derive_auth_credential("pw", b"\x01" * crypto.SALT_LEN)
    assert a != b


def test_derive_auth_credential_changes_with_password():
    salt = b"\x00" * crypto.SALT_LEN
    a = crypto.derive_auth_credential("alpha", salt)
    b = crypto.derive_auth_credential("beta", salt)
    assert a != b


def test_derive_auth_credential_rejects_short_salt():
    with pytest.raises(ValueError):
        crypto.derive_auth_credential("pw", b"\x00" * (crypto.SALT_LEN - 1))


# ---------- Argon2id encryption key ----------


def test_derive_encryption_key_is_deterministic():
    salt = b"\x00" * crypto.SALT_LEN
    a = crypto.derive_encryption_key("hunter2", salt)
    b = crypto.derive_encryption_key("hunter2", salt)
    assert a == b
    assert len(a) == crypto.KEY_LEN


def test_derive_encryption_key_differs_from_auth_credential():
    """Same password, same salt under different KDFs must produce
    different keys. Otherwise a leaked auth hash would equal the
    encryption key."""
    salt = b"\x00" * crypto.SALT_LEN
    auth = crypto.derive_auth_credential("pw", salt)
    enc = crypto.derive_encryption_key("pw", salt)
    assert auth != enc


def test_derive_encryption_key_changes_with_salt():
    a = crypto.derive_encryption_key("pw", b"\x00" * crypto.SALT_LEN)
    b = crypto.derive_encryption_key("pw", b"\x01" * crypto.SALT_LEN)
    assert a != b


# ---------- AES-256-GCM encrypt / decrypt ----------


def test_encrypt_decrypt_roundtrip():
    key = b"k" * crypto.KEY_LEN
    blob = crypto.encrypt(key, b"hello marble", aad=b"acct|dev|ts")
    assert crypto.decrypt(key, blob, aad=b"acct|dev|ts") == b"hello marble"


def test_encrypt_uses_fresh_nonce_each_call():
    key = b"k" * crypto.KEY_LEN
    a = crypto.encrypt(key, b"same plaintext", aad=b"aad")
    b = crypto.encrypt(key, b"same plaintext", aad=b"aad")
    assert a.nonce != b.nonce
    assert a.ciphertext != b.ciphertext


def test_decrypt_with_wrong_key_raises():
    blob = crypto.encrypt(b"k" * crypto.KEY_LEN, b"x", b"aad")
    with pytest.raises(InvalidTag):
        crypto.decrypt(b"j" * crypto.KEY_LEN, blob, b"aad")


def test_decrypt_with_tampered_ciphertext_raises():
    blob = crypto.encrypt(b"k" * crypto.KEY_LEN, b"x", b"aad")
    bad = crypto.EncryptedBlob(
        nonce=blob.nonce, ciphertext=blob.ciphertext[:-1] + bytes([blob.ciphertext[-1] ^ 1])
    )
    with pytest.raises(InvalidTag):
        crypto.decrypt(b"k" * crypto.KEY_LEN, bad, b"aad")


def test_decrypt_with_tampered_aad_raises():
    """AAD authentication is the load-bearing claim: swapping the
    account_id || device_id || client_ts metadata must fail decryption
    even when nonce + ciphertext + key are otherwise valid."""
    key = b"k" * crypto.KEY_LEN
    blob = crypto.encrypt(key, b"payload", aad=b"acct1|dev1|ts1")
    with pytest.raises(InvalidTag):
        crypto.decrypt(key, blob, aad=b"acct1|dev2|ts1")


def test_decrypt_with_tampered_nonce_raises():
    key = b"k" * crypto.KEY_LEN
    blob = crypto.encrypt(key, b"x", b"aad")
    bad = crypto.EncryptedBlob(
        nonce=bytes([blob.nonce[0] ^ 1]) + blob.nonce[1:],
        ciphertext=blob.ciphertext,
    )
    with pytest.raises(InvalidTag):
        crypto.decrypt(key, bad, b"aad")


def test_encrypt_rejects_wrong_length_key():
    with pytest.raises(ValueError):
        crypto.encrypt(b"too short", b"x", b"aad")


# ---------- Ed25519 device keypair ----------


def test_generate_device_keypair_returns_correct_sizes():
    kp = crypto.generate_device_keypair()
    assert len(kp.private_seed) == crypto.ED25519_SEED_LEN
    assert len(kp.public_key) == crypto.ED25519_PUBLIC_LEN


def test_generate_device_keypair_is_unique_per_call():
    kp1 = crypto.generate_device_keypair()
    kp2 = crypto.generate_device_keypair()
    assert kp1.private_seed != kp2.private_seed
    assert kp1.public_key != kp2.public_key


def test_sign_verify_roundtrip():
    kp = crypto.generate_device_keypair()
    sig = crypto.sign(kp.private_seed, b"some op blob")
    assert len(sig) == crypto.ED25519_SIG_LEN
    assert crypto.verify(kp.public_key, b"some op blob", sig) is True


def test_verify_rejects_tampered_message():
    kp = crypto.generate_device_keypair()
    sig = crypto.sign(kp.private_seed, b"original message")
    assert crypto.verify(kp.public_key, b"different message", sig) is False


def test_verify_rejects_tampered_signature():
    kp = crypto.generate_device_keypair()
    sig = crypto.sign(kp.private_seed, b"message")
    bad = bytes([sig[0] ^ 1]) + sig[1:]
    assert crypto.verify(kp.public_key, b"message", bad) is False


def test_verify_rejects_signature_from_other_key():
    kp_a = crypto.generate_device_keypair()
    kp_b = crypto.generate_device_keypair()
    sig = crypto.sign(kp_a.private_seed, b"message")
    assert crypto.verify(kp_b.public_key, b"message", sig) is False


def test_sign_rejects_wrong_length_seed():
    with pytest.raises(ValueError):
        crypto.sign(b"too short", b"message")


def test_verify_rejects_wrong_length_public_key():
    sig = crypto.sign(crypto.generate_device_keypair().private_seed, b"m")
    with pytest.raises(ValueError):
        crypto.verify(b"too short", b"m", sig)


# ---------- end-to-end op envelope smoke ----------


def test_full_op_envelope_roundtrip():
    """Compose what the sync transport will do: derive key from password,
    sign and encrypt a payload bound to AAD, then verify and decrypt on
    the other side. Failure here means the layered crypto contracts do
    not line up the way SPEC §7.4 and §7.5 assume."""
    salt = crypto.generate_salt()
    key = crypto.derive_encryption_key("hunter2", salt)
    kp = crypto.generate_device_keypair()

    aad = b"account-42|device-7|2026-06-19T00:00:00Z"
    plaintext = b'{"op": "INSERT", "note_id": "01KV..."}'
    blob = crypto.encrypt(key, plaintext, aad)

    signature = crypto.sign(
        kp.private_seed, b"device-7" + b"|" + b"2026-06-19T00:00:00Z" + b"|" + blob.ciphertext
    )

    # ---- server stores blob + signature + plaintext metadata ----

    # ---- another client (or same client later) verifies + decrypts ----
    sig_ok = crypto.verify(
        kp.public_key,
        b"device-7" + b"|" + b"2026-06-19T00:00:00Z" + b"|" + blob.ciphertext,
        signature,
    )
    assert sig_ok is True
    recovered = crypto.decrypt(key, blob, aad)
    assert recovered == plaintext
