"""Local device identity for sync (SPEC §7.3, §7.11).

After login a device holds a small bundle on disk: its account, its server-
assigned identifiers, the per-account salt, and its own Ed25519 **private**
seed. The master encryption key K is deliberately NOT stored — it is re-derived
from the password whenever a sync actually needs to seal/open ops, so a stolen
identity file alone cannot decrypt anything.

The file lives at ~/.marbles/identity.json with 0600 permissions. Secret and
binary fields are base64-encoded.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from ulid import ULID

from core import crypto
from core.remote import SyncClient
from core.wire import Identity

DEFAULT_IDENTITY_PATH = Path.home() / ".marbles" / "identity.json"


@dataclass(frozen=True)
class DeviceIdentity:
    """The persisted, non-K credential bundle for a logged-in device."""

    account_id: str
    email: str
    device_id: str
    device_seed: bytes  # Ed25519 private seed — secret
    salt: bytes         # per-account, used to re-derive auth_credential and K

    def save(self, path: Path = DEFAULT_IDENTITY_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "account_id": self.account_id,
            "email": self.email,
            "device_id": self.device_id,
            "device_seed": base64.b64encode(self.device_seed).decode(),
            "salt": base64.b64encode(self.salt).decode(),
        }
        # Write 0600 from the start so the seed is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)

    @classmethod
    def load(cls, path: Path = DEFAULT_IDENTITY_PATH) -> "DeviceIdentity | None":
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            account_id=raw["account_id"],
            email=raw["email"],
            device_id=raw["device_id"],
            device_seed=base64.b64decode(raw["device_seed"]),
            salt=base64.b64decode(raw["salt"]),
        )

    def to_wire_identity(self, enc_key: bytes) -> Identity:
        """Build the in-memory bundle the push/pull codec needs, with the
        password-derived K supplied at call time (never persisted)."""
        return Identity(
            account_id=self.account_id,
            device_id=self.device_id,
            enc_key=enc_key,
            device_seed=self.device_seed,
        )


def perform_login(
    client: SyncClient,
    email: str,
    password: str,
    *,
    prior: DeviceIdentity | None = None,
    register_if_missing: bool = False,
) -> DeviceIdentity:
    """Run the full login/enroll flow (SPEC §7.11) and return the identity.

    Fetches the salt (or registers a new account when missing and allowed),
    derives `auth_credential` locally, and enrolls this device's public key.
    The encryption key K is never produced here — enrollment proving the
    password is correct is enough; K is derived later, at sync time.

    Reuses `prior.device_seed` when re-logging in on the same device so the
    device keeps a stable identity; otherwise generates a fresh keypair.
    Raises `SyncError` (403) on a wrong password for an existing account.
    """
    if client.account_exists(email):
        salt = client.get_salt(email)
    elif register_if_missing:
        _, salt = client.create_account(email)
        auth_credential = crypto.derive_auth_credential(password, salt)
        client.set_auth(email, auth_credential)
    else:
        from core.remote import SyncError

        raise SyncError(404, "no account for that email; pass register_if_missing")

    auth_credential = crypto.derive_auth_credential(password, salt)

    if prior is not None and prior.email == email:
        device_seed = prior.device_seed
        device_id = prior.device_id
        public_key = crypto.public_key_from_seed(device_seed)
    else:
        keypair = crypto.generate_device_keypair()
        device_seed = keypair.private_seed
        public_key = keypair.public_key
        device_id = "dev_" + str(ULID())

    account_id = client.enroll_device(email, auth_credential, device_id, public_key)

    return DeviceIdentity(
        account_id=account_id,
        email=email,
        device_id=device_id,
        device_seed=device_seed,
        salt=salt,
    )
