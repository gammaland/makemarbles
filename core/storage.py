from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite_utils

from core.models import Note

DEFAULT_DB_PATH = Path.home() / ".marbles" / "marbles.db"


def _safe_fts_query(raw: str) -> str | None:
    """Quote each whitespace-separated token as an FTS5 phrase.

    Phrases bypass operator parsing, so 'C++', 'AND', or stray quotes
    become literal search terms instead of syntax errors. Empty input
    returns None so callers can short-circuit."""
    tokens = raw.split()
    if not tokens:
        return None
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class Storage:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite_utils.Database(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        if "notes" not in self.db.table_names():
            self.db["notes"].create(
                {"id": str, "content": str, "tag": str, "created_at": str},
                pk="id",
            )
            self.db["notes"].enable_fts(
                ["content", "tag"], create_triggers=True, tokenize="porter unicode61"
            )
        self._migrate_embedding_columns()

    def _migrate_embedding_columns(self) -> None:
        """Idempotently add columns the v0.2 semantic-search path needs.

        These are added even before the embedding engine is wired in so
        that storage and the `reembed --dry-run` command can already speak
        about pending rows. See docs/adr/2026-06-13-embedding-model.md §7.
        """
        existing = {c.name for c in self.db["notes"].columns}
        if "embedding_model" not in existing:
            self.db["notes"].add_column("embedding_model", str)
        if "embedded_at" not in existing:
            self.db["notes"].add_column("embedded_at", str)

    def add(self, note: Note) -> str:
        self.db["notes"].insert(
            {
                "id": note.id,
                "content": note.content,
                "tag": note.tag,
                "created_at": note.created_at.isoformat(),
            }
        )
        return note.id

    def get(self, note_id: str) -> Note | None:
        rows = list(self.db["notes"].rows_where("id = ?", [note_id], limit=1))
        return self._to_note(rows[0]) if rows else None

    def recent(self, days: int = 7, limit: int = 100) -> list[Note]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db["notes"].rows_where(
            "created_at >= ?",
            [cutoff],
            order_by="created_at desc",
            limit=limit,
        )
        return [self._to_note(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[Note]:
        fts = _safe_fts_query(query)
        if fts is None:
            return []
        rows = self.db["notes"].search(fts, limit=limit)
        return [self._to_note(r) for r in rows]

    def count(self) -> int:
        return self.db["notes"].count

    def pending_embed_count(self, model_name: str) -> int:
        """Count notes that need (re-)embedding for the given target model.

        Pending = either never embedded, or embedded under a different model.
        Powers `marbles reembed --dry-run` and progress reporting during
        a reembed pass.
        """
        row = next(
            self.db.query(
                "SELECT COUNT(*) AS n FROM notes "
                "WHERE embedding_model IS NULL OR embedding_model != ?",
                [model_name],
            )
        )
        return int(row["n"])

    def find_by_prefix(self, prefix: str, limit: int = 10) -> list[Note]:
        rows = self.db["notes"].rows_where(
            "id LIKE ?", [prefix + "%"], order_by="created_at desc", limit=limit
        )
        return [self._to_note(r) for r in rows]

    def delete(self, note_id: str) -> bool:
        try:
            self.db["notes"].delete(note_id)
        except sqlite_utils.db.NotFoundError:
            return False
        return True

    @staticmethod
    def _to_note(row: dict) -> Note:
        return Note(
            id=row["id"],
            content=row["content"],
            tag=row["tag"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
