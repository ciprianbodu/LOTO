"""Decision algorithm: pick the optimal (scorer, sim_depth, use_blacklist) per
(game, urna, pool_size) — for the auto-pilot.

Selection rules (in priority order):
    1. Method must BEAT the random baseline consistently — at least 60% of
       sim_depth windows must show lift > 0 over the random baseline at the
       same (game, pool, window).
    2. Among qualifying methods, pick the one with HIGHEST mean lift across
       all sim_depth windows (averaged with double-weight on larger windows
       since they exercise more historical data).
    3. For the chosen method, pick the optimal sim_depth window — the one
       that maximises avg_hits AND has at least 30 test draws (statistical
       stability). Prefer larger windows on ties.
    4. Use blacklist if the +BL variant beats the no-BL variant at that
       chosen (method, window).

The output is written to best_methods.json under `auto_pilot_per_pool[gk][kN]`
with full traceability (rationale + supporting numbers).
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


CONSISTENCY_THRESHOLD = 0.60  # method must beat random in ≥60% of windows
MIN_TEST_DRAWS_FOR_STABILITY = 30

# Câte metode intră în ensemble-ul de scoring (blend ponderat), în plus față de
# câștigătorul unic `scorer` (păstrat pt compatibilitate/afișare). Loteria e
# aleatoare — diferențele dintre metodele calificate sunt în mare parte zgomot
# statistic (vezi CLAUDE.md); combinarea top-K reduce riscul ca o singură
# metodă "norocoasă" pe date de test să domine complet pool-ul, în loc să
# pretindă că am găsit "cea mai bună" metodă. Cu 1 metodă calificată, ensemble
# degenerează la un singur membru (weight=1.0) — bit-identic cu comportamentul
# vechi (fără normalizare/combinare în loto_engine.py).
ENSEMBLE_MAX_METHODS = 3

# Pragul de hituri pe care bench-ul ALEGE metoda câștigătoare (rata extragerilor cu
# ≥ acest număr de numere ghicite). Implicit 3 (rată mai densă → selecție mai stabilă).
# NU schimbă șansele — loteria e aleatoare; doar pe ce metrică se alege câștigătorul.
# Reversibil: schimbă aici, sau setează LOTO_BENCH_TARGET=4.
BENCH_HIT_TARGET = int(os.environ.get("LOTO_BENCH_TARGET", "3"))


def _safe_lift(method_hits: float, random_hits: float) -> float:
    return float(method_hits) - float(random_hits)


def _wilson_lower_bound(successes: float, n: float, z: float = 1.0) -> float:
    """Limită inferioară de încredere (Wilson score) pt o proporție.

    Evenimentele T+ (implicit 3+) pe pool-uri mici pot fi rare (puține evenimente în
    sute de extrageri de test) — o comparație pe medie brută poate favoriza o
    metodă cu 1 eveniment din pură șansă în fața uneia cu 0, fără nicio
    semnificație statistică. Wilson reduce increderea în proporții estimate din
    puține evenimente, PUR determinist (fără aleatorism nou) pe datele existente
    din folds.csv. z=1.0 ≈ interval unilateral ~84%, conservator dar nu excesiv.
    """
    n = float(n)
    if n <= 0:
        return 0.0
    phat = max(0.0, min(1.0, float(successes) / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return max(0.0, (center - margin) / denom)


def _windows_method_beats_random(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> tuple[int, int]:
    """Return (n_windows_beat, n_windows_total) for method vs random baseline."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0, 0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0, 0
    n_beat = 0
    for p in common_pcts:
        if method_by_pct[p] > random_by_pct[p]:
            n_beat += 1
    return n_beat, len(common_pcts)


