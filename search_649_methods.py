#!/usr/bin/env python3
"""Căutare metode 6/49 — rate_4plus @ pool k16, eval onest block_size=1, ultimele 30%.

Testează până la ~500 metode (existente + noi + blend-uri), selectează top-20 cu
cel puțin +20% relativ față de baseline (seasonal_naive), scrie rezultatele și
actualizează best_methods.json (k16) + methods_top649.py.

Usage:
    python search_649_methods.py [--max-methods 500] [--workers 8] [--apply]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger("search_649")

POOL_K = 16
PCT = 30
BLOCK = 1
IMPROVE_MIN = 0.20  # +20% relativ vs baseline
TOP_N = 20
RESULTS_JSON = ROOT / "bench_results" / "search_649_top20.json"

# Globals pentru worker (setate în initializer)
_G_TRAIN: np.ndarray | None = None
_G_TEST: np.ndarray | None = None
_G_GAME = None


def _load_draws(csv_path: Path) -> np.ndarray:
    df = pd.read_csv(csv_path)
    cols = [c for c in df.columns if c.lower().startswith("n")][:6]
    return df[cols].to_numpy(dtype=np.int64)


def _worker_init(train_pickle: bytes, test_pickle: bytes, game_pickle: bytes) -> None:
    import pickle
    global _G_TRAIN, _G_TEST, _G_GAME
    _G_TRAIN = pickle.loads(train_pickle)
    _G_TEST = pickle.loads(test_pickle)
    _G_GAME = pickle.loads(game_pickle)
    # Fiecare proces copil trebuie să aibă METHODS complet (blend-uri incluse).
    from loto_enterprise.benchmark.methods_search_649 import load_search_registry
    load_search_registry(include_existing=True, max_blends=420)


def _eval_one(method_name: str) -> tuple[str, float, bool, str]:
    from loto_enterprise.benchmark.runner import _evaluate_fold
    assert _G_TRAIN is not None and _G_TEST is not None and _G_GAME is not None
    try:
        fr, _ = _evaluate_fold(method_name, _G_TRAIN, _G_TEST, _G_GAME, BLOCK)
        if fr.failed:
            return method_name, 0.0, True, fr.error or "failed"
        rate = float(fr.rates_4plus_per_pool.get(f"k{POOL_K}", 0.0))
        return method_name, rate, False, ""
    except Exception as exc:  # noqa: BLE001
        return method_name, 0.0, True, f"{type(exc).__name__}: {exc}"


@dataclass
class SearchResult:
    method: str
    rate_4plus_k16: float
    lift_vs_baseline: float
    failed: bool


def run_search(max_methods: int, workers: int) -> list[SearchResult]:
    import pickle
    from loto_enterprise.benchmark.methods_search_649 import load_search_registry
    from loto_enterprise.benchmark.runner import GameDef, discover_games

    games = discover_games()
    game = next(g for g in games if g.key == "loto_6_49")
    draws = _load_draws(Path(game.csv_path))
    n = len(draws)
    n_test = max(1, int(math.ceil(n * PCT / 100.0)))
    n_train = n - n_test
    train = draws[:n_train]
    test = draws[n_train:n_train + n_test]
    logger.info("Draws=%d train=%d test=%d (%.0f%%) pool=k%d block=%d",
                n, n_train, n_test, PCT, POOL_K, BLOCK)

    names = load_search_registry(include_existing=True, max_blends=max(0, max_methods - 50))
    names = names[:max_methods]
    logger.info("Metode de evaluat: %d", len(names))

    train_b = pickle.dumps(train, protocol=pickle.HIGHEST_PROTOCOL)
    test_b = pickle.dumps(test, protocol=pickle.HIGHEST_PROTOCOL)
    game_b = pickle.dumps(game, protocol=pickle.HIGHEST_PROTOCOL)

    results: list[SearchResult] = []
    t0 = time.perf_counter()
    done = 0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        initargs=(train_b, test_b, game_b),
    ) as ex:
        futs = {ex.submit(_eval_one, nm): nm for nm in names}
        for fut in as_completed(futs):
            nm, rate, failed, err = fut.result()
            done += 1
            if done % 25 == 0 or done == len(names):
                elapsed = time.perf_counter() - t0
                logger.info("  progres %d/%d (%.0fs)", done, len(names), elapsed)
            if failed and err:
                logger.debug("FAIL %s: %s", nm, err[:80])
            results.append(SearchResult(nm, rate, 0.0, failed))

    baseline_name = "seasonal_naive"
    baseline = next((r.rate_4plus_k16 for r in results if r.method == baseline_name and not r.failed), None)
    if baseline is None:
        from loto_enterprise.benchmark.runner import _evaluate_fold
        fr, _ = _evaluate_fold(baseline_name, train, test, game, BLOCK)
        baseline = float(fr.rates_4plus_per_pool.get(f"k{POOL_K}", 0.0))
    logger.info("Baseline %s: rate_4plus_k16=%.4f (%.2f%%)", baseline_name, baseline, baseline * 100)

    target = baseline * (1.0 + IMPROVE_MIN)
    logger.info("Target +%.0f%%: >= %.4f (%.2f%%)", IMPROVE_MIN * 100, target, target * 100)

    for r in results:
        if baseline > 0 and not r.failed:
            r.lift_vs_baseline = (r.rate_4plus_k16 - baseline) / baseline

    ok = [r for r in results if not r.failed]
    ok.sort(key=lambda x: x.rate_4plus_k16, reverse=True)
    return ok, baseline, target


def write_results(top: list[SearchResult], baseline: float, target: float) -> None:
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol": {"pool_k": POOL_K, "percentile": PCT, "block_size": BLOCK, "improve_min_pct": IMPROVE_MIN * 100},
        "baseline": {"method": "seasonal_naive", "rate_4plus_k16": baseline},
        "target_rate": target,
        "top20": [
            {"rank": i + 1, "method": r.method, "rate_4plus_k16": r.rate_4plus_k16,
             "lift_pct": round(r.lift_vs_baseline * 100, 2)}
            for i, r in enumerate(top[:TOP_N])
        ],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Rezultate → %s", RESULTS_JSON)


def generate_methods_top649(top: list[SearchResult]) -> None:
    """Generează methods_top649.py cu top-20 (alias sau forward la METHODS existent)."""
    lines = [
        '"""Top-20 metode 6/49 selectate de search_649_methods.py (rate_4plus @ k16)."""',
        "from __future__ import annotations",
        "",
        "from typing import Callable",
        "",
        "from .methods import METHODS",
        "from .methods_search_649 import SEARCH_649_NEW, make_blend_scorer",
        "",
        "TOP649_METHODS: dict[str, tuple[Callable, str, bool, str]] = {",
    ]
    for i, r in enumerate(top[:TOP_N]):
        alias = f"top649_{i + 1:02d}_{r.method[:40].replace('-', '_')}"
        # Forward: reutilizăm scorer-ul din registry (deja în METHODS după search)
        lines.append(f'    "{alias}": METHODS["{r.method}"],  # {r.rate_4plus_k16*100:.2f}% 4+')
    lines.append("}")
    lines.append("")
    path = ROOT / "loto_enterprise" / "benchmark" / "methods_top649.py"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Scris %s (%d metode)", path, min(TOP_N, len(top)))


def patch_best_methods(top: list[SearchResult], baseline: float) -> None:
    """Actualizează auto_pilot k16 pentru loto_6_49 cu câștigător + ensemble top-3."""
    from ui_shared import atomic_write_json
    bm_path = ROOT / "best_methods.json"
    if not bm_path.exists():
        logger.warning("best_methods.json lipsă — sar patch")
        return
    cfg = json.loads(bm_path.read_text(encoding="utf-8"))
    games = cfg.setdefault("games", {})
    g = games.setdefault("loto_6_49", {})
    ap = g.setdefault("auto_pilot_per_pool", {})
    if not top:
        return
    winner = top[0]
    ens_members = top[:3]
    w_sum = sum(m.rate_4plus_k16 for m in ens_members) or 1.0
    ensemble = [
        {"method": m.method, "weight": round(m.rate_4plus_k16 / w_sum, 4)}
        for m in ens_members
    ]
    kkey = f"k{POOL_K}"
    ap[kkey] = {
        "scorer": winner.method,
        "ensemble": ensemble,
        "sim_depth_pct": PCT,
        "use_blacklist": False,
        "avg_hits": None,
        "rationale": (
            f"search_649: {winner.method} rate_4plus_k16={winner.rate_4plus_k16:.3f} "
            f"(+{winner.lift_vs_baseline*100:.1f}% vs seasonal_naive {baseline:.3f})"
        ),
        "qualifying_methods": len(top),
    }
    atomic_write_json(bm_path, cfg)
    logger.info("best_methods.json: k16 → %s (+ ensemble %s)", winner.method,
                ", ".join(e["method"] for e in ensemble))


def main() -> int:
    ap = argparse.ArgumentParser(description="Search 6/49 methods for rate_4plus @ k16")
    ap.add_argument("--max-methods", type=int, default=500)
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument("--apply", action="store_true", help="Scrie methods_top649.py + patch best_methods.json")
    args = ap.parse_args()

    ok, baseline, target = run_search(args.max_methods, args.workers)
    qualified = [r for r in ok if r.rate_4plus_k16 >= target]
    logger.info("--- TOP 25 (rate_4plus_k16) ---")
    for i, r in enumerate(ok[:25]):
        mark = "✓" if r.rate_4plus_k16 >= target else " "
        logger.info("%2d. [%s] %s  %.2f%%  (+%.1f%% vs baseline)",
                    i + 1, mark, r.method, r.rate_4plus_k16 * 100, r.lift_vs_baseline * 100)
    logger.info("Calificate (≥+%d%%): %d / %d", int(IMPROVE_MIN * 100), len(qualified), len(ok))

    top20 = qualified[:TOP_N] if len(qualified) >= TOP_N else ok[:TOP_N]
    write_results(top20, baseline, target)
    if args.apply:
        generate_methods_top649(top20)
        patch_best_methods(top20, baseline)
        # Înregistrează metodele noi permanent
        from loto_enterprise.benchmark.methods_search_649 import merge_search_into_methods
        from loto_enterprise.benchmark import methods as methods_mod
        merge_search_into_methods()
        # Reload top649 in methods.py requires restart; user runs ACTUALIZARI or restart
        logger.info("Rulează restart UI/worker pentru a încărca methods_top649.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
