# MakeMarbles: Technical Specification

> Single source of truth for the system's current shape. The ADRs in
> [`docs/adr/`](./adr/) capture decisions and the alternatives we weighed; this
> document captures the state that those decisions produce. When code and SPEC
> disagree, that is a bug in one of them.
>
> Last reviewed: 2026-06-24

## Status legend

Every subsection carries one tag.

| Tag | Meaning |
| --- | --- |
| `[shipped]` | Landed on `main`, covered by tests, available to users today. |
| `[designed]` | Design decisions locked. Code not landed yet. |
| `[in design]` | Open discussion. Subject to change. |
| `[planned]` | On the roadmap. No design yet. |
| `[out of scope]` | Explicitly rejected. See positioning ADR for the why. |

---

## 1. What MakeMarbles is

A local-first personal knowledge layer with two symmetric I/O channels: humans
capture and search through a CLI, LLM agents do the same through an MCP server.
Notes live in a single SQLite file on the user's machine. Optional paid sync
(Phase 2) keeps multiple devices in step over a zero-knowledge server.

Positioning, business model, and what we deliberately do not build are fixed in
[`docs/adr/2026-06-03-product-positioning.md`](./adr/2026-06-03-product-positioning.md).
This SPEC implements that positioning; it does not relitigate it.

---

## 2. Data model

### 2.1 Note `[shipped]`

The durable atomic unit. SQLite table `notes`:

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | TEXT | no | ULID. Sortable by creation time without a separate index. |
| `content` | TEXT | no | The note body. Markdown allowed. No length cap. |
| `tag` | TEXT | yes | Single optional tag. Multi-tag support is `[out of scope]`. |
| `created_at` | TEXT | no | ISO 8601 UTC string. |
| `embedding_model` | TEXT | yes | Name of the model whose vector currently represents this note. `NULL` means not yet embedded under any model. `[shipped]` column, currently unused by code. |
| `embedded_at` | TEXT | yes | ISO 8601 UTC when the current vector was produced. `[shipped]` column, currently unused by code. |

Indexes: primary key on `id` only. FTS5 handles content search.

### 2.2 FTS5 index `[shipped]`

SQLite FTS5 virtual table over `(content, tag)` with Porter stemming and the
unicode61 tokenizer. Triggers keep it in sync with `notes` on every insert,
update, and delete. User queries are escaped phrase-by-phrase so that operators
like `AND`, `C++`, or stray quotes become literal terms instead of syntax
errors.

### 2.3 Vector index `[shipped]`

A `sqlite-vec` virtual table `notes_vec` stores one L2-normalized float vector
per note. Schema:

| Column | Type | Notes |
| --- | --- | --- |
| `note_id` | TEXT PRIMARY KEY | Joins back to `notes.id`. |
| `embedding` | FLOAT[N] | N is fixed at table creation, taken from the first vector inserted. Today's default (e5-small) means N = 384. |

The table is created lazily on the first `upsert_vector` call. Search is
brute-force flat KNN, which is the right indexing strategy for the personal-
scale corpora this product targets (low tens of thousands of notes at most);
IVF or HNSW would add tuning surface for no perceptible latency improvement at
this scale. When the configured model changes to a different dimensionality,
`reset_vector_index()` drops and recreates the table; `upsert_vector` raises
`VectorDimMismatch` if a caller forgets that step.

### 2.4 Op log `[designed]`

For multi-device sync. The op log is a per-account append-only sequence of
operations. Three op types, each describing a complete intent rather than a
delta:

```
INSERT  { note_id, content, tag, created_at, client_ts }
UPDATE  { note_id, content, tag, created_at, client_ts }   // whole-row snapshot
DELETE  { note_id, client_ts }
```

Update ops carry the entire resulting row, not a field diff. This trades a
small amount of payload size for a much simpler client (no diff computation)
and a row-level last-write-wins conflict rule that matches single-user
multi-device usage. Section 7 describes the model end to end.

---

## 3. CLI surface

All commands work without a network connection. Local SQLite is always the
source of truth. Every command that produces output supports `--json` for
scripting.

### 3.1 Capture and read

#### `marbles log <content>` `[shipped]`

Capture a single note.

- `-t, --tag TEXT` Optional tag.
- `-e, --editor` Open `$EDITOR` with a Markdown template. Lines starting with
  `#` are stripped, git-commit style.
- Reads from stdin when piped (`echo ... | marbles log`, `marbles log < file.md`,
  `marbles log -`). Stdin overrides the positional argument.
- `--json` Emit the persisted Note as JSON.

#### `marbles recent` `[shipped]`

List recent notes.

- `--days N` (default: 7) Window.
- `--limit N` (default: 100) Cap.
- `--json` Emit a JSON array.

