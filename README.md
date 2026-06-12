# MakeMarbles

> Local-first AI journal with dual-channel I/O — human via CLI, LLM agents via MCP.

**Status**: `v0.1.0-alpha` · Python 3.12+ · AGPL-3.0-or-later

---

## Why

You are talking to an LLM agent in your terminal. A relevant thought hits — about the project, a meeting earlier, a half-baked idea. You don't want to break flow to open a notes app, find the right vault, type, and switch back. Tomorrow, in a fresh agent session, you want that thought retrievable — by you, and by the agent itself.

The problem MakeMarbles addresses: **frictionless capture from the shell, durable retrieval by both human and AI agent, with notes stored locally as the source of truth.**

Existing tools each cover part of this, but not the combination:

| Tool                  | Local-first         | LLM-agent callable          | Open source         | Capture path                            |
| --------------------- | ------------------- | --------------------------- | ------------------- | --------------------------------------- |
| **MakeMarbles**       | SQLite              | CLI + MCP (v0.2)            | AGPL client         | `marbles log "..."` from any shell      |
| Obsidian              | markdown vault      | 3rd-party plugins           | closed, free        | open app → file → write                 |
| Logseq                | files               | 3rd-party plugins           | yes (AGPL)          | open app → write                        |
| Notion                | cloud               | REST API (rate-limited)     | no                  | open app → page → block                 |
| Mem.ai / Reflect      | cloud               | closed AI features only     | no                  | cloud-only                              |
| Apple Notes / Bear    | local               | none                        | no                  | open app                                |
| Screenpipe            | local               | passive ingestion only      | yes                 | continuous screen recording             |

MakeMarbles makes a narrow bet: **the user is comfortable in a terminal, spends part of their day inside an LLM agent, and wants their journal addressable from both sides without uploading it to a cloud service.** If you're not that person, Obsidian and Logseq are more polished. If you don't mind cloud, Mem.ai is more AI-feature-rich. MakeMarbles exists for the gap they leave open.

The two I/O channels share one implementation:

- **Human channel** — a CLI you call from a shell alias, terminal, or iOS Shortcut.
- **LLM channel** — an MCP server (v0.2) exposing the same operations as tools, so agents capture and search inside their own context.

No API contract drift, no background daemon, no cloud round-trip.

---

## Architecture: Dual-channel I/O

```
   ┌──────────┐                  ┌──────────────┐
   │  Human   │                  │   LLM Agent  │
   │ (shell)  │                  │  (Claude...) │
   └────┬─────┘                  └──────┬───────┘
        │                               │
        ▼                               ▼
   ┌──────────────┐              ┌──────────────┐
   │ marbles CLI  │              │  MCP server  │  ← v0.2
   │  (Typer)     │              │  (thin shim) │
   └──────┬───────┘              └──────┬───────┘
          │                             │
          └──────────────┬──────────────┘
                         ▼
              ┌─────────────────────┐
              │   core/  (shared)   │
              │ models + storage    │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │   SQLite + FTS5     │
              │ ~/.marbles/marbles  │
              └─────────────────────┘
```

The MCP server is a **thin wrapper over the same `core/` module the CLI uses** — same code path, no parallel implementation to drift.

---

## Quick start

```bash
# Clone (PyPI release pending)
git clone https://github.com/gammaland/makemarbles
cd makemarbles

# Install with uv (recommended)
uv sync
uv run marbles log "Idea: weekly review automation" -t idea

# Or with pip
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
marbles log "Idea: weekly review automation" -t idea
```

Sample session:

```
$ marbles log "Hybrid search beats keyword-only on recall" -t research
✓ 01KTZ0ER  Hybrid search beats keyword-only on recall

$ marbles log "Plan: ship MCP server by Friday"
✓ 01KTZ0ES  Plan: ship MCP server by Friday

$ marbles recent --days 1
                            last 1 day(s)
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ when          ┃ tag        ┃ content                                    ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 06-12 15:50   │            │ Plan: ship MCP server by Friday            │
│ 06-12 15:50   │ research   │ Hybrid search beats keyword-only on recall │
└───────────────┴────────────┴────────────────────────────────────────────┘

$ marbles search "hybrid"
                          matches for 'hybrid'
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ when          ┃ tag        ┃ content                                    ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 06-12 15:50   │ research   │ Hybrid search beats keyword-only on recall │
└───────────────┴────────────┴────────────────────────────────────────────┘
```

---

## What's shipped (v0.1.0-alpha)

- **CLI**: `log`, `recent`, `search`, `count`
- **Storage**: local SQLite at `~/.marbles/marbles.db` (single file, no daemon)
- **Search**: FTS5 keyword index with Porter stemming + BM25 ranking
- **IDs**: ULID — sortable by creation time without a separate timestamp column

## Roadmap

| Version | Adds                                          | Status        |
| ------- | --------------------------------------------- | ------------- |
| v0.1    | CLI + FTS5 keyword search                     | shipped       |
| v0.2    | MCP server (`add_note`, `search_notes`)       | in sprint     |
| v0.3    | Local vector search (ONNX, multilingual)      | planned       |
| v0.4    | Hybrid retrieval (FTS5 + vector via RRF)      | planned       |
| v0.5    | Cross-device sync (Cloudflare Durable Objects)| planned       |

Sync remains optional — the local SQLite is always the source of truth.

---

## License

AGPL-3.0-or-later for the client. The future sync server will be closed-source — this is an intentional "open core" split: forks and self-hosting stay open and free; the hosted sync service is the commercial offering.
