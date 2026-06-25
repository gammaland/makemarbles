"""Unit tests for the embedding engine.

Strategy: mock out the ONNX session and the tokenizer so logic can be tested
without downloading a real ~470 MB model. Verifies prefix discipline, pooling
math, attention-mask handling, normalization, and token_type_ids feed gating.

End-to-end tests against the real model live in tests/integration/ (deferred).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

# These deps are needed to run the embedding engine. When the environment
# hasn't sync'd them yet (offline / pre-Phase-4-deps), skip the whole module
# rather than crash pytest collection.
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from core.vector import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    EmbeddingEngine,
    ModelConfig,
    _cls_pool_and_normalize,
    _mean_pool_and_normalize,
    _select_providers,
    get_known_model,
)


# ---------- fixtures: minimal stand-ins for ORT session and Tokenizer ----------


@dataclass
class FakeEncoding:
    ids: list[int]
    attention_mask: list[int]


class FakeTokenizer:
    """Records the most recently encoded string and yields predictable ids."""

    def __init__(self, fixed_len: int = 4) -> None:
        self.fixed_len = fixed_len
        self.last_text: str | None = None

    def encode(self, text: str) -> FakeEncoding:
        self.last_text = text
        ids = list(range(1, self.fixed_len + 1))
        mask = [1] * self.fixed_len
        return FakeEncoding(ids=ids, attention_mask=mask)


@dataclass
class FakeInput:
    name: str


class FakeSession:
    """Returns a deterministic last_hidden_state derived from input_ids.

    For each token id k, the hidden vector is [k, k, k, ...] in the given dim.
    With our FakeTokenizer ids = [1, 2, 3, 4] and dim = 3, mean over tokens is
    [2.5, 2.5, 2.5]; normalized this is [1, 1, 1] / sqrt(3).
    """

    def __init__(
        self,
        dim: int = 3,
        inputs: list[str] | None = None,
        captured_feeds: list[dict[str, np.ndarray]] | None = None,
    ) -> None:
        self.dim = dim
        self._inputs = [FakeInput(name=n) for n in (inputs or ["input_ids", "attention_mask"])]
        self.captured_feeds = captured_feeds if captured_feeds is not None else []

    def get_inputs(self) -> list[FakeInput]:
        return self._inputs

    def run(
        self, output_names: list[str] | None, input_feed: dict[str, np.ndarray]
    ) -> list[np.ndarray]:
        self.captured_feeds.append({k: v.copy() for k, v in input_feed.items()})
        ids = input_feed["input_ids"]  # (1, T)
        last_hidden = np.broadcast_to(ids[..., None], ids.shape + (self.dim,)).astype(
            np.float32
        )
        return [last_hidden]


def _make_engine(
    session: FakeSession | None = None,
    tokenizer: FakeTokenizer | None = None,
    dim: int = 3,
) -> EmbeddingEngine:
    session = session or FakeSession(dim=dim)
    tokenizer = tokenizer or FakeTokenizer()
    return EmbeddingEngine(
        model_dir=Path("/nonexistent"),
        config=ModelConfig(name="fake-model", dim=dim),
        session=session,
        tokenizer=tokenizer,
    )


# ---------- prefix discipline ----------


def test_embed_passage_prepends_passage_prefix():
    tok = FakeTokenizer()
    engine = _make_engine(tokenizer=tok)
    engine.embed_passage("hello world")
    assert tok.last_text == PASSAGE_PREFIX + "hello world"


def test_embed_query_prepends_query_prefix():
    tok = FakeTokenizer()
    engine = _make_engine(tokenizer=tok)
    engine.embed_query("what did I say about kafka")
    assert tok.last_text == QUERY_PREFIX + "what did I say about kafka"


def test_passage_and_query_paths_use_different_prefixes():
    tok = FakeTokenizer()
    engine = _make_engine(tokenizer=tok)
    engine.embed_passage("x")
    passage_text = tok.last_text
    engine.embed_query("x")
    query_text = tok.last_text
    assert passage_text != query_text
    assert passage_text.startswith("passage:")
    assert query_text.startswith("query:")


# ---------- output shape and normalization ----------


def test_embed_returns_unit_norm_vector():
    engine = _make_engine(dim=8)
    vec = engine.embed_passage("anything")
    assert vec.shape == (8,)
    assert np.isclose(np.linalg.norm(vec), 1.0)


def test_embed_query_returns_unit_norm_vector():
    engine = _make_engine(dim=8)
    vec = engine.embed_query("anything")
    assert np.isclose(np.linalg.norm(vec), 1.0)


def test_embed_uses_float_output():
    engine = _make_engine()
    vec = engine.embed_passage("anything")
    assert vec.dtype.kind == "f"


# ---------- pooling math ----------


def test_mean_pool_ignores_padded_positions():
    # Two tokens, dim 2; second token is padding.
    last_hidden = np.array([[[1.0, 0.0], [9.0, 9.0]]])  # (1, 2, 2)
    mask = np.array([[1, 0]])  # second position masked out
    pooled = _mean_pool_and_normalize(last_hidden, mask)
    # Mean over unmasked tokens = (1, 0); normalized stays (1, 0).
    assert np.allclose(pooled, np.array([[1.0, 0.0]]))


def test_mean_pool_averages_unmasked_tokens():
    last_hidden = np.array([[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]])
    mask = np.array([[1, 1, 1]])
    pooled = _mean_pool_and_normalize(last_hidden, mask)
    expected = np.array([1.0, 1.0, 1.0]) / np.sqrt(3.0)
    assert np.allclose(pooled[0], expected)


def test_mean_pool_handles_all_zero_mask_without_crashing():
    # Defensive: an all-padding row shouldn't divide by zero. Tokenizers won't
    # actually produce this, but the math should still be safe.
    last_hidden = np.zeros((1, 3, 4))
    mask = np.zeros((1, 3), dtype=np.int64)
    pooled = _mean_pool_and_normalize(last_hidden, mask)
    assert pooled.shape == (1, 4)
    assert not np.isnan(pooled).any()


# ---------- cls pooling (BGE-style) and per-model config ----------


def test_cls_pool_takes_first_token_and_normalizes():
    # First token points +x; later tokens point +y. CLS pooling must keep the
    # first token's direction, not the average.
    last_hidden = np.array([[[2.0, 0.0], [0.0, 9.0], [0.0, 9.0]]])  # (1, 3, 2)
    pooled = _cls_pool_and_normalize(last_hidden)
    assert np.allclose(pooled, np.array([[1.0, 0.0]]))


class _FixedSession:
    """Returns a fixed, non-parallel last_hidden so mean and cls pooling differ."""

    def get_inputs(self) -> list[FakeInput]:
        return [FakeInput(name="input_ids"), FakeInput(name="attention_mask")]

    def run(self, output_names, input_feed):  # noqa: ANN001
        # token 0 -> +x, tokens 1..3 -> +y; mean leans +y, cls stays +x.
        lh = np.array([[[5.0, 0.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]])
        return [lh]


def test_engine_dispatches_to_cls_pooling_when_configured():
    session = _FixedSession()
    eng_cls = EmbeddingEngine(
        model_dir=Path("/nonexistent"),
        config=ModelConfig(name="m", dim=2, pooling="cls"),
        session=session,
        tokenizer=FakeTokenizer(),
    )
    eng_mean = EmbeddingEngine(
        model_dir=Path("/nonexistent"),
        config=ModelConfig(name="m", dim=2, pooling="mean"),
        session=session,
        tokenizer=FakeTokenizer(),
    )
    v_cls = eng_cls.embed_passage("x")
    v_mean = eng_mean.embed_passage("x")
    assert np.allclose(v_cls, np.array([1.0, 0.0]))  # first-token direction
    assert not np.allclose(v_cls, v_mean)


def test_empty_prefix_model_does_not_prepend_anything():
    tok = FakeTokenizer()
    engine = EmbeddingEngine(
        model_dir=Path("/nonexistent"),
        config=ModelConfig(name="m", dim=3, query_prefix="", passage_prefix=""),
        session=FakeSession(dim=3),
        tokenizer=tok,
    )
    engine.embed_passage("raw note")
    assert tok.last_text == "raw note"
    engine.embed_query("raw query")
    assert tok.last_text == "raw query"


def test_known_models_registry_carries_benchmark_alternatives():
    mini = get_known_model("paraphrase-multilingual-MiniLM-L12-v2")
    assert mini.dim == 384 and mini.pooling == "mean" and mini.query_prefix == ""
    bge = get_known_model("bge-m3")
    assert bge.dim == 1024 and bge.pooling == "cls" and bge.passage_prefix == ""


# ---------- session feed construction ----------


def test_feeds_token_type_ids_when_graph_requires_it():
    captured: list[dict[str, np.ndarray]] = []
    session = FakeSession(
        dim=3,
        inputs=["input_ids", "attention_mask", "token_type_ids"],
        captured_feeds=captured,
    )
    engine = _make_engine(session=session)
    engine.embed_passage("x")
    assert "token_type_ids" in captured[0]
    assert (captured[0]["token_type_ids"] == 0).all()


def test_omits_token_type_ids_when_graph_does_not_use_it():
    captured: list[dict[str, np.ndarray]] = []
    session = FakeSession(
        dim=3,
        inputs=["input_ids", "attention_mask"],
        captured_feeds=captured,
    )
    engine = _make_engine(session=session)
    engine.embed_passage("x")
    assert "token_type_ids" not in captured[0]


def test_input_ids_have_int64_dtype():
    # ORT is strict about input dtype; ints must be int64.
    captured: list[dict[str, np.ndarray]] = []
    session = FakeSession(dim=3, captured_feeds=captured)
    engine = _make_engine(session=session)
    engine.embed_passage("x")
    assert captured[0]["input_ids"].dtype == np.int64
    assert captured[0]["attention_mask"].dtype == np.int64


def test_input_ids_have_batch_dim():
    captured: list[dict[str, np.ndarray]] = []
    session = FakeSession(dim=3, captured_feeds=captured)
    engine = _make_engine(session=session)
    engine.embed_passage("x")
    assert captured[0]["input_ids"].ndim == 2
    assert captured[0]["input_ids"].shape[0] == 1


# ---------- providers ----------


def test_select_providers_includes_coreml_on_darwin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core.vector.platform.system", lambda: "Darwin")
    providers = _select_providers()
    assert providers[0] == "CoreMLExecutionProvider"
    assert "CPUExecutionProvider" in providers


def test_select_providers_is_cpu_only_off_darwin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core.vector.platform.system", lambda: "Linux")
    assert _select_providers() == ["CPUExecutionProvider"]
