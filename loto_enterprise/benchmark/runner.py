"""Walk-forward / regressive benchmark runner — v2 (spec-compliant).

For every (game, urn, method, percentile_window, real|random) fold we:
    1. Train on the first (1 - P/100) of draws.
    2. Score numbers using a block-walk-forward (re-score every BLOCK_SIZE
       test draws, expanding history). For block_size = 99999 this is a
       single training per fold (fast). For block_size = 1 you get true
       per-step walk-forward (very slow for trainable nets).
    3. For each draw in the test window evaluate hits AT MULTIPLE POOL SIZES
       draw_n .. draw_n + 6 (so a 6/49 game records hits for pools of
       6, 7, 8, 9, 10, 11, 12). For Urna 2 the pool is fixed = draw_n.
    4. Capture CPU%, RAM, GPU%, VRAM peak via a background sampler thread.

Output:
    bench_results/folds.csv         one row per (game, method, pct, real?, fold)
                                    with hits@K for K = draw_n..draw_n+6
    bench_results/report.json       aggregated stats + winner per (game, pool)
    bench_results/report.txt        plain text fallback
    bench_results/console.txt       saved rich-table output

Use `bench_all_methods.py` as the CLI entry-point.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .methods import METHODS, call_method, method_meta
from .hardware import (
    snapshot as hw_snapshot,
    format_snapshot,
)
from .hw_sampler import HwSampler, HwSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game definitions
# ---------------------------------------------------------------------------

@dataclass
class GameDef:
    key: str
    label: str
    csv_path: str
    cols: List[str]
    max_num: int
    draw_n: int
    pool_extra: int = 6   # evaluate hits for pools = draw_n .. draw_n + pool_extra
    is_single_pick: bool = False  # joker_urna2 → fixed pool_size = draw_n


def _list_istoric_dirs() -> List[Path]:
    """Look in canonical locations for the istoric folder."""
    candidates = [
        Path("_ISTORIC"),
        Path("_istoric"),
        Path("ISTORIC"),
        Path("istoric"),
        Path("_LOTO/istoric"),
        Path("_LOTO/ISTORIC"),
    ]
    return [p for p in candidates if p.exists()]


def discover_games(istoric_dir: Optional[str] = None) -> List[GameDef]:
    """Auto-detect game CSVs by filename pattern."""
    if istoric_dir:
        base = Path(istoric_dir)
        if not base.exists():
            raise FileNotFoundError(f"Folderul {istoric_dir} nu există")
        bases = [base]
    else:
        bases = _list_istoric_dirs()
        if not bases:
            raise FileNotFoundError(
                "Niciun folder ISTORIC găsit. Caut în: ./_ISTORIC, ./ISTORIC, ./istoric, ./_LOTO/istoric"
            )

    games: List[GameDef] = []
    seen_keys = set()
    for base in bases:
        for p in sorted(base.glob("*.csv")):
            name = p.name.lower()
            if "6_49" in name or "649" in name:
                key = "loto_6_49"
                if key in seen_keys:
                    continue
                games.append(GameDef(
                    key=key, label="Loto 6/49", csv_path=str(p),
                    cols=["n1", "n2", "n3", "n4", "n5", "n6"],
                    max_num=49, draw_n=6, pool_extra=14,  # K=6..20 (extins 2026-05-25)
                ))
                seen_keys.add(key)
            elif "5_40" in name or "540" in name:
                key = "loto_5_40"
                if key in seen_keys:
                    continue
                games.append(GameDef(
                    key=key, label="Loto 5/40", csv_path=str(p),
                    cols=["n1", "n2", "n3", "n4", "n5"],
                    max_num=40, draw_n=5, pool_extra=14,  # K=5..19 (extins 2026-05-25)
                ))
                seen_keys.add(key)
            elif "joker" in name:
                if "joker_urna1" not in seen_keys:
                    games.append(GameDef(
                        key="joker_urna1", label="Joker — Urna 1 (5/45)",
                        csv_path=str(p),
                        cols=["n1", "n2", "n3", "n4", "n5"],
                        max_num=45, draw_n=5, pool_extra=14,  # K=5..19 (extins 2026-05-25)
                    ))
                    seen_keys.add("joker_urna1")
                if "joker_urna2" not in seen_keys:
                    games.append(GameDef(
                        key="joker_urna2", label="Joker — Urna 2 (1/20)",
                        csv_path=str(p),
                        cols=["joker"],
                        max_num=20, draw_n=1, pool_extra=0,
                        is_single_pick=True,
                    ))
                    seen_keys.add("joker_urna2")
    if not games:
        raise RuntimeError("Nu am detectat niciun CSV de joc.")
    order = {"loto_6_49": 0, "loto_5_40": 1, "joker_urna1": 2, "joker_urna2": 3}
    games.sort(key=lambda g: order.get(g.key, 99))
    return games


def load_draws(game: GameDef) -> np.ndarray:
    df = pd.read_csv(game.csv_path)
    missing = [c for c in game.cols if c not in df.columns]
    if missing:
        raise ValueError(f"{game.csv_path}: lipsesc coloanele {missing}")
    arr = df[game.cols].to_numpy(dtype=np.int64)
    mask = np.all((arr >= 1) & (arr <= game.max_num), axis=1)
    return arr[mask]


# ---------------------------------------------------------------------------
# Per-fold evaluation
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    game: str
    method: str
    percentile: int
    is_random: bool
    n_train: int
    n_test: int
    runtime_sec: float
    # Hit rates per pool size: keys "k6", "k7", "k8", ... "kN" (NO blacklist)
    hits_per_pool: Dict[str, float] = field(default_factory=dict)
    # Hit rates per pool size WITH blacklist applied (bottom 25% excluded)
    hits_per_pool_bl: Dict[str, float] = field(default_factory=dict)
    avg_hits_topk: float = 0.0          # avg hits at K = draw_n (base pool)
    max_hits_topk: int = 0
    blacklist_size: int = 0             # how many numbers were blacklisted per score round
    cpu_pct_peak: float = 0.0
    cpu_pct_avg: float = 0.0
    ram_gb_peak: float = 0.0
    gpu_pct_peak: float = 0.0
    gpu_pct_avg: float = 0.0
    vram_mb_peak: float = 0.0
    blocks: int = 0
    failed: bool = False
    error: str = ""


def _top_k(scores: Dict[int, float], k: int) -> List[int]:
    if not scores:
        return []
    return [n for n, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]]


def _evaluate_fold(
    method_name: str,
    train_draws: np.ndarray,
    test_draws: np.ndarray,
    game: GameDef,
    block_size: int,
) -> Tuple[FoldResult, HwSnapshot]:
    """Run a single fold with hardware sampling. Returns (FoldResult, hw_snap)."""
    n_test = len(test_draws)
    pool_sizes = [game.draw_n] if game.is_single_pick else [
        game.draw_n + i for i in range(game.pool_extra + 1)
    ]
    fr = FoldResult(
        game=game.key, method=method_name, percentile=0, is_random=False,
        n_train=len(train_draws), n_test=n_test, runtime_sec=0.0,
        hits_per_pool={f"k{k}": 0.0 for k in pool_sizes},
        hits_per_pool_bl={f"k{k}": 0.0 for k in pool_sizes},
    )

    sampler = HwSampler(interval=0.1).start()
    t0 = time.perf_counter()
    try:
        per_pool_totals = {k: 0 for k in pool_sizes}
        per_pool_bl_totals = {k: 0 for k in pool_sizes}
        per_pool_max = {k: 0 for k in pool_sizes}
        blocks = 0
        empty_blocks = 0  # Count blocks where call_method returned {} (silent failure)
        bl_sizes_seen: List[int] = []
        history = train_draws
        pos = 0
        while pos < n_test:
            end = min(pos + block_size, n_test)
            scores, _t = call_method(method_name, history, game.max_num)
            blocks += 1

            if not scores:
                # Method returned empty scores — track but continue.
                # Daca TOATE block-urile returneaza {} (metoda complet broken
                # pe configuratia curenta, ex. NF model fara CUDA), o sa marcam
                # fold-ul ca failed la finalul loop-ului prin raise. Asta scoate
                # fold-ul din mediile calculate de _aggregate si previne ca
                # baseline-urile sa "castige" doar pentru ca rivalii au 0.000.
                empty_blocks += 1
                history = np.concatenate([history, test_draws[pos:end]], axis=0)
                pos = end
                continue

            # Pre-compute top-K WITHOUT blacklist
            top_sets = {k: set(_top_k(scores, k)) for k in pool_sizes}

            # Blacklist = numere "moarte" (absente din ultimele K extrageri).
            # Semnal INDEPENDENT de scorerul curent — analog produsului de
            # producție (intersecție multi-window). Folosim ferestre scurte ca
            # blacklist-ul să fie ne-vid și să poată intersecta cu top-K.
            # Calibrăm K după draw_n × max_num ca să țintim ~20-30% blacklist.
            target_slots = max(game.max_num * 2, 20)
            recent_window = max(10, target_slots // max(game.draw_n, 1))
            recent_slice = history[-recent_window:]
            seen_recent = set(int(v) for row in recent_slice for v in row)
            blacklist = {n for n in range(1, game.max_num + 1) if n not in seen_recent}
            bl_sizes_seen.append(len(blacklist))

            # Top-K WITH blacklist: exclude blacklisted, then take top-K
            filtered_scores = {n: s for n, s in scores.items() if n not in blacklist}
            top_sets_bl = {k: set(_top_k(filtered_scores, k)) for k in pool_sizes}

            for j in range(pos, end):
                actual = set(int(x) for x in test_draws[j])
                for k in pool_sizes:
                    h = len(top_sets[k] & actual)
                    h_bl = len(top_sets_bl[k] & actual)
                    per_pool_totals[k] += h
                    per_pool_bl_totals[k] += h_bl
                    if h > per_pool_max[k]:
                        per_pool_max[k] = h
            history = np.concatenate([history, test_draws[pos:end]], axis=0)
            pos = end

        # Daca TOATE block-urile au returnat {} -> metoda nu a produs niciun
        # scor real pe fold-ul asta. Marcam ca failed cu un mesaj clar.
        # Cel mai frecvent caz: model neural fara CUDA, dep lipsa, sau exceptie
        # interna in scorer prinsa de wrapper-ul lui call_method().
        if blocks > 0 and empty_blocks == blocks:
            raise RuntimeError(
                f"method '{method_name}' returned empty scores on all "
                f"{blocks} blocks (likely missing CUDA/dep or scorer error)"
            )

        # Aggregate per-pool average (both conditions)
        for k in pool_sizes:
            fr.hits_per_pool[f"k{k}"] = per_pool_totals[k] / max(n_test, 1)
            fr.hits_per_pool_bl[f"k{k}"] = per_pool_bl_totals[k] / max(n_test, 1)
        fr.avg_hits_topk = fr.hits_per_pool.get(f"k{game.draw_n}", 0.0)
        fr.max_hits_topk = per_pool_max[game.draw_n]
        fr.blacklist_size = int(np.mean(bl_sizes_seen)) if bl_sizes_seen else 0
        fr.blocks = blocks
    except Exception as exc:
        fr.failed = True
        fr.error = f"{type(exc).__name__}: {exc}"
        logger.error("[%s/%s] fold failed: %s", game.key, method_name, exc)
    finally:
        fr.runtime_sec = time.perf_counter() - t0
        snap = sampler.stop().snapshot()
        sampler.close()
        fr.cpu_pct_peak = snap.cpu_pct_peak
        fr.cpu_pct_avg = snap.cpu_pct_avg
        fr.ram_gb_peak = snap.ram_gb_peak
        fr.gpu_pct_peak = snap.gpu_pct_peak
        fr.gpu_pct_avg = snap.gpu_pct_avg
        fr.vram_mb_peak = snap.vram_mb_peak
    return fr, snap


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def run_benchmark(
    games: List[GameDef],
    methods: List[str],
    percentiles: List[int],
    block_size: int = 99999,
    random_seed: int = 1234,
    out_dir: str = "bench_results",
    progress_cb=None,
    use_cache: bool = True,
) -> Dict:
    """`use_cache=False` -> skip disk cache lookup/store. Folosit cand:
       - utilizatorul forteaza rebench (vrea masuratori proaspete)
       - schimbarea de hardware (CPU->GPU) invalideaza datele cache-uite
         (cache-ul nu include hardware/torch version in key)."""
    out_path = Path(out_dir)
    out_path.mkdir(exist_ok=True, parents=True)

    fold_rows: List[FoldResult] = []
    method_meta_map = {m: method_meta(m) for m in methods}

    total_folds_est = 0
    for game in games:
        for m in methods:
            meta = method_meta_map[m]
            if not meta["available"]:
                continue
            for pct in percentiles:
                total_folds_est += 2  # real + random
    fold_idx = 0

    for game in games:
        try:
            draws = load_draws(game)
        except Exception as exc:
            logger.error("[%s] CSV load failed: %s", game.key, exc)
            continue
        n = len(draws)
        logger.info("[%s] %d draws loaded from %s", game.key, n, game.csv_path)
        rng = np.random.default_rng(random_seed + (hash(game.key) % 9973))
        idx = np.arange(n)
        rng.shuffle(idx)
        shuffled_draws = draws[idx]

        # P1: hash CSV o SINGURĂ dată per joc (draws e identic la toate fold-urile;
        # înainte era recalculat la fiecare fold → ~1280× degeaba).
        csv_hash_game = None
        if use_cache:
            try:
                from .bench_cache import compute_csv_hash, get_cached_fold, store_cached_fold
                csv_hash_game = compute_csv_hash(draws)
            except Exception as _cache_exc:
                logger.debug(f"[bench_cache] init/hash failed: {_cache_exc}")

        for method in methods:
            meta = method_meta_map[method]
            if not meta["available"]:
                logger.info("[%s/%s] SKIP (unavailable: %s)",
                            game.key, method, meta.get("unavailable_reason"))
                continue

            for pct in percentiles:
                # 100% percentile → use a tiny train (last_50_safe) for shape sanity
                n_test = max(1, int(math.ceil(n * pct / 100.0)))
                n_train = max(0, n - n_test)
                # Minimum train guard to keep models stable
                if pct >= 100:
                    n_train = max(80, n_train)
                    n_test = n - n_train
                if n_train < 80:
                    logger.info("[%s/%s/%d%%] skip — train too small (%d)",
                                game.key, method, pct, n_train)
                    continue

                for is_random in (False, True):
                    src = shuffled_draws if is_random else draws
                    train = src[:n_train]
                    test = src[n_train:n_train + n_test]

                    fold_idx += 1

                    # === Cache lookup: skip if cached (CSV unchanged) ===
                    # use_cache=False -> ignoram cache complet (utilizator a fortat
                    # rebench, sau hardware s-a schimbat de la CPU la GPU).
                    cached_fr = None
                    csv_hash_for_fold = csv_hash_game  # hoisted per-game (P1)
                    if use_cache and csv_hash_for_fold is not None:
                        try:
                            cached_fr = get_cached_fold(csv_hash_for_fold, method, pct, game.key, is_random)
                        except Exception as _cache_exc:
                            logger.debug(f"[bench_cache] lookup failed: {_cache_exc}")

                    if cached_fr is not None:
                        fr = cached_fr
                        logger.info(
                            "[%d/%d] [%s/%s/%d%%/%s] CACHE HIT — skip compute",
                            fold_idx, total_folds_est,
                            game.key, method, pct, "RND" if is_random else "REAL",
                        )
                    else:
                        fr, _snap = _evaluate_fold(method, train, test, game, block_size)
                        fr.percentile = pct
                        fr.is_random = is_random
                        # Store rezultatul pentru rulari viitoare
                        if csv_hash_for_fold is not None:
                            try:
                                store_cached_fold(csv_hash_for_fold, method, pct, game.key, is_random, fr)
                            except Exception as _store_exc:
                                logger.debug(f"[bench_cache] store failed: {_store_exc}")
                        logger.info(
                            "[%d/%d] [%s/%s/%d%%/%s] hits@k%d=%.3f t=%.1fs cpu_peak=%.0f%% gpu_peak=%.0f%% vram=%.0fMB ram=%.1fGB",
                            fold_idx, total_folds_est,
                            game.key, method, pct, "RND" if is_random else "REAL",
                            game.draw_n, fr.avg_hits_topk, fr.runtime_sec,
                            fr.cpu_pct_peak, fr.gpu_pct_peak, fr.vram_mb_peak, fr.ram_gb_peak,
                        )

                    fold_rows.append(fr)
                    if progress_cb:
                        progress_cb(fold_idx, total_folds_est, fr, game)

    # ----- Save per-fold CSV -----
    rows = []
    pool_keys_per_game = {}
    for g in games:
        if g.is_single_pick:
            pool_keys_per_game[g.key] = [f"k{g.draw_n}"]
        else:
            pool_keys_per_game[g.key] = [f"k{g.draw_n + i}" for i in range(g.pool_extra + 1)]

    for fr in fold_rows:
        row = asdict(fr)
        # Flatten hits_per_pool (WITHOUT blacklist)
        for k, v in row.pop("hits_per_pool").items():
            row[k] = v
        # Flatten hits_per_pool_bl (WITH blacklist) with "_bl" suffix
        for k, v in row.pop("hits_per_pool_bl").items():
            row[f"{k}_bl"] = v
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(out_path / "folds.csv", index=False)

    # ----- Aggregate per (game, pool) → winner -----
    report = _aggregate(df, games, methods, method_meta_map, pool_keys_per_game)
    with open(out_path / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def _aggregate(
    df: pd.DataFrame,
    games: List[GameDef],
    methods: List[str],
    method_meta_map: Dict[str, dict],
    pool_keys_per_game: Dict[str, List[str]],
) -> Dict:
    report: Dict = {
        "games": {},
        "method_meta": method_meta_map,
        "n_folds_total": int(len(df)),
    }
    for game in games:
        # IMPORTANT: excludem fold-urile failed=True (metode care au returnat
        # empty scores pe tot fold-ul, ex. NF fara CUDA) ca sa nu polueze
        # mediile cu 0.000. Altfel un baseline cu hit_rate=0.77 ar "castiga"
        # fata de transformer-ele care n-au rulat deloc dar apar cu mean=0.
        if not df.empty and "failed" in df.columns:
            sub = df[(df["game"] == game.key) & (df["failed"] == False)]  # noqa: E712
        else:
            sub = df[df["game"] == game.key] if not df.empty else df
        pool_keys = pool_keys_per_game[game.key]
        per_method = {}
        for method in methods:
            real = sub[(sub["method"] == method) & (sub["is_random"] == False)] if not sub.empty else sub  # noqa: E712
            rnd = sub[(sub["method"] == method) & (sub["is_random"] == True)] if not sub.empty else sub    # noqa: E712
            meta = method_meta_map[method]
            if real.empty:
                per_method[method] = {
                    "available": meta["available"],
                    "skipped": True,
                    "reason": meta.get("unavailable_reason", ""),
                    "family": meta.get("family"),
                }
                continue
            entry = {
                "available": meta["available"],
                "family": meta["family"],
                "requires_train": meta["requires_train"],
                "n_folds_real": int(len(real)),
                "mean_runtime_sec": float(real["runtime_sec"].mean()),
                "cpu_pct_peak": float(real["cpu_pct_peak"].max()),
                "cpu_pct_avg": float(real["cpu_pct_avg"].mean()),
                "ram_gb_peak": float(real["ram_gb_peak"].max()),
                "gpu_pct_peak": float(real["gpu_pct_peak"].max()),
                "gpu_pct_avg": float(real["gpu_pct_avg"].mean()),
                "vram_mb_peak": float(real["vram_mb_peak"].max()),
                "per_pool": {},
            }
            for k in pool_keys:
                if k not in real.columns:
                    continue
                real_mean = float(real[k].mean())
                rnd_mean = float(rnd[k].mean()) if (not rnd.empty and k in rnd.columns) else 0.0
                # WITH blacklist column has _bl suffix
                bl_col = f"{k}_bl"
                real_mean_bl = float(real[bl_col].mean()) if bl_col in real.columns else 0.0
                rnd_mean_bl = float(rnd[bl_col].mean()) if (not rnd.empty and bl_col in rnd.columns) else 0.0
                entry["per_pool"][k] = {
                    "avg_hits_real": real_mean,
                    "avg_hits_shuffled": rnd_mean,
                    "lift_vs_shuffle": real_mean - rnd_mean,
                    "hit_rate_real": real_mean / game.draw_n,
                    # WITH blacklist
                    "avg_hits_real_bl": real_mean_bl,
                    "avg_hits_shuffled_bl": rnd_mean_bl,
                    "lift_vs_shuffle_bl": real_mean_bl - rnd_mean_bl,
                    "blacklist_helps": real_mean_bl - real_mean,  # positive = blacklist improves
                }
            per_method[method] = entry

        # Winner per pool size for BOTH conditions (no_bl / with_bl) + global best
        winners_per_pool = {}        # WITHOUT blacklist (legacy / default)
        winners_per_pool_bl = {}     # WITH blacklist applied
        winners_per_pool_best = {}   # whichever of the two has higher score
        for k in pool_keys:
            ranked_nobl = []
            ranked_bl = []
            for m, d in per_method.items():
                if not d.get("available") or d.get("skipped"):
                    continue
                stats = d.get("per_pool", {}).get(k)
                if not stats:
                    continue
                ranked_nobl.append((m, stats["avg_hits_real"], stats["lift_vs_shuffle"]))
                ranked_bl.append((m, stats["avg_hits_real_bl"], stats["lift_vs_shuffle_bl"]))
            ranked_nobl.sort(key=lambda r: (r[1], r[2]), reverse=True)
            ranked_bl.sort(key=lambda r: (r[1], r[2]), reverse=True)
            if ranked_nobl:
                winners_per_pool[k] = {
                    "winner": ranked_nobl[0][0],
                    "avg_hits": ranked_nobl[0][1],
                    "lift_vs_shuffle": ranked_nobl[0][2],
                    "ranking": [{"method": r[0], "avg_hits": r[1], "lift": r[2]} for r in ranked_nobl],
                }
            if ranked_bl:
                winners_per_pool_bl[k] = {
                    "winner": ranked_bl[0][0],
                    "avg_hits": ranked_bl[0][1],
                    "lift_vs_shuffle": ranked_bl[0][2],
                    "ranking": [{"method": r[0], "avg_hits": r[1], "lift": r[2]} for r in ranked_bl],
                }
            # Best of two — used by production engine (we plug in whichever wins)
            if ranked_nobl and ranked_bl:
                nobl_top = ranked_nobl[0]
                bl_top = ranked_bl[0]
                if bl_top[1] > nobl_top[1]:
                    winners_per_pool_best[k] = {
                        "winner": bl_top[0], "avg_hits": bl_top[1],
                        "use_blacklist": True, "delta_vs_no_bl": bl_top[1] - nobl_top[1],
                    }
                else:
                    winners_per_pool_best[k] = {
                        "winner": nobl_top[0], "avg_hits": nobl_top[1],
                        "use_blacklist": False, "delta_vs_with_bl": nobl_top[1] - bl_top[1],
                    }

        # Overall winner across all pools — both conditions
        def _overall(score_key: str):
            tmp = {}
            for m, d in per_method.items():
                if not d.get("available") or d.get("skipped"):
                    continue
                pools = d.get("per_pool", {})
                if not pools:
                    continue
                tmp[m] = float(np.mean([s[score_key] for s in pools.values()]))
            return sorted(tmp.items(), key=lambda kv: kv[1], reverse=True)
        ranked_overall = _overall("avg_hits_real")
        ranked_overall_bl = _overall("avg_hits_real_bl")

        report["games"][game.key] = {
            "label": game.label,
            "csv_path": game.csv_path,
            "max_num": game.max_num,
            "draw_n": game.draw_n,
            "pool_keys": pool_keys,
            "winners_per_pool": winners_per_pool,
            "winners_per_pool_bl": winners_per_pool_bl,
            "winners_per_pool_best": winners_per_pool_best,
            "overall_ranking": [{"method": m, "avg_hits": v} for m, v in ranked_overall],
            "overall_ranking_bl": [{"method": m, "avg_hits": v} for m, v in ranked_overall_bl],
            "overall_winner": ranked_overall[0][0] if ranked_overall else None,
            "overall_winner_bl": ranked_overall_bl[0][0] if ranked_overall_bl else None,
            "per_method": per_method,
        }
    return report