#### `marbles search <query>` `[shipped]`

Hybrid search by default. FTS5 keyword and vector cosine are run in parallel
and combined with Reciprocal Rank Fusion (constant k = 60, the de facto
standard from the original RRF paper). FTS5 contributes lexical precision;
the embedding model contributes paraphrase recall.

- With no flag, the command runs hybrid when a vector index exists, FTS5
  alone when it does not. Degradation is silent because `marbles search`
  must never block on a model download the user did not ask for.
- On a Chinese-dominant corpus the FTS5 channel contributes effectively
  nothing (see §6.1), so hybrid collapses to vector-only and `--exact` is
  near-useless. This is expected, not a bug.
- `--exact` forces FTS5 only (the v0.1 behavior), bypassing the embedding
  engine entirely. Use for grep-style lookups.
- `--semantic` forces vector only. Refuses with a clear hint when
  `marbles reembed` has not yet populated the index.
- `--limit N` (default: 20) caps results.
- `--json` emits a JSON array.

`--semantic` and `--exact` are mutually exclusive.

#### `marbles count` `[shipped]`

Total note count. `--json` emits `{"count": N}`.

#### `marbles rm <id-prefix>` `[shipped]`

Delete a note by ULID or a unique prefix. Ambiguous prefixes are refused with
the candidate list. In a TTY the command confirms before deleting; pipes and
agent contexts skip confirmation automatically. `-y` always skips.

#### `marbles shell` `[shipped]`

Interactive REPL with command history and tab completion. Accepts the same
verbs as the top-level CLI.

### 3.2 Semantic search

#### `marbles reembed [--model NAME] [--dry-run]` `[shipped]`

Re-vector notes under a target embedding model.

- With no flags, downloads the model on first use (HuggingFace primary,
  GitHub Releases mirror fallback) into `~/.marbles/models/<name>/`, loads
  the engine, and embeds every note whose `embedding_model` is missing or
  stamped under a different model. Progress is shown via a `rich` bar.
  Safe to interrupt; resuming picks up where it left off because
  `iter_pending_for_embed` is computed from `notes.embedding_model` on
  every call.
- `--dry-run, -n` reports the pending count without changing anything.
- `--model, -m NAME` overrides the model configured in
  `~/.marbles/config.toml`. Changing to a model with a different
  dimensionality auto-resets the vec index (notes themselves are
  untouched; vectors are derived cache per ADR 2026-06-13 §6.5).
- `--json` emits `{"model": ..., "pending": N, "dry_run": true}` in
  dry-run mode, or `{"model": ..., "processed": N, "dry_run": false}`
  after a real pass.

#### `marbles search ... --semantic` `[shipped]`

Hybrid retrieval (FTS5 + vector via RRF) is the default for `marbles search`;
the `--semantic` and `--exact` flags select a single channel. Flags, defaults,
and degradation behavior are specified in full under §3.1 `marbles search`.

Validated end to end on 2026-06-21: `marbles reembed` embeds the corpus, and on
a 50-note / 25-query synthetic eval set the dense channel recalls 95% @5/@10 of
paraphrase queries that share no surface tokens with their gold note, where FTS5
recalls 0%. Hybrid does not regress on lexical-overlap queries. Numbers, method,
and caveats: ADR 2026-06-13 Appendix A. The harness lives at
`tools/eval_semantic.py` with its labeled set at `tools/eval/semantic_eval.json`
(general-purpose and non-personal, safe to commit / run in CI; the real-dogfood
eval per §8 is run privately against `~/.marbles`).

### 3.3 Sync

The following commands are designed but not yet implemented. They will fail
with a clear "not yet available" message until v0.2 lands. See Section 7.

#### `marbles login` `[designed]`

Prompt for email and master password. Derive the auth credential (PBKDF2-SHA256,
600k iterations) and the encryption key (Argon2id, 64 MiB, t=3). The auth
credential is sent to the server for verification. The encryption key never
leaves the device.

On first login, a new Ed25519 device keypair is generated. The public key is
registered with the server; the private key is stored locally and used to sign
every outbound op.

#### `marbles sync [--once]` `[designed]`

Pull new ops since the last seen `op_id`, push any pending local ops.
`--once` exits after one pass; without it, the command stays connected over a
WebSocket and streams updates until interrupted.

#### `marbles sync status` `[designed]`

Show last successful pull time, last push time, pending op count, current
device id, current account email.

#### `marbles devices list | revoke <device-id>` `[designed]`

List active devices on the account, or revoke a specific device's signing key.
A revoked device can no longer push new ops. Notes already decrypted on that
device remain decrypted locally; revocation does not reach into a device the
account no longer trusts.

#### `marbles logout` `[designed]`

Clear local credentials and the device keypair. The local SQLite remains
intact and usable in offline mode.

