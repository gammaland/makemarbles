# ADR 2026-06-24: Sync Server Architecture (Phase 2)

**Status:** Accepted
**Date:** 2026-06-24
**Supersedes:** none
**Related:** `docs/adr/2026-06-03-product-positioning.md`, `docs/SPEC.md` §7

## 1. Context

Phase 2 adds optional paid sync across a user's devices. The cryptographic
client is already shipped and frozen: `core/crypto.py` (KDFs, AES-256-GCM,
Ed25519), `core/wire.py` (`pack_push` / `unpack_and_verify`, the envelope
format), `core/oplog.py` + the local `ops` table, and `core/sync.py`
(`push_backlog` over a `Transport` protocol). SPEC §7.1–§7.8 fix the wire
format, the zero-knowledge threat model, the two-KDF identity scheme, row-level
last-write-wins conflict resolution, and the free/Pro split.

What is *not* yet decided is the **server**: where ops live, how `op_id` is
assigned, the API surface, how requests are authenticated, and how the Pro gate
is enforced. This ADR records those decisions and the alternatives weighed. The
authoritative shape that results lives in SPEC §7.9–§7.13.

The server's job is deliberately small (SPEC §7, threat model): it is a **sealed
encrypted relay**. It never sees plaintext, content, note ids, op types, or the
note count. It verifies device signatures, assigns a monotonic `op_id`, stores
the ciphertext, and fans it out to the account's other devices.

## 2. Hard constraints (inherited, non-negotiable)

| Constraint | Source | Implication for the server |
| --- | --- | --- |
| `op_id` monotonic and **gapless** within an account | §7.2 | Writes for one account must be serialized through a single authority. |
| Server learns nothing but routing metadata | §7.6 | Server stores opaque `blob` + `signature` + `account_id`/`device_id`/`client_ts` only. No indexing of content is even possible. |
| Server verifies the device signature before accepting an op | §7.4 | Server holds each device's registered Ed25519 public key and enforces revocation. |
| Reject `client_ts` more than 5 min ahead of server clock | §7.2 | Skew check on push. |
| Up to 5 active devices per Pro account | §7.3 | Device cap enforced at registration. |
| Wire format frozen by the shipped client | §7.4/§7.5 | Push envelope and pull op shape are fixed; the server adapts to them, not the reverse. |
| `Transport.push(envelope) -> int` is synchronous | `core/sync.py` | Push maps to a request that returns the assigned `op_id`. No background daemon (CLAUDE.md principle 5). |

## 3. Decision 1 — Topology: one Durable Object per account

**Decided: each account is a single Cloudflare Durable Object (DO), addressed by
`account_id`. The op log, the monotonic `op_id` counter, the device registry,
and the set of live WebSocket connections all live in that DO.**

A DO is single-threaded and serializes every request to it. That property *is*
the gapless-monotonic-`op_id` requirement (§7.2), for free, with no locks, no
transactions across shards, no race. "One account, one user" (positioning ADR)
maps one-to-one onto "one account, one DO", which also gives natural per-account
isolation and a natural home for live fan-out.

Alternatives weighed:

- **Everything in Cloudflare D1 (SQLite).** Familiar SQL and easy range scans
  for pull. But a gapless monotonic counter under concurrent writes needs an
  explicit serialization (a transaction + `SELECT max(op_id) FOR UPDATE`-style
  dance D1 does not cleanly offer), reintroducing exactly the coordination the
  DO gives away. Rejected as the primary store for the op log.
- **Hybrid: op log in the DO, mirrored to D1 for analytics/range queries.**
  Extra moving part for a query pattern (`GET /ops?after=N`) the DO's own
  ordered storage already serves well. Rejected for v0.2; revisit only if an
  analytics need appears.

**Consequence:** a small **global registry in D1** is still needed for the one
lookup that happens *before* an account's DO is addressable — login maps
`email -> account_id + salt + auth_hash + is_pro`. Everything else lives in the
per-account DO.

