"""MCP server exposing marbles as tools for LLM agents.

Thin wrapper over the same core/ module the CLI uses — same code path,
no parallel implementation to drift."""

from mcp.server.fastmcp import FastMCP

from core.models import Note
from core.storage import Storage

mcp = FastMCP("marbles")


def _storage() -> Storage:
    return Storage()


@mcp.tool()
def add_note(content: str, tag: str | None = None) -> dict:
    """Capture a new note in the local marbles journal.

    Use this when the user shares an idea, decision, observation, or
    fragment worth remembering, or when you produce something the user
    asked to keep. Content can be multi-line markdown. Returns the
    persisted note including its ULID."""
    if not content.strip():
        raise ValueError("content must not be empty.")
    note = Note(content=content, tag=tag)
    _storage().add(note)
    return note.model_dump(mode="json")


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> list[dict]:
    """Full-text keyword search across the user's marbles.

    Use this before answering anything that might reference prior
    captured context. Query is matched as literal phrase tokens against
    the FTS5 index (Porter stemming, BM25 ranking); operators like AND
    are treated as literal words. Empty query returns []."""
    notes = _storage().search(query, limit=limit)
    return [n.model_dump(mode="json") for n in notes]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
