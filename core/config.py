"""User configuration loaded from ~/.marbles/config.toml.

The config file is optional. When absent or partial, every field falls back
to a sensible default tied to the v0.2 embedding decision (see
docs/adr/2026-06-13-embedding-model.md).

Schema:

    [embedding]
    model_name = "multilingual-e5-small"
    models_dir = "~/.marbles/models"   # optional; default shown
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".marbles" / "config.toml"
DEFAULT_MODEL_NAME = "multilingual-e5-small"
DEFAULT_MODELS_DIR = Path.home() / ".marbles" / "models"


@dataclass(frozen=True)
class EmbeddingConfig:
    model_name: str = DEFAULT_MODEL_NAME
    models_dir: Path = DEFAULT_MODELS_DIR

    @property
    def model_dir(self) -> Path:
        """Path to the directory holding this model's weights and tokenizer."""
        return self.models_dir / self.model_name


@dataclass(frozen=True)
class Config:
    embedding: EmbeddingConfig = EmbeddingConfig()


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def load_config(path: Path | None = None) -> Config:
    """Read config from the given path, or DEFAULT_CONFIG_PATH if None.

    Missing file or missing sections fall through to defaults. Unknown keys
    are ignored silently so future versions can add fields without breaking
    older installs.
    """
    path = path or DEFAULT_CONFIG_PATH
    if not path.is_file():
        return Config()

    with path.open("rb") as f:
        raw = tomllib.load(f)

    emb_raw = raw.get("embedding", {})
    embedding = EmbeddingConfig(
        model_name=emb_raw.get("model_name", DEFAULT_MODEL_NAME),
        models_dir=_expand(emb_raw["models_dir"])
        if "models_dir" in emb_raw
        else DEFAULT_MODELS_DIR,
    )
    return Config(embedding=embedding)