### 3.4 Removed or excluded

- `marbles tags` `[planned, backlog]` Surfacing tags as first-class is on the
  backlog, not committed.
- `marbles export` `[planned, backlog]` Same.
- iOS Shortcut writes go through a sync-server endpoint, not a CLI command on
  the phone. See Section 8.

---

## 4. MCP surface

Stdio MCP server `marbles-mcp`, registered with any MCP-compatible client.
Implementation is a thin wrapper over the same `core/` modules the CLI uses.
No separate code path can drift.

| Tool | Signature | Status |
| --- | --- | --- |
| `add_note` | `add_note(content: str, tag: str \| None) -> Note` | `[shipped]` |
| `search_notes` | `search_notes(query: str, limit: int = 10) -> Note[]` | `[shipped]` |

Future MCP tools (`recent_notes`, `delete_note`, semantic variants) are not
committed. We will add them when a concrete agent workflow needs them, not
preemptively.

---

## 5. Storage layer

### 5.1 Database location `[shipped]`

`~/.marbles/marbles.db` by default. Overridable in code; not currently
overridable from the CLI. Parent directory is created on first use.

### 5.2 Migrations `[shipped]`

There is no external migration framework. Schema changes are idempotent
column-add operations performed at `Storage` construction time. New columns
default to `NULL`. The current pass adds `embedding_model` and `embedded_at`
to pre-v0.2 databases without touching existing rows.

This stays viable as long as forward changes are additive. The first
non-additive change (column rename, table split) requires a real migration
runner; that bridge gets crossed when we reach it.

### 5.3 Concurrency `[shipped]`

SQLite WAL is not explicitly enabled. The CLI is short-lived and the MCP
server is single-process; we have not yet seen contention in practice. We will
revisit when concurrent agent + shell access becomes a measurable issue.

### 5.4 Backups `[out of scope, by design]`

We do not ship a backup tool. The SQLite file is one path; users back it up
however they back up any file. Once sync (Section 7) ships, the encrypted op
log on the server is a second copy. We will not silently upload anything
before then.

---

## 6. Semantic search subsystem `[partially built]`

See [`docs/adr/2026-06-13-embedding-model.md`](./adr/2026-06-13-embedding-model.md)
for the model selection (default: `multilingual-e5-small`, MIT, 384-dim, ONNX,
weights mirrored in our GitHub Releases). The runtime layout is ONNX Runtime
plus `tokenizers`, with E5 prefix discipline applied at every call site.

### 6.1 Current state

- `core/vector.py` defines `EmbeddingEngine` with `embed_passage` and
  `embed_query` methods, the E5 prefix discipline, attention-masked mean
  pooling, L2 normalization, and a `KNOWN_MODELS` registry for sizing the
  vec table. `[shipped]`
- `core/config.py` reads `~/.marbles/config.toml` for the configured model
  name and models directory. `[shipped]`
- `notes.embedding_model` and `notes.embedded_at` columns exist. `[shipped]`
- `notes_vec` sqlite-vec virtual table backs vector storage and KNN search.
  `Storage.upsert_vector`, `Storage.vector_search`, `Storage.iter_pending_for_embed`,
  and `Storage.reset_vector_index` form the full lifecycle. `[shipped]`
- `marbles reembed` runs end to end: ensures weights, loads the engine,
  embeds every pending note with a `rich` progress bar, and stamps the
  `embedding_model` + `embedded_at` columns. Auto-resets the vec index when
  the configured model's dimensionality changes. `--dry-run` reports the
  backlog without changing anything; `--json` emits a structured result.
  `[shipped]`
- `core/search.py` provides `hybrid_search` (Reciprocal Rank Fusion over
  FTS5 BM25 and vector cosine, k = 60) and `vector_only_search`. Both
  degrade gracefully: an absent embedder or an empty vec index makes
  hybrid behave like FTS5, and vector-only returns empty rather than
  erroring. `[shipped]`
- `marbles search` defaults to hybrid; `--exact` and `--semantic` flags
  give the user explicit control over the channel mix. A manual end-to-end
  smoke with three obviously-paraphrased notes returned the correct top-1
  match on all three paraphrase queries; the algorithm itself works as
  designed, the ADR §8 dogfood eval set is what will validate model
  quality on real personal notes. `[shipped]`
- `core/model_download.py` resolves model artifacts from a configurable
  source chain. The default for `multilingual-e5-small` tries HuggingFace
  (Xenova ONNX mirror) first and falls back to our own GitHub Releases
  mirror. Downloads land atomically (stream to `.partial`, optional SHA-256
  verify, then rename) so an interrupted download cannot leave a half-written
  weights file behind. `[shipped]`
