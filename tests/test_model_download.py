"""Unit tests for core.model_download.

No network. We stub _stream_to_file to write deterministic bytes (or raise),
which exercises every branch of ensure_model_files and the fallback chain.
A live smoke test against HuggingFace lives in tests/integration/ and is
gated by an environment variable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core import model_download as md


# ---------- helpers ----------


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_spec(payload: bytes, sha256: str | None = None) -> md.ModelSpec:
    return md.ModelSpec(
        artifacts=(
            md.ModelArtifact(local_name="model.bin", remote_path="model.bin", sha256=sha256),
        ),
        sources=(
            md.ModelSource(name="primary", base_url="https://primary.example/"),
            md.ModelSource(name="fallback", base_url="https://fallback.example/"),
        ),
    )


def _install_spec(
    monkeypatch: pytest.MonkeyPatch, name: str, spec: md.ModelSpec
) -> None:
    monkeypatch.setitem(md.MODEL_SPECS, name, spec)


def _stub_streamer(
    monkeypatch: pytest.MonkeyPatch,
    fn,
) -> list[str]:
    """Replace _stream_to_file with fn; return a list collecting call URLs."""
    calls: list[str] = []

    def wrapper(url, target, artifact_name, progress_cb):
        calls.append(url)
        return fn(url, target, artifact_name, progress_cb)

    monkeypatch.setattr(md, "_stream_to_file", wrapper)
    return calls


# ---------- sha256 ----------


def test_sha256_of_matches_hashlib(tmp_path: Path):
    f = tmp_path / "x"
    f.write_bytes(b"hello world")
    assert md.sha256_of(f) == _sha(b"hello world")


# ---------- happy path: primary source succeeds ----------


def test_downloads_from_primary_when_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = b"weights\x00\x01\x02"
    _install_spec(monkeypatch, "m", _make_spec(payload))

    def write_payload(url, target, name, cb):
        target.write_bytes(payload)

    calls = _stub_streamer(monkeypatch, write_payload)
    result = md.ensure_model_files("m", tmp_path)

    assert result == {"model.bin": "primary"}
    assert calls == ["https://primary.example/model.bin"]
    assert (tmp_path / "model.bin").read_bytes() == payload


# ---------- fallback path ----------


def test_falls_back_to_secondary_when_primary_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = b"weights"
    _install_spec(monkeypatch, "m", _make_spec(payload))

    def streamer(url, target, name, cb):
        if "primary" in url:
            raise ConnectionError("primary host unreachable")
        target.write_bytes(payload)

    calls = _stub_streamer(monkeypatch, streamer)
    result = md.ensure_model_files("m", tmp_path)

    assert result == {"model.bin": "fallback"}
    assert calls == [
        "https://primary.example/model.bin",
        "https://fallback.example/model.bin",
    ]


def test_raises_when_every_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_spec(monkeypatch, "m", _make_spec(b"weights"))

    def streamer(url, target, name, cb):
        raise ConnectionError(f"unreachable: {url}")

    _stub_streamer(monkeypatch, streamer)
    with pytest.raises(md.ModelDownloadError):
        md.ensure_model_files("m", tmp_path)


def test_partial_file_is_cleaned_up_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_spec(monkeypatch, "m", _make_spec(b"weights"))

    def streamer(url, target, name, cb):
        target.write_bytes(b"half-written")  # imitate partial write
        raise ConnectionError("interrupted")

    _stub_streamer(monkeypatch, streamer)
    with pytest.raises(md.ModelDownloadError):
        md.ensure_model_files("m", tmp_path)

    leftovers = list(tmp_path.glob("*.partial"))
    assert leftovers == []


# ---------- sha256 verification ----------


def test_sha256_mismatch_fails_over_to_next_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = b"good-weights"
    _install_spec(monkeypatch, "m", _make_spec(payload, sha256=_sha(payload)))

    def streamer(url, target, name, cb):
        if "primary" in url:
            target.write_bytes(b"corrupt-weights")
        else:
            target.write_bytes(payload)

    _stub_streamer(monkeypatch, streamer)
    result = md.ensure_model_files("m", tmp_path)

    assert result == {"model.bin": "fallback"}
    assert (tmp_path / "model.bin").read_bytes() == payload


def test_sha256_mismatch_everywhere_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_spec(monkeypatch, "m", _make_spec(b"good", sha256=_sha(b"good")))

    def streamer(url, target, name, cb):
        target.write_bytes(b"bad")

    _stub_streamer(monkeypatch, streamer)
    with pytest.raises(md.ModelDownloadError):
        md.ensure_model_files("m", tmp_path)


# ---------- cache behavior ----------


def test_skips_download_when_file_already_present_and_no_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_spec(monkeypatch, "m", _make_spec(b"weights"))
    (tmp_path / "model.bin").write_bytes(b"already-here")

    called: list[str] = []
    monkeypatch.setattr(
        md, "_stream_to_file", lambda *a, **k: called.append("downloaded")
    )
    result = md.ensure_model_files("m", tmp_path)

    assert called == []
    assert result == {"model.bin": "cached"}
    assert (tmp_path / "model.bin").read_bytes() == b"already-here"


def test_skips_download_when_file_already_present_and_hash_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = b"weights"
    _install_spec(monkeypatch, "m", _make_spec(payload, sha256=_sha(payload)))
    (tmp_path / "model.bin").write_bytes(payload)

    called: list[str] = []
    monkeypatch.setattr(
        md, "_stream_to_file", lambda *a, **k: called.append("downloaded")
    )
    result = md.ensure_model_files("m", tmp_path)

    assert called == []
    assert result == {"model.bin": "cached"}


def test_replaces_existing_file_when_hash_does_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = b"good-weights"
    _install_spec(monkeypatch, "m", _make_spec(payload, sha256=_sha(payload)))
    (tmp_path / "model.bin").write_bytes(b"corrupt-existing-file")

    def streamer(url, target, name, cb):
        target.write_bytes(payload)

    _stub_streamer(monkeypatch, streamer)
    result = md.ensure_model_files("m", tmp_path)

    assert result == {"model.bin": "primary"}
    assert (tmp_path / "model.bin").read_bytes() == payload


# ---------- progress callback ----------


def test_progress_callback_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_spec(monkeypatch, "m", _make_spec(b"weights"))

    events: list[tuple] = []

    def streamer(url, target, name, cb):
        target.write_bytes(b"weights")
        if cb is not None:
            cb(name, 7, 7)

    _stub_streamer(monkeypatch, streamer)
    md.ensure_model_files(
        "m", tmp_path, progress_cb=lambda n, d, t: events.append((n, d, t))
    )
    assert events == [("model.bin", 7, 7)]


# ---------- registry validation ----------


def test_unknown_model_raises_key_error(tmp_path: Path):
    with pytest.raises(KeyError):
        md.ensure_model_files("does-not-exist", tmp_path)


def test_default_e5_small_spec_is_registered():
    spec = md.MODEL_SPECS["multilingual-e5-small"]
    artifact_names = {a.local_name for a in spec.artifacts}
    assert artifact_names == {"model.onnx", "tokenizer.json"}
    source_names = [s.name for s in spec.sources]
    assert source_names[0] == "huggingface"
    assert "github" in source_names[1]
