# ADR 2026-06-13: Embedding Model Selection for Local Semantic Search

**Status:** Accepted (pending empirical validation; see §6)
**Date:** 2026-06-13
**Supersedes:** none
**Related:** `docs/adr/2026-06-03-product-positioning.md`

## 1. Context

MakeMarbles v0.1 ships with FTS5 keyword search. Phase 4 of the roadmap adds **semantic search** so a query like *"how did I feel about the rebalance bug?"* surfaces a marble that reads *"kafka consumer group is thrashing again, demoralizing"*: no token overlap, same meaning.

This requires a text embedding model. The choice is consequential and hard to reverse later: changing models forces a full re-embedding pass over every user's database and a sync-protocol bump if vectors are ever exchanged across devices. We want to make the call deliberately, on the record, with our reasoning visible.

This ADR records both the decision and the alternatives we weighed, so a future reader (or a future us) can see the tradeoff space and challenge the conclusion when conditions change.

## 2. What we need the embedding to do

A marble is a short, personal, often multilingual text, anywhere from a sentence to a few paragraphs. The retrieval task is **recall from personal history**, not web-scale search. Concretely:

- **Inputs**: notes in English and Chinese (often mixed in one entry), median length ~30 tokens, occasional long-form drafts up to ~2k tokens.
- **Queries**: short, exploratory, often paraphrased (e.g. *"that thing I read about hybrid retrieval"*, not *"hybrid retrieval RRF paper"*).
- **Volume**: thousands to low tens of thousands of notes per user over years. Not millions.
- **Latency budget**: embedding a captured note must not block the CLI's write path perceptibly. A write-then-embed-async pattern is fine, but synchronous embedding (e.g. inside the MCP `add_note` tool call) should still feel snappy.
- **Quality bar**: "good enough that the user notices it pulling up the right marble more often than FTS5 alone". This is a subjective threshold, validated by dogfooding, not a leaderboard rank.

## 3. Product constraints (non-negotiable)

These come from the v0.1.0 positioning ADR and the project's local-first thesis. Any candidate that violates one is out.

| Constraint | Implication for model choice |
| --- | --- |
| **Local-first, no cloud round-trip** | API-only models (OpenAI, Cohere, Voyage) are excluded. The model must run on the user's machine, offline. |
| **Single-file, no daemon** | We can't ship a long-running embedding server. The model must load fast enough that a short-lived CLI process can use it without feeling sluggish. |
| **Multilingual (English + Chinese, others a bonus)** | English-only models are excluded. The user writes in both languages, often in the same note. |
| **Redistributable license** | The model weights' license must allow us to mirror them in our own release artifacts if the upstream host disappears. MIT, Apache-2.0, and most permissive licenses qualify; non-commercial-only and research-only licenses do not. This is a stronger requirement than "AGPL-compatible", and it is what lets §6.5 work. |
| **Modest install footprint** | We promise "single SQLite file + a small CLI". A 2 GB model download contradicts that promise. Target: under ~250 MB compressed, under ~500 MB on disk. |
| **ONNX-runnable** | Avoids pulling PyTorch into the install (~800 MB). Models with published ONNX exports or trivially exportable architectures are preferred. |

## 4. Decision criteria (with weights)

Within the constraints, candidates are scored on:

1. **Quality on multilingual short-text retrieval** (weight: high). Specifically the kind of recall described in §2: paraphrase recall in mixed-language personal notes. Public MTEB and C-MTEB scores are *signal*, not ground truth, since our domain is narrower than the benchmark.
2. **Size on disk** (weight: high). Directly affects install friction.
3. **Inference speed on CPU** (weight: medium). We target consumer machines without GPUs. Apple Silicon CoreML acceleration via ONNX is a plus.
4. **Vector dimensionality** (weight: medium). Lower dims mean smaller index, less RAM, faster cosine, but too low hurts quality. 384 to 768 is the sweet spot for sqlite-vec at this scale.
5. **Maintenance and community signal** (weight: low-medium). Is the model still updated? Is the ONNX export maintained?

## 5. Candidate landscape

We surveyed seven candidates that pass §3's constraints. Numbers below are approximate and based on the model authors' published specs as of mid-2026; precise benchmark snapshots will be re-measured during validation (§6).

