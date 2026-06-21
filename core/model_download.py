"""Fetch embedding model artifacts to the local cache.

Each model has a fixed list of artifacts (model.onnx, tokenizer.json) and an
ordered list of download sources. The primary source is HuggingFace; the
fallback is our own GitHub Releases mirror, which makes the system survive a
HuggingFace takedown (see docs/adr/2026-06-13-embedding-model.md §6.5).

Downloads land atomically: stream to a `.partial` tempfile in the destination
directory, verify SHA-256 when a known hash is registered, then rename onto
the final path. A failure at any step leaves the previous good copy (or
nothing) in place.
"""

from __future__ import annotations

import hashlib
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB

ProgressCb = Callable[[str, int, int], None]
"""Signature: (artifact_local_name, bytes_downloaded, total_bytes_or_zero) -> None.
total is 0 when the server omits Content-Length."""


@dataclass(frozen=True)
class ModelArtifact:
    local_name: str
    remote_path: str
    sha256: str | None = None


@dataclass(frozen=True)
class ModelSource:
    name: str
    base_url: str


@dataclass(frozen=True)
class ModelSpec:
    artifacts: tuple[ModelArtifact, ...]
    sources: tuple[ModelSource, ...]


# Public registry. Add an entry per supported model. SHA-256 hashes stay
# None for the v0.2 alpha; v0.2 GA pins them once we ship signed weights via
# our own release artifact pipeline.
MODEL_SPECS: dict[str, ModelSpec] = {
    "multilingual-e5-small": ModelSpec(
        artifacts=(
            ModelArtifact(local_name="model.onnx", remote_path="onnx/model.onnx"),
            ModelArtifact(local_name="tokenizer.json", remote_path="tokenizer.json"),
        ),
        sources=(
            ModelSource(
                name="huggingface",
                base_url="https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/",
            ),
            ModelSource(
                name="github-releases-mirror",
                base_url="https://github.com/gammaland/makemarbles/releases/download/v0.2.0/",
            ),
        ),
    ),
    # Benchmark alternatives (ADR 2026-06-13 §8). HF only; we do not mirror
    # models we are merely evaluating, only the shipped default.
    "paraphrase-multilingual-MiniLM-L12-v2": ModelSpec(
        artifacts=(
            ModelArtifact(local_name="model.onnx", remote_path="onnx/model.onnx"),
            ModelArtifact(local_name="tokenizer.json", remote_path="tokenizer.json"),
        ),
        sources=(
            ModelSource(
                name="huggingface",
                base_url="https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/",
            ),
        ),
    ),
    # bge-m3 fp32 stores its 2.1 GB of weights in an external data file that
    # ONNX Runtime loads automatically when it sits beside model.onnx.
    "bge-m3": ModelSpec(
        artifacts=(
            ModelArtifact(local_name="model.onnx", remote_path="onnx/model.onnx"),
            ModelArtifact(local_name="model.onnx_data", remote_path="onnx/model.onnx_data"),
            ModelArtifact(local_name="tokenizer.json", remote_path="tokenizer.json"),
        ),
        sources=(
            ModelSource(
                name="huggingface",
                base_url="https://huggingface.co/Xenova/bge-m3/resolve/main/",
            ),
        ),
    ),
}


class ModelDownloadError(RuntimeError):
    """Raised when every source for an artifact fails or a hash mismatches."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(CHUNK):
            h.update(data)
    return h.hexdigest()


def _stream_to_file(
    url: str,
    target: Path,
    artifact_name: str,
    progress_cb: ProgressCb | None,
) -> None:
    """Stream `url` to `target`. Raises on transport failure or HTTP error.

    Set a User-Agent so HF does not treat us as an anonymous scraper and
    so a future server-side audit log can attribute downloads correctly.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "makemarbles/0.2"})
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length", "0") or "0")
        downloaded = 0
        with target.open("wb") as out:
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    progress_cb(artifact_name, downloaded, total)


def _fetch_one(
    artifact: ModelArtifact,
    sources: tuple[ModelSource, ...],
    final_path: Path,
    progress_cb: ProgressCb | None,
) -> str:
    """Try sources in order until one succeeds. Returns the source name.

    Each attempt writes to a fresh `.partial` tempfile in `final_path`'s
    directory and renames into place only after SHA-256 (when known) passes.
    """
    last_error: Exception | None = None
    for source in sources:
        url = source.base_url + artifact.remote_path
        with tempfile.NamedTemporaryFile(
            dir=final_path.parent,
            prefix=final_path.name + ".",
            suffix=".partial",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _stream_to_file(url, tmp_path, artifact.local_name, progress_cb)
            if artifact.sha256 is not None:
                got = sha256_of(tmp_path)
                if got != artifact.sha256:
                    raise ModelDownloadError(
                        f"sha256 mismatch for {artifact.local_name} via {source.name}: "
                        f"expected {artifact.sha256}, got {got}"
                    )
            tmp_path.replace(final_path)
            return source.name
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            last_error = exc
            continue
    raise ModelDownloadError(
        f"all sources failed for {artifact.local_name}; last error: {last_error}"
    )


def ensure_model_files(
    model_name: str,
    model_dir: Path,
    progress_cb: ProgressCb | None = None,
) -> dict[str, str]:
    """Make sure every artifact for model_name is in model_dir and valid.

    Returns a dict mapping artifact local_name to either 'cached' (already
    present and good) or the name of the source it was downloaded from. The
    caller decides whether to log it.
    """
    if model_name not in MODEL_SPECS:
        raise KeyError(f"No download spec registered for model {model_name!r}.")
    spec = MODEL_SPECS[model_name]
    model_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, str] = {}
    for artifact in spec.artifacts:
        local = model_dir / artifact.local_name
        if local.exists():
            if artifact.sha256 is None or sha256_of(local) == artifact.sha256:
                result[artifact.local_name] = "cached"
                continue
            # Existing file but wrong hash: remove and re-download.
            local.unlink()
        result[artifact.local_name] = _fetch_one(
            artifact, spec.sources, local, progress_cb
        )
    return result