- 100 unit and integration tests covering the above, including prefix
  discipline, pooling math, normalization, provider selection (mocked ONNX
  session), dim-mismatch handling, model-filtered KNN, source-chain
  fallback, SHA-256 verification, partial-file cleanup, and cache reuse.
  An end-to-end smoke (downloads e5-small from HuggingFace, loads the
  engine, runs a 4-query retrieval ranking eval) was run manually and
  returned Recall@2 = 4/4. `[shipped]`
- **Real-note dogfood validated (2026-06-24).** On 80 of the user's own
  notes (date-stratified over 8 months, Chinese-dominant) and 25 hand-authored
  queries (21 paraphrase / 4 lexical), e5-small scores vector recall@5 0.96,
  recall@10 1.00, MRR 0.92; FTS5 keyword scores 0.00 across the board. The
  synthetic-set result (§3.2) now holds on real personal data. The harness
  gained a `--data` flag so the private set can be run off-repo. `[shipped]`
- **e5-small vs MiniLM head-to-head on real notes (2026-06-24): keep e5-small.**
  On the same dogfood set, `paraphrase-multilingual-MiniLM-L12-v2` scored vector
  recall@5 1.00 / MRR 0.903 vs e5-small's 0.96 / 0.919. MiniLM caught the one
  query e5 missed at rank 7 (d19); e5 ranked its hits slightly higher (better
  MRR). Both are 384-dim and ~450 MB fp32. The gaps are one query out of 25 —
  noise at this sample size — so there is no reason to switch the default. e5
  keeps the marginal MRR edge plus its documented E5 prefix discipline. MiniLM
  has a HuggingFace (Xenova) source in `model_download.py` but no mirror
  fallback. `[shipped]`
- **Finding: keyword search is near-useless on Chinese, even with token
  overlap.** unicode61 tokenizes a run of CJK as one whole-phrase token and the
  search path ANDs all query terms, so a natural-language query almost never
  reproduces an exact contiguous CJK token run. For a Chinese-dominant corpus
  `--exact` returns nothing useful and hybrid (§3.1) reduces to vector-only,
  because FTS contributes no ranks to fuse. This is a real limitation of the
  current keyword path, not an eval artifact.

### 6.2 Not yet built

- Our own GitHub Releases mirror artifact. The fallback URL is registered but
  empty until v0.2 GA, when `tools/export_onnx.py` lands and we publish a
  release with the int8-quantized weights as attached files.
- SHA-256 hashes for the current artifact set. v0.2 alpha skips verification;
  v0.2 GA pins hashes against the release artifacts we sign ourselves.
- A second download source (mirror fallback) for the MiniLM and bge-m3
  benchmark models. They have a single HuggingFace (Xenova) source today; only
  e5-small, the shipped default, has the GitHub Releases fallback.

### 6.3 Embedding model as a config value, not a constant

Model identity lives in `~/.marbles/config.toml` under `[embedding]
model_name`. Changing it triggers re-embed on next search rather than next
write; the writer never tries to be clever about model versioning.

---

## 7. Sync subsystem `[client crypto + push pipeline shipped; server fully designed (§7.9–§7.13), not built]`

Phase 2. Builds on Cloudflare Durable Objects as the server runtime. The
client is local-first: the SQLite file is the source of truth, the server is
an encrypted relay.

Implementation status as of 2026-06-24:

- `core/crypto.py` ships the cryptographic primitives the rest of §7 will
  rely on: PBKDF2-SHA256 auth credential derivation (600k iterations),
  Argon2id encryption key derivation (64 MiB / t=3 / p=1), AES-256-GCM
  encrypt/decrypt with random 96-bit nonces and AAD binding, and Ed25519
  per-device signing/verification. 25 tests cover round-trip behavior and
  tamper detection on every input axis (key, nonce, ciphertext, AAD,
  message, signature, public key).
- `core/oplog.py` + the `ops` table ship the local op log (§7.1). Every note
  insert/delete atomically appends one op; payloads are stored in plaintext
  locally and have no crypto/network dependency. See §7.1.
- `core/wire.py` + `core/sync.py` ship the **client push pipeline** (§7.4/§7.5):
  `pack_push` seals one op into an encrypted (AES-256-GCM, AAD-bound) and
  signed (Ed25519) envelope; `unpack_and_verify` is the verify-then-decrypt
  inverse a pulling peer runs; `push_backlog` drains `unsynced_ops()` over a
  `Transport` and records each server-assigned op_id. All testable with no
  server (a fake transport verifies + decrypts like a peer).
- The **server is now fully designed** in §7.9–§7.13 (topology, data model, API
  surface, op_id assignment, entitlement) and ADR 2026-06-24. Decisions:
  one Durable Object per account, HTTP push/catch-up + receive-only WebSocket,
  an `is_pro` entitlement flag with Stripe deferred.