| Model | Params | Dim | Disk (fp32) | Languages | Notable strengths | Notable concerns |
| --- | --- | --- | --- | --- | --- | --- |
| **paraphrase-multilingual-MiniLM-L12-v2** | 118 M | 384 | ~470 MB | 50+ | Battle-tested, well-supported ONNX export, fast on CPU | Older architecture; weaker than newer multilingual models on harder retrieval |
| **multilingual-e5-small** | 118 M | 384 | ~470 MB | 100+ | Instruction-tuned (`query:` / `passage:` prefixes), strong MTEB/C-MTEB for its size | Prefix discipline required, easy to misuse |
| **multilingual-e5-base** | 278 M | 768 | ~1.1 GB | 100+ | Materially better recall than the `small` variant | Exceeds our ~500 MB disk target |
| **bge-m3** | 568 M | 1024 | ~2.2 GB | 100+ | Top-tier multilingual quality, supports dense + sparse + ColBERT in one model | Too large for our install-footprint promise; over-spec for personal-scale recall |
| **gte-multilingual-base** | 305 M | 768 | ~1.2 GB | 70+ | Strong recent multilingual scores, long-context (8k) | Exceeds disk target; long-context isn't a v0.1 need |
| **jina-embeddings-v2-base-zh** | 161 M | 768 | ~644 MB | EN + ZH only | Explicitly bilingual EN/ZH, long-context | Two-language ceiling limits future scope; over disk target |
| **nomic-embed-text-v1.5** | 137 M | 64-768 (Matryoshka) | ~550 MB | English-leaning | Matryoshka dims allow tunable index size | Multilingual quality is not its design goal |

**Excluded by §3 constraints**: OpenAI `text-embedding-3-*`, Cohere `embed-v3`, Voyage `voyage-3` (all API-only); SBERT models licensed CC-BY-NC.

## 6. Decision

**Adopt `multilingual-e5-small` as the default embedding model for v0.2.**

Reasoning:

- **It clears every §3 constraint comfortably.** ~470 MB on disk is at the upper edge of our footprint target but acceptable; we accept this as the cost of multilingual coverage. ONNX export is available and maintained.
- **384-dim vectors are the right granularity for our scale.** With ~10k notes per power user, a 384-dim float32 index is ~15 MB, invisible next to the SQLite file. Stepping up to 768 dims would double that without an obviously proportional recall gain at this corpus size.
- **Instruction tuning matches our usage.** E5's `query:` / `passage:` prefix discipline maps cleanly onto our two call sites: the CLI/MCP write path emits `passage:` embeddings, the search path emits `query:`. We document this in code rather than leaving it to chance.
- **It is the best-balanced point on the size/quality curve for personal-scale recall.** bge-m3 would likely retrieve better, but the marginal improvement does not justify quintupling our install footprint for a journal that the user themselves wrote and largely remembers.
- **It is not a one-way door.** Section 7 keeps an `embedding_model` column on every vector row, and §8 specifies the upgrade path. A user who wants bge-m3 can opt in; a future default change is a re-embed migration, not a schema break.

We pick the *small* variant over *base* explicitly because the install-footprint promise is load-bearing for the product's narrative ("local-first, single file, no daemon"). A 1 GB+ default download contradicts that more than a slightly weaker recall does.

## 6.5 Model availability and the re-embed contract

A model weight file is not a database. It is a third-party artifact whose continued availability we do not control. HuggingFace is the de facto host today; it is stable but not a permanent archive, and individual models do get removed (license disputes, author requests, organizational changes). We need to be explicit about what happens if our chosen model becomes unreachable.

**What is actually at risk.** The user's notes are not at risk. Vector embeddings are a derived cache of `notes.content`, and `notes.content` is the source of truth in SQLite. If a model disappears, what is lost is the ability to (a) embed new notes with that exact model and (b) keep using the existing vectors *under that model*. The notes themselves remain readable, searchable via FTS5, and re-embeddable with any other model we choose to adopt.

**Three defenses, in order of how often they save us.**

1. **License choice (the cheapest defense).** §3 already requires a redistributable license. Our chosen model and every viable alternative in §5 satisfies this. That single property is what makes defenses 2 and 3 legal.
2. **We mirror the weights ourselves.** Each release of `makemarbles` that uses an embedding model attaches the corresponding ONNX weight file to its GitHub Release as a downloadable artifact. The first-run download logic tries HuggingFace first, then falls back to our release mirror. GitHub Releases is not a permanent archive either, but it is a second independent host under our control, and the storage cost is negligible for our scale.
3. **Re-embedding is a routine operation, not a recovery operation.** §7 and §8 below treat model switching as a first-class CLI command (`marbles reembed`), not as a migration script. The point is that switching models is *expected* to happen: when a better model comes out, when a user wants to opt into a heavier one, *or* when the current default becomes unreachable. The same code path handles all three cases. Cost on a 10k-note corpus is minutes on Apple Silicon, not hours.

**The contract this gives users.** Their notes never depend on a third party staying online. Their vector index does, but only for ongoing freshness; it can always be rebuilt locally from the notes themselves. The worst realistic failure mode is: "the user's vector index is stale until they download a replacement model and run `marbles reembed`", during which FTS5 keyword search continues to work normally. That is a degraded experience for a known-bounded window, not data loss.

