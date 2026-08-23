"""Walk-forward / regressive benchmark runner — v2 (spec-compliant).

For every (game, urn, method, percentile_window, real|random) fold we:
    1. Train on the first (1 - P/100) of draws.
    2. Score numbers using a block-walk-forward (re-score every BLOCK_SIZE
       test draws, expanding history). For block_size = 99999 this is a
       single training per fold (fast). For block_size = 1 you get true
       per-step walk-forward (very slow for trainable nets).
    3. For each draw in the test window evaluate hits AT MULTIPLE POOL SIZES
       draw_n .. draw_n + 14. For Urna 2 the pool is fixed = draw_n.
    4. Capture CPU% and RAM peak via a background sampler thread.

Output:
    bench_results/folds.csv         one row per (game, method, pct, real?, fold)
                                    with hits@K for K = draw_n..draw_n+14
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
import numpy as np
import pandas as pd

from .methods import METHODS, call_method, method_meta
from .hardware import (
    snapshot as hw_snapshot,
    format_snapshot,
)
from .hw_sampler import HwSampler, HwSnapshot
from loto_enterprise.core.ranking import rank_by_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game definitions
# ---------------------------------------------------------------------------

@dataclass
class GameDef:
    key: str
    label: str
    csv_path: str
    cols: list[str]
    max_num: int
    draw_n: int
    pool_extra: int = 14  # evaluate hits for pools = draw_n .. draw_n + pool_extra
    is_single_pick: bool = False  # joker_urna2 → fixed pool_size = draw_n


def _list_istoric_dirs() -> list[Path]:
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


def discover_games(istoric_dir: str | None = None) -> list[GameDef]:
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

    games: list[GameDef] = []
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
    hits_per_pool: dict[str, float] = field(default_factory=dict)
    # Hit rates per pool size WITH blacklist applied (bottom 25% excluded)
    hits_per_pool_bl: dict[str, float] = field(default_factory=dict)
    family: str = ""                    # familia/librăria metodei (ml-*, classical-*, math-*, baseline...)
    avg_hits_topk: float = 0.0          # avg hits at K = draw_n (base pool)
    max_hits_topk: int = 0
    rate_4plus: float = 0.0             # rata extragerilor cu >=4 numere ghicite (regula 4+)
    rates_4plus_per_pool: dict[str, float] = field(default_factory=dict)
    rate_3plus: float = 0.0             # rata extragerilor cu >=3 numere ghicite (regula 3+)
    rates_3plus_per_pool: dict[str, float] = field(default_factory=dict)
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


def _top_k(scores: dict[int, float], k: int) -> list[int]:
    # Deleagă la regula canonică (scor desc, apoi număr mare întâi — evită
    # degenerarea 1..K) din core.ranking — ACEEAȘI folosită de producție
    # (pool_selection). Cu freq=None + scoruri finite comportamentul e
    # bit-identic cu vechea implementare, deci fold-urile din bench cache
    # (CACHE_VERSION curent) rămân valide — fără bump.
    # Non-finitele sunt sărite (ca în select_pool_from_scores): un NaN nu
    # mai bagă numărul în pool pe ordinea de inserare.
    finite: dict[int, float] = {}
    for n, s in scores.items():
        try:
            fs = float(s)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fs):
            finite[int(n)] = fs
    return rank_by_score(finite, k)


def _evaluate_fold(
    method_name: str,
    train_draws: np.ndarray,
    test_draws: np.ndarray,
    game: GameDef,
    block_size: int,
) -> tuple[FoldResult, HwSnapshot]:
    """Run a single fold with hardware sampling. Returns (FoldResult, hw_snap)."""
    n_test = len(test_draws)
    pool_sizes = [game.draw_n] if game.is_single_pick else [
        game.draw_n + i for i in range(game.pool_extra + 1)
    ]
    fr = FoldResult(
        game=game.key, method=method_name, percentile=0, is_random=False,
        n_train=len(train_draws), n_test=n_test, runtime_sec=0.0,
        family=str(method_meta(method_name).get("family", "") or ""),
        hits_per_pool={f"k{k}": 0.0 for k in pool_sizes},
        hits_per_pool_bl={f"k{k}": 0.0 for k in pool_sizes},
        rates_4plus_per_pool={f"k{k}": 0.0 for k in pool_sizes},
        rates_3plus_per_pool={f"k{k}": 0.0 for k in pool_sizes},
    )

    sampler = HwSampler(interval=0.1).start()
    t0 = time.perf_counter()
    try:
        per_pool_totals = {k: 0 for k in pool_sizes}
        per_pool_bl_totals = {k: 0 for k in pool_sizes}
        per_pool_max = {k: 0 for k in pool_sizes}
        per_pool_4plus = {k: 0 for k in pool_sizes}   # nr. extrageri cu >=4 numere ghicite
        per_pool_3plus = {k: 0 for k in pool_sizes}   # nr. extrageri cu >=3 numere ghicite
        n_eval = 0                                     # nr. total extrageri evaluate
        blocks = 0
        empty_blocks = 0  # Count blocks where call_method returned {} (silent failure)
        bl_sizes_seen: list[int] = []
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
                n_eval += 1
                for k in pool_sizes:
                    h = len(top_sets[k] & actual)
                    h_bl = len(top_sets_bl[k] & actual)
                    per_pool_totals[k] += h
                    per_pool_bl_totals[k] += h_bl
                    if h >= 4:                       # regula 4+: numărăm hiturile mari
                        per_pool_4plus[k] += 1
                    if h >= 3:                       # regula 3+ (prag alternativ, configurabil)
                        per_pool_3plus[k] += 1
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
            fr.rates_4plus_per_pool[f"k{k}"] = per_pool_4plus[k] / max(n_eval, 1)
            fr.rates_3plus_per_pool[f"k{k}"] = per_pool_3plus[k] / max(n_eval, 1)
        fr.avg_hits_topk = fr.hits_per_pool.get(f"k{game.draw_n}", 0.0)
        fr.max_hits_topk = per_pool_max[game.draw_n]
        # Regula 4+: rata de extrageri cu >=4 numere ghicite la pool-ul de bază (draw_n)
        fr.rate_4plus = fr.rates_4plus_per_pool.get(f"k{game.draw_n}", 0.0)
        fr.rate_3plus = fr.rates_3plus_per_pool.get(f"k{game.draw_n}", 0.0)
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
# Worker la nivel de MODUL (picklable) pentru ProcessPoolExecutor — paralelizare
# reala a metodelor CPU (ocoleste GIL-ul, spre deosebire de threads pe numpy pur).
# ---------------------------------------------------------------------------
def _eval_fold_worker(args):
    """Rulat in PROCES separat: (method, train, test, game, block_size, pct, is_random)
    → (method, pct, is_random, fr). Cache lookup/store se face in procesul principal."""
    method, train, test, game, block_size, pct, is_random = args
    try:
        fr, _snap = _evaluate_fold(method, train, test, game, block_size)
        fr.percentile = pct
        fr.is_random = is_random
        return (method, pct, is_random, fr, None)
    except Exception as exc:  # noqa: BLE001
        return (method, pct, is_random, None, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def run_benchmark(
    games: list[GameDef],
    methods: list[str],
    percentiles: list[int],
    block_size: int = 99999,
    random_seed: int = 1234,
    out_dir: str = "bench_results",
    progress_cb=None,
    use_cache: bool = True,
) -> dict:
    """`use_cache=False` -> skip disk cache lookup/store. Folosit cand:
       - utilizatorul forteaza rebench (vrea masuratori proaspete)
       - schimbarea de hardware (CPU->GPU) invalideaza datele cache-uite
         (cache-ul nu include hardware/torch version in key)."""
    out_path = Path(out_dir)
    out_path.mkdir(exist_ok=True, parents=True)

    fold_rows: list[FoldResult] = []
    method_meta_map = {m: method_meta(m) for m in methods}

    # GPU eliminat complet: toate metodele sunt CPU. Nu mai există clasificare
    # GPU/CPU, split de task-uri, env CUDA sau track GPU separat.
    total_folds_est = 0
    for game in games:
        for m in methods:
            meta = method_meta_map[m]
            if not meta["available"]:
                continue
            for pct in percentiles:
                total_folds_est += 2  # real + random
    # Marker parsabil de UI: gpu=0 mereu (păstrat pt compatibilitate cu parsing-ul UI).
    logger.info("[BENCH-SPLIT] cpu=%d gpu=0 total=%d", total_folds_est, total_folds_est)
    done_global = 0
    _since_flush = 0  # rezultate de la ultimul flush (cache SAU compute) → flush robust

    # Importuri o singură dată (erau în buclă).
    import os as _os
    import hashlib as _hl
    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
    try:
        from .bench_cache import compute_csv_hash, get_cached_fold, store_cached_fold
        _cache_ok = True
    except Exception as _cache_exc:  # noqa: BLE001
        logger.debug(f"[bench_cache] import failed: {_cache_exc}")
        _cache_ok = False

    def _flush_folds():
        """Scrie folds.csv ATOMIC din fold_rows-urile de PÂNĂ ACUM. Apelat periodic ca
        rezultatele să supraviețuiască unei anulări/crash (înainte se scria DOAR la final
        → 2h de bench anulat = folds.csv gol). Întoarce DataFrame-ul (pt agregare)."""
        if not fold_rows:
            return pd.DataFrame()
        rows = []
        for fr in fold_rows:
            row = asdict(fr)
            for k, v in row.pop("hits_per_pool").items():
                row[k] = v
            for k, v in row.pop("hits_per_pool_bl").items():
                row[f"{k}_bl"] = v
            for k, v in row.pop("rates_4plus_per_pool", {}).items():
                row[f"rate_4plus_{k}"] = v
            for k, v in row.pop("rates_3plus_per_pool", {}).items():
                row[f"rate_3plus_{k}"] = v
            rows.append(row)
        _df = pd.DataFrame(rows)
        try:
            _tmp = out_path / "folds.csv.tmp"
            _df.to_csv(_tmp, index=False)
            _tmp.replace(out_path / "folds.csv")  # atomic (OneDrive-safe)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[bench] flush folds.csv esuat: %s", exc)
        return _df

    def _handle_result(game, method, pct, is_random, fr, from_cache, kind):
        nonlocal done_global, _since_flush
        done_global += 1
        fold_rows.append(fr)
        tag = "CACHE HIT" if from_cache else f"hits@k{game.draw_n}={fr.avg_hits_topk:.3f} t={fr.runtime_sec:.1f}s"
        # [N/M] = total GRAND; eticheta CPU/GPU (kind) scrisă AUTORITAR în linie, ca UI-ul
        # să nu mai ghicească din nume (clasificarea după nume diverja de cea după familie
        # → cpu_done depășea cpu_tot). Format: [game/method/pct/REAL|RND/CPU|GPU].
        logger.info("[%d/%d] [%s/%s/%d%%/%s/%s] %s", done_global, total_folds_est,
                    game.key, method, pct, "RND" if is_random else "REAL", kind.upper(), tag)
        # Flush la fiecare 100 rezultate (cache SAU compute) → folds.csv rămâne proaspăt
        # pe parcurs (clasament live + rezultate parțiale la anulare). NU pe
        # `done_global % 100 == 0 and not from_cache`: rata flush-ul dacă al 100-lea
        # rezultat pica pe un cache hit → folds.csv rămânea vechi TOT bench-ul. Count-based
        # = robust (un burst de cache → 1 flush la 100, nu pe fiecare).
        _since_flush += 1
        if _since_flush >= 100:
            _flush_folds()
            _since_flush = 0
        if progress_cb:
            progress_cb(done_global, total_folds_est, fr, game)

    # ── PRE-PASS: încarcă TOATE jocurile, construiește task-urile, rezolvă cache-ul.
    # Adunăm TOATE task-urile (din toate jocurile) într-un singur pool global de procese.
    all_cpu_compute = []   # (method, train, test, game, block_size, pct, is_random, csv_hash)

    for game in games:
        try:
            draws = load_draws(game)
        except Exception as exc:
            logger.error("[%s] CSV load failed: %s", game.key, exc)
            continue
        n = len(draws)
        logger.info("[%s] %d draws loaded from %s", game.key, n, game.csv_path)
        # hash DETERMINIST per joc (hash() built-in e randomizat per proces → cache miss).
        _game_seed = int(_hl.md5(game.key.encode()).hexdigest()[:8], 16) % 9973
        rng = np.random.default_rng(random_seed + _game_seed)
        idx = np.arange(n)
        rng.shuffle(idx)
        shuffled_draws = draws[idx]

        csv_hash_game = None
        if use_cache and _cache_ok:
            try:
                csv_hash_game = compute_csv_hash(draws)
            except Exception as _e:  # noqa: BLE001
                logger.debug(f"[bench_cache] hash failed: {_e}")

        for method in methods:
            meta = method_meta_map[method]
            if not meta["available"]:
                logger.info("[%s/%s] SKIP (unavailable: %s)",
                            game.key, method, meta.get("unavailable_reason"))
                continue
            for pct in percentiles:
                n_test = max(1, int(math.ceil(n * pct / 100.0)))
                n_train = max(0, n - n_test)
                if pct >= 100:
                    n_train = max(80, n_train)
                    n_test = n - n_train
                if n_train < 80:
                    logger.info("[%s/%s/%d%%] skip — train too small (%d)",
                                game.key, method, pct, n_train)
                    continue
                for is_random in (False, True):
                    # Cache lookup instant; doar cache-miss-urile intră la calcul.
                    cached = None
                    if use_cache and _cache_ok and csv_hash_game is not None:
                        try:
                            cached = get_cached_fold(csv_hash_game, method, pct, game.key, is_random)
                        except Exception:  # noqa: BLE001
                            pass
                    if cached is not None:
                        _handle_result(game, method, pct, is_random, cached, True, "cpu")
                    else:
                        src = shuffled_draws if is_random else draws
                        args = (method, src[:n_train], src[n_train:n_train + n_test],
                                game, block_size, pct, is_random, csv_hash_game)
                        all_cpu_compute.append(args)

    # ── EXECUȚIE CONCURENTĂ: UN singur pool de PROCESE CPU ────────────────────────
    # Fiecare fold rulează într-un proces izolat → starea globală (RNG numpy) NU se
    # contaminează între folds concurente → rezultat determinist, identic cu rularea
    # secvențială (spre deosebire de thread-uri care ar partaja RNG-ul global).
    _nc = _os.cpu_count() or 4

    # ── BUGET DE MEMORIE pentru procese (evită commit-limit Windows 0xc000012d) ──────
    # Worker-ele CPU importă registry-ul de metode (sklearn/statsmodels ≈ 0.6 GB/proces).
    # Limităm numărul de procese după RAM-ul DISPONIBIL ca să nu-l epuizăm.
    _PER_PROC_GB = 0.6
    try:
        import psutil as _ps
        _avail_gb = _ps.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        _avail_gb = 8.0
    _proc_budget = max(2, int((_avail_gb * 0.50) / _PER_PROC_GB))  # ~50%% din RAM liber

    # Worker-e SINGLE-THREAD BLAS → N procese = N nuclee CURAT (fără oversubscription).
    # Metodele-s independente → paralelismul pe PROCESE bate threading-ul BLAS intra-metodă.
    for _tv in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        _os.environ.setdefault(_tv, "1")
    _cpu_cap = _nc - max(2, _nc // 4)  # ~75% din nuclee
    n_workers = max(1, min(_cpu_cap, _proc_budget))
    logger.info("[bench] RAM disp %.1f GB → buget %d procese (CPU=%d) "
                "[per-proc ~%.1f GB; commit-limit-safe]",
                _avail_gb, _proc_budget, n_workers, _PER_PROC_GB)
    fut_kind = {}   # fut -> (game, csv_hash, args)  (args pt re-rulare la pool rupt)

    def _make_pool(compute, max_workers):
        if not compute:
            return None
        try:
            ex = ProcessPoolExecutor(max_workers=max_workers)
            for args in compute:
                method, train, test, game, bs, pct, is_random, csv_hash = args
                fut = ex.submit(_eval_fold_worker, (method, train, test, game, bs, pct, is_random))
                fut_kind[fut] = (game, csv_hash, args)
            logger.info("[bench] CPU pool: %d task-uri pe %d procese", len(compute), max_workers)
            return ex
        except Exception as exc:  # noqa: BLE001
            logger.warning("[bench] ProcessPool indisponibil (%s) — fallback secvential.", exc)
            return None

    def _run_seq_one(args):
        """Rulează UN task în procesul principal (fără pool) — folosit la fallback/re-rulare."""
        method, train, test, game, bs, pct, is_random, csv_hash = args
        try:
            fr, _ = _evaluate_fold(method, train, test, game, bs)
            fr.percentile = pct
            fr.is_random = is_random
            if _cache_ok and csv_hash is not None:
                store_cached_fold(csv_hash, method, pct, game.key, is_random, fr)
            _handle_result(game, method, pct, is_random, fr, False, "cpu")
        except Exception as e2:  # noqa: BLE001
            logger.error("[%s/%s] secvential failed: %s", game.key, method, e2)

    _ex = _make_pool(all_cpu_compute, n_workers)

    # task-uri ale căror futures au crăpat (pool rupt / worker omorât de OOM) →
    # re-rulate SECVENȚIAL la final (în proces principal, unul câte unul → fără explozie
    # de memorie), ca să NU pierdem rezultate.
    failed_tasks = []

    # ── Drenaj cu WATCHDOG: dacă NIMIC nu se mai termină într-o fereastră întreagă,
    # declarăm restul "hung", le abandonăm și continuăm cu folds-urile deja strânse
    # (decizia tolerează lipsuri). Pragul e pe INACTIVITATE totală, nu pe durata unui
    # task. Configurabil prin LOTO_BENCH_STALL_TIMEOUT (sec).
    _stall_timeout = float(_os.environ.get("LOTO_BENCH_STALL_TIMEOUT", "900"))  # 15 min fara NICIUN rezultat = hung
    _hung = False
    if fut_kind:
        try:
            pending = set(fut_kind.keys())
            while pending:
                done, pending = wait(pending, timeout=_stall_timeout, return_when=FIRST_COMPLETED)
                if not done:
                    # Fereastra intreaga fara niciun rezultat -> restul e blocat.
                    logger.error(
                        "[bench] WATCHDOG: %d task-uri fara niciun rezultat in %.0fs — "
                        "le abandonez. Bench-ul continua cu folds-urile stranse.",
                        len(pending), _stall_timeout,
                    )
                    for fut in pending:
                        game, csv_hash, args = fut_kind[fut]
                        logger.error("[bench] HUNG abandonat: [%s] task=%s", game.key, args[0])
                        fut.cancel()
                    _hung = True
                    pending = set()
                    break
                for fut in done:
                    game, csv_hash, args = fut_kind[fut]
                    try:
                        method, pct, is_random, fr, err = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("[%s] future a crăpat (%s) — programez re-rulare secvențială.",
                                     game.key, exc)
                        failed_tasks.append(args)
                        continue
                    if err or fr is None:
                        logger.error("[%s/%s] task failed: %s", game.key, method, err)
                        continue
                    # cache store în procesul principal (subprocesul de calcul nu-l face)
                    if _cache_ok and csv_hash is not None:
                        try:
                            store_cached_fold(csv_hash, method, pct, game.key, is_random, fr)
                        except Exception:  # noqa: BLE001
                            pass
                    _handle_result(game, method, pct, is_random, fr, False, "cpu")
        finally:
            # La hang: NU asteptam workerii blocati — anulam si abandonam procesele.
            _wait_clean = not _hung
            if _ex is not None:
                _ex.shutdown(wait=_wait_clean, cancel_futures=_hung)

    # Fallback secvenţial: (a) pool care n-a putut porni, (b) task-uri cu future crăpat.
    if _ex is None and all_cpu_compute:
        for a in all_cpu_compute:
            _run_seq_one(a)
    if failed_tasks:
        logger.warning("[bench] Re-rulez SECVENȚIAL %d task-uri (pool rupt/OOM) — fără pierdere de rezultate.",
                        len(failed_tasks))
        for a in failed_tasks:
            _run_seq_one(a)

    # ----- Save per-fold CSV (scriere finală; pe parcurs s-a flush-uit periodic) -----
    df = _flush_folds()
    pool_keys_per_game = {}
    for g in games:
        if g.is_single_pick:
            pool_keys_per_game[g.key] = [f"k{g.draw_n}"]
        else:
            pool_keys_per_game[g.key] = [f"k{g.draw_n + i}" for i in range(g.pool_extra + 1)]
    if df.empty:
        df = pd.DataFrame()

    # ----- Aggregate per (game, pool) → winner -----
    report = _aggregate(df, games, methods, method_meta_map, pool_keys_per_game)
    with open(out_path / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def _aggregate(
    df: pd.DataFrame,
    games: list[GameDef],
    methods: list[str],
    method_meta_map: dict[str, dict],
    pool_keys_per_game: dict[str, list[str]],
) -> dict:
    report: dict = {
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
