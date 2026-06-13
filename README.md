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
| **MakeMarbles**       | SQLite              | CLI + MCP                   | AGPL client         | `marbles log "..."` from any shell      |
| Obsidian              | markdown vault      | 3rd-party plugins           | closed, free        | open app → file → write                 |
| Logseq                | files               | 3rd-party plugins           | yes (AGPL)          | open app → write                        |
| Notion                | cloud               | REST API (rate-limited)     | no                  | open app → page → block                 |
| Mem.ai / Reflect      | cloud               | closed AI features only     | no                  | cloud-only                              |
| Apple Notes / Bear    | local               | none                        | no                  | open app                                |
| Screenpipe            | local               | passive ingestion only      | yes                 | continuous screen recording             |

MakeMarbles makes a narrow bet: **the user is comfortable in a terminal, spends part of their day inside an LLM agent, and wants their journal addressable from both sides without uploading it to a cloud service.** If you're not that person, Obsidian and Logseq are more polished. If you don't mind cloud, Mem.ai is more AI-feature-rich. MakeMarbles exists for the gap they leave open.

The two I/O channels share one implementation:

- **Human channel** — a CLI you call from a shell alias, terminal, or iOS Shortcut.
- **LLM channel** — an MCP server exposing the same operations as tools, so agents capture and search inside their own context.

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
   │ marbles CLI  │              │  MCP server  │
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

## Capturing multi-line notes

`marbles log` accepts content through five paths. Pick whichever fits the moment:

```bash
# 1. Editor — opens $EDITOR (vim/nano/code) with a markdown template.
#    Lines starting with '#' are stripped, git-commit style. Best for prose.
marbles log -e

# 2. Pipe — anything on stdin becomes the note body, newlines preserved.
printf 'line one\nline two\n' | marbles log
git log -1 --pretty=%B | marbles log -t commit

# 3. Heredoc — single-shot block from the shell.
marbles log "$(cat <<'EOF'
para one
para two with **markdown**
EOF
)"

# 4. File redirect — turn an existing draft into a note.
marbles log < draft.md

# 5. Shell escapes — zsh/bash $'...' interprets \n.
marbles log $'quick\nmulti-line\nthought'
```

For scripts and agents, every command takes `--json` for structured output:

```bash
$ marbles log "shipping notes" --json
{"id":"01KV0F4HH...","content":"shipping notes","tag":null,"created_at":"2026-06-13T12:26:03Z"}

$ marbles search "marathon" --json | jq '.[].content'
```

Prefer staying in one session? `marbles shell` drops into an interactive REPL with command history and tab completion — `log -e` works inside it too.

## Deleting notes

Every `recent` / `search` row carries a 12-character id prefix; pass it to `rm`:

```bash
$ marbles recent
│ 01KV0FJ0RX34 │ 06-13 05:33 │       │ third note  │

$ marbles rm 01KV0FJ0RX34
delete 01KV0FJ0  third note
confirm? [y/N]: y
✓ deleted 01KV0FJ0
```

Pass `-y` to skip confirmation; non-tty contexts (pipes, agents) skip it automatically. Ambiguous prefixes are refused with the candidate list instead of guessing.

---

## MCP server — wire marbles into your agent

`marbles-mcp` exposes two tools to any MCP-compatible client (Claude Desktop, Claude Code, Cursor, Cline, Continue):

| Tool           | Signature                                       | Purpose                                 |
| -------------- | ----------------------------------------------- | --------------------------------------- |
| `add_note`     | `add_note(content: str, tag: str \| None)`      | Capture a marble from inside the agent  |
| `search_notes` | `search_notes(query: str, limit: int = 10)`     | Recall prior context before answering   |

Both tools share the same `core/` storage as the CLI — no parallel implementation, no schema drift.

### Install

```bash
uv sync --extra mcp           # or: pip install -e '.[mcp]'
```

### Claude Desktop / Cursor / Cline

Add to your client's MCP config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "marbles": {
      "command": "/absolute/path/to/makemarbles/.venv/bin/marbles-mcp"
    }
  }
}
```

### Claude Code

```bash
claude mcp add marbles /absolute/path/to/makemarbles/.venv/bin/marbles-mcp
```

After restart, the agent can ask "remember that I decided to use FastMCP" → `add_note`, or "what did I say about kafka rebalances last week?" → `search_notes`. The database at `~/.marbles/marbles.db` is the same one your CLI writes to.

---

## What's shipped (v0.1.0-alpha)

- **CLI**: `log`, `recent`, `search`, `count`, `rm`, `shell`
- **Input**: positional arg, stdin pipe, `--editor` for multi-line composition
- **Output**: pretty tables by default, `--json` on every command for scripting and agent use
- **MCP server**: `marbles-mcp` with `add_note` + `search_notes` tools (stdio transport)
- **Storage**: local SQLite at `~/.marbles/marbles.db` (single file, no daemon)
- **Search**: FTS5 keyword index with Porter stemming + BM25 ranking; user queries are escaped so `C++` and stray operators don't crash
- **IDs**: ULID — sortable by creation time without a separate timestamp column

## Roadmap

| Version | Adds                                          | Status        |
| ------- | --------------------------------------------- | ------------- |
| v0.1    | CLI + FTS5 keyword search + MCP server        | shipped       |
| v0.2    | Local vector search (ONNX, multilingual)      | planned       |
| v0.3    | Hybrid retrieval (FTS5 + vector via RRF)      | planned       |
| v0.4    | Cross-device sync (Cloudflare Durable Objects)| planned       |

Sync remains optional — the local SQLite is always the source of truth.

---

## Design decisions

Non-trivial choices are recorded as ADRs in [`docs/adr/`](./docs/adr/). Each one captures the context, the alternatives considered, and the tradeoffs accepted — so a future reader (or contributor) can challenge the decision when conditions change rather than reverse-engineering it from code.

- [**2026-06-03** — Product positioning, open-source strategy, business model](./docs/adr/2026-06-03-product-positioning.md)
- [**2026-06-13** — Embedding model selection for local semantic search](./docs/adr/2026-06-13-embedding-model.md)

---

## License

AGPL-3.0-or-later for the client. The future sync server will be closed-source — this is an intentional "open core" split: forks and self-hosting stay open and free; the hosted sync service is the commercial offering.
