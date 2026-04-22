"""
Modul de Backtesting pentru LOTO

Compară variantele generate cu rezultatele extragerilor anterioare din CSV
pentru a calcula câte numere ar fi fost ghicite în medie.

Backtesting Retroactiv: Generează previziuni pentru fiecare punct istoric
folosind doar datele disponibile până la acel moment (walk-forward simulation).
"""

from __future__ import annotations

import logging
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
        """Parametrii pentru fiecare tip de joc."""
        params = {
            "6/49": {"max_n": 49, "draw_n": 6, "num_cols": ["n1", "n2", "n3", "n4", "n5", "n6"]},
            "5/40": {"max_n": 40, "draw_n": 5, "num_cols": ["n1", "n2", "n3", "n4", "n5"]},
            "joker": {"max_n": 45, "draw_n": 6, "num_cols": ["n1", "n2", "n3", "n4", "n5", "joker"]},
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
        
        variant_set = set(variant)
        results = []
        
        for idx, date, draw_nums in target_draws:
            # Calculăm hits ținând cont de posibile duplicate la Joker
            # (dacă un număr din variantă apare în draw_nums)
            hit_numbers = []
            temp_draw = list(draw_nums)
            for n in variant:
                if n in temp_draw:
                    hit_numbers.append(n)
                    temp_draw.remove(n) # Eliminăm pentru a nu număra de două ori dacă varianta are duplicate
            
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
        
        # Evaluăm fiecare variantă
        all_hits = []
        all_results = []
        
        for variant in variants:
            results = self.evaluate_variant(variant, target_draws)
            for r in results:
                all_hits.append(r.hits)
                all_results.append(r)
        
        # Calculăm statisticile
        hits_array = np.array(all_hits)
        
        distribution = {}
        for h in range(self.params["draw_n"] + 1):
            count = int(np.sum(hits_array == h))
            if count > 0:
                distribution[h] = count
        
        # Sortăm după hits descrescător pentru top performing
        sorted_results = sorted(all_results, key=lambda x: x.hits, reverse=True)
        top_performing = sorted_results[:10]  # Top 10
        
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
        print(f"\nPerioada evaluată: Ultimele {summary.total_draws_evaluated} extrageri")
        print(f"Variante testate: {summary.variants_tested}")
        print(f"\n--- STATISTICI GENERALE ---")
        print(f"Medie numere ghicite per extragere: {summary.avg_hits_per_draw:.2f}")
        print(f"Rată de succes medie: {summary.avg_hit_rate*100:.1f}%")
        print(f"Mediană numere ghicite: {summary.median_hits:.1f}")
        print(f"Deviație standard: {summary.std_hits:.2f}")
        print(f"Cel mai bun rezultat: {summary.best_draw_hits} numere")
        print(f"Cel mai slab rezultat: {summary.worst_draw_hits} numere")
        
        print(f"\n--- DISTRIBUȚIE NUMERE GHICITE ---")
        for hits in sorted(summary.distribution.keys(), reverse=True):
            count = summary.distribution[hits]
            pct = count / (summary.variants_tested * summary.total_draws_evaluated) * 100
            bar = "█" * int(pct / 2)
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
                                  simulation_step: int = 1) -> List[RetroactivePrediction]:
        """
        Backtesting Retroactiv: Generează previziuni pentru fiecare punct istoric.
        
        Args:
            pool_size: Dimensiunea pool-ului pentru wheeling
            guarantee: Garanția set cover
            lookback_percent: Ce % din istoric să folosească pentru analiza frecvenței la FIECARE pas
            backtest_depth_percent: Procentul din istoric (coada) de testat
            filter_consecutives: Dacă să aplice filtrul anti-secvență
            cold_percent: Procentul de numere reci
            max_variants: Limita de variante
            simulation_step: Din câte în câte extrageri să facă simulare (1 = toate)
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
        
        # Iterăm prin fiecare punct de simulare
        for sim_idx in range(start_idx, n_draws, simulation_step):
            sim_num = sim_idx - start_idx + 1
            
            # Data extragerii vizate (următoarea după punctul de simulare)
            target_date = self.dates[sim_idx] if sim_idx < len(self.dates) else f"Draw_{sim_idx}"
            
            # Data la care facem simularea (extragerea curentă)
            sim_date = self.dates[sim_idx - 1] if sim_idx > 0 else "Start"
            
            logger.info(f"[BACKTEST RETROACTIV] Simulare {sim_num}/{n_simulate} pentru {target_date}")
            
            try:
                # Creăm un subset al datelor până la acest moment (walk-forward)
                # Folosim toate extragerile de la început până la sim_idx
                # Slicing DataFrame-ul original este mai eficient
                historical_df = self.df.iloc[:sim_idx].copy()
                
                if len(historical_df) < 5:
                    logger.warning(f"[BACKTEST] Prea puține date istorice la simulare {sim_num}")
                    continue
                
                # Inițializăm motorul
                engine = LotoEngine(self.game_type)
                engine.data = historical_df
                engine._build_draw_matrix()
                
                # Rulăm pipeline-ul instituțional (fără progress callback pentru viteză în loop)
                lines, _, _, _, _, _ = engine.run_institutional_pipeline(
                    progress_cb=None,
                    pool_size=pool_size,
                    guarantee=guarantee,
                    max_variants=max_variants,
                    lookback=lookback_percent,
                    filter_consecutives=filter_consecutives
                )
                
                # Evaluăm rezultatul contra extragerii reale
                actual_draw = self.draws[sim_idx]
                
                # Calculăm hits pentru fiecare variantă și luăm maximul (sau union?)
                # Userul vrea "ce hits ar fi fost". De obicei se raportează maximul per extragere dacă joci toate variantele.
                # Sau hits pe întreaga mulțime de numere prezise (pool). 
                # Să calculăm hits pentru cea mai bună variantă din setul generat.
                
                max_hits = 0
                for v in lines:
                    # actual_draw este listă acum (pentru a suporta duplicate Joker)
                    h = len(set(v) & set(actual_draw))
                    if h > max_hits:
                        max_hits = h
                
                #predicted_numbers = set()
                #for v in lines:
                #    predicted_numbers.update(v)
                #hits_pool = len(predicted_numbers & actual_draw)
                
                # Creăm înregistrarea retroactivă
                retro_pred = RetroactivePrediction(
                    simulation_date=str(sim_date),
                    target_draw_date=str(target_date),
                    variants=lines,
                    predicted_numbers=set(engine.hard_core),
                    actual_numbers=actual_draw,
                    hits=max_hits, # Folosim max hits din variante pentru realism
                    pool_size=pool_size,
                    guarantee=guarantee,
                    game_type=self.game_type
                )
                
                retro_predictions.append(retro_pred)
                
                logger.info(f"[BACKTEST RETROACTIV] Rezultat: {max_hits} numere ghicite (Max dintr-o variantă)")
                
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