## 7. Schema and code implications

To keep the door open for future model changes and for users who prefer to opt into heavier models:

- **`notes.embedding_model TEXT`**: every embedded row records which model produced its vector. Mixed-model corpora are tolerated at read time (the search path uses only vectors matching the currently-configured model; the rest are treated as "not yet embedded" and surfaced by FTS5 only).
- **`notes.embedded_at TIMESTAMP NULL`**: separates "captured" from "embedded" so the async embedding worker has explicit state.
- **Embedding is async by default.** Write path returns immediately; a background task (in-process for the CLI, deferred for the MCP server) populates the vector. Synchronous embedding remains available behind a flag for tests and benchmarks.
- **Model identity is a config value, not a constant.** `~/.marbles/config.toml` carries the model name; changing it triggers a re-embed pass on next search, not on next write.
- **`marbles reembed [--model <name>]`**: explicit CLI command to re-vector the entire corpus under a given model. Resumable (skips rows already at the target `embedding_model`), progress-reported, safe to interrupt. This is the user-visible expression of §6.5's "re-embed is routine" contract.
- **Weight artifacts are exported from `intfloat/multilingual-e5-small` using `optimum-cli`, with int8 quantization.** The export script lives at `tools/export_onnx.py` and is part of the release pipeline. We export ourselves rather than depend on a third-party ONNX mirror so the license chain from upstream is clean for §6.5's mirroring guarantee, and so we can pin the quantization scheme rather than inheriting whatever a third party chose.

These hooks are cheap to add now and absorb almost any future model change without a breaking migration.

## 8. Validation plan

This decision is provisional until we ship and measure. Before v0.2 GA we will:

1. **Build a small eval set** from real dogfood notes: 50 to 100 marbles, 20 paraphrase-style queries with hand-labeled relevance. Both English and Chinese queries; some mixed-language.
2. **Run head-to-head**: multilingual-e5-small (chosen) vs. paraphrase-multilingual-MiniLM-L12-v2 (cheapest fallback) vs. bge-m3 (quality ceiling). Metrics: Recall@5, Recall@10, MRR. Latency: p50/p95 of single-note embedding on an M-series Mac and on a mid-range x86 laptop.
3. **Publish results in this ADR's appendix.** If e5-small is not materially better than MiniLM on our eval, we drop to MiniLM (smaller, simpler, older but understood). If e5-small is materially worse than bge-m3 *and* a non-trivial fraction of users have the disk/RAM budget, we reconsider offering bge-m3 as the default. That bar is high.

## 9. Consequences accepted

- **~470 MB first-run download.** We surface this in `marbles search` the first time a user runs semantic search, with a clear progress indicator. No silent multi-hundred-MB downloads.
- **CPU-only inference is the default.** We do not ship GPU acceleration. ONNX Runtime's CoreML execution provider gives Apple Silicon users a meaningful speedup for free; we'll enable it when available, but don't depend on it.
- **One model at a time per user.** The architecture supports mixed-model corpora, but we do not encourage running two models in parallel. The complexity isn't worth it at personal scale.
- **English-Chinese mixed notes get an honest "good enough", not a "perfect"**. e5-small handles bilingual content well, but a sentence that switches languages mid-clause is not its strongest suit. We accept this and revisit if dogfooding surfaces concrete failures.
- **Weight artifacts are mirrored in our GitHub Releases.** First-run download tries HuggingFace first and falls back to the release mirror if HF is unreachable or the model has been removed. This is operationally cheap and removes a single point of failure that we cannot otherwise control. See §6.5.

## 10. Revisit triggers

We will re-open this decision if any of the following becomes true:

- A clearly stronger multilingual model under ~250 MB ships with a permissive license. Today there is none we are aware of; the field moves quickly enough that this is plausible within a year.
- Our eval set (§8) shows e5-small underperforming MiniLM on real notes. In that case, simpler wins.
- Sync (Phase 2) introduces a constraint we did not anticipate. For instance, if vectors *are* eventually transmitted (rather than re-derived per device), a smaller dimensionality might become more valuable than it is today.
- A user-visible failure mode emerges from the e5 prefix discipline. For example, people running `marbles-mcp` against a fork that forgets the `passage:` prefix and produces silently bad embeddings. If that recurs, we revisit whether the prefix-tuned approach is worth the footgun.
- Our chosen model is removed from HuggingFace *and* no permissive-license replacement of comparable quality exists. The §6.5 mirror keeps the existing version usable indefinitely, so this trigger is about adopting a *new* default, not about emergency recovery.

---

*ADR conventions: this document records the decision **and** the alternatives considered, so future readers can reconstruct why we chose what we did. If you disagree with the choice today, open an issue with the evidence; that is the intended use of this file.*
