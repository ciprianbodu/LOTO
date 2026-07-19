#!/usr/bin/env python3
"""Scan TOATE metodele available: rate 3+ / 4+ @ k10 și k16, eval onest (ultimele 30%).

Folosește același _evaluate_fold ca bench-ul (block_size=1). Scrie
bench_results/scan_all_hits.json + tipărește top-ul pe consolă.

Usage:
    py -3.14 scan_all_methods_hits.py [--workers N] [--pools 10,16]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger("scan_hits")

PCT = 30
# block_size=1 = 770 reantrenări/metodă (ore). 50 ≈ walk-forward onest, practic.
BLOCK = 50
OUT_JSON = ROOT / "bench_results" / "scan_all_hits.json"

_G_TRAIN = None
_G_TEST = None
_G_GAME = None
_G_POOLS: tuple[int, ...] = (10, 16)


def _worker_init(train_b: bytes, test_b: bytes, game_b: bytes, pools: tuple[int, ...]) -> None:
    global _G_TRAIN, _G_TEST, _G_GAME, _G_POOLS
    _G_TRAIN = pickle.loads(train_b)
    _G_TEST = pickle.loads(test_b)
    _G_GAME = pickle.loads(game_b)
    _G_POOLS = pools
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def _eval_one(method_name: str) -> dict:
    from loto_enterprise.benchmark.runner import _evaluate_fold

    assert _G_TRAIN is not None and _G_TEST is not None and _G_GAME is not None
    out: dict = {"method": method_name, "failed": False, "error": ""}
    try:
        fr, _ = _evaluate_fold(method_name, _G_TRAIN, _G_TEST, _G_GAME, BLOCK)
        if fr.failed:
            out["failed"] = True
            out["error"] = fr.error or "failed"
            return out
        for k in _G_POOLS:
            out[f"rate_3plus_k{k}"] = float(fr.rates_3plus_per_pool.get(f"k{k}", 0.0))
            out[f"rate_4plus_k{k}"] = float(fr.rates_4plus_per_pool.get(f"k{k}", 0.0))
            out[f"avg_hits_k{k}"] = float(fr.hits_per_pool.get(f"k{k}", 0.0))
        return out
    except Exception as exc:  # noqa: BLE001
        out["failed"] = True
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out


def _load_draws(csv_path: Path, draw_n: int) -> np.ndarray:
    df = pd.read_csv(csv_path)
    cols = [c for c in df.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"]
    cols = sorted(cols, key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
    return df[cols[:draw_n]].to_numpy(dtype=np.int64)


# Meta-selector: rulează zeci de metode înăuntru → blocaj pe scan paralel.
_SKIP_SLOW = {"omnius"}


def _available_methods() -> list[str]:
    from loto_enterprise.benchmark.methods import list_methods, method_meta
    try:
        from loto_enterprise.benchmark.disabled import load_disabled
        disabled = load_disabled()
    except Exception:  # noqa: BLE001
        disabled = set()
    return [
        m for m in list_methods()
        if method_meta(m).get("available", True)
        and m not in disabled
        and m not in _SKIP_SLOW
    ]


def scan_game(game, pools: tuple[int, ...], methods: list[str], workers: int) -> list[dict]:
    draws = _load_draws(Path(game.csv_path), game.draw_n)
    n = len(draws)
    n_test = max(1, int(math.ceil(n * PCT / 100.0)))
    n_train = n - n_test
    train, test = draws[:n_train], draws[n_train:n_train + n_test]
    logger.info(
        "%s: draws=%d train=%d test=%d (%.0f%%) pools=%s methods=%d workers=%d",
        game.key, n, n_train, n_test, PCT, pools, len(methods), workers,
    )
    train_b, test_b, game_b = pickle.dumps(train), pickle.dumps(test), pickle.dumps(game)
    results: list[dict] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        initargs=(train_b, test_b, game_b, pools),
    ) as ex:
        futs = {ex.submit(_eval_one, m): m for m in methods}
        done = 0
        for fut in as_completed(futs):
            done += 1
            row = fut.result()
            row["game"] = game.key
            results.append(row)
            if done % 10 == 0 or done == len(methods):
                logger.info("  %s progress %d/%d (%.0fs)", game.key, done, len(methods), time.perf_counter() - t0)
    return results


def _print_top(rows: list[dict], game: str, pool: int, metric: str, n: int = 15) -> None:
    col = f"{metric}_k{pool}"
    ok = [r for r in rows if r.get("game") == game and not r.get("failed") and col in r]
    ok.sort(key=lambda r: r[col], reverse=True)
    print(f"\n=== {game} @ k{pool} — TOP {n} după {metric.replace('rate_', '').replace('plus', '+')} ===")
    base = next((r for r in ok if r["method"] == "random"), None)
    base_v = float(base[col]) if base else None
    for i, r in enumerate(ok[:n], 1):
        v = r[col] * 100
        lift = ""
        if base_v and base_v > 0:
            lift = f"  (lift vs random {(r[col] - base_v) / base_v * 100:+.1f}%)"
        r3 = r.get(f"rate_3plus_k{pool}", 0) * 100
        r4 = r.get(f"rate_4plus_k{pool}", 0) * 100
        print(f"  {i:2d}. {r['method']:32s}  3+={r3:5.2f}%  4+={r4:5.2f}%{lift}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 8) * 4 // 5))
    ap.add_argument("--pools", default="10,16")
    ap.add_argument("--games", default="loto_6_49,loto_5_40,joker_urna1")
    args = ap.parse_args()
    pools = tuple(int(x) for x in args.pools.split(",") if x.strip())
    want = {g.strip() for g in args.games.split(",") if g.strip()}

    from loto_enterprise.benchmark.runner import discover_games

    methods = _available_methods()
    logger.info("Available methods: %d", len(methods))
    games = [g for g in discover_games() if g.key in want and not g.is_single_pick]
    all_rows: list[dict] = []
    t0 = time.perf_counter()
    for g in games:
        all_rows.extend(scan_game(g, pools, methods, args.workers))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pct_test": PCT,
        "block_size": BLOCK,
        "pools": list(pools),
        "n_methods": len(methods),
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "results": all_rows,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote %s (%.1fs)", OUT_JSON, payload["elapsed_sec"])

    for g in games:
        for k in pools:
            _print_top(all_rows, g.key, k, "rate_3plus")
            _print_top(all_rows, g.key, k, "rate_4plus", n=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
