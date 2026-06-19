"""Cryptographic primitives for MakeMarbles sync.

Layout per SPEC §7.3 and §7.4:

- Two key derivations from the same master password. The auth credential
  travels to the server; the encryption key never leaves the device. Each
  uses a different KDF so a brute-force on stored auth hashes does not
  shortcut to the encryption key.
- AES-256-GCM with random 96-bit nonces and AAD-bound metadata for each op
  payload. Random nonces are safe at our op volume; a counter scheme would
  introduce sync-state failure modes (restored backups, fresh devices)
  that random sampling sidesteps.
- Ed25519 per-device keypair for op signatures. Each device signs the
  metadata-blob tuple the server stores; revocation cuts off the public
  key on the server side.

This module is pure: no I/O, no global state, no key storage. Callers
hold key material in memory and decide where (or whether) to persist it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Key derivation parameters. Locked by SPEC §7.3 so the same password can
# be re-derived to the same keys across client versions and platforms.
PBKDF2_ITERATIONS = 600_000
ARGON2_MEMORY_KIB = 64 * 1024  # 64 MiB
ARGON2_TIME_COST = 3
ARGON2_PARALLELISM = 1
KEY_LEN = 32  # bytes; AES-256 wants 32, Ed25519 seed is 32, auth cred is 32

# AEAD parameters.
NONCE_LEN = 12  # AES-GCM canonical nonce length (96 bits)
SALT_LEN = 16   # per-account constant, generated at registration

# Ed25519 sizes.
ED25519_SEED_LEN = 32
ED25519_PUBLIC_LEN = 32
ED25519_SIG_LEN = 64


# ---------- salts ----------


def generate_salt() -> bytes:
    """Cryptographically random 16-byte salt for KDF use.

    Generated once per account at registration and stored on the server
    alongside the account record. Clients fetch it as part of the login
    handshake, before mixing the password in.
    """
    return secrets.token_bytes(SALT_LEN)


# ---------- key derivation ----------


def derive_auth_credential(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 with 600,000 iterations.

    The output bytes are what the client sends to the server on login.
    The server stores a hash of this value (its own choice of hashing
    scheme) and verifies on subsequent logins.
    """
    if len(salt) < SALT_LEN:
        raise ValueError(f"salt must be at least {SALT_LEN} bytes")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def derive_encryption_key(password: str, salt: bytes) -> bytes:
    """Argon2id with 64 MiB / t=3 / p=1.

    Output is the symmetric AES-256-GCM key for every op payload on this
    account. It must never leave the device.
    """
    if len(salt) < SALT_LEN:
        raise ValueError(f"salt must be at least {SALT_LEN} bytes")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_LEN,
        type=Type.ID,
    )


# ---------- authenticated encryption ----------


@dataclass(frozen=True)
class EncryptedBlob:
    """An AES-256-GCM ciphertext bundled with its nonce.

    The GCM authentication tag is appended to `ciphertext` by the
    cryptography library; we do not split it out, so decryption pairs
    `nonce` and `ciphertext` together.
    """

    nonce: bytes
    ciphertext: bytes  # raw ciphertext || 16-byte GCM tag


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> EncryptedBlob:
    """Encrypt under AES-256-GCM with a fresh random 96-bit nonce.

    `aad` (additional authenticated data) is the concatenation of the
    metadata the server stores alongside the blob: per SPEC §7.4 that is
    `account_id || device_id || client_ts` as bytes. AAD is authenticated
    but not encrypted, so swapping any of these fields breaks decryption.
    """
    if len(key) != KEY_LEN:
        raise ValueError(f"key must be {KEY_LEN} bytes, got {len(key)}")
    aead = AESGCM(key)
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = aead.encrypt(nonce, plaintext, aad)
    return EncryptedBlob(nonce=nonce, ciphertext=ct)


def decrypt(key: bytes, blob: EncryptedBlob, aad: bytes) -> bytes:
    """Decrypt an EncryptedBlob; raises cryptography's InvalidTag on any
    tamper of ciphertext, nonce, AAD, or key. The exception is the GCM
    integrity signal; callers should not narrow it without thought."""
    if len(key) != KEY_LEN:
        raise ValueError(f"key must be {KEY_LEN} bytes, got {len(key)}")
    aead = AESGCM(key)
    return aead.decrypt(blob.nonce, blob.ciphertext, aad)


# ---------- device signing keys ----------


@dataclass(frozen=True)
class DeviceKeypair:
    """Per-device Ed25519 keypair.

    `private_seed` is the 32-byte seed (Ed25519's representation). The
    public key is derived from it deterministically; we store both so
    callers do not have to re-derive on every signature.
    """

    private_seed: bytes  # 32 bytes; sensitive
    public_key: bytes    # 32 bytes; safe to share with the server


def generate_device_keypair() -> DeviceKeypair:
    """Generate a fresh Ed25519 keypair locally.

    Called once per device at first sign-in. The seed is written to local
    storage; the public key is registered with the server.
    """
    sk = ed25519.Ed25519PrivateKey.generate()
    seed = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return DeviceKeypair(private_seed=seed, public_key=pub)


def load_private_key(seed: bytes) -> ed25519.Ed25519PrivateKey:
    if len(seed) != ED25519_SEED_LEN:
        raise ValueError(
            f"private seed must be {ED25519_SEED_LEN} bytes, got {len(seed)}"
        )
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def load_public_key(public: bytes) -> ed25519.Ed25519PublicKey:
    if len(public) != ED25519_PUBLIC_LEN:
        raise ValueError(
            f"public key must be {ED25519_PUBLIC_LEN} bytes, got {len(public)}"
        )
    return ed25519.Ed25519PublicKey.from_public_bytes(public)


def sign(private_seed: bytes, message: bytes) -> bytes:
    """Produce an Ed25519 signature over `message`. Returns 64 bytes."""
    return load_private_key(private_seed).sign(message)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return True if the signature is valid, False otherwise.

    Wrapping cryptography's exception-on-fail API into a bool because at
    the verify call site we usually want to branch, not unwind. Callers
    that want the exception can use `load_public_key(...).verify(...)`
    directly.
    """
    try:
        load_public_key(public_key).verify(signature, message)
        return True
    except InvalidSignature:
        return False
