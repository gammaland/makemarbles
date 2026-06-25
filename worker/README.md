# makemarbles-sync

The MakeMarbles zero-knowledge sync server: a TypeScript Cloudflare Worker with
one Durable Object per account. It is a **sealed encrypted relay** — it verifies
device signatures, assigns a gapless monotonic `op_id`, stores opaque
ciphertext, and (later) fans it out to an account's other devices. It never sees
plaintext, note content, note ids, op types, or the note count.

Authoritative design: [`../docs/SPEC.md`](../docs/SPEC.md) §7.9–§7.13 and
[`../docs/adr/2026-06-24-sync-server-architecture.md`](../docs/adr/2026-06-24-sync-server-architecture.md).

## Status

First slice. Implemented:

- `AccountDO` (one Durable Object per account): the gapless `op_id` counter, the
  op store, and a minimal device registry.
- `POST /push` — verify device + Ed25519 signature, clock-skew check (±300 s),
  assign `op_id`, store. Returns `{ op_id }` (the client's `Transport.push`).
- `GET /ops?after=N` — return ops with `op_id > N` in order (pull wire format).
- `POST /devices`, `POST /devices/revoke` — minimal registry for push verification.

Not yet built: the login handshake + JWT edge auth (SPEC §7.11), the `is_pro`
entitlement gate (§7.13), and the live receive-only WebSocket fan-out (§7.9).
Until login lands, endpoints take `account_id` from the request.

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