def _weighted_mean_lift(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> float:
    """Compute mean lift weighted by sim_depth (larger windows weight more)."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0.0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for p in common_pcts:
        weight = float(p)  # 100% window weighs 10x more than 10% window
        lift = float(method_by_pct[p]) - float(random_by_pct[p])
        weighted_sum += weight * lift
        weight_total += weight
    return weighted_sum / weight_total if weight_total > 0 else 0.0


def _build_ensemble_weights(entries: list[tuple[str, float]]) -> list[dict]:
    """Transformă [(method, confidence), ...] (deja ordonate, top-K) în
    [{"method": m, "weight": w}, ...] cu ponderi proporționale cu confidence
    (limita Wilson), normalizate la sumă 1. Floor mic (1e-6) ca metodele cu
    confidence=0 (evenimente insuficiente) să nu fie complet excluse — cad la
    ponderi egale între ele, nu la zero."""
    if not entries:
        return []
    floored = [(m, max(float(c), 1e-6)) for m, c in entries]
    total = sum(c for _, c in floored)
    if total <= 0:
        w = 1.0 / len(floored)
        return [{"method": m, "weight": round(w, 4)} for m, _ in floored]
    return [{"method": m, "weight": round(c / total, 4)} for m, c in floored]


def decide_optimal_config_for_pool(
    folds_df: pd.DataFrame,
    game_key: str,
    pool_size: int,
    draw_n: int,
) -> dict:
    """Run the decision algorithm for one (game, pool_size) pair.

    Returns a dict with the chosen scorer, sim_depth, use_blacklist + rationale.
    """
    base_col = f"k{pool_size}"
    base_col_bl = f"k{pool_size}_bl"
    rate_target_col = f"rate_{BENCH_HIT_TARGET}plus_k{pool_size}"

    if base_col not in folds_df.columns:
        return {"error": f"column {base_col} missing in folds.csv"}

    sub = folds_df[(folds_df["game"] == game_key) & (folds_df["failed"] != True)]  # noqa: E712
    if sub.empty:
        return {"error": f"no folds for game {game_key}"}

    real_random = sub[(sub["method"] == "random") & (sub["is_random"] == False)]  # noqa: E712
    methods = [m for m in sub["method"].unique() if m != "random"]

    # Coloane candidate pt rata T+, în ordine de preferință; sare peste coloane
    # all-NaN (cache vechi fără 3+) și cade pe 4+ → niciodată NaN în decizie.
    _rate_target_candidate_cols = (
        rate_target_col, f"rate_{BENCH_HIT_TARGET}plus",
        f"rate_4plus_k{pool_size}", "rate_4plus",
    )
    # Coloanele care CORESPUND țintei curente; orice rezoluție în afara lor =
    # fallback de metrică (ex. ținta 3+ dar folds.csv vechi are doar 4+) —
    # trebuie SEMNALIZAT (rate_col_mismatch + rationale), nu tăcut.
    _target_cols = {rate_target_col, f"rate_{BENCH_HIT_TARGET}plus"}
    _mismatch_cols_used: set[str] = set()

    def _resolve_rate_col(frame: pd.DataFrame) -> str | None:
        for c in _rate_target_candidate_cols:
            if c in frame.columns and frame[c].notna().any():
                if c not in _target_cols and c not in _mismatch_cols_used:
                    _mismatch_cols_used.add(c)  # log O DATĂ per (game, pool, coloană)
                    logger.warning(
                        "[decision] %s k%d: coloanele rate_%dplus lipsesc/all-NaN în folds.csv — "
                        "decizia cade pe %r (metrica 4+), deși ținta e %d+. Re-rulează bench-ul.",
                        game_key, pool_size, BENCH_HIT_TARGET, c, BENCH_HIT_TARGET,
                    )
                return c
        return None

    def _rate_target_mean(frame: pd.DataFrame) -> float:
        c = _resolve_rate_col(frame)
        if c is None:
            return 0.0
        v = float(frame[c].mean())
        return v if v == v else 0.0  # nu e NaN (NaN != NaN)

    def _rate_target_confidence(frame: pd.DataFrame) -> float:
        # Limită inferioară Wilson pe proporția AGREGATĂ (evenimente totale /
        # extrageri totale testate, pooled peste toate ferestrele sim_depth) —
        # mai robustă la zgomot decât media brută pe evenimente rare (4+ hituri).
        c = _resolve_rate_col(frame)
        if c is None or "n_test" not in frame.columns:
            return 0.0
        pairs = frame[[c, "n_test"]].dropna()
        if pairs.empty:
            return 0.0
        successes = float((pairs[c] * pairs["n_test"]).sum())
        n_total = float(pairs["n_test"].sum())
        return _wilson_lower_bound(successes, n_total)

    qualifying: list[tuple[str, float, int, int, float, float]] = []
    for m in methods:
        real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
        if real_m.empty:
            continue
        n_beat, n_total = _windows_method_beats_random(real_m, real_random, base_col)
        if n_total == 0:
            continue
        consistency = n_beat / n_total
        if consistency < CONSISTENCY_THRESHOLD:
            continue
        w_lift = _weighted_mean_lift(real_m, real_random, base_col)
        # REGULA T+: rata medie de extrageri cu >=BENCH_HIT_TARGET numere ghicite
        # (r4 = rată brută, doar pt afișare) + r4_conf (limită Wilson, pt sortare).
        r4 = _rate_target_mean(real_m)
        r4_conf = _rate_target_confidence(real_m)
        qualifying.append((m, w_lift, n_beat, n_total, r4, r4_conf))

    if not qualifying:
        # Fallback: nicio metodă nu a bătut random ≥60% din ferestre.
        # Prioritizăm tot regula T+ (robust, via Wilson), apoi avg_hits (la egalitate).
        ranked = []
        for m in methods + ["random"]:
            real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
            if real_m.empty:
                continue
            r4 = _rate_target_mean(real_m)
            r4_conf = _rate_target_confidence(real_m)
            ranked.append((m, r4_conf, r4, float(real_m[base_col].mean())))
        ranked.sort(key=lambda kv: (kv[1], kv[3]), reverse=True)
        if not ranked:
            return {"error": "no valid folds for any method"}
        scorer = ranked[0][0]
        rationale = (
            f"FALLBACK: no method consistently beat random "
            f"(≥{int(CONSISTENCY_THRESHOLD*100)}% of windows); "
            f"picked highest {BENCH_HIT_TARGET}+ rate (raw={ranked[0][2]:.3f}, "
            f"Wilson_lb={ranked[0][1]:.3f}) + avg_hits"
        )
        # Fallback: NICIO metodă a bătut random — conservator, ensemble cu un
        # singur membru (nu combinăm metode neconfirmate statistic).
        ensemble = _build_ensemble_weights([(scorer, 1.0)])
    else:
        # REGULA T+: sortăm întâi după limita Wilson a ratei BENCH_HIT_TARGET+
        # (robustă la evenimente rare/zgomot — nu doar rata brută), apoi după
        # lift mediu, apoi consistență. Metoda care prinde T+ cel mai des ȘI cu
        # dovezi suficiente câștigă, nu cea cu 1 eveniment norocos.
        qualifying.sort(key=lambda r: (r[5], r[1], r[2] / max(r[3], 1)), reverse=True)
        scorer = qualifying[0][0]
        rationale = (
            f"{scorer}: rată {BENCH_HIT_TARGET}+ @ k{pool_size} = {qualifying[0][4]:.3f} "
            f"(Wilson_lb={qualifying[0][5]:.3f}), beat random in "
            f"{qualifying[0][2]}/{qualifying[0][3]} windows (lift {qualifying[0][1]:+.4f}); "
            f"din {len(qualifying)} metode calificate [prioritate: {BENCH_HIT_TARGET}+ numere ghicite, robust]"
        )
        # Ensemble: top ENSEMBLE_MAX_METHODS calificate, ponderate cu limita
        # Wilson a ratei T+ (r[5]) — variance-reduction, NU o pretenție de
        # "predicție mai bună" (loteria e aleatoare; diferențele dintre
        # metodele calificate sunt majoritar zgomot statistic). Cu o singură
        # metodă calificată, degenerează la ensemble cu 1 membru (weight=1.0).
        top_k = qualifying[:ENSEMBLE_MAX_METHODS]
        ensemble = _build_ensemble_weights([(m, r4_conf) for m, _, _, _, _, r4_conf in top_k])

    # Pick the best sim_depth for the chosen scorer based on target hit rate (percentage of drawings)
    real_chosen = sub[(sub["method"] == scorer) & (sub["is_random"] == False)]  # noqa: E712
    rate_col = _resolve_rate_col(real_chosen)
    if rate_col is None:
        rate_col = base_col  # fallback to avg_hits — tot un fallback de metrică
        _mismatch_cols_used.add(base_col)


    by_pct = real_chosen.groupby("percentile").agg(
        target_rate=(rate_col, "mean"),
        avg_hits=(base_col, "mean"),
        n_test=("n_test", "mean"),
        runtime=("runtime_sec", "mean"),
    )
    # Filter to windows with enough test data
    stable = by_pct[by_pct["n_test"] >= MIN_TEST_DRAWS_FOR_STABILITY]
    if stable.empty:
        stable = by_pct
    # Sort by target_rate desc (optimizing percentage of hits), tiebreak on avg_hits desc
    stable = stable.sort_values(
        by=["target_rate", "avg_hits"], ascending=[False, False]
    )
    best_pct = int(stable.index[0])
    best_avg = float(stable.iloc[0]["avg_hits"])

    # Decide use_blacklist at the chosen (method, sim_depth)
    use_bl = False
    bl_avg = None
    if base_col_bl in real_chosen.columns:
        at_pct = real_chosen[real_chosen["percentile"] == best_pct]
        if not at_pct.empty:
            no_bl = float(at_pct[base_col].mean())
            bl_avg = float(at_pct[base_col_bl].mean())
            if bl_avg > no_bl:
                use_bl = True

    # Semnalizare fallback de metrică: dacă ORICE rezoluție a folosit o coloană
    # din afara țintei (3+ absent → 4+), o marcăm în rezultat + rationale — ca
    # UI-ul și logurile să nu pretindă că decizia s-a luat pe metrica "T+".
    rate_col_mismatch = bool(_mismatch_cols_used)
    if rate_col_mismatch:
        rationale += (
            f" [ATENȚIE: metrica {BENCH_HIT_TARGET}+ indisponibilă în folds.csv — "
            f"decizie luată pe {', '.join(sorted(_mismatch_cols_used))}; re-rulează bench-ul]"
        )

    return {
        "scorer": scorer,
        "ensemble": ensemble,
        "sim_depth_pct": best_pct,
        "use_blacklist": use_bl,
        "avg_hits": best_avg,
        "avg_hits_with_blacklist": bl_avg,
        "rationale": rationale,
        "rate_col_used": rate_col,
        "rate_col_mismatch": rate_col_mismatch,
        "qualifying_methods": len(qualifying),
        "consistency_threshold": CONSISTENCY_THRESHOLD,
    }


def build_auto_pilot_matrix(
    folds_csv_path: str,
    games_meta: dict[str, dict],
) -> dict[str, dict[str, dict]]:
    """Build the full {game_key: {pool_key: config}} matrix from folds.csv.

    Args:
        folds_csv_path: path to bench_results/folds.csv
        games_meta: {game_key: {"draw_n": int, "pool_range": [int]}}

    Returns:
        Nested dict suitable for embedding in best_methods.json.
    """
    df = pd.read_csv(folds_csv_path)
    if "failed" not in df.columns:
        df["failed"] = False
    matrix: dict[str, dict[str, dict]] = {}
    for gk, meta in games_meta.items():
        matrix[gk] = {}
        for k in meta["pool_range"]:
            cfg = decide_optimal_config_for_pool(
                df, gk, pool_size=k, draw_n=meta["draw_n"]
            )
            matrix[gk][f"k{k}"] = cfg
    return matrix


def update_best_methods_with_auto_pilot(
    best_methods_path: str = "best_methods.json",
    folds_csv_path: str = "bench_results/folds.csv",
) -> dict:
    """Read folds.csv, run decision algo, write `auto_pilot_per_pool` into best_methods.json."""
    bm_path = Path(best_methods_path)
    if not bm_path.exists():
        raise FileNotFoundError(f"{best_methods_path} not found")
    if not Path(folds_csv_path).exists():
        raise FileNotFoundError(f"{folds_csv_path} not found")

    cfg = json.loads(bm_path.read_text(encoding="utf-8"))
    games = cfg.get("games", {})

    games_meta = {}
    for gk, gd in games.items():
        draw_n = int(gd.get("draw_n", 6))
        if gk == "joker_urna2":
            pool_range = [draw_n]
        else:
            # Aligned with runner.py pool_extra=14 → K=draw_n..draw_n+14
            # (2026-05-26: extins de la +6 → +14 ca să acopere pool size sweep
            # K=15/18/20 cerut de user pentru testare 4+ hits stable).
            pool_range = list(range(draw_n, draw_n + 15))  # draw_n .. draw_n+14
        games_meta[gk] = {"draw_n": draw_n, "pool_range": pool_range}

    matrix = build_auto_pilot_matrix(folds_csv_path, games_meta)

    for gk in games:
        games[gk]["auto_pilot_per_pool"] = matrix.get(gk, {})

    from ui_shared import atomic_write_json
    atomic_write_json(bm_path, cfg)  # atomic: tmp+fsync+os.replace
    return matrix
