"""
Modul de Backtesting pentru LOTO

Compară variantele generate cu rezultatele extragerilor anterioare din CSV
pentru a calcula câte numere ar fi fost ghicite în medie.

Backtesting Retroactiv: Generează previziuni pentru fiecare punct istoric
folosind doar datele disponibile până la acel moment (walk-forward simulation).
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# Import pentru generare previziuni
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from loto_engine import LotoEngine

logger = logging.getLogger(__name__)

_GAME_DRAW_N = {
    "6/49": 6,
    "5/40": 5,
    "joker": 5,
}


def scored_variant_numbers(variant: List[int], game_type: str) -> List[int]:
    """Numbers that count against draw columns for hit scoring.

    Joker tickets include the Urna 2 value for display; it must not count as an
    Urna 1 hit in backtests or walk-forward expansion.
    """
    draw_n = int(_GAME_DRAW_N.get(game_type, 6))
    vals = [int(n) for n in list(variant)]
    if game_type == "joker":
        return vals[:draw_n]
    return vals


@dataclass
class BacktestResult:
    """Rezultatul unei singure comparări variantă vs extragere."""
    draw_date: Optional[str]
    draw_numbers: Set[int]
    variant: List[int]
    hits: int
    hit_numbers: Set[int]
    hit_rate: float
    draw_index: int = 0
    hits_union: int = 0  # Câte numere s-au potrivit în întregul pool (union) la această extragere


@dataclass
class BacktestSummary:
    """Sumarul complet al backtest-ului."""
    total_draws_evaluated: int
    variants_tested: int
    avg_hits_per_draw: float
    avg_hit_rate: float
    best_draw_hits: int
    worst_draw_hits: int
    median_hits: float
    std_hits: float
    distribution: Dict[int, int]  # hits -> count
    top_performing_draws: List[BacktestResult]
    avg_union_hits: float = 0.0  # Media hit-urilor pe întregul pool
    all_results: List[BacktestResult] = field(default_factory=list)


@dataclass
class RetroactivePrediction:
    """Previziune generată retroactiv pentru un punct istoric."""
    simulation_date: str  # Data la care s-ar fi făcut previziunea
    target_draw_date: str  # Data extragerii vizate
    variants: List[List[int]]  # Variantele generate
    predicted_numbers: Set[int]  # Toate numerele prezise (union)
    actual_numbers: Set[int]  # Numerele care au ieșit efectiv
    hits: int  # Câte numere s-au potrivit
    pool_size: int  # Dimensiunea pool-ului folosit
    guarantee: int  # Garanția folosită
    game_type: str  # Tipul jocului
    draw_index: int = 0  # Indexul extragerii
    hits_union: int = 0  # Câte numere s-au potrivit în întregul pool (union)
    omnius_ticket: List[int] = field(default_factory=list)  # biletul OMNIUS (top draw_n din pool)
    omnius_hits: int = 0  # Câte numere a nimerit biletul OMNIUS la această extragere


class LotoBacktester:
    """
    Motor de backtesting pentru strategii LOTO.
    
    Evaluează performanța variantelor generate pe ultimele N% din extragerile istorice.
    """
    
    def __init__(self, data_input: str | pd.DataFrame, game_type: str = "6/49"):
        """
        Inițializează backtester-ul.
        
        Args:
            data_input: Calea către fișierul CSV sau un DataFrame deja încărcat
            game_type: Tipul jocului ("6/49", "5/40", "joker")
        """
        self.data_input = data_input
        self.game_type = game_type
        self.params = self._get_game_params(game_type)
        self.df: Optional[pd.DataFrame] = None
        if isinstance(data_input, pd.DataFrame):
            self.df = data_input
            self.csv_path = Path("dataframe_input")
        else:
            self.csv_path = Path(data_input)
            
        self.draws: List[Set[int]] = []
        self.dates: List[Optional[str]] = []
        
        self._load_data()
    
    def _get_game_params(self, game_type: str) -> Dict:
        """Parametrii pentru fiecare tip de joc.

        Pentru Joker, draw_n = 5 (urna 1, numere 1-45). Coloana `joker` (1-20,
        urna 2) NU intră în hit-counting pe pool — e drum separat scorat de
        pipeline-ul `hard_core_joker`.
        """
        params = {
            "6/49": {"max_n": 49, "draw_n": 6, "num_cols": ["n1", "n2", "n3", "n4", "n5", "n6"]},
            "5/40": {"max_n": 40, "draw_n": 5, "num_cols": ["n1", "n2", "n3", "n4", "n5"]},
            "joker": {"max_n": 45, "draw_n": 5, "num_cols": ["n1", "n2", "n3", "n4", "n5"]},
        }
        return params.get(game_type, params["6/49"])
    
    def _load_data(self) -> None:
        """Încarcă datele din CSV și extrage numerele."""
        if self.df is None:
            if not self.csv_path.exists():
                raise FileNotFoundError(f"Fișierul {self.csv_path} nu există")
            self.df = pd.read_csv(self.csv_path)
            logger.info(f"[BACKTEST] Date încărcate: {len(self.df)} rânduri din {self.csv_path}")
        else:
            logger.info(f"[BACKTEST] Folosesc DataFrame direct ({len(self.df)} rânduri)")
        
        # Detectăm coloanele cu numere
        num_cols = self._detect_number_columns()
        if not num_cols:
            raise ValueError(f"Nu s-au găsit coloane cu numere în {self.csv_path}")
        
        logger.info(f"[BACKTEST] Coloane detectate: {num_cols}")
        
        # Extragem numerele și datele
        for idx, row in self.df.iterrows():
            numbers = []
            for col in num_cols:
                if col in row and pd.notna(row[col]):
                    try:
                        num = int(row[col])
                        numbers.append(num)
                    except (ValueError, TypeError):
                        continue
            
            if len(numbers) >= self.params["draw_n"]:
                self.draws.append(numbers)
                # Încercăm să extragem data dacă există
                date_val = None
                for date_col in ["date", "data", "draw_date", "extragere", "Data"]:
                    if date_col in row:
                        date_val = str(row[date_col])
                        break
                self.dates.append(date_val)
        
        logger.info(f"[BACKTEST] Extrageri valide procesate: {len(self.draws)}")
    
    def _detect_number_columns(self) -> List[str]:
        """Detectează automat coloanele care conțin numere."""
        cols = []
        
        # Căutăm coloane standard n1, n2, etc.
        expected = self.params["num_cols"]
        for col in expected:
            if col in self.df.columns:
                cols.append(col)
        
        if len(cols) >= self.params["draw_n"]:
            return cols[:self.params["draw_n"]]
        
        # Fallback: căutăm coloane care încep cu 'n' sau 'N' și conțin numere
        for col in self.df.columns:
            if col.lower().startswith('n') and col[1:].isdigit():
                if col not in cols:
                    cols.append(col)
        
        return cols[:self.params["draw_n"]]

    def _scored_variant_numbers(self, variant: List[int]) -> List[int]:
        return scored_variant_numbers(variant, self.game_type)
    
    def get_last_percentile_draws(self, percentile: float = 20.0) -> List[Tuple[Optional[str], Set[int]]]:
        """
        Returnează ultimele N% extrageri pentru evaluare.
        
        Args:
            percentile: Procentul din extrageri (default 20%)
            
        Returns:
            Lista de tuple (data, numere) pentru perioada evaluată
        """
        if not self.draws:
            return []
        
        n_draws = len(self.draws)
        n_eval = max(1, int(n_draws * percentile / 100.0))
        
        # Luăm ultimele N extrageri
        start_idx = n_draws - n_eval
        
        result = []
        for i in range(start_idx, n_draws):
            result.append((i, self.dates[i], self.draws[i]))
        
        logger.info(f"[BACKTEST] Perioada evaluare: ultimele {n_eval} extrageri din {n_draws} ({percentile}%)")
        return result
    
    def evaluate_variant(self, variant: List[int], target_draws: Optional[List] = None) -> List[BacktestResult]:
        """
        Evaluează o singură variantă contra extragerilor țintă.
        
        Args:
            variant: Lista de numere din variantă
            target_draws: Lista de extrageri (default: ultimele 20%)
            
        Returns:
            Lista de rezultate pentru fiecare extragere
        """
        if target_draws is None:
            target_draws = self.get_last_percentile_draws(20.0)
        
        scored_variant = self._scored_variant_numbers(variant)
        results = []
        
        for idx, date, draw_nums in target_draws:
            # Multiset hit count: păstrăm semantica originală (duplicate consumă o singură
            # apariție din extragere), dar înlocuim list.remove() O(n) cu Counter O(1).
            draw_counts = Counter(draw_nums)
            hit_numbers = []
            for n in scored_variant:
                if draw_counts[n] > 0:
                    hit_numbers.append(n)
                    draw_counts[n] -= 1

            hit_count = len(hit_numbers)
            hit_rate = hit_count / self.params["draw_n"] if self.params["draw_n"] > 0 else 0
            
            results.append(BacktestResult(
                draw_date=date,
                draw_index=idx,
                draw_numbers=set(draw_nums),
                variant=variant,
                hits=hit_count,
                hit_numbers=set(hit_numbers),
                hit_rate=hit_rate
            ))
        
        return results
    
    def evaluate_variants(self, variants: List[List[int]], percentile: float = 20.0) -> BacktestSummary:
        """
        Evaluează multiple variante și generează un sumar.

        Args:
            variants: Lista de variante (fiecare variantă e o listă de numere)
            percentile: Procentul din extrageri pentru evaluare

        Returns:
            Sumarul backtest-ului

        PERF: Vectorizat cu numpy (matmul) — pe 210 variante × 127 extrageri
        durează ~75ms (vs minute în varianta veche cu Counter loops).
        """
        target_draws = self.get_last_percentile_draws(percentile)

        if not target_draws:
            logger.warning("[BACKTEST] Nu există extrageri pentru evaluare")
            return BacktestSummary(
                total_draws_evaluated=0,
                variants_tested=len(variants),
                avg_hits_per_draw=0.0,
                avg_hit_rate=0.0,
                best_draw_hits=0,
                worst_draw_hits=0,
                median_hits=0.0,
                std_hits=0.0,
                distribution={},
                top_performing_draws=[]
            )

        if not variants:
            return BacktestSummary(
                total_draws_evaluated=len(target_draws),
                variants_tested=0,
                avg_hits_per_draw=0.0, avg_hit_rate=0.0,
                best_draw_hits=0, worst_draw_hits=0,
                median_hits=0.0, std_hits=0.0,
                distribution={}, top_performing_draws=[]
            )

        import time as _bt_time
        t0 = _bt_time.perf_counter()

        # Vectorized hit counting: variant_bin @ draw_bin.T în loc de loop dublu.
        max_n = int(self.params["max_n"])
        draw_n = int(self.params["draw_n"])
        V = len(variants)
        D = len(target_draws)

        variant_bin = np.zeros((V, max_n + 1), dtype=np.int8)
        for vi, v in enumerate(variants):
            for n in self._scored_variant_numbers(v):
                if 1 <= n <= max_n:
                    variant_bin[vi, n] = 1

        draw_bin = np.zeros((D, max_n + 1), dtype=np.int8)
        for di, (_idx, _date, draw_nums) in enumerate(target_draws):
            for n in draw_nums:
                if 1 <= n <= max_n:
                    draw_bin[di, n] = 1

        hits_matrix = (variant_bin.astype(np.int16) @ draw_bin.T.astype(np.int16))
        union_bin = (variant_bin.any(axis=0)).astype(np.int16)
        union_hits_per_draw = draw_bin.astype(np.int16) @ union_bin

        all_results: list = []
        all_hits_flat: list = []
        for vi in range(V):
            variant = list(variants[vi])
            for di in range(D):
                idx, date, draw_nums = target_draws[di]
                h = int(hits_matrix[vi, di])
                all_results.append(BacktestResult(
                    draw_date=date,
                    draw_index=int(idx),
                    draw_numbers=set(draw_nums),
                    variant=variant,
                    hits=h,
                    hit_numbers=set(),  # lazy: populat doar pentru top performers
                    hit_rate=(h / draw_n) if draw_n > 0 else 0.0,
                    hits_union=int(union_hits_per_draw[di]),
                ))
                all_hits_flat.append(h)

        hits_array = np.asarray(all_hits_flat, dtype=np.int64)
        union_hits_array = np.asarray(union_hits_per_draw, dtype=np.int64)

        elapsed_ms = (_bt_time.perf_counter() - t0) * 1000
        logger.info(f"[BACKTEST] Vectorized eval: {V}×{D}={V*D} pairs în {elapsed_ms:.0f}ms")
        
        distribution = {}
        for h in range(self.params["draw_n"] + 1):
            count = int(np.sum(hits_array == h))
            if count > 0:
                distribution[h] = count
        
        # Sortăm după hits descrescător pentru top performing
        sorted_results = sorted(all_results, key=lambda x: x.hits, reverse=True)
        top_performing = sorted_results[:10]  # Top 10
        # Populăm hit_numbers DOAR pentru top performers (display field)
        for r in top_performing:
            r.hit_numbers = set(self._scored_variant_numbers(r.variant)) & r.draw_numbers
        
        summary = BacktestSummary(
            total_draws_evaluated=len(target_draws),
            variants_tested=len(variants),
            avg_hits_per_draw=float(np.mean(hits_array)) if len(hits_array) > 0 else 0.0,
            avg_hit_rate=float(np.mean(hits_array / self.params["draw_n"])) if len(hits_array) > 0 else 0.0,
            best_draw_hits=int(np.max(hits_array)) if len(hits_array) > 0 else 0,
            worst_draw_hits=int(np.min(hits_array)) if len(hits_array) > 0 else 0,
            median_hits=float(np.median(hits_array)) if len(hits_array) > 0 else 0.0,
            std_hits=float(np.std(hits_array)) if len(hits_array) > 0 else 0.0,
            distribution=distribution,
            avg_union_hits=float(np.mean(union_hits_array)) if len(union_hits_array) > 0 else 0.0,
            top_performing_draws=top_performing,
            all_results=all_results
        )
        
        logger.info(f"[BACKTEST] Evaluare completă: {summary.variants_tested} variante × {summary.total_draws_evaluated} extrageri")
        return summary
    
    def print_summary(self, summary: BacktestSummary) -> None:
        """Afișează un raport formatat al rezultatelor."""
        print("\n" + "=" * 60)
        print("          RAPORT BACKTESTING LOTO")
        print("=" * 60)
        print(f"\nPerioada evaluata: Ultimele {summary.total_draws_evaluated} extrageri")
        print(f"Variante testate: {summary.variants_tested}")
        print(f"\n--- STATISTICI GENERALE ---")
        print(f"Medie numere ghicite per extragere: {summary.avg_hits_per_draw:.2f}")
        print(f"Rata de succes medie: {summary.avg_hit_rate*100:.1f}%")
        print(f"Mediana numere ghicite: {summary.median_hits:.1f}")
        print(f"Deviatie standard: {summary.std_hits:.2f}")
        print(f"Cel mai bun rezultat: {summary.best_draw_hits} numere")
        print(f"Cel mai slab rezultat: {summary.worst_draw_hits} numere")
        
        print(f"\n--- DISTRIBUTIE NUMERE GHICITE ---")
        for hits in sorted(summary.distribution.keys(), reverse=True):
            count = summary.distribution[hits]
            pct = count / (summary.variants_tested * summary.total_draws_evaluated) * 100
            bar = "#" * int(pct / 2)
            print(f"  {hits} numere: {count:4d} ({pct:5.1f}%) {bar}")
        
        if summary.top_performing_draws:
            print(f"\n--- TOP {min(5, len(summary.top_performing_draws))} REZULTATE ---")
            for i, r in enumerate(summary.top_performing_draws[:5], 1):
                date_str = r.draw_date if r.draw_date else "N/A"
                print(f"  {i}. {date_str}: {r.hits} numere ({', '.join(map(str, sorted(r.hit_numbers)))})")
        
        print("=" * 60 + "\n")
    
    def run_retroactive_backtest(self, pool_size: int = 12, guarantee: int = 4,
                                  lookback_percent: float = 20.0,
                                  backtest_depth_percent: float = 5.0,
                                  filter_consecutives: bool = True,
                                  max_variants: int = 0,
                                  simulation_step: int = 1,
                                  use_feedback: bool = True,
                                  enable_hard_inversion: bool = True,
                                  smart_reduction: bool = True,
                                  progress_cb=None) -> List[RetroactivePrediction]:
        """
        Backtesting Retroactiv: Genereaza previziuni pentru fiecare punct istoric.
        
        Args:
            pool_size: Dimensiunea pool-ului pentru wheeling
            guarantee: Garantia set cover
            lookback_percent: Ce % din istoric sa foloseasca pentru analiza frecventei la FIECARE pas
            backtest_depth_percent: Procentul din istoric (coada) de testat
            filter_consecutives: Daca sa aplice filtrul anti-secventa
            max_variants: Limita de variante
            simulation_step: Din cate in cate extrageri sa faca simulare (1 = toate)
            use_feedback: Daca sa foloseasca Adaptive Local Tuning (Metoda 1)
            enable_hard_inversion: Daca sa aplice Hard Inversion partiala (temp_blacklist)
                dupa catastrofe. Setati False pentru ablation studies care masoara
                contributia exclusiva a regime_reset.
        """
        if len(self.draws) < 10:
            logger.warning("[BACKTEST] Prea puține date pentru backtesting retroactiv")
            return []
        
        # Determinăm perioada de simulare
        n_draws = len(self.draws)
        n_simulate = max(1, int(n_draws * backtest_depth_percent / 100.0))
        start_idx = n_draws - n_simulate
        
        logger.info(f"[BACKTEST RETROACTIV] Simulăm pentru {n_simulate} extrageri din {n_draws}")
        
        retro_predictions = []
        feedback_map: Dict[int, float] = {}  # num -> multiplier
        # State adaptiv in-memory pentru backtest (NU atinge adaptive_state.json)
        adaptive_history: List[Dict] = []
        streak_zero = 0
        active_mode = "normal"
        reset_duration = 0
        # Hard inversion temporară între iterații: pool-ul de la pasul anterior
        # care a produs catastrofă (excludere o singură extragere).
        prev_pool_for_inversion: List[int] = []
        prev_event_for_inversion: Optional[str] = None

        # Importăm noul modul; fallback la logica veche dacă lipsește
        try:
            from loto_enterprise.core.adaptive_feedback import (
                compute_post_draw_feedback,
                compute_temp_blacklist,
            )
            _has_adaptive = True
        except ImportError:
            _has_adaptive = False
            logger.warning("[BACKTEST] adaptive_feedback indisponibil — folosesc legacy feedback.")

        # Iterăm prin fiecare punct de simulare
        for sim_idx in range(start_idx, n_draws, simulation_step):
            sim_num = sim_idx - start_idx + 1

            if progress_cb is not None:
                try:
                    progress_cb(sim_num / max(1, n_simulate))
                except Exception:  # noqa: BLE001
                    pass

            # Data extragerii vizate (următoarea după punctul de simulare)
            target_date = self.dates[sim_idx] if sim_idx < len(self.dates) else f"Draw_{sim_idx}"

            # Data la care facem simularea (extragerea curentă)
            sim_date = self.dates[sim_idx - 1] if sim_idx > 0 else "Start"

            logger.info(f"[BACKTEST RETROACTIV] Simulare {sim_num}/{n_simulate} pentru {target_date}")

            try:
                # Creăm un subset al datelor până la acest moment (walk-forward)
                historical_df = self.df.iloc[:sim_idx].copy()

                if len(historical_df) < 5:
                    logger.warning(f"[BACKTEST] Prea puține date istorice la simulare {sim_num}")
                    continue

                # Inițializăm motorul
                engine = LotoEngine(self.game_type)
                engine.data = historical_df
                engine._build_draw_matrix()

                # Aplicăm feedback-ul acumulat din pașii anteriori (BEFORE pipeline)
                engine.error_correction_map = feedback_map.copy()
                # Inject regime mode pentru ca TimesFM să folosească ponderi reactive
                # dacă suntem în reset (catastrofe consecutive în istoric)
                engine._adaptive_mode = active_mode
                engine._adaptive_event = adaptive_history[-1].get("event") if adaptive_history else None
                # Hard inversion temporară: dacă pasul anterior a fost catastrofă,
                # pasăm pool-ul ratat ca temp_blacklist pentru această iterație.
                # Skip complet dacă enable_hard_inversion=False (ablation mode).
                if (enable_hard_inversion and _has_adaptive
                        and prev_event_for_inversion == "catastrophe"
                        and prev_pool_for_inversion):
                    try:
                        engine._temp_blacklist = compute_temp_blacklist(
                            last_pool=list(prev_pool_for_inversion),
                            last_event=prev_event_for_inversion,
                            universe_size=int(engine.params.get("max_n", 49)),
                            pool_size=int(pool_size),
                            enable_full_inversion=True,
                        )
                    except Exception as _e_inv:
                        logger.warning(f"[BACKTEST] Eroare temp_blacklist: {_e_inv}")
                        engine._temp_blacklist = set()
                else:
                    engine._temp_blacklist = set()

                # Rulăm pipeline-ul instituțional (fără persistență — backtest in-memory)
                lines, _, _, _, _, audit = engine.run_institutional_pipeline(
                    progress_cb=None,
                    pool_size=pool_size,
                    guarantee=guarantee,
                    max_variants=max_variants,
                    lookback=lookback_percent,
                    filter_consecutives=filter_consecutives,
                    smart_reduction=smart_reduction,
                    enable_adaptive_persistence=False,
                )

                # Evaluăm rezultatul contra extragerii reale
                actual_draw = self.draws[sim_idx]

                max_hits = 0
                for v in lines:
                    h = len(set(self._scored_variant_numbers(v)) & set(actual_draw))
                    if h > max_hits:
                        max_hits = h

                predicted_union = set().union(
                    *(self._scored_variant_numbers(v) for v in lines)
                ) if lines else set()
                hits_union = len(predicted_union & set(actual_draw))

                # Biletul OMNIUS retroactiv = top draw_n numere din pool după scorul
                # metodei câștigătoare (același criteriu ca biletul OMNIUS afișat azi,
                # dar regenerat onest pentru fiecare extragere trecută, fără lookahead).
                _draw_n = 6 if self.game_type == "6/49" else 5
                _omni_scores = getattr(engine, "_last_pool_scores", None) or {}
                _omnius_ticket = sorted(
                    sorted(engine.hard_core, key=lambda n: _omni_scores.get(int(n), 0.0),
                           reverse=True)[:_draw_n]
                )
                _omnius_hits = len(set(_omnius_ticket) & set(actual_draw))

                retro_pred = RetroactivePrediction(
                    simulation_date=str(sim_date),
                    target_draw_date=str(target_date),
                    variants=lines,
                    predicted_numbers=set(engine.hard_core),
                    actual_numbers=actual_draw,
                    hits=max_hits,
                    hits_union=hits_union,
                    pool_size=pool_size,
                    guarantee=guarantee,
                    game_type=self.game_type,
                    draw_index=sim_idx,
                    omnius_ticket=_omnius_ticket,
                    omnius_hits=_omnius_hits,
                )

                retro_predictions.append(retro_pred)

                logger.info(f"[BACKTEST RETROACTIV] Rezultat: {max_hits} numere ghicite (Max dintr-o varianta), pool_hits={hits_union}")

                if use_feedback:
                    if _has_adaptive:
                        # Folosim feedback-ul diferențiat (catastrophe-aware) din modulul nou
                        new_map, event, info = compute_post_draw_feedback(
                            last_pool=list(engine.hard_core),
                            actual_draw=list(actual_draw),
                            current_map=feedback_map,
                            history=adaptive_history,
                            game_type=self.game_type,
                            pool_size=pool_size,
                            streak_zero=streak_zero,
                            prev_mode=active_mode,
                            reset_duration=reset_duration,
                        )
                        feedback_map = new_map
                        streak_zero = int(info["streak_zero"])
                        active_mode = info["active_mode"]
                        reset_duration = int(info.get("reset_duration", 0))
                        adaptive_history.append({
                            "date": str(target_date),
                            "pool_hits": int(info["pool_hits"]),
                            "event": event,
                        })
                        # Limităm istoricul (backtest poate fi lung)
                        adaptive_history = adaptive_history[-50:]
                        # Setăm starea pentru hard inversion la următoarea iterație
                        prev_event_for_inversion = event
                        prev_pool_for_inversion = list(engine.hard_core)
                    else:
                        # Fallback legacy (status quo dinaintea adaptive_feedback)
                        actual_set = set(actual_draw)
                        predicted_set = set(engine.hard_core)
                        for m in actual_set - predicted_set:
                            feedback_map[m] = feedback_map.get(m, 1.0) + 0.1
                        for fp in predicted_set - actual_set:
                            feedback_map[fp] = feedback_map.get(fp, 1.0) - 0.05
                        for k in list(feedback_map.keys()):
                            feedback_map[k] = max(0.5, min(2.0, feedback_map[k]))
                            feedback_map[k] = 1.0 + (feedback_map[k] - 1.0) * 0.8

            except Exception as e:
                logger.error(f"[BACKTEST RETROACTIV] Eroare la simulare {sim_num}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                continue
        
        # Statistici finale
        if retro_predictions:
            total_hits = sum(p.hits for p in retro_predictions)
            avg_hits = total_hits / len(retro_predictions)
            logger.info(f"[BACKTEST RETROACTIV] Complet: {len(retro_predictions)} simulări, medie {avg_hits:.2f} hits")
        
        return retro_predictions


def quick_backtest(data_input: str | pd.DataFrame, variants: List[List[int]], game_type: str = "6/49", percentile: float = 20.0) -> BacktestSummary:
    """
    Funcție conveniență pentru backtest rapid.
    
    Args:
        data_input: Calea către CSV sau DataFrame
        variants: Lista de variante de evaluat
        game_type: Tipul jocului
        percentile: Procentul din extrageri pentru evaluare (default 20%)
        
    Returns:
        Sumarul backtest-ului
    """
    backtester = LotoBacktester(data_input, game_type)
    summary = backtester.evaluate_variants(variants, percentile)
    backtester.print_summary(summary)
    return summary


if __name__ == "__main__":
    # Demo/test
    logging.basicConfig(level=logging.INFO)
    
    # Exemplu de utilizare
    test_variants = [
        [5, 12, 23, 34, 41, 48],
        [3, 15, 27, 38, 44, 49],
        [1, 10, 20, 30, 40, 45],
    ]
    
    # Căutăm un fișier CSV în directorul curent
    import glob
    csv_files = glob.glob("*.csv")
    
    if csv_files:
        print(f"\nTestare cu fișierul: {csv_files[0]}")
        try:
            quick_backtest(csv_files[0], test_variants, "6/49", 20.0)
        except Exception as e:
            print(f"Eroare la testare: {e}")
    else:
        print("Nu s-a găsit niciun fișier CSV pentru testare")
