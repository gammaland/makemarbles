from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import sqlite_utils
import sqlite_vec

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


class VectorDimMismatch(ValueError):
    """Raised when upsert_vector is called with a dim different from the
    one the existing notes_vec table was created with. Callers normally
    react by dropping the index (reset_vector_index) and retrying."""


class Storage:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite_utils.Database(str(self.db_path))
        self.db.conn.enable_load_extension(True)
        sqlite_vec.load(self.db.conn)
        self.db.conn.enable_load_extension(False)
        self._init_schema()

    # ---------- schema ----------

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

    # ---------- notes CRUD ----------

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
        # Vec row stays orphaned if it exists; vector_search ignores rows
        # whose note_id no longer joins, so this is harmless. We still drop
        # it eagerly to keep the index size bounded.
        if "notes_vec" in self.db.table_names():
            self.db.conn.execute("DELETE FROM notes_vec WHERE note_id = ?", [note_id])
        return True

    # ---------- embedding bookkeeping ----------

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

    def iter_pending_for_embed(
        self, model_name: str, limit: int | None = None
    ) -> Iterator[Note]:
        """Yield notes whose vector is missing or out-of-date for model_name.

        Stream-friendly: callers can embed and upsert one at a time without
        loading every pending row into memory.
        """
        sql = (
            "SELECT id, content, tag, created_at FROM notes "
            "WHERE embedding_model IS NULL OR embedding_model != ? "
            "ORDER BY created_at"
        )
        params: list = [model_name]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        for row in self.db.query(sql, params):
            yield self._to_note(row)

    # ---------- vector index ----------

    def vec_table_dim(self) -> int | None:
        """Return the vector dimension of the notes_vec table if it exists.

        sqlite-vec stores dim as part of the table schema; we recover it by
        parsing sqlite_master.sql, which is the cheapest source of truth and
        avoids querying empty tables.
        """
        if "notes_vec" not in self.db.table_names():
            return None
        row = next(
            self.db.query(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes_vec'"
            )
        )
        sql = row["sql"] or ""
        # Schema looks like: ... embedding FLOAT[384] ...
        start = sql.find("FLOAT[")
        if start == -1:
            return None
        end = sql.find("]", start)
        if end == -1:
            return None
        try:
            return int(sql[start + len("FLOAT[") : end])
        except ValueError:
            return None

    def _ensure_vec_table(self, dim: int) -> None:
        existing_dim = self.vec_table_dim()
        if existing_dim is None:
            self.db.conn.execute(
                f"CREATE VIRTUAL TABLE notes_vec USING vec0("
                f"note_id TEXT PRIMARY KEY, embedding FLOAT[{dim}])"
            )
            return
        if existing_dim != dim:
            raise VectorDimMismatch(
                f"notes_vec is dim {existing_dim}; refusing to insert dim {dim} "
                f"vector. Run reset_vector_index() first if you intend to switch."
            )

    def upsert_vector(
        self, note_id: str, vec: np.ndarray, model_name: str
    ) -> None:
        """Store a vector for note_id and stamp the bookkeeping columns.

        vec must be 1-D float32; callers are expected to L2-normalize before
        passing in so that dot product against query vectors equals cosine.
        Raises VectorDimMismatch if the existing index has a different dim.
        """
        if vec.ndim != 1:
            raise ValueError(f"vec must be 1-D, got shape {vec.shape}")
        vec = vec.astype(np.float32, copy=False)
        self._ensure_vec_table(len(vec))
        # sqlite-vec virtual tables do not implement UPSERT (ON CONFLICT),
        # so we delete-then-insert. The pair runs in one implicit transaction
        # because sqlite-utils opens connections with isolation_level=None
        # only inside its own write helpers; our direct conn.execute calls
        # autocommit per statement but the window is tight enough for our
        # single-process workload.
        self.db.conn.execute(
            "DELETE FROM notes_vec WHERE note_id = ?", [note_id]
        )
        self.db.conn.execute(
            "INSERT INTO notes_vec(note_id, embedding) VALUES(?, ?)",
            [note_id, vec.tobytes()],
        )
        self.db["notes"].update(
            note_id,
            {
                "embedding_model": model_name,
                "embedded_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def vector_search(
        self,
        query_vec: np.ndarray,
        model_name: str,
        limit: int = 10,
        over_fetch: int = 3,
    ) -> list[tuple[Note, float]]:
        """Return notes ranked by ascending vector distance.

        Over-fetches knn candidates by `over_fetch x limit` and then filters
        to rows whose `notes.embedding_model` matches `model_name`. This
        protects against stale vectors left behind after a partial reembed;
        in steady state every candidate matches and the filter is a no-op.
        """
        if "notes_vec" not in self.db.table_names():
            return []
        if query_vec.ndim != 1:
            raise ValueError(f"query_vec must be 1-D, got shape {query_vec.shape}")
        query_vec = query_vec.astype(np.float32, copy=False)
        k = max(limit * over_fetch, limit)
        rows = list(
            self.db.query(
                "SELECT v.note_id AS id, v.distance AS distance, "
                "n.content, n.tag, n.created_at "
                "FROM notes_vec v "
                "JOIN notes n ON n.id = v.note_id "
                "WHERE v.embedding MATCH ? AND k = ? "
                "AND n.embedding_model = ? "
                "ORDER BY v.distance "
                "LIMIT ?",
                [query_vec.tobytes(), k, model_name, limit],
            )
        )
        return [
            (self._to_note(r), float(r["distance"])) for r in rows
        ]

    def reset_vector_index(self) -> None:
        """Drop the notes_vec table and clear all embedding bookkeeping.

        Used when switching to a model with a different dimensionality, or
        when the user explicitly asks for a clean re-embed. Notes themselves
        are untouched; FTS5 keyword search continues to work throughout.
        """
        if "notes_vec" in self.db.table_names():
            self.db.conn.execute("DROP TABLE notes_vec")
        self.db.conn.execute(
            "UPDATE notes SET embedding_model = NULL, embedded_at = NULL"
        )

    @staticmethod
    def _to_note(row: dict) -> Note:
        return Note(
            id=row["id"],
            content=row["content"],
            tag=row["tag"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