- Still **not built**: the `worker/` package itself, the `login` handshake that
  produces the `Identity` bundle, the `sync` / `devices` CLI commands, and
  pull/replay back into the local tables.

### 7.1 Op model `[local log shipped; server replay designed]`

Each local mutation produces exactly one op. INSERT and UPDATE carry the
complete resulting row (Section 2.4); DELETE carries the target id and the
client timestamp.

The op log is append-only at every layer: client emits, server stores, peers
replay. The server never modifies an op after writing it. Compaction is not
defined in v0.2; the log grows linearly with edits, which at personal scale
is acceptable for years.

**Shipped (client side).** `core/storage.py` maintains an `ops` table from the
first note captured, even on the free tier — emission is a single extra row
insert with no crypto or network dependency, which removes any need to backfill
ops when a user later turns on sync. `add` and `delete` each write the note
mutation and its op inside one transaction (raw `conn.execute`, since
sqlite-utils' own `.insert()` commits internally and cannot share the
transaction), so a note never persists without its op. Embedding writes
(`upsert_vector`) emit no op: vectors are local-derived, recomputable, and
non-synced (§7.6). Two columns track lifecycle: `local_seq` (the append order
on this device) and `server_op_id` (NULL until the server accepts the op;
`unsynced_ops()` reads `server_op_id IS NULL` to find the push backlog).
Payloads are JSON, deterministically encoded (`sort_keys`) so a future
signature over the blob is stable. Stored in plaintext locally — the same data
already lives in `notes` on the same disk; encryption happens only at push
(§7.4).

**Designed (server side).** `op_id` assignment, cross-device replay, and the
last-write-wins merge on pull (§7.2) land with the wire protocol and server.

### 7.2 Ordering and conflict resolution `[designed]`

Two different identifiers are responsible for two different jobs.

- **`op_id`** is assigned by the server when it accepts an op. It is monotonic
  and gapless within an account. Clients use it to ask "give me everything
  after N" on pull, and to record their high-water mark locally. Nothing about
  conflict resolution depends on it.
- **`client_ts`** is set by the originating device, in wall-clock UTC, at the
  moment the op was produced. It is the only signal used to break ties under
  the row-level last-write-wins rule: when two updates touch the same
  `note_id`, the one with the higher `client_ts` wins. If `client_ts` ties
  exactly, the tiebreaker is the lexical order of `(device_id, op_id)`.

To protect against clock-skew accidents (phone time set wrong) the server
rejects any op whose `client_ts` is more than five minutes ahead of its own
clock. Late ops are accepted without fuss.

The system does not use Lamport timestamps, vector clocks, or hash chains.
The single-user multi-device access pattern does not produce true concurrent
writes often enough to pay for them, and the field-level LWW that would
benefit from finer ordering metadata was deliberately traded away for client
simplicity.

### 7.3 Identity and device pairing `[primitives shipped, integration designed]`

Authentication is email plus a master password. The same password derives two
separate secrets through two separate KDFs:

```
master_password
  ├── auth_credential   = PBKDF2-SHA256(pw, salt_auth, 600_000 iterations)
  │                       sent to the server
  │                       server stores a hash of this
  │
  └── encryption_key K  = Argon2id(pw, salt_enc, 64 MiB, t = 3, p = 1)
                          never leaves the device
                          used for AES-256-GCM on every op payload
```

Salts are per-account constants generated at registration and stored on the
server alongside the account record. Clients fetch the salts as part of the
login handshake, before the password is mixed in.

Each device, on first sign-in, generates an Ed25519 keypair locally. The
public key is registered with the server under a generated `device_id`. The
private key stays on the device. Every outbound op carries a signature from
this key.

**Pro tier accounts support up to 5 active devices.** Adding a sixth requires
revoking an existing one. The cap is a soft policy enforced at the server.

**Revocation** (`marbles devices revoke <device-id>`) tells the server to
reject any further ops signed by that key. Local data already decrypted on
the revoked device is not retracted; this matches the threat model where
losing physical access to a device is treated the same as losing an SSH
private key.

**Password recovery is not offered in v0.2.** Forgetting the password loses
the data permanently. The registration flow is loud about this. A recovery
code mechanism is planned for v0.3 once dogfood feedback shows it is needed.

### 7.4 Encryption schema `[shipped — client codec in core/wire.py]`

Every op payload is AES-256-GCM-encrypted with the master encryption key K.
The plaintext sealed is `{"type": <op_type>, "payload": <op payload>}`, so the
op type and note_id ride inside the ciphertext and are invisible to the server
(§7.6).

- **Nonce**: 96 bits, freshly random per op. At 10 thousand ops per day the
  expected time until a nonce collision under random sampling exceeds the age
  of the universe; a counter scheme is not worth the operational risk
  (counter resync on backup restore is a common footgun).
