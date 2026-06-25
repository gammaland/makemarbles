# makemarbles-sync

The MakeMarbles zero-knowledge sync server: a TypeScript Cloudflare Worker with
one Durable Object per account. It is a **sealed encrypted relay** — it verifies
device signatures, assigns a gapless monotonic `op_id`, stores opaque
ciphertext, and (later) fans it out to an account's other devices. It never sees
plaintext, note content, note ids, op types, or the note count.

Authoritative design: [`../docs/SPEC.md`](../docs/SPEC.md) §7.9–§7.13 and
[`../docs/adr/2026-06-24-sync-server-architecture.md`](../docs/adr/2026-06-24-sync-server-architecture.md).

## Status

Implemented:

- `AccountDO` (one Durable Object per account): the gapless `op_id` counter, the
  op store, and the device registry.
- **Account lifecycle (D1 registry):** `POST /account`, `PUT /account/auth`,
  `GET /account/salt` — register, set `auth_hash`, fetch the per-account salt.
- **Device enrollment (password-gated):** `POST /devices` verifies the presented
  `auth_credential` against the stored hash, then stores the client-generated
  device public key. This is the only routine moment the password is used
  (scheme B, SPEC §7.11).
- `POST /push` — verify device + Ed25519 signature, clock-skew check (±300 s),
  assign `op_id`, store. Returns `{ op_id }` (the client's `Transport.push`).
- `GET /ops?after=N` — return ops with `op_id > N` in order (pull wire format).
- `POST /devices/revoke` — revoke a device; its future ops are rejected.

Not yet built: the **device-request-signature** check on pull / revoke (those
still take `account_id` from the request), the `is_pro` entitlement gate
(§7.13), the live receive-only WebSocket fan-out (§7.9), and the client-side
`marbles login` / `devices` commands.

## Signature compatibility

The server reproduces the client's signing input
(`core/wire.py::_signing_input` = `device_id || client_ts || blob`) byte-for-byte
in `src/crypto.ts`. `test/golden.json` is a vector signed by the Python client;
the test suite asserts the worker verifies it, guarding cross-language drift.

## Develop

```bash
npm install
npm test         # vitest in the real workerd runtime (Miniflare)
npm run typecheck
npm run dev      # local wrangler dev
```
