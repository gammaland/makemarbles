"""Client login + device-identity tests (SPEC §7.11).

Drives the full login/enroll flow against an in-memory fake that mimics the
worker's account + enrollment logic, so the client orchestration is verified
end to end with no network and no running server.
"""

from __future__ import annotations

import base64

import pytest

from core import crypto
from core.identity import DeviceIdentity, perform_login
from core.remote import HttpResponse, SyncClient, SyncError


class FakeServer:
    """In-memory stand-in for the worker (SPEC §7.10/§7.11 logic).

    Implements the `Http` protocol. Stores accounts and the (account -> device
    pubkeys) registry; gates enrollment on the auth_credential hash exactly as
    the real server does.
    """

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}  # email -> {account_id, salt, auth_hash}
        self.devices: dict[str, bytes] = {}  # device_id -> pubkey

    @staticmethod
    def _hash(auth_credential_b64: str) -> str:
        import hashlib

        return hashlib.sha256(base64.b64decode(auth_credential_b64)).hexdigest()

    def request(self, method: str, url: str, body: dict | None) -> HttpResponse:
        path = url.split("//", 1)[-1].split("/", 1)[1]  # strip scheme+host
        path = "/" + path

        if method == "GET" and path.startswith("/account/salt"):
            email = path.split("email=", 1)[1]
            acct = self.accounts.get(email)
            if not acct:
                return HttpResponse(404, {"error": "unknown account"})
            return HttpResponse(200, {"salt": acct["salt"]})

        if method == "POST" and path == "/account":
            email = body["email"]
            if email in self.accounts:
                return HttpResponse(409, {"error": "email already registered"})
            salt_b64 = base64.b64encode(crypto.generate_salt()).decode()
            acct = {"account_id": "acc_" + str(len(self.accounts)), "salt": salt_b64, "auth_hash": None}
            self.accounts[email] = acct
            return HttpResponse(200, {"account_id": acct["account_id"], "salt": salt_b64})

        if method == "PUT" and path == "/account/auth":
            acct = self.accounts.get(body["email"])
            if not acct:
                return HttpResponse(404, {"error": "unknown account"})
            acct["auth_hash"] = self._hash(body["auth_credential"])
            return HttpResponse(200, {"status": "ok"})

        if method == "POST" and path == "/devices":
            acct = self.accounts.get(body["email"])
            if not acct or not acct["auth_hash"]:
                return HttpResponse(403, {"error": "bad credentials"})
            if self._hash(body["auth_credential"]) != acct["auth_hash"]:
                return HttpResponse(403, {"error": "bad credentials"})
            self.devices[body["device_id"]] = base64.b64decode(body["pubkey"])
            return HttpResponse(200, {"account_id": acct["account_id"], "device_id": body["device_id"]})

        return HttpResponse(404, {"error": "not found"})


def _client() -> tuple[SyncClient, FakeServer]:
    server = FakeServer()
    return SyncClient("http://test", http=server), server


def test_register_then_enroll_persists_identity(tmp_path):
    client, server = _client()
    identity = perform_login(client, "a@test", "hunter2", register_if_missing=True)

    assert identity.email == "a@test"
    assert identity.account_id.startswith("acc_")
    assert identity.device_id.startswith("dev_")
    # The device's public key reached the registry, derived from the saved seed.
    assert server.devices[identity.device_id] == crypto.public_key_from_seed(identity.device_seed)

    path = tmp_path / "identity.json"
    identity.save(path)
    loaded = DeviceIdentity.load(path)
    assert loaded == identity


def test_login_without_register_when_missing_raises():
    client, _ = _client()
    with pytest.raises(SyncError) as ei:
        perform_login(client, "ghost@test", "pw")
    assert ei.value.status == 404


def test_wrong_password_on_existing_account_is_403():
    client, _ = _client()
    perform_login(client, "b@test", "right-password", register_if_missing=True)
    with pytest.raises(SyncError) as ei:
        perform_login(client, "b@test", "wrong-password")
    assert ei.value.status == 403


def test_relogin_same_device_keeps_seed():
    client, _ = _client()
    first = perform_login(client, "c@test", "pw", register_if_missing=True)
    again = perform_login(client, "c@test", "pw", prior=first)
    assert again.device_seed == first.device_seed
    assert again.device_id == first.device_id


def test_identity_file_is_owner_only(tmp_path):
    client, _ = _client()
    identity = perform_login(client, "d@test", "pw", register_if_missing=True)
    path = tmp_path / "identity.json"
    identity.save(path)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_to_wire_identity_carries_supplied_key():
    client, _ = _client()
    identity = perform_login(client, "e@test", "pw", register_if_missing=True)
    k = b"\x01" * 32
    wire_id = identity.to_wire_identity(k)
    assert wire_id.enc_key == k
    assert wire_id.account_id == identity.account_id
    assert wire_id.device_seed == identity.device_seed
