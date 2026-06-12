from datetime import datetime, timezone

from pydantic import BaseModel, Field
from ulid import ULID


def _new_id() -> str:
    return str(ULID())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Note(BaseModel):
    id: str = Field(default_factory=_new_id)
    content: str
    tag: str | None = None
    created_at: datetime = Field(default_factory=_now)
