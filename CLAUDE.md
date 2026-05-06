# MakeMarbles

Local-first personal knowledge layer for AI agents.

## Project Overview

CLI-first tool for zero-friction note capture with semantic search, cross-device sync via Cloudflare Durable Objects, and AI-native integration (Claude Code skills consume data via CLI).

## Tech Stack

- Python 3.12+, Typer, Rich, Pydantic, httpx
- SQLite + sqlite-vec (vector search) + FTS5 (keyword search)
- ONNX Runtime (paraphrase-multilingual-MiniLM-L12-v2)
- Cloudflare Durable Objects (sync)
- AES-256-GCM encryption at rest

## Project Structure

```
makemarbles/
├── core/           # Core logic: models, storage, vector, search, sync, crypto
├── cli/            # Typer CLI commands
├── mcp/            # MCP server (thin CLI wrapper, P2)
├── worker/         # CF DO sync worker (TypeScript)
├── tests/          # pytest
├── docs/           # Design docs and decisions
└── pyproject.toml
```

## Key Commands

```bash
marbles log "content"        # Write a note
marbles recent [--days N]    # Recent notes
marbles search "query"       # Hybrid semantic + keyword search
marbles insight              # AI daily insight
marbles sync status          # Sync status
marbles login                # OAuth Device Flow
```

## Design Principles

1. CLI-first: LLMs call CLI directly, zero schema overhead
2. Local-first: SQLite is source of truth, cloud is encrypted relay
3. AI-native: structured for AI read/write/search, not human browsing
4. Zero-friction: write now, AI organizes later (async processing)
5. Append-mostly: notes rarely update, simplifies sync

## Detailed Design

See `docs/design-decisions.md` for full architecture rationale.
