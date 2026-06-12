# MakeMarbles

> Local-first AI journal with dual-channel I/O — human via CLI, LLM agents via MCP.

**Status**: `v0.1.0-alpha` · Python 3.12+ · AGPL-3.0-or-later

---

## Why

Most note tools are built for humans reading on screen. Today, a personal knowledge layer also needs to be **callable by AI agents** — to capture context during an LLM session, to search prior notes inside an agent's reasoning loop, and to do so without uploading your private journal to a cloud service.

Existing note apps treat AI as a UI feature glued on the side. MakeMarbles treats it as a first-class I/O channel:

- **Human channel** — a CLI (`marbles log …`) you can call from a shell alias, terminal, or iOS Shortcut.
- **LLM channel** — an MCP server exposing the same operations as tools, so agents capture and search in their own context.

Both channels read and write the same local SQLite store. No API contract drift, no background daemon, no cloud round-trip.

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

Full design rationale: [docs/roadmap.md](docs/roadmap.md), [docs/design-decisions.md](docs/design-decisions.md).

---

## Design notes

- [`docs/design-decisions.md`](docs/design-decisions.md) — why FTS5, why ULID, why SQLite as source of truth, why no sync in v0.1
- [`docs/roadmap.md`](docs/roadmap.md) — phased plan through v0.5
- [`docs/mvp_sprint_2026-06.md`](docs/mvp_sprint_2026-06.md) — current sprint plan and acceptance log

---

## License

AGPL-3.0-or-later for the client. The future sync server will be closed-source — this is an intentional "open core" split: forks and self-hosting stay open and free; the hosted sync service is the commercial offering.
