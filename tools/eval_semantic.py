"""General semantic-search eval for MakeMarbles (ADR 2026-06-13 §8).

Builds an isolated temp database from tools/eval/semantic_eval.json (a fully
synthetic, non-personal set safe for the public repo), embeds every note under
the configured model, then scores three retrieval channels against the labeled
queries:

    fts     lexical only (FTS5 / BM25)
    vector  dense only (vector_only_search)
    hybrid  FTS5 + vector fused by RRF (hybrid_search)

Metrics: Recall@5, Recall@10, MRR. A query counts as recalled at k if any of
its gold ids appears in the top-k; MRR uses the rank of the first gold hit.

Usage:
    python tools/eval_semantic.py [--model NAME] [--json]

This touches no user data: it writes to a throwaway temp dir and deletes it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# Run from the repo root (so `core` imports resolve) without installing.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402
from core.models import Note  # noqa: E402
from core.search import hybrid_search, vector_only_search  # noqa: E402
from core.storage import Storage  # noqa: E402
from core.vector import EmbeddingEngine, get_known_model  # noqa: E402

EVAL_PATH = ROOT / "tools" / "eval" / "semantic_eval.json"
KS = (5, 10)


def _first_gold_rank(result_ids: list[str], gold: set[str]) -> int | None:
    for rank, nid in enumerate(result_ids, start=1):
        if nid in gold:
            return rank
    return None


def _score(per_query_ranks: list[int | None]) -> dict[str, float]:
    n = len(per_query_ranks)
    out: dict[str, float] = {}
    for k in KS:
        hits = sum(1 for r in per_query_ranks if r is not None and r <= k)
        out[f"recall@{k}"] = hits / n
    out["mrr"] = sum((1.0 / r) for r in per_query_ranks if r is not None) / n
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="Override the configured model name.")
    ap.add_argument("--data", default=None,
                    help="Path to an eval set JSON (defaults to the committed synthetic set). "
                         "Use this to run a private real-note dogfood set off-repo.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = ap.parse_args()

    data_path = Path(args.data).expanduser() if args.data else EVAL_PATH
    data = json.loads(data_path.read_text(encoding="utf-8"))
    notes = data["notes"]
    queries = data["queries"]

    cfg = load_config()
    model_name = args.model or cfg.embedding.model_name
    model_dir = cfg.embedding.models_dir / model_name
    engine = EmbeddingEngine(model_dir=model_dir, config=get_known_model(model_name))

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "eval.db"
        storage = Storage(db_path=db_path)
        for n in notes:
            note = Note(content=n["content"], tag=n.get("tag"))
            note.id = n["id"]  # pin the id so gold labels line up
            storage.add(note)
            vec = engine.embed_passage(n["content"])
            storage.upsert_vector(n["id"], vec, model_name)

        # ranks[group][channel] -> list of first-gold ranks (None = missed)
        ranks: dict[str, dict[str, list[int | None]]] = {}
        misses: list[dict] = []
        for query in queries:
            group = query.get("group", "paraphrase")
            gold = set(query["gold"])
            fts_ids = [n.id for n in storage.search(query["q"], limit=10)]
            vec_ids = [
                n.id for n in vector_only_search(storage, query["q"], engine, model_name, limit=10)
            ]
            hyb_ids = [
                n.id
                for n in hybrid_search(storage, query["q"], engine, model_name, limit=10)
            ]
            r = {
                "fts": _first_gold_rank(fts_ids, gold),
                "vector": _first_gold_rank(vec_ids, gold),
                "hybrid": _first_gold_rank(hyb_ids, gold),
            }
            for scope in (group, "overall"):
                bucket = ranks.setdefault(scope, {"fts": [], "vector": [], "hybrid": []})
                for ch in ("fts", "vector", "hybrid"):
                    bucket[ch].append(r[ch])
            if r["hybrid"] is None or r["hybrid"] > 5:
                misses.append({"q": query["q"], "gold": query["gold"], "hybrid_rank": r["hybrid"]})

        results = {
            scope: {ch: _score(rs) for ch, rs in chans.items()}
            for scope, chans in ranks.items()
        }
        group_n = {
            scope: len(chans["fts"]) for scope, chans in ranks.items()
        }

    if args.json:
        print(json.dumps({"model": model_name, "n_queries": len(queries),
                          "group_n": group_n, "metrics": results,
                          "hybrid_misses": misses}, ensure_ascii=False, indent=2))
        return 0

    print(f"model: {model_name}   notes: {len(notes)}   queries: {len(queries)}")
    hdr = f"{'channel':<8} {'recall@5':>9} {'recall@10':>10} {'mrr':>7}"
    for scope in ("overall", "paraphrase", "lexical"):
        if scope not in results:
            continue
        print(f"\n[{scope}]  n={group_n[scope]}")
        print(hdr)
        print("-" * len(hdr))
        for ch in ("fts", "vector", "hybrid"):
            m = results[scope][ch]
            print(f"{ch:<8} {m['recall@5']:>9.3f} {m['recall@10']:>10.3f} {m['mrr']:>7.3f}")
    if misses:
        print("\nhybrid misses (gold not in top-5):")
        for m in misses:
            print(f"  rank={m['hybrid_rank']}  gold={m['gold']}  {m['q']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