- **Additional authenticated data (AAD)**: the byte concatenation of
  `account_id`, `device_id`, and `client_ts`. Server-side metadata cannot be
  swapped to point at a different account or device without breaking the GCM
  tag.
- **Plaintext op payload** is JSON (see Section 2.4 for the schema per type).

Each op also carries an **Ed25519 signature** by the originating device over
`device_id || client_ts || blob`. The server verifies the signature against
the registered public key before accepting the op. Pulling clients verify the
signature again on receipt; we do not trust the server to deliver an op that
no device ever signed.

We do not maintain a hash chain over the op log. A malicious server could
silently drop or reorder accepted ops; we accept that exposure in v0.2 in
exchange for a much simpler protocol. We do trust the server not to forge
content, because content goes through the GCM tag and the device signature.

### 7.5 Wire format `[client side shipped; transport designed]`

The push/pull envelope codec is implemented in `core/wire.py` (`pack_push` /
`unpack_and_verify`) and the client push loop in `core/sync.py`
(`push_backlog` over a `Transport` protocol). The concrete transport — the
WebSocket / `GET /ops?after=` server — is still designed only.

Push (client to server):

```json
{
  "account_id": "...",
  "device_id":  "...",
  "client_ts":  "2026-06-18T12:34:56Z",
  "blob":       "<base64: nonce || ciphertext || gcm_tag>",
  "signature":  "<base64: Ed25519 over device_id || client_ts || blob>"
}
```

Pull (server to client), per op:

```json
{
  "op_id":      1234,
  "server_ts":  "2026-06-18T12:34:57Z",
  "device_id":  "...",
  "client_ts":  "2026-06-18T12:34:56Z",
  "blob":       "...",
  "signature":  "..."
}
```

Transport is WebSocket while the client is online (for live broadcast across
devices) and a plain `GET /ops?after={op_id}` over HTTPS for bootstrap and
catch-up.

### 7.6 What the server can see `[designed]`

A precise statement of the residual metadata exposure.

The server **can** observe:

- `account_id` and per-account write volume
- which `device_id` produced each op
- `client_ts` and `server_ts` for every op (write timing patterns)
- the ciphertext length (rough op size)

The server **cannot** observe:

- the op type (INSERT / UPDATE / DELETE)
- the `note_id`, so it cannot link ops to specific notes
- `content`, `tag`, or `created_at`
- total note count
- which notes have been deleted

This is what zero-knowledge means under this design: the server is a sealed
relay for content, but it does see when and how often each device writes.
Users who consider write-pattern metadata sensitive should know this before
opting into sync. Single-device free tier exposes none of it.

### 7.7 Free vs. Pro `[designed]`

| Capability | Free | Pro |
| --- | --- | --- |
| All CLI and MCP commands locally | yes | yes |
| Sync across devices | no | yes |
| Active devices per account | n/a | up to 5 |
| Server account at all | no | yes |
| Pricing | free | USD 5 / month |

The free tier touches no network. `marbles login` exists only as the gate
into the Pro tier; without a subscription the server refuses ops.

### 7.8 Key rotation `[planned]`

Not in v0.2. Changing the master password in v0.2 requires deleting the
server account, re-encrypting locally, and registering a new account.
`marbles rotate-key` lands in v0.3.

### 7.9 Server topology and runtime `[designed]`

The server is a TypeScript Cloudflare Worker plus Durable Objects. See
[`docs/adr/2026-06-24-sync-server-architecture.md`](./adr/2026-06-24-sync-server-architecture.md)
for the decisions and alternatives. It lands as a new `worker/` package.

**One Durable Object per account**, addressed by `account_id`. Because a DO
serializes every request to it, the account's `op_id` counter is monotonic and
gapless (§7.2) with no locks. The per-account DO owns:

- the monotonic `op_id` counter,
- the append-only op store,
- the device registry (public keys + revocation state),
- the set of live WebSocket connections for the account.

