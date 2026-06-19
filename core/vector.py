"""Embedding engine for semantic search.

Loads an ONNX-exported sentence embedding model and produces unit-normalized
vectors. The public API exposes embed_passage / embed_query rather than a
single embed(text, role) so that E5's instruction-prefix discipline is visible
at every call site.

See:
- docs/adr/2026-06-13-embedding-model.md   — why multilingual-e5-small
- docs/private/embedding-stack.md          — runtime stack and tokenizer details
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "

DEFAULT_MAX_LENGTH = 512


@dataclass(frozen=True)
class ModelConfig:
    """Static metadata about a loaded embedding model."""

    name: str
    dim: int
    max_length: int = DEFAULT_MAX_LENGTH


KNOWN_MODELS: dict[str, ModelConfig] = {
    "multilingual-e5-small": ModelConfig(
        name="multilingual-e5-small", dim=384
    ),
}


def get_known_model(name: str) -> ModelConfig:
    """Look up a model's static metadata by name.

    Storage uses this to size the vec0 table before any embedding actually
    runs. Adding a new model means adding an entry here plus an export to
    GitHub Releases; see docs/adr/2026-06-13-embedding-model.md §10 for the
    revisit triggers.
    """
    if name not in KNOWN_MODELS:
        raise KeyError(
            f"Unknown embedding model {name!r}. Add it to core.vector.KNOWN_MODELS."
        )
    return KNOWN_MODELS[name]


class _NodeArg(Protocol):
    name: str


class InferenceSession(Protocol):
    """Minimal slice of ort.InferenceSession we depend on, for testability."""

    def run(
        self, output_names: list[str] | None, input_feed: dict[str, np.ndarray]
    ) -> list[np.ndarray]: ...

    def get_inputs(self) -> list[_NodeArg]: ...


def _select_providers() -> list[str]:
    """Pick ONNX Runtime execution providers based on platform.

    macOS gets CoreML first (real speedup on Apple Silicon for small models)
    and falls back to CPU for unsupported ops. Other platforms go CPU-only;
    we deliberately do not depend on CUDA or DirectML.
    """
    if platform.system() == "Darwin":
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _build_session(model_path: Path) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(
        str(model_path), sess_options=opts, providers=_select_providers()
    )


def _mean_pool_and_normalize(
    last_hidden: np.ndarray, attention_mask: np.ndarray
) -> np.ndarray:
    """Attention-masked mean pool, then L2 normalize.

    last_hidden: (B, T, D) float
    attention_mask: (B, T) int
    returns: (B, D) float, each row unit-norm so dot product == cosine similarity.
    """
    mask = attention_mask[..., None].astype(np.float32)
    summed = (last_hidden * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1.0)
    pooled = summed / counts
    norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
    return pooled / norms


class EmbeddingEngine:
    """Produces unit-normalized embeddings for marbles using an E5-family model."""

    def __init__(
        self,
        model_dir: Path,
        config: ModelConfig,
        *,
        session: InferenceSession | None = None,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        """Construct an engine.

        Tests inject `session` and `tokenizer` to bypass ONNX file I/O and the
        SentencePiece tokenizer. Production code passes neither and we load
        both from `model_dir`.
        """
        self.model_dir = model_dir
        self.config = config

        if tokenizer is None:
            tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
            tok.enable_truncation(max_length=config.max_length)
            tokenizer = tok
        self.tokenizer = tokenizer

        if session is None:
            session = _build_session(model_dir / "model.onnx")
        self.session = session

    def embed_passage(self, text: str) -> np.ndarray:
        """Embed a note for storage. Uses E5's 'passage:' prefix."""
        return self._embed(PASSAGE_PREFIX + text)

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a search query. Uses E5's 'query:' prefix."""
        return self._embed(QUERY_PREFIX + text)

    def _embed(self, prepared_text: str) -> np.ndarray:
        enc = self.tokenizer.encode(prepared_text)
        input_ids = np.array([enc.ids], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask], dtype=np.int64)
        feeds: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        # E5 / XLM-R variants ship a token_type_ids input; supply zeros when the
        # exported graph asks for it, otherwise leave it out.
        if any(i.name == "token_type_ids" for i in self.session.get_inputs()):
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        (last_hidden,) = self.session.run(["last_hidden_state"], feeds)
        pooled = _mean_pool_and_normalize(last_hidden, attention_mask)
        return pooled[0]
