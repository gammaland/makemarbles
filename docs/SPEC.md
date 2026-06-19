# MakeMarbles: Technical Specification

> Single source of truth for the system's current shape. The ADRs in
> [`docs/adr/`](./adr/) capture decisions and the alternatives we weighed; this
> document captures the state that those decisions produce. When code and SPEC
> disagree, that is a bug in one of them.
>
> Last reviewed: 2026-06-18

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

#### `marbles search ... --semantic` `[planned]`

Hybrid retrieval (FTS5 + vector via RRF). Flag name and default behavior are
not yet decided.

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
weights mirrored in our GitHub Releases). See the private engineering
reference at `docs/private/embedding-stack.md` for the runtime layout
(ONNX Runtime, `tokenizers`, E5 prefix discipline, weight export pipeline).

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

### 6.2 Not yet built

- Our own GitHub Releases mirror artifact. The fallback URL is registered but
  empty until v0.2 GA, when `tools/export_onnx.py` lands and we publish a
  release with the int8-quantized weights as attached files.
- SHA-256 hashes for the current artifact set. v0.2 alpha skips verification;
  v0.2 GA pins hashes against the release artifacts we sign ourselves.
- The ADR §8 dogfood eval set (50 to 100 marbles, 20 paraphrase queries)
  for empirically validating model quality on real personal notes.

### 6.3 Embedding model as a config value, not a constant

Model identity lives in `~/.marbles/config.toml` under `[embedding]
model_name`. Changing it triggers re-embed on next search rather than next
write; the writer never tries to be clever about model versioning.

---

## 7. Sync subsystem `[designed, not built]`

Phase 2. Builds on Cloudflare Durable Objects as the server runtime. The
client is local-first: the SQLite file is the source of truth, the server is
an encrypted relay.

### 7.1 Op model `[designed]`

Each local mutation produces exactly one op. INSERT and UPDATE carry the
complete resulting row (Section 2.4); DELETE carries the target id and the
client timestamp.

The op log is append-only at every layer: client emits, server stores, peers
replay. The server never modifies an op after writing it. Compaction is not
defined in v0.2; the log grows linearly with edits, which at personal scale
is acceptable for years.

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

### 7.3 Identity and device pairing `[designed]`

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

### 7.4 Encryption schema `[designed]`

Every op payload is AES-256-GCM-encrypted with the master encryption key K.

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

### 7.5 Wire format `[designed]`

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
  overlap with the query but is semantically related, on a dogfood eval set
  of 50 to 100 notes with 20 paraphrase queries.
- `marbles reembed` executes end to end without `--dry-run`, populating
  `embedding_model` and `embedded_at` for every note.
- Embedding model weights download from HuggingFace with a documented
  fallback to a GitHub Releases mirror.

Phase 2 acceptance criteria:

- `marbles login`, `marbles sync`, `marbles sync status`,
  `marbles devices list/revoke`, `marbles logout` all work.
- A second device with the same credentials pulls all existing ops and stays
  in step over WebSocket within seconds of a write on the first device.
- All op payloads are AES-256-GCM encrypted; the server logs show no
  plaintext content under any failure mode.

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
