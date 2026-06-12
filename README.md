# MakeMarbles

> Local-first AI journal with dual-channel I/O — human via CLI, LLM agents via MCP.

**Status**: `v0.1.0-alpha` (in active development) — see [docs/mvp_sprint_2026-06.md](docs/mvp_sprint_2026-06.md) for current sprint scope.

## Thesis

The two ways a personal knowledge layer needs to be written into and read from:

- **Human channel** — a CLI you can call from a terminal, shell alias, or iOS Shortcut. Zero schema overhead.
- **LLM channel** — an MCP server exposing the same operations as tools, so AI agents can capture and search notes inside their own context.

Both channels read and write the same SQLite store. No API contract drift. No background daemon.

## Quick start

```bash
# Install (once published)
pipx install makemarbles

# Capture
marbles log "Idea: weekly review automation"

# Read
marbles recent --days 7
marbles search "review"
```

## Architecture

See [docs/design-decisions.md](docs/design-decisions.md) and [docs/roadmap.md](docs/roadmap.md).

## License

AGPL-3.0-or-later (client). Future sync server is closed-source. Per-component license matrix in `docs/design-decisions.md`.
