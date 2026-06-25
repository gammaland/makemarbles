# MakeMarbles

Local-first personal knowledge layer with two symmetric I/O channels: humans through a CLI, LLM agents through an MCP server. Notes live in a single SQLite file on the user's machine.

For the full picture of what is shipped, designed, or planned, see [`docs/SPEC.md`](./docs/SPEC.md). Per-decision rationale lives in [`docs/adr/`](./docs/adr/). This file is the entry-point summary, kept short on purpose.

## Tech stack (what is actually wired today)

- Python 3.12+, Typer, Rich, Pydantic, `sqlite-utils`, `python-ulid`, `prompt-toolkit`
- SQLite with FTS5 (Porter stemming, BM25, query escaping for special chars)
- FastMCP for the MCP server (stdio transport)

Dependencies that appear in `pyproject.toml` for v0.2 work (ONNX Runtime, `tokenizers`, NumPy) are scaffolded but not yet exercised at runtime; tests guard them with `pytest.importorskip`.

## Project structure

```
makemarbles/
├── core/           # models.py, storage.py, vector.py (skeleton), config.py
├── cli/            # main.py: Typer commands
├── marbles_mcp/    # MCP server (thin wrapper over core/)
├── tests/          # pytest suite
├── tools/          # build-time scripts (planned: export_onnx.py)
├── docs/           # SPEC.md, adr/, private/ (gitignored)
└── pyproject.toml
```

`worker/` (Cloudflare Durable Objects sync server) is in Phase 2: per-account `AccountDO` (push/pull/device registry), the D1 account registry, and password-gated device enrollment are implemented and tested; client `marbles login`/`logout` work and the full register→enroll→push→pull→decrypt loop is verified end to end against `wrangler dev`. Not built yet: device-signed pull, `is_pro` gate, live WebSocket, `sync`/`devices` CLI, pull replay. See `worker/README.md`.

## Key commands

```bash
# Shipped (v0.1.0)
marbles log "content" [-t tag] [-e]   # capture; -e opens $EDITOR
marbles recent [--days N]             # browse
marbles search "query"                # FTS5 keyword search
marbles rm <id-prefix> [-y]           # delete
marbles count
marbles shell                         # interactive REPL

# Scaffolded for v0.2 (not fully wired)
marbles reembed [--model NAME] [--dry-run]   # --dry-run works; full path refuses cleanly

# Sync, Phase 2 (login/logout shipped; rest designed)
marbles login | logout                        # register/enroll device; verified e2e
marbles sync [--once] | sync status           # designed, not built
marbles devices list | revoke <device-id>     # designed, not built
```

All output commands support `--json` for scripting and agent use.

## Design principles

1. **Local-first**: SQLite is the source of truth. The sync server, when it ships, is an encrypted relay.
2. **Dual-channel I/O**: CLI for humans, MCP for LLM agents, single `core/` backing both.
3. **Append-mostly**: notes rarely update; the sync model leans into this with row-level last-write-wins.
4. **Zero-knowledge sync**: every op payload is encrypted client-side before it leaves the device. See `docs/SPEC.md` §7.
5. **No background daemon**: every command is short-lived. No watchers, no schedulers.

## Detailed design

- [`docs/SPEC.md`](./docs/SPEC.md): single source of truth for the system's current shape.
- [`docs/adr/`](./docs/adr/): per-decision records, including positioning (2026-06-03) and embedding model selection (2026-06-13).
