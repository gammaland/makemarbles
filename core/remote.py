"""HTTP client for the sync server (SPEC §7.11).

A thin, dependency-light wrapper over the account + enrollment endpoints. The
HTTP layer is injectable (an `Http` protocol) so the login flow can be tested
end to end against an in-memory fake server with no network. Bytes-valued
fields (salt, auth_credential, device public key) cross the wire base64-encoded;
this module decodes/encodes at the boundary so callers deal in raw `bytes`.

WebSocket live sync and the device-request-signed pull endpoints land later;
this slice covers exactly what `marbles login` needs.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class SyncError(Exception):
    """A non-2xx response from the sync server. `status` is the HTTP code so
    callers can distinguish 404 (no such account) and 403 (bad credentials)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict


class Http(Protocol):
    """Whatever can make a JSON request and return status + decoded body."""

    def request(self, method: str, url: str, body: dict | None) -> HttpResponse: ...


class UrllibHttp:
    """Default `Http` built on the stdlib, so sync adds no new dependency."""

    def request(self, method: str, url: str, body: dict | None) -> HttpResponse:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"content-type": "application/json", "user-agent": "makemarbles/0.2"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return HttpResponse(status=resp.status, body=json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"error": raw}
            return HttpResponse(status=e.code, body=parsed)


class SyncClient:
    """Typed access to the sync server's account + enrollment endpoints."""

    def __init__(self, base_url: str, http: Http | None = None):
        self.base = base_url.rstrip("/")
        self.http = http or UrllibHttp()

    def _ok(self, resp: HttpResponse) -> dict:
        if resp.status // 100 != 2:
            raise SyncError(resp.status, resp.body.get("error", "request failed"))
        return resp.body

    def account_exists(self, email: str) -> bool:
        """True if an account exists for the email (via the salt lookup)."""
        resp = self.http.request("GET", f"{self.base}/account/salt?email={email}", None)
        if resp.status == 404:
            return False
        self._ok(resp)
        return True

    def get_salt(self, email: str) -> bytes:
        resp = self.http.request("GET", f"{self.base}/account/salt?email={email}", None)
        return base64.b64decode(self._ok(resp)["salt"])

    def create_account(self, email: str) -> tuple[str, bytes]:
        """Register a new account; returns (account_id, salt)."""
        resp = self.http.request("POST", f"{self.base}/account", {"email": email})
        body = self._ok(resp)
        return body["account_id"], base64.b64decode(body["salt"])

    def set_auth(self, email: str, auth_credential: bytes) -> None:
        resp = self.http.request(
            "PUT",
            f"{self.base}/account/auth",
            {"email": email, "auth_credential": base64.b64encode(auth_credential).decode()},
        )
        self._ok(resp)

    def enroll_device(
        self, email: str, auth_credential: bytes, device_id: str, pubkey: bytes
    ) -> str:
        """Enroll a device's public key (password-gated); returns account_id."""
        resp = self.http.request(
            "POST",
            f"{self.base}/devices",
            {
                "email": email,
                "auth_credential": base64.b64encode(auth_credential).decode(),
                "device_id": device_id,
                "pubkey": base64.b64encode(pubkey).decode(),
            },
        )
        return self._ok(resp)["account_id"]