## 4. Decision 2 — Transport: HTTP for push/catch-up, WebSocket for live receive

**Decided: push is `POST /push` (HTTP, returns the assigned `op_id`);
bootstrap and catch-up are `GET /ops?after={op_id}`; live cross-device delivery
is a receive-only WebSocket using the DO WebSocket Hibernation API.**

This matches the frozen client: `Transport.push` is synchronous request/response,
so HTTP POST returning the `op_id` is the exact fit. Pull-by-cursor (`after=N`)
is a plain ranged read of the DO's ordered op storage and serves both first-sync
(`after=0`) and reconnect catch-up. The WebSocket carries only *inbound* ops
(the DO fans out each freshly accepted op to the account's other open sockets);
the client never needs to push over the socket, which keeps the socket logic
trivial and the no-daemon principle intact (the socket is open only while
`marbles sync` runs in the foreground).

The **Hibernation API** lets idle sockets evict their JS context without
dropping the connection, so an account with devices "connected but quiet" does
not bill DO wall-clock — important for an always-could-be-open sync session.

Alternative weighed: **HTTP polling only for v0.2.0**, WebSocket deferred. Cheaper
to ship, but live cross-device propagation is the headline of the Phase 2
acceptance criteria (§8: "a second device … stays in step within seconds") and
DO's WebSocket model is the part most worth building well. We take the
WebSocket now.

## 5. Decision 3 — Pro gate: an entitlement flag now, Stripe deferred

**Decided: the account record carries an `is_pro` boolean. The server refuses
push/pull/WebSocket with `402 Payment Required` when it is false. The Stripe
Checkout + webhook flow that *sets* the flag is deferred past v0.2.**

The product's distinctive engineering — zero-knowledge E2EE sync over a
serialized op log — is what this phase exists to build and show. A payments
integration is well-trodden CRUD that adds non-core code and a third-party
webhook surface without exercising anything novel. Decoupling the *gate*
(`is_pro`) from the *billing that flips it* lets the gate ship and be tested in
v0.2 while billing lands when the product is actually charging. The flag is the
seam: wiring Stripe later only has to flip `is_pro`, touching no sync code.

Alternative weighed: **full Stripe in v0.2.** More "real", but it front-loads
non-showcase work and a live payment dependency into the milestone whose point
is the sync engine. Deferred.

> **Amendment (2026-06-24, same day):** request authentication is **device
> Ed25519 signatures, not session tokens.** After a device is enrolled (a
> password-gated `POST /devices`), every ongoing request is signed by the device
> key and verified against the registered public key; push is self-authenticated
> by its op signature. There is no JWT, no session store, and revocation is
> instant. This supersedes the "session token" language in §4/§6 below. See
> SPEC §7.11 for the canonical scheme.

## 6. What this ADR does not decide

- ~~The exact session-token format~~ — superseded by the amendment above: there
  is no session token; ongoing requests use device signatures (SPEC §7.11).
- Key rotation / recovery codes — already deferred to v0.3 (§7.8, §7.3).
- A hash chain over the op log — explicitly out of scope for v0.2 (§7.4); a
  malicious server can still drop or reorder accepted ops. Note that because
  `op_id` is gapless by construction, an *honest-but-buggy* server's accidental
  drop is detectable by the client as a gap; defending against a *malicious*
  renumbering server is what a hash chain would add, and is left to a later ADR.

## 7. Consequences

- A new `worker/` package (TypeScript, Cloudflare Workers + Durable Objects)
  enters the repo with Phase 2, as foretold by CLAUDE.md.
- The server is small and auditable: its entire trusted surface is "verify
  signature, check skew, assign op_id, store, fan out, enforce entitlement +
  device cap". Everything that matters for confidentiality already happened on
  the client.
- The `is_pro` seam keeps the door open for billing without a sync-code change.
- SPEC §7.9–§7.13 specify the resulting shape in detail.
