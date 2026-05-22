import logging

import numpy as np
import pandas as pd

from loto_engine import LotoEngine
from loto_enterprise.core.timesfm_engine import get_timesfm_scores_v2

# Pondere pentru hit-uri nucleu vs extragerea reală (backtest calibrare).
# Fatalitate blacklist ≈ −50 puncte/hit; ~12 puncte/hit menține blacklist-ul dominant dar lasă pool_size să influențeze tuning-ul.
POOL_HIT_CALIB_WEIGHT = 12


def run_calibration(
    engine,
    test_draws=2,
    depths_to_test=None,
    pool_size=12,
    progress_cb=None,
):
    """
    Auto-tuning pentru adâncimea simulării (`sim_depth_pct`).

    Folosește intersectarea blacklist-urilor regresive ca înainte, și în plus —
    pentru `pool_size` fixat — construiește nucleul cu aceeași logică ca în
    pipeline (`_get_timesfm_pool`) și acordă bonus pentru câte numere din extragerea
    țintă cad în nucleu. Astfel adâncimea optimă poate depinde de dimensiunea
    nucleului (ex. 10 vs 15).
    """
    if depths_to_test is None:
        depths_to_test = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

    pool_size = int(pool_size)
    pool_size = max(6, min(24, pool_size))

    # Oprim temporar log-urile INFO, dar lasam un avertisment sa se stie ca incepe
    logging.warning(
        "[CALIBRARE] Incepere Auto-Tuning (pool_size=%s). Initializare model...",
        pool_size,
    )
    
    old_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.WARNING)
    
    df = engine.data.copy()
    total_draws = len(df)
    
    results = {
        d: {
            "score": 0,
            "total_excluded": 0,
            "total_fatalities": 0,
            "fatality_dates": [],
            "total_pool_hits": 0,
        }
        for d in depths_to_test
    }
    
    total_iters = test_draws * 10 # 10 pasi de regresie per extragere
    current_iter = 0
    
    for idx, i in enumerate(range(total_draws - test_draws, total_draws), 1):
        # Pregatim engine-ul pentru extragerea de test curenta
        train_df = df.iloc[:i].copy()
        engine.data = train_df
        engine._build_draw_matrix()
        
        row = df.iloc[i]
        n_cols = sorted([c for c in df.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], 
                        key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
        true_draw = set([int(row[c]) for c in n_cols if pd.notna(row.get(c))])
        
        # 1. Felii regresive: scoruri TimesFM + blacklist (percentila 25) per pas 100%..10%
        slice_blacklists: dict[int, set] = {}
        slice_scores: dict[int, dict[int, float]] = {}

        for step in range(100, 9, -10):
            current_iter += 1
            if progress_cb:
                p_val = min(current_iter / total_iters, 0.99)
                progress_cb(p_val, f"🚀 Analiză neurală: Pas {step}% (Extragere {idx}/{test_draws})")
            
            try:
                num_rows = int(len(train_df) * (step / 100))
                if num_rows < 32: continue
                
                data_slice = train_df.tail(num_rows).copy()
                # dm_slice logic similar to get_regressive_blacklist_v2
                dm_slice = engine._draw_matrix[-num_rows:] if engine._draw_matrix is not None else None

                scores = get_timesfm_scores_v2(
                    data_slice, dm_slice, engine.params, 
                    is_joker_drum=(engine.game_type == "joker"),
                    context_len=num_rows,
                    is_regressive_step=True
                )
                
                if scores:
                    slice_scores[step] = dict(scores)
                    vals = list(scores.values())
                    threshold = np.percentile(vals, 25)
                    slice_blacklists[step] = {n for n, s in scores.items() if s <= threshold}
            except Exception as exc:
                logging.error("[CALIBRARE] Eroare la calcul slice %d%%: %s", step, exc)

        def _scores_for_depth(d: int) -> dict[int, float]:
            if d in slice_scores:
                return slice_scores[d]
            if not slice_scores:
                return {}
            # depths_to_test poate include valori non‑multiplu de 10: alegem cel mai apropiat pas disponibil
            nearest = min(slice_scores.keys(), key=lambda s: abs(s - d))
            return dict(slice_scores[nearest])

        # 2. Evaluăm fiecare adâncime: blacklist (intersecție) + nucleu de dimensiune pool_size
        for depth in depths_to_test:
            # Blacklist-ul pentru adancimea 'depth' este intersectia tuturor feliilor de la 100 pana la 'depth'
            relevant_steps = [s for s in slice_blacklists.keys() if s >= depth]
            if not relevant_steps:
                continue
                
            current_blacklist = slice_blacklists[relevant_steps[0]]
            for s in relevant_steps[1:]:
                current_blacklist = current_blacklist.intersection(slice_blacklists[s])
            
            excluded_count = len(current_blacklist)
            fatality_nums = current_blacklist.intersection(true_draw)
            fatalities = len(fatality_nums)

            results[depth]["total_excluded"] += excluded_count
            results[depth]["total_fatalities"] += fatalities

            # Nucleu „ca în producție”: scoruri la fereastra depth + pool_size + blacklist la această adâncime
            tfm_for_depth = _scores_for_depth(depth)
            try:
                proposed_pool = engine._get_timesfm_pool(
                    tfm_for_depth,
                    pool_size=pool_size,
                    blacklist=current_blacklist,
                )
                pool_hits = len(set(proposed_pool) & true_draw)
            except Exception as e:
                logging.debug("[CALIBRARE] Pool simulat depth=%s: %s", depth, e)
                pool_hits = 0
            results[depth]["total_pool_hits"] += pool_hits

            # Salvăm data extragerii dacă există fatalități
            if fatalities > 0:
                draw_date = row.get("date", "N/A")
                if pd.notna(draw_date):
                    results[depth]["fatality_dates"].append(str(draw_date).split()[0])

    # 3. Scoruri finale (blacklist + acoperire nucleu pentru pool_size-ul UI)
    for depth in depths_to_test:
        res = results[depth]
        ph = res["total_pool_hits"]
        res["score"] = (
            res["total_excluded"]
            - (res["total_fatalities"] * 50)
            + (ph * POOL_HIT_CALIB_WEIGHT)
        )
        res["avg_excluded"] = res["total_excluded"] / test_draws if test_draws > 0 else 0
        res["avg_pool_hits"] = ph / test_draws if test_draws > 0 else 0.0
        
    best_depth = 40
    best_score = -99999
    
    for depth, stats in results.items():
        if stats['score'] > best_score:
            best_score = stats['score']
            best_depth = depth
            
    # Restore original data
    engine.data = df
    engine._build_draw_matrix()
    
    try:
        # Get actual eliminated numbers for the FULL (current) dataset at the best depth
        eliminated_set = engine._get_timesfm_regressive_blacklist(best_depth)
        results[best_depth]["eliminated_now"] = sorted(list(eliminated_set))
    except Exception as e:
        results[best_depth]["eliminated_now"] = []
        logging.error(f"[CALIBRARE] Eroare la obtinerea numerelor eliminate curente: {e}")
    
    # Restauram log-urile
    logging.getLogger().setLevel(old_level)
    
    return best_depth, results

def test_empiric():
    engine = LotoEngine(game_type="6/49")
    success = engine.load_data("input.csv")
    if not success:
        print("Failed to load data")
        return
        
    test_draws = 4
    depths = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    
    print(f"Testing dynamic Auto-Tuner pe ultimele {test_draws} extrageri...")
    best_depth, results = run_calibration(engine, test_draws=test_draws, depths_to_test=depths)
    
    print("\n\n#####################################################")
    print(" REZULTATE AUTO-TUNER DINAMIC (BACKTESTING)")
    print("#####################################################")
    
    for depth, stats in results.items():
        aph = stats.get("avg_pool_hits", 0.0)
        print(
            f"--> Adancime {depth}% : Scor = {stats['score']:.1f} | "
            f"Excluse: {stats['total_excluded']} | Fatalitati: {stats['total_fatalities']} | "
            f"Medie excluse: {stats['avg_excluded']:.2f} | Hit nucleu/extragere: {aph:.2f}"
        )
            
    print(f"\n=====================================================")
    print(f" RECOMANDARE DINAMICĂ: Setează 'Adancime Simulare' la {best_depth}%")
    print(f"=====================================================")

if __name__ == "__main__":
    test_empiric()