A small **global registry in D1** holds only what must be read *before* an
account's DO is addressable: the login lookup `email -> account_id, salt,
auth_hash, is_pro`. Nothing content-adjacent lives in D1.

The server is a sealed relay (§7.6): its entire trusted job is verify-signature,
check-skew, assign-`op_id`, store, fan-out, enforce-entitlement. It cannot index
or read content; the ciphertext is opaque to it.

### 7.10 Server data model `[designed]`

**D1 (global), table `accounts`:**

| Column | Notes |
| --- | --- |
| `account_id` | ULID, primary key. Also the DO name. |
| `email` | Unique. |
| `salt` | Per-account 16-byte salt (base64), generated at registration (§7.3). Fetched unauthenticated at login so the client can derive its keys. |
| `auth_hash` | A server-side hash of the client-uploaded `auth_credential` (which is itself PBKDF2-stretched, §7.3). The raw credential is never stored. |
| `is_pro` | Boolean entitlement gate (§7.12). |
| `created_at` | ISO 8601 UTC. |

**Durable Object (per account) transactional storage:**

| Key | Value |
| --- | --- |
| `next_op_id` | Integer counter, starts at 1. |
| `op:{op_id padded}` | `{ device_id, client_ts, server_ts, blob, signature }` — the stored op. Zero-padded key so a prefix range scan returns ops in `op_id` order for pull. |
| `device:{device_id}` | `{ pubkey, created_at, revoked }`. The Ed25519 public key the server checks signatures against, plus revocation state. |

The DO stores the op exactly as relayed plus the two server-assigned fields
(`op_id`, `server_ts`). `blob` and `signature` are opaque base64. There is no
content, note_id, or op type anywhere in server storage (§7.6).

### 7.11 API surface `[designed]`

All requests are HTTPS (TLS via Workers). After login, push/pull/connect carry
a **session token** — a signed JWT scoped to `(account_id, device_id, exp)`,
validated at the Worker edge before the request is routed to the account DO.

| Method + path | Auth | Purpose |
| --- | --- | --- |
| `POST /account` | none | Create account for an email; server generates and returns `{ account_id, salt }`. |
| `PUT /account/auth` | none (proves possession via the credential itself) | Set `auth_hash` from the client-derived `auth_credential`. Completes registration. |
| `GET /account/salt?email=` | none | Return the per-account `salt` so the client can derive `auth_credential` and `K` at login. |
| `POST /login` | none | `{ email, auth_credential }`; server checks against `auth_hash`, returns `{ account_id, session_token }`. |
| `POST /devices` | session | Register this device's Ed25519 public key; returns the generated `device_id`. Idempotent per device on re-login. |
| `GET /devices` | session | List active (non-revoked) devices. |
| `POST /devices/{id}/revoke` | session | Mark a device revoked; the DO rejects its future ops (§7.3). |
| `POST /push` | session | Submit one push envelope (§7.5); returns `{ op_id }`. This is the concrete `Transport.push`. |
| `GET /ops?after={op_id}` | session | Return ops with `op_id > after`, in order, each in the pull wire format (§7.5). `after=0` bootstraps. |
| `GET /connect` (WebSocket) | session | Open a receive-only live stream; the DO pushes each newly accepted op (pull format) to the account's other sockets. Uses the DO WebSocket Hibernation API. |

**Login handshake.** Salts are server-owned (§7.3). The client fetches the salt
(`GET /account/salt`), derives `auth_credential = PBKDF2(pw, salt)` and the
encryption key `K = Argon2id(pw, salt)` locally, then `POST /login` with only
the `auth_credential`. `K` never leaves the device. On first login on a new
device, the client generates an Ed25519 keypair and registers the public key via
`POST /devices`.

**`marbles sync` loop.** Without `--once`, the command opens the `GET /connect`
WebSocket and, in the same foreground process, drains the local push backlog via
`POST /push` and catches up via `GET /ops?after=`. It is reconcile-by-cursor:
the client tracks the highest `op_id` it has applied, ignores ops it already
has, and uses `GET /ops?after=` to fill any gap a live socket missed. No
background daemon; the socket is open only while the command runs.

### 7.12 op_id assignment, push processing, idempotency `[designed]`

On `POST /push` the account DO, serially:

1. Validates the session token and that `device_id` is registered and **not
   revoked**; else `403`.
2. **Verifies the Ed25519 signature** over `device_id || client_ts || blob`
   against the stored device public key (§7.5); else `403`. The server refuses
   to store an op no registered device signed.
3. **Skew check:** rejects `client_ts` more than 300 s ahead of server time
   (§7.2) with `409`; late ops are accepted without fuss.
4. Assigns `op_id = next_op_id++` and stamps `server_ts`.
5. Persists the op atomically in DO storage.
6. Fans the op out (pull format) to the account's other live WebSockets.
7. Returns `{ op_id }`.

**Idempotency / at-least-once.** The client marks an op synced only after the
transport returns its `op_id` (`core/sync.py`). If the connection drops after
step 5 but before the client records the result, the client re-pushes on the
next run; the server assigns a **new** `op_id` to the duplicate. This is
harmless: replay is idempotent under row-level last-write-wins (§7.2) — a
re-applied INSERT/UPDATE resolves to the same row, a re-applied DELETE is a
no-op. v0.2 does not deduplicate server-side; a client-supplied idempotency key
is a possible v0.3 refinement.

**Drop/reorder.** Because `op_id` is gapless by construction, a client can
detect an *accidental* gap in a pull. Defending against a *malicious* server
that renumbers or withholds accepted ops would require a hash chain, which v0.2
deliberately omits (§7.4).

### 7.13 Entitlement, limits, and abuse controls `[designed]`

- **Pro gate.** `push`, `ops`, and `connect` refuse with `402 Payment Required`
  when `is_pro` is false. The free tier never reaches these endpoints (§7.7);
  the gate is the server's enforcement of that. Billing that *sets* `is_pro`
  (Stripe Checkout + webhook) is deferred past v0.2; the flag is the seam.
- **Device cap.** Registration of a 6th active device returns `409`; the user
  must revoke one first (§7.3).
- **Payload cap.** A push envelope's `blob` is capped (target: 1 MiB) so a
  single op cannot be used to balloon account storage; note content has no local
  cap (§2.1), so a pathological note is rejected at push with `413`.
- **Rate limiting.** A soft per-account write-rate limit bounds abuse; the exact
  budget is set at deploy time, not in this SPEC.

---

## 8. Version plan

### v0.1.0-alpha `[shipped, dogfood]`

Capture, search, retrieve, delete. CLI plus MCP. No network.

Acceptance criteria, all met:

- `marbles log` accepts content via positional arg, stdin pipe, file redirect,
  and `$EDITOR`.
- `marbles recent`, `marbles search`, `marbles count`, `marbles rm`,
  `marbles shell` work end to end with `--json` available.
- `marbles-mcp` exposes `add_note` and `search_notes`, validated by an
  end-to-end stdio smoke test.
- 33+ unit and integration tests pass.

### v0.1.0 `[planned, imminent]`

Tagging and public-repo flip of v0.1.0-alpha after dogfood. No new features.
GitHub Actions CI gate added. Repository scrub confirms no private content
leaks (no real names, no internal-only docs, no draft material) before the
visibility flip.

### v0.2.0 `[in development]`

Phase 4 first, Phase 2 second. The two are independent.

Phase 4 acceptance criteria:

- `marbles search "minimal pair query"` returns a note that has no token
  overlap with the query but is semantically related. **Met** on both a
  synthetic general eval set (50 notes / 25 queries, EN/ZH/mixed: dense recall
  0.95 @5/@10 vs 0.00 for FTS5; ADR 2026-06-13 Appendix A) **and the real-note
  dogfood** (2026-06-24, §6.1: 80 real notes / 25 queries, vector recall@5 0.96,
  recall@10 1.00 vs 0.00 for FTS5). The e5-small vs MiniLM head-to-head on real
  notes is now **resolved** (2026-06-24, §6.1): effectively a tie, default stays
  e5-small.
- `marbles reembed` executes end to end without `--dry-run`, populating
  `embedding_model` and `embedded_at` for every note. **Met** (verified
  2026-06-21).
- Embedding model weights download from HuggingFace with a documented
  fallback to a GitHub Releases mirror. **Met** (HF fetch exercised on the
  first real reembed).

Phase 2 acceptance criteria (server shape specified in §7.9–§7.13 and
ADR 2026-06-24):

- `marbles login`, `marbles sync`, `marbles sync status`,
  `marbles devices list/revoke`, `marbles logout` all work.
- A second device with the same credentials pulls all existing ops and stays
  in step over WebSocket within seconds of a write on the first device.
- All op payloads are AES-256-GCM encrypted; the server logs show no
  plaintext content under any failure mode.
- The server enforces signature verification, the `client_ts` skew check, the
  `is_pro` entitlement gate, and the 5-device cap per §7.12–§7.13; ops signed by
  a revoked device are rejected.

### v0.3.0 `[planned]`

Phase 3 (iOS Shortcut writes via the sync server) and the key-rotation /
recovery-code features deferred from v0.2.

### v0.4.0 and beyond `[planned]`

Hybrid retrieval polish, the AI insight pipeline, the Textual TUI, and
whatever the v0.2 dogfood surfaces as the next real friction.

---

## 9. Out of scope

Anti-features locked by the positioning ADR.

- Cloud-only storage. SQLite stays the source of truth.
- Multi-user shared notebooks. One account, one user.
- A second sync server we do not run. Self-hosting the sync server is
  permitted by the open protocol, but not supported by the hosted product.
- A built-in editor. `$EDITOR` does what users already configured it to do.
- Web UI in the box. A static `marbles digest` HTML for weekly review is on
  the roadmap; an interactive web app is not.
- LLM features tied to a single vendor. The MCP server is the integration
  surface; we do not call any LLM API ourselves.
- Background daemon. The CLI and MCP server are short-lived processes; nothing
  runs in the background.

---

*If this document and the code disagree, file an issue. The disagreement is
the bug; the SPEC is here so we can see it.*
