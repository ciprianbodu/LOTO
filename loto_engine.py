"""
Loto Engine - Motor principal de analiză și predicție (vectorizat unde e posibil).
"""

from __future__ import annotations

import hashlib
import json
import warnings
import functools
from datetime import datetime

import itertools
import numpy as np
import pandas as pd
from numba import jit
import time
import logging
import os
import sys

# Scoring = câștigătorul benchmark (metode CPU) → fallback frecvență.
# Tot suportul GPU/neural (TimesFM/torch/foundation) a fost eliminat din aplicație.
# Selecția pool-ului (top-N pur după scor, aliniat bench / țintă 3+) e logică pură CPU.
from loto_enterprise.core.pool_selection import select_pool_from_scores
# Tie-break CANONIC „top-N după scor" (regula de aur 8 din CLAUDE.md): orice
# selecție top-N din engine trece prin el, ca pool-ul GENERAT să folosească exact
# regula cu care bench-ul îl VALIDEAZĂ (`runner._top_k`).
from loto_enterprise.core.ranking import rank_by_score

# Adaptive feedback (învățare persistentă post-extragere)
try:
    from loto_enterprise.core.adaptive_feedback import (
        load_adaptive_state,
        save_adaptive_state,
        compute_post_draw_feedback,
        record_predicted_pool,
        get_state_summary,
    )
    _HAS_ADAPTIVE = True
except ImportError:
    _HAS_ADAPTIVE = False

# Configurăm logging cu timestamp pentru debug detaliat
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
VERSION = "1.1.2"
warnings.filterwarnings("ignore")

def generate_combinatorial_wheel(pool, pick=6, guarantee=4, max_variants=0, scores=None):
    """
    Sistem de Wheeling (Set Cover Optimizat Memorie & Viteză)
    Optimizat pentru hit-uri de 4 și 5 numere prin prioritizarea scorurilor NQI/Frecvență.
    """
    start_time = time.time()
    pool_len = len(pool)
    logging.info(f"[WHEEL] Inițializare sistem Wheeling pentru pool de {pool_len} numere. Pick={pick}, Guarantee={guarantee}.")

    if pool_len < pick:
        return [list(pool)], 100.0

    # Sortăm pool-ul după scoruri pentru a favoriza numerele puternice
    if scores:
        pool = sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    else:
        pool = sorted(list(pool))

    # Generăm toate combinațiile de garanție ca ținte, dar le sortăm numeric
    # pentru a fi consistente cu rezultatul numeric al wheeling-ului.
    # Folosim o listă pentru ordinea greedy și un set pentru lookup rapid.
    all_targets_list = [tuple(sorted(t)) for t in itertools.combinations(pool, guarantee)]

    # Dacă avem scoruri, sortăm țintele pentru a le acoperi întâi pe cele mai probabile
    if scores:
        all_targets_list.sort(key=lambda t: sum(scores.get(n, 0) for n in t), reverse=True)
        
    total_targets = len(all_targets_list)
    covered_targets = set()
    wheel = []
    
    iteration = 0
    max_search_per_iter = 10000 if pool_len <= 15 else 50000

    # P3: cache ticket→target-set. Același ticket reapare la iterații diferite
    # (base_ticket overlap) → evităm recalcul itertools.combinations. Bit-identic.
    _tt_cache: dict = {}

    while len(covered_targets) < total_targets:
        if max_variants > 0 and len(wheel) >= max_variants:
            logging.info(f"[WHEEL] S-a atins limita maxima cerută de variante: {max_variants}.")
            break
            
        iteration += 1
        best_ticket = None
        best_coverage = -1
        best_targets_covered = set()
        
        # Găsim prima țintă neacoperită (cea mai valoroasă datorită sortării)
        target_to_cover = None
        for t in all_targets_list:
            if t not in covered_targets:
                target_to_cover = t
                break

        if not target_to_cover:
            break
        base_ticket = set(target_to_cover)
        # remaining_pool păstrează ordinea sortată după scor
        remaining_pool = [n for n in pool if n not in base_ticket]
        
        search_count = 0
        if len(remaining_pool) >= (pick - guarantee):
            # itertools.combinations pe un pool sortat va genera combinații
            # începând cu cele mai bune numere (scor maxim).
            for extra_nums in itertools.combinations(remaining_pool, pick - guarantee):
                ticket = tuple(sorted(list(base_ticket) + list(extra_nums)))

                # Evaluăm rapid (cu cache pe ticket — P3)
                ticket_targets = _tt_cache.get(ticket)
                if ticket_targets is None:
                    ticket_targets = set(itertools.combinations(ticket, guarantee))
                    _tt_cache[ticket] = ticket_targets
                new_coverage = ticket_targets - covered_targets
                
                if len(new_coverage) > best_coverage:
                    best_coverage = len(new_coverage)
                    best_ticket = ticket
                    best_targets_covered = ticket_targets
                
                search_count += 1
                if search_count > max_search_per_iter:
                    break
        
        if best_ticket:
            wheel.append(list(best_ticket))
            covered_targets.update(best_targets_covered)
            if iteration % 20 == 0 or len(covered_targets) == total_targets:
                logging.info(f"[WHEEL] Progres {iteration}: Acoperite {len(covered_targets)}/{total_targets} ținte. Bilete: {len(wheel)}")
        else:
            logging.warning("[WHEEL] Nu am găsit acoperire suplimentară, oprire timpurie.")
            break
            
        if iteration > 1000:  # Timeout extins pt pool-uri mari, dar mult mai rapid
            logging.warning(f"[WHEEL] TIMEOUT: 1000 iterații.")
            break
            
    coverage_pct = 100.0 if total_targets == 0 else round((len(covered_targets) / total_targets) * 100, 2)
    logging.info(f"[WHEEL] Generare completă în {time.time() - start_time:.2f}s. Total variante: {len(wheel)}. Acoperire: {coverage_pct}%")
    return wheel, coverage_pct


def hypergeometric_hit_forecast(pool_size: int, draw_n: int, max_n: int, n_draws: int = 127) -> dict:
    """
    Calculează probabilitatea teoretică P(k+ hits) pentru un pool RANDOM și
    recomandă pool-ul minim necesar pentru a vedea ≥3 evenimente pe ținta
    principală (3+) și pe 4+/5+.

    Baseline matematic — orice engine trebuie să bată cel puțin P(3+) random
    ca să fie util. Pe 4+ / 5+ la pool mic evenimentele așteptate sunt rare.

    Returns dict cu:
        - random_baseline.P(k+)% per k=1..draw_n
        - recommendations.pool_for_3_events_{3,4,5}+
    """
    try:
        from math import comb
    except ImportError:
        return {}
    if pool_size > max_n or draw_n > max_n or draw_n <= 0:
        return {}
    total_combos = comb(max_n, draw_n)
    if total_combos == 0:
        return {}

    def p_exactly_k(k, pool):
        if k < 0 or k > draw_n or k > pool:
            return 0.0
        return comb(pool, k) * comb(max_n - pool, draw_n - k) / total_combos

    forecast: dict = {"pool_size": pool_size, "n_draws": n_draws, "random_baseline": {}}
    for k in range(1, draw_n + 1):
        p_geq_k = sum(p_exactly_k(j, pool_size) for j in range(k, draw_n + 1))
        forecast["random_baseline"][f"P({k}+)%"] = round(p_geq_k * 100, 4)
        forecast["random_baseline"][f"E({k}+)/n"] = round(p_geq_k * n_draws, 2)

    target_events = 3
    recommendations: dict = {}
    for target_k in (3, 4, 5):
        if target_k > draw_n:
            continue
        rec_pool = None
        for trial_pool in range(pool_size, min(max_n, pool_size + 20) + 1):
            p_k = sum(
                comb(trial_pool, j) * comb(max_n - trial_pool, draw_n - j) / total_combos
                for j in range(target_k, draw_n + 1)
            )
            if p_k * n_draws >= target_events:
                rec_pool = trial_pool
                break
        recommendations[f"pool_for_3_events_{target_k}+"] = rec_pool
    forecast["recommendations"] = recommendations
    return forecast


class LotoEngine:
    # Routes _get_timesfm_scores through loto_enterprise.core.method_selector
    # — uses the model that WON the benchmark for this game/pool. Fallback:
    # frecvență (recency-weighted). Set env LOTO_USE_BENCH_WINNER=0 pentru a
    # forța fallback-ul pe frecvență (A/B).
    use_bench_winner: bool = bool(int(os.environ.get("LOTO_USE_BENCH_WINNER", "1")))

    def __init__(self, game_type: str = "6/49"):
        self.game_type = game_type
        self.params = self._get_game_params(game_type)
        self.audit: dict = {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "compute_device": "cpu",  # GPU eliminat complet — scoring exclusiv CPU
        }
        self.hard_core: list = []
        self.hard_core_stats: dict = {}
        self.hard_core_joker_stats: dict = {}
        self.data: pd.DataFrame | None = None
        self.arena2_index = None
        self._draw_matrix: np.ndarray | None = None
        self.error_correction_map: dict[int, float] = {}  # num -> bias_multiplier
        # Pool size hint for method_selector (the wheeling pool, typically 12)
        self._winner_pool_hint: int = 12

    def _get_game_params(self, game_type: str):
        params = {
            "6/49": {
                "max_n": 49,
                "draw_n": 6,
                "play_n": 6,
                "scheme": "2-2-2",
                "lookback": 20,
            },
            "5/40": {
                "max_n": 40,
                "draw_n": 5,
                "play_n": 5,
                "scheme": "2-1-2",
                "lookback": 25,
            },
            "joker": {
                "max_n": 45,
                "draw_n": 5,
                "play_n": 5,
                "scheme": "2-2-1",
                "lookback": 15,
                "max_joker": 20
            },
        }
        return params.get(game_type, params["6/49"])

    def load_data(self, csv_path: str) -> bool:
        """Încarcă date din CSV."""
        try:
            self.data = pd.read_csv(csv_path)
            self._build_draw_matrix()
            self.audit["rows_loaded"] = len(self.data)
            self.audit["game_detected"] = self.game_type
            # Bug 1.6 Fix: hashlib.sha256 determinist în loc de hash() care variază între rulări
            self.audit["hash"] = hashlib.sha256(self.data.values.tobytes()).hexdigest()[:16]
            return True
        except Exception as e:
            print(f"Eroare la încărcare date: {e}")
            return False

    def _extract_draw_at_index(self, idx: int) -> list[int] | None:
        """Returnează numerele extrase la index-ul `idx` (0-based) din self.data
        sau None dacă nu există / nu sunt valide.
        """
        if self.data is None or idx < 0 or idx >= len(self.data):
            return None
        if self._draw_matrix is not None and idx < len(self._draw_matrix):
            row = self._draw_matrix[idx]
            nums = [int(v) for v in row if int(v) > 0]
            if nums:
                return nums
        # Fallback prin coloane n*
        try:
            row = self.data.iloc[idx]
            n_cols = sorted(
                [c for c in self.data.columns
                 if str(c).lower().startswith("n") and str(c).lower() != "numbers"],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I), nu toate 6
            nums = []
            for c in n_cols:
                if c in row and pd.notna(row[c]):
                    try:
                        nums.append(int(row[c]))
                    except (ValueError, TypeError):
                        continue
            return nums if nums else None
        except Exception:
            return None

    def _extract_date_at_index(self, idx: int) -> str | None:
        """Returnează data extragerii la index-ul `idx` ca string sau None."""
        if self.data is None or idx < 0 or idx >= len(self.data):
            return None
        try:
            row = self.data.iloc[idx]
            for col in ("date", "data", "draw_date", "Data"):
                if col in row and pd.notna(row[col]):
                    return str(row[col])
        except Exception:
            pass
        return None

    def _build_draw_matrix(self) -> None:
        """Construiește o dată matrice (rows x draw_n) de numere întregi — fără split pe string în buclă."""
        if self.data is None:
            self._draw_matrix = None
            return
        df = self.data
        if "numbers" in df.columns:
            self._draw_matrix = None
            return
        n_cols = [c for c in df.columns if str(c).lower().startswith("n")]
        n_cols = sorted(n_cols, key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))[
            : int(self.params["draw_n"])
        ]
        if not n_cols:
            self._draw_matrix = None
            return
        raw = df[n_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
        self._draw_matrix = np.nan_to_num(raw, nan=0.0).astype(np.int32)

    def analyze_frequency(self) -> np.ndarray:
        """Analiză frecvență numerelor (vectorizat pe matrice sau coloana numbers)."""
        logging.info(f"[ENGINE] Analiză frecvență (Versiune {VERSION})...")
        if self.data is None:
            return np.array([], dtype=np.int64)

        max_n = int(self.params["max_n"])

        if self._draw_matrix is not None and self._draw_matrix.size:
            vals = self._draw_matrix.ravel()
            vals = vals[(vals >= 1) & (vals <= max_n)]
            if vals.size == 0:
                return np.zeros(max_n, dtype=np.int64)
            freq = np.bincount(vals.astype(np.int64), minlength=max_n + 1)
            return freq[1 : max_n + 1]

        # Fallback (dacă lipsește _draw_matrix)
        all_numbers = []
        if self.data is not None:
            n_cols = sorted(
                [c for c in self.data.columns if str(c).lower().startswith('n')],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I)
            if n_cols:
                raw_vals = self.data[n_cols].values.ravel()
                all_numbers = raw_vals[~np.isnan(raw_vals)].astype(int).tolist()
            elif "numbers" in self.data.columns:
                for _, row in self.data.iterrows():
                    try:
                        nums = [int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()]
                        all_numbers.extend(nums)
                    except (ValueError, TypeError) as exc:
                        logging.debug("analyze_frequency: skip row (parse): %s", exc)
                        continue
        
        if not all_numbers:
            return np.zeros(max_n, dtype=np.int64)
            
        arr = np.asarray(all_numbers, dtype=np.int64)
        arr = arr[(arr >= 1) & (arr <= max_n)]
        freq = np.bincount(arr, minlength=max_n + 1)
        return freq[1 : max_n + 1]

    def analyze_joker_frequency(self) -> np.ndarray:
        """Analiză frecvență pentru Urna 2 la Joker (1-20)."""
        if self.data is None or "joker" not in self.data.columns:
            return np.zeros(20, dtype=np.int64)
            
        joker_vals = pd.to_numeric(self.data["joker"], errors="coerce").dropna().astype(np.int64)
        joker_vals = joker_vals[(joker_vals >= 1) & (joker_vals <= 20)]
        if joker_vals.empty:
            return np.zeros(20, dtype=np.int64)
        freq = np.bincount(joker_vals, minlength=21)
        return freq[1:21]

    def generate_predictions(self, guarantee=4, max_variants=0, scores=None):
        """Generează predicții bazate pe analiză."""
        if not hasattr(self, 'hard_core') or not self.hard_core:
            return [], 0.0

        # Wheeling: implicit greedy (bit-identic). Alternative selectabile prin env
        # LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla|union34
        # (necunoscut → greedy). Lista completă: wheeling_methods.WHEEL_METHODS.
        _wheel_method_env = os.environ.get("LOTO_WHEEL_METHOD", "").strip().lower()
        if _wheel_method_env:
            # Override explicit — comportament neschimbat (backward-compat).
            _wheel_method = _wheel_method_env
        elif max_variants == 0:
            # Implicit, fără cap de bilete ("garanție completă"): design de acoperire
            # CUNOSCUT-OPTIM din covering_designs/ (La Jolla) când există pt
            # C(pool, pick, guarantee); altfel cade automat pe ILP, iar ILP pe greedy.
            # Lanțul e monoton: niciodată mai multe bilete decât înainte, aceeași
            # garanție 100%. Măsurat pe pool 12/garanție 4: 6/49 54→41 bilete,
            # 5/40+Joker 123→113 (ILP la 15s nici nu atingea aceste valori).
            _wheel_method = "lajolla"
        else:
            # Buget de bilete fix (max_variants>0): păstrăm greedy (neschimbat).
            _wheel_method = "greedy"
        if _wheel_method and _wheel_method != "greedy":
            from wheeling_methods import generate_wheel
            variants, coverage_pct = generate_wheel(
                _wheel_method,
                pool=self.hard_core,
                pick=self.params["draw_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores,
            )
        else:
            variants, coverage_pct = generate_combinatorial_wheel(
                pool=self.hard_core,
                pick=self.params["draw_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores
            )
        
        # Atașăm Joker din nucleul dur de Joker dacă e cazul
        if self.game_type == "joker" and hasattr(self, 'hard_core_joker') and self.hard_core_joker:
            # Atașăm jokerul favorit pe fiecare variantă de Urna 1. Codul rămâne
            # ciclic (generic pe lungimea listei), dar cu urna2 single-pick
            # (pool 1, aliniat bench) toate variantele primesc ACELAȘI joker.
            jokers = self.hard_core_joker
            joker_pool_size = len(jokers)
            for idx, variant in enumerate(variants):
                assigned_joker = jokers[idx % joker_pool_size]
                variant.append(assigned_joker)  # Elementul 6 este Joker-ul

        return variants, coverage_pct

    def run_institutional_pipeline(self, progress_cb=None, pool_size=12, guarantee=4, max_variants=0, lookback=0, filter_consecutives=False, smart_reduction=False, sim_depth_pct=10, enable_adaptive_persistence=False, pure_bench_mode=False, manual_blacklist=None, track_pool_variation=True):
        """Rulează pipeline-ul complet de analiză.

        enable_adaptive_persistence: Dacă True (live mode), încarcă/salvează
            adaptive_state.json — învățare persistentă din extrageri reale.
            Backtester-ul îl lasă False (își gestionează propriul state in-memory).

        manual_blacklist: Set/listă de numere pe care utilizatorul vrea să le
            excludă din selecție (ex: butonul "Inverseaza Pool" din UI care
            tratează pool-ul anterior ca blacklist). Se combină cu blacklist-ul
            calculat automat de motor, cu safety check anti-saturare.

        track_pool_variation: Dacă True (implicit — producție), compară pool-ul
            cu ultimul pool salvat pentru același joc+dimensiune+trecere și
            rescrie pool_history.json. Pașii de walk-forward/backtest îl lasă
            False: scriu pe aceeași cheie ca producția, deci ar suprascrie
            pool-ul real. NU folosi enable_adaptive_persistence ca poartă —
            producția îl pasează tot False (worker.py).
        """
        # Memoram pool_size-ul cerut pentru ca _scores_via_bench_winner să poată
        # selecta câștigătorul corect din best_methods.json (per pool size).
        self._winner_pool_hint = int(pool_size)
        self.audit["pool_size_requested_by_ui"] = int(pool_size)
        logging.info(
            f"[PIPELINE] ▶ pool_size primit de la UI = {pool_size}, guarantee = {guarantee}"
        )

        # Garanția cerută în UI se respectă ÎNTOTDEAUNA — fără escaladare implicită.
        # (Istoric: pe pool=10 garanția era suprascrisă silențios cu draw_n → full
        # wheel C(10,draw_n), de ~10x mai scump decât coverul minim pentru garanția
        # cerută, iar UI-ul afișa în continuare garanția veche. Cine vrea full wheel
        # setează explicit Garanție = draw_n în UI.)

        # === ADAPTIVE FEEDBACK PRE-RUN: detectăm extrageri reale apărute de la
        # ultima predicție și ajustăm error_correction_map ÎNAINTE de TimesFM. ===
        adaptive_event = None
        adaptive_info = None
        if enable_adaptive_persistence and _HAS_ADAPTIVE and self.data is not None:
            try:
                state = load_adaptive_state(self.game_type, pool_size)
                last_rows = int(state.get("last_data_rows", 0))
                current_rows = int(len(self.data))
                last_pool = state.get("last_pool", [])

                if last_pool and current_rows > last_rows:
                    new_actual = self._extract_draw_at_index(last_rows)
                    if new_actual:
                        rs = state.get("regime_state", {})
                        new_map, adaptive_event, adaptive_info = compute_post_draw_feedback(
                            last_pool=last_pool,
                            actual_draw=new_actual,
                            current_map=state.get("error_correction_map", {}),
                            history=state.get("history", []),
                            game_type=self.game_type,
                            pool_size=pool_size,
                            streak_zero=int(rs.get("streak_zero", 0)),
                            prev_mode=rs.get("active_mode", "normal"),
                            reset_duration=int(rs.get("reset_duration", 0)),
                        )
                        self.error_correction_map = new_map
                        # Persistăm istoricul + map-ul actualizat
                        history = list(state.get("history", []))
                        history.append({
                            "date": self._extract_date_at_index(last_rows),
                            "pool_hits": int(adaptive_info["pool_hits"]),
                            "actual": [int(n) for n in new_actual],
                            "event": adaptive_event,
                        })
                        state["error_correction_map"] = new_map
                        state["history"] = history[-50:]
                        state["regime_state"] = {
                            "streak_zero": int(adaptive_info["streak_zero"]),
                            "rolling_avg": (float(adaptive_info["rolling_avg"])
                                            if adaptive_info.get("rolling_avg") is not None else None),
                            "last_reset": (self._extract_date_at_index(last_rows)
                                           if adaptive_info["active_mode"] == "reset"
                                           else state.get("regime_state", {}).get("last_reset")),
                            "active_mode": adaptive_info["active_mode"],
                            "reset_duration": int(adaptive_info.get("reset_duration", 0)),
                        }
                        save_adaptive_state(self.game_type, pool_size, state)
                        logging.info(
                            f"[ADAPTIVE] Eveniment={adaptive_event} | hits={adaptive_info['pool_hits']} | "
                            f"streak_zero={adaptive_info['streak_zero']} | mode={adaptive_info['active_mode']} | "
                            f"missed={adaptive_info['missed']} | fp={adaptive_info['false_positives']}"
                        )
                # Stocăm evenimentul pentru consum în pipeline (diversificare/regime mode)
                self._adaptive_event = adaptive_event
                self._adaptive_info = adaptive_info
                self._adaptive_mode = (
                    adaptive_info["active_mode"] if adaptive_info else
                    state.get("regime_state", {}).get("active_mode", "normal")
                )
                # Hard Inversion Temporară: dacă tocmai am avut catastrofă,
                # excludem pool-ul ratat la următoarea selecție (1 extragere).
                try:
                    from loto_enterprise.core.adaptive_feedback import compute_temp_blacklist as _compute_temp_bl
                    evaluated = (adaptive_info or {}).get("evaluated_pool") if adaptive_info else None
                    if evaluated is None:
                        evaluated = state.get("last_pool", [])
                    self._temp_blacklist = _compute_temp_bl(
                        last_pool=list(evaluated or []),
                        last_event=adaptive_event,
                        universe_size=int(self.params.get("max_n", 49)),
                        pool_size=int(pool_size),
                        enable_full_inversion=True,
                    )
                    if self._temp_blacklist:
                        logging.warning(
                            f"[HARD-INVERSION] Catastrofă detectată — excludem temporar "
                            f"{sorted(self._temp_blacklist)} din pool-ul următor."
                        )
                except Exception as _e_inv:
                    logging.error(f"[HARD-INVERSION] Eroare la calcul temp_blacklist: {_e_inv}")
                    self._temp_blacklist = set()
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la procesarea feedback-ului: {e}")
                self._adaptive_event = None
                self._adaptive_info = None
                self._adaptive_mode = "normal"
                self._temp_blacklist = set()
        else:
            # NU suprascriem dacă au fost setate extern (e.g. de backtester
            # care îi pasează regime_mode în mod manual).
            if not hasattr(self, "_adaptive_event"):
                self._adaptive_event = None
            if not hasattr(self, "_adaptive_info"):
                self._adaptive_info = None
            if not hasattr(self, "_adaptive_mode"):
                self._adaptive_mode = "normal"
            if not hasattr(self, "_temp_blacklist"):
                self._temp_blacklist = set()

        logging.info(f"[PIPELINE] Inițializare scoring (câștigător bench CPU) [pool_size={pool_size}, guarantee={guarantee}, max_variants={max_variants}, lookback={lookback}%, smart_reduction={smart_reduction}]...")
        
        if lookback > 0 and self.data is not None and not self.data.empty:
            effective_rows = int(len(self.data) * (lookback / 100.0))
            effective_rows = max(effective_rows, 1)
            actual_lookback = effective_rows
            if effective_rows == 0:
                actual_lookback = 1 # Măcar o extragere
            logging.info(f"[PIPELINE] Aplic limită de istoric: Ultimele {lookback}% ({actual_lookback} extrageri).")
            self.data = self.data.tail(effective_rows).copy()
            self._build_draw_matrix()
            
        if progress_cb:
            progress_cb("Inițializare motor...", 10)

        logging.info("[PIPELINE] Începe analiza frecvenței...")
        freq = self.analyze_frequency()

        if progress_cb:
            progress_cb("Analiza frecvenței...", 30)

        # 4. Calcul scoruri per număr (câștigătorul bench CPU / ensemble)
        tfm_scores = {}
        total_draws = len(self.data) if self.data is not None else 0
        actual_lookback = total_draws
        self.audit["effective_rows_used"] = total_draws
        self.audit["lookback_pct"] = lookback

        # Etichetă progres = metoda REALĂ de scoring (câștigătorul bench dacă
        # use_bench_winner e ON), altfel „frecvență".
        _score_lbl = "frecvență"
        if self.use_bench_winner:
            try:
                from loto_enterprise.core.method_selector import get_winner_name
                _gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
                       "joker": "joker_urna1"}.get(self.game_type, "loto_6_49")
                _wn = get_winner_name(_gk, pool_size=int(getattr(self, "_winner_pool_hint", 16)))
                if _wn:
                    _score_lbl = _wn
            except Exception:  # noqa: BLE001
                pass
        if progress_cb:
            progress_cb(f"Scoring: {_score_lbl} (ctx={actual_lookback})...", 15)

        self._tfm_window_cb = None

        start_score = time.perf_counter()
        try:
            tfm_scores = self._get_timesfm_scores(context_len=actual_lookback)
        finally:
            self._tfm_window_cb = None  # Cleanup ca să nu polueze apeluri viitoare
        score_time = (time.perf_counter() - start_score) * 1000

        if progress_cb:
            progress_cb(f"{_score_lbl} complet ({score_time/1000:.1f}s). Construiesc pool...", 45)

        if 'performance' not in self.audit:
            self.audit['performance'] = {}
        self.audit['performance']['score_time_ms'] = round(score_time, 2)
        logging.info(f"[PIPELINE] Scoring ({_score_lbl}) timp: {score_time:.2f}ms")

        # Feedback adaptiv pe scoruri — DEZACTIVAT (cerere user: fără filtre).
        # Pool-ul = scor pur al metodei câștigătoare / ensemble.
        blacklist = set()
        self.audit["sim_depth_pct"] = sim_depth_pct
        self.audit['reduction_filter'] = {
            'combined_blacklist': [],
            'total_blocked': 0,
            'model_used': 'DISABLED_ALL_FILTERS',
            'sim_depth_pct': sim_depth_pct,
            'disabled_by_user': True,
        }
        logging.info("[PIPELINE] Filtre dezactivate — pool = top-scor pur; singura excepție: auto-invert (manual_blacklist).")

        # === Manual Inversion (utilizator: butonul "Inverseaza Pool") ===
        # Userul a apăsat butonul de inversare după ce pool-ul anterior n-a ieșit
        # bine în extragerile reale. Tratăm pool-ul anterior ca blacklist explicit.
        # Stocam pe instanta ca atribut pentru a fi RESPECTAT STRICT pana la final
        # (fallback-ul de completare a pool-ului poate altfel sa-l incalce).
        self._manual_blacklist_set: set[int] = set()
        if manual_blacklist:
            try:
                manual_set = set(int(n) for n in manual_blacklist if int(n) > 0)
            except (TypeError, ValueError):
                manual_set = set()
                logging.warning("[MANUAL-INVERSION] manual_blacklist invalid, ignor.")
            if manual_set:
                max_n_safe = int(self.params.get("max_n", 49))
                combined = blacklist | manual_set
                if len(combined) >= max_n_safe - pool_size:
                    logging.warning(
                        f"[MANUAL-INVERSION] Manual ({len(manual_set)}) + auto ({len(blacklist)}) "
                        f"= {len(combined)} blocheaza prea mult din univers ({max_n_safe}). "
                        f"Pool_size cerut: {pool_size}. Skip pentru a evita pool gol."
                    )
                    # Marcam in audit ca inversarea NU s-a aplicat (pool prea mare pt joc)
                    # -> UI poate avertiza in loc sa arate Pool 2 identic cu Pool 1.
                    self.audit["manual_inversion"] = {
                        "skipped": True,
                        "reason": "pool prea mare pentru inversare",
                        "n_requested_exclude": len(manual_set),
                        "n_auto_blacklist": len(blacklist),
                        "max_num": max_n_safe,
                        "pool_size": pool_size,
                        "pool_max_pentru_inversare": max(1, (max_n_safe - len(blacklist)) // 2),
                    }
                else:
                    blacklist = combined
                    self._manual_blacklist_set = set(manual_set)  # STRICT
                    self.audit["manual_inversion"] = {
                        "trigger": "user_invert_button",
                        "excluded": sorted(manual_set),
                        "n_excluded": len(manual_set),
                        "scope": "single_run",
                    }
                    logging.info(
                        f"[MANUAL-INVERSION] User a cerut excluderea a {len(manual_set)} numere "
                        f"(pool anterior). Blacklist total: {len(blacklist)} din {max_n_safe}."
                    )

        self.hard_core = self._get_timesfm_pool(tfm_scores, pool_size=pool_size, blacklist=blacklist)

        # Biletul OMNIUS a fost eliminat din UI și din walk-forward → snapshot-ul
        # de scoruri (`_last_pool_scores`) și audit['omnius_pool_scores'] nu mai au
        # niciun consumator. Scoase: erau doar date moarte în payload și în raport.
        # (Metoda de scoring "omnius" a fost la rândul ei eliminată în 2026-08-09.)

        # Transparența pipeline-ului: snapshot la fiecare etapă (pentru afișare în UI).
        # Cronologia e: NQI_raw → Smart → Anti-Seq → POST-HOC (final).
        self.audit['pipeline_stages'] = {
            "1_nqi_raw": sorted(self.hard_core.copy()),
        }

        # Flow minimal (cerere user 2026-07-08): scoring → pool top-N → wheel.
        # Fără POST-HOC, anti-secvență, anomaly filter sau alte rafinări.
        # Singura „inversare": manual_blacklist (auto-invert Pool 2).
        self.audit["pure_bench_mode"] = True
        self.audit["filters_disabled"] = True
        if len(self.hard_core) > pool_size:
            logging.warning(f"[PIPELINE] Nucleul dur avea {len(self.hard_core)} numere. Trunchiere la {pool_size} după scor.")
            # Trunchiere prin regula canonică (nu sortare proprie): la scoruri egale
            # decide numărul mare, exact ca bench-ul. Ramură defensivă — selectorul
            # întoarce deja cel mult pool_size numere.
            ranked = rank_by_score({int(n): float(tfm_scores.get(n, 0.0)) for n in self.hard_core}, pool_size)
            self.hard_core = sorted(ranked)
        elif len(self.hard_core) < pool_size:
            logging.warning(f"[PIPELINE] Nucleul dur avea doar {len(self.hard_core)} numere. Pool_size solicitat: {pool_size}.")
        logging.info(f"[PIPELINE] Nucleu (Pool) generat prin {_score_lbl}: {self.hard_core}")
        self._consecutive_filter_applied = False
        self.audit['pipeline_stages']["2_smart_selector"] = sorted(self.hard_core.copy())
        self.audit['pipeline_stages']["3_anti_sequence"] = sorted(self.hard_core.copy())
        self.audit['pipeline_stages']["4_post_hoc_final"] = sorted(self.hard_core.copy())
        
        if self.game_type == "joker":
            logging.info(f"[PIPELINE] Scoring Urna 2 (Joker — câștigător bench / TimesFM)...")
            j_scores = self._get_timesfm_scores(is_joker_drum=True, context_len=actual_lookback)
            # joker_urna2 e single-pick (pool 1) în TOT lanțul bench→decizie→UI
            # (_pool_hint=1, decision.py pool_range=[draw_n]=[1]) — păstrăm UN
            # singur număr (cel mai bun după scor), nu top-2 hardcodat cum era.
            # Candidații alternativi rămân disponibili în audit['joker_predictions'].
            if j_scores:
                # Tie-break CANONIC (rank_by_score), NU sortare proprie: bench-ul
                # validează joker_urna2 prin `runner._top_k` → aceeași regulă, altfel
                # la scoruri egale engine-ul ar alege alt număr decât cel validat.
                ranked_j = rank_by_score(j_scores, 5)
                self.hard_core_joker = [int(ranked_j[0])]
                logging.info(f"[PIPELINE] Nucleu Joker (Urna 2): {self.hard_core_joker}")
                self.audit['joker_predictions'] = {int(n): round(float(j_scores[n]), 4) for n in ranked_j}
            else:
                freq_joker = self.analyze_joker_frequency()
                self.hard_core_joker = self._get_hard_core_joker(freq_joker, pool_size=1)
                # Candidați informativi (top-5 după frecvență) și pe fallback,
                # ca UI-ul să aibă aceeași sursă indiferent de path-ul de scoring.
                self.audit['joker_predictions'] = {
                    int(i) + 1: int(freq_joker[i]) for i in np.argsort(freq_joker)[-5:][::-1]
                }
                logging.info(f"[PIPELINE] Nucleu Joker (Fallback Frecvență): {self.hard_core_joker}")
            # Auto-invert (Pool 2) NU inversează Urna 2: univers mic (1-20), iar
            # scoring-ul urna2 ignoră manual_blacklist → pass 2 recalculează pe
            # aceleași date și obține ACELAȘI joker la ambele pool-uri. Cheie de
            # audit explicită ca UI-ul să poată avertiza, nu să afirme implicit
            # că Pool 2 conține numere excluse din Pool 1.
            self.audit['joker_urna2_inverted'] = False

        if progress_cb:
            progress_cb("Generare predicții finale (Wheeling)...", 70)

        # === STRICT MANUAL BLACKLIST ENFORCEMENT ===
        # Filtrul anti-secventa si fallback-ul de completare a pool-ului pot
        # reintroduce numere din manual_blacklist. Aplicam un filtru HARD aici,
        # imediat inainte de wheeling, ca sa garantam ca pool-ul final NU
        # contine niciun numar exclus de user.
        if getattr(self, "_manual_blacklist_set", None):
            mb = set(self._manual_blacklist_set)
            violated = [n for n in self.hard_core if n in mb]
            if violated:
                logging.warning(
                    f"[MANUAL-BLACKLIST] {len(violated)} numere din pool-ul exclus au fost "
                    f"reintroduse de pipeline ({sorted(violated)}). Le elimin si completez."
                )
                # Elimin violarile
                self.hard_core = [n for n in self.hard_core if n not in mb]
                # Completez cu top-scoring numere care NU sunt in manual_blacklist.
                # Selecția trece prin rank_by_score (regula canonică): filtrarea
                # (blacklist manual + numerele deja în pool) rămâne la apelant,
                # conform contractului modulului.
                if tfm_scores:
                    clean_scores = {
                        int(n): float(s) for n, s in tfm_scores.items()
                        if n not in mb and n not in self.hard_core
                    }
                else:
                    clean_scores = {
                        i + 1: float(f) for i, f in enumerate(freq)
                        if (i + 1) not in mb and (i + 1) not in self.hard_core
                    }
                needed = pool_size - len(self.hard_core)
                for n in rank_by_score(clean_scores, needed):
                    self.hard_core.append(int(n))
                self.hard_core = sorted(self.hard_core)
                self.audit.setdefault("manual_inversion", {})["enforced_violations_fixed"] = {
                    "removed": sorted(violated),
                    "added_replacements": [n for n in self.hard_core if n not in violated][-needed:] if needed > 0 else [],
                }
                logging.info(f"[MANUAL-BLACKLIST] Pool final dupa enforcement: {self.hard_core}")

            else:
                logging.info(f"[MANUAL-BLACKLIST] Pool curat ({len(self.hard_core)} numere, niciun violator).")
            if 'pipeline_stages' in self.audit:
                self.audit['pipeline_stages']["4_post_hoc_final"] = sorted(self.hard_core.copy())

        logging.info("[PIPELINE] Începe generarea predicțiilor (Wheeling Set Cover)...")
        # Folosim tfm_scores dacă sunt disponibile, altfel fallback pe frecvență pentru wheeling
        wheeling_scores = tfm_scores if tfm_scores else {i+1: float(f) for i, f in enumerate(freq)}
        # Contract cu UI: garanția EFECTIV folosită la wheel (identică cu cea cerută
        # în UI — nu mai există nicio escaladare pe drum). UI-ul o afișează ca atare.
        self.audit["wheel_guarantee_used"] = int(guarantee)
        lines, coverage_pct = self.generate_predictions(guarantee=guarantee, max_variants=max_variants, scores=wheeling_scores)
        
        # Nu se aplică NICIUN filtru pe variante după wheeling: orice eliminare ar
        # putea sparge garanția de acoperire. Dacă vreodată se reintroduce unul,
        # foloseşte `wheeling_methods.filter_preserving_coverage` (scoate bilete doar
        # dacă rămân redundante) şi revalidează cu `compute_coverage_pct`.
        logging.info(f"[PIPELINE] S-au generat {len(lines)} variante de joc. Acoperire: {coverage_pct}%")

        if progress_cb:
            progress_cb("Validare rezultate...", 90)

        # Recalculăm statisticile pentru afișare corectă în UI (procente)
        final_freq = self.analyze_frequency()
        self.hard_core_stats = {int(num): int(final_freq[num-1]) for num in self.hard_core if num-1 < len(final_freq)}
        
        if self.game_type == "joker":
            final_j_freq = self.analyze_joker_frequency()
            self.hard_core_joker_stats = {int(num): int(final_j_freq[num-1]) for num in self.hard_core_joker if num-1 < len(final_j_freq)}

        p10, p90 = np.percentile(final_freq, [10, 90]) if final_freq.size else (0.0, 0.0)
        g_range = [p10 * self.params["draw_n"], p90 * self.params["draw_n"]]

        context = {
            "first_3": [],
            "last_3": []
        }
        if self.data is not None and not self.data.empty:
            draw_n = int(self.params.get("draw_n", 6))
            def extract_draws(df_subset):
                draws = []
                for _, row in df_subset.iterrows():
                    d = {"date": str(row.get("date", "")).split()[0] if "date" in row else "N/A", "numbers": [], "joker": None}
                    n_cols = sorted([c for c in df_subset.columns if str(c).lower().startswith("n") and str(c).lower() != "numbers"], key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"))
                    nums = [row[c] for c in n_cols if pd.notna(row.get(c))]
                    d["numbers"] = [int(x) for x in nums][:draw_n]
                    if "joker" in df_subset.columns and pd.notna(row.get("joker")):
                        d["joker"] = int(row["joker"])
                    draws.append(d)
                return draws

            context["first_3"] = extract_draws(self.data.head(3))
            context["last_3"] = extract_draws(self.data.tail(3))
            
        context["coverage_pct"] = coverage_pct
        # Necesar UI-ului ca să atribuie corect cauza unei acoperiri <100%: limita
        # de variante SAU garanție degenerată. Fără el, mesajul acuza mereu limita,
        # inclusiv când era deja 0 (nelimitat), și sfătuia „pune 0" fără efect.
        context["max_variants"] = int(max_variants)
            
        if progress_cb:
            progress_cb("Pipeline complet!", 100)

        # --- TRACK POOL VARIATION ---
        # Pașii de walk-forward / backtest NU au voie să scrie aici: cheia e
        # `{joc}_{pool}_{pass}` — EXACT cheia de producție. Cei ~1940 de pași ai
        # unui ciclu WF suprascriau intrarea reală, iar `pool_variation` din raport
        # compara pool-ul curent cu un pool dintr-un punct istoric arbitrar. În plus,
        # cele ~25 de procese WF scriau concurent același `.tmp` (nume fix în
        # ui_shared.atomic_write_*) → 216 linii „Eroare la tracker-ul de variație"
        # în loto.log între 2026-07-06 și 2026-08-08.
        if track_pool_variation:
            try:
                import json
                from pathlib import Path
                from datetime import datetime
            
                history_file = Path("pool_history.json")
                history = {}
                if history_file.exists():
                    # Un fișier corupt (scriere parțială / sync OneDrive) nu are voie să
                    # dezactiveze tracker-ul PERMANENT: fără asta excepția se repeta la
                    # fiecare rulare, iar `pool_variation` rămânea gol la nesfârșit.
                    # Repornim de la zero — istoricul e informativ, nu critic.
                    try:
                        with open(history_file, "r", encoding="utf-8") as f:
                            history = json.load(f)
                        if not isinstance(history, dict):
                            raise ValueError(f"structură neașteptată: {type(history).__name__}")
                    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                        logging.warning("[PIPELINE] pool_history.json corupt (%s) → îl reconstruiesc.", exc)
                        history = {}
            
                # Cheia SEPARĂ Pool 1 de Pool 2. Fără sufix, auto-inversarea (care rulează
                # pipeline-ul de două ori pe același joc+pool) scria ambele pool-uri sub
                # aceeași cheie: ultima trecere câștiga, iar la rularea următoare Pool 1 se
                # compara cu Pool 2 al rulării precedente. Cum Pool 2 e prin construcție
                # DISJUNCT de Pool 1, tracker-ul raporta mereu schimbare totală
                # (toate numerele „added", toate „removed") — informație fără conținut.
                _pass = "p2" if getattr(self, "_manual_blacklist_set", None) else "p1"
                hist_key = f"{self.game_type}_{pool_size}_{_pass}"
                # Cheile în formatul vechi (fără sufix) nu mai sunt citite de nimeni și ar
                # rămâne în fișier la nesfârșit; le eliminăm la prima scriere.
                history = {k: v for k, v in history.items()
                           if k.endswith("_p1") or k.endswith("_p2")}
                last_pool = history.get(hist_key, {}).get("pool", [])
            
                pool_variation = {}
                if last_pool:
                    added = sorted(list(set(self.hard_core) - set(last_pool)))
                    removed = sorted(list(set(last_pool) - set(self.hard_core)))
                    pool_variation = {
                        "added": added,
                        "removed": removed,
                        "changed": bool(added or removed)
                    }
            
                history[hist_key] = {
                    "pool": self.hard_core,
                    "date": datetime.now().isoformat()
                }
                from ui_shared import atomic_write_json
                atomic_write_json(history_file, history)  # atomic: tmp+fsync+os.replace
                
                self.audit["pool_variation"] = pool_variation
            except Exception as e:
                logging.error(f"[PIPELINE] Eroare la tracker-ul de variație: {e}")

        # --- ADAPTIVE FEEDBACK POST-RUN: persistăm pool-ul nou + state ---
        if enable_adaptive_persistence and _HAS_ADAPTIVE:
            try:
                record_predicted_pool(
                    game_type=self.game_type,
                    pool_size=pool_size,
                    pool=self.hard_core,
                    data_rows=int(len(self.data)) if self.data is not None else 0,
                )
                summary = get_state_summary(self.game_type, pool_size)
                self.audit["adaptive_state"] = {
                    "event": adaptive_event,
                    "active_mode": self._adaptive_mode,
                    "last_hits": summary.get("last_hits"),
                    "streak_zero": summary.get("streak_zero"),
                    "rolling_avg": summary.get("rolling_avg"),
                    "baseline": round(summary.get("baseline", 0.0), 3),
                    "boosts": summary.get("boosts", []),
                    "penalties": summary.get("penalties", []),
                    "missed": (adaptive_info.get("missed") if adaptive_info else []),
                    "false_positives": (adaptive_info.get("false_positives") if adaptive_info else []),
                }
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la persistarea pool-ului: {e}")

        # === Hit Forecast Diagnostic — baseline matematic + recomandare pool size ===
        # Calculează P(k+ hits) pentru pool RANDOM și recomandă pool minim
        # pentru ≥3 evenimente 3+/4+/5+. UI-ul citește audit.hit_forecast.
        try:
            n_recent_for_forecast = max(int(len(self.data) * 0.05), 1) if self.data is not None else 100
            forecast = hypergeometric_hit_forecast(
                pool_size=len(self.hard_core) if self.hard_core else int(pool_size),
                draw_n=int(self.params["draw_n"]),
                max_n=int(self.params["max_n"]),
                n_draws=max(n_recent_for_forecast, 100),
            )
            if forecast:
                self.audit["hit_forecast"] = forecast
        except Exception as _exc_fc:
            logging.debug(f"[PIPELINE] hit_forecast a eșuat: {_exc_fc}")

        logging.info("[PIPELINE] Pipeline completat cu succes.")
        return lines, p10, p90, g_range, context, self.audit

    def _get_initial_hard_core(self, freq: np.ndarray, pool_size=12, filter_consecutives=False, blacklist=None) -> list:
        """Selectează nucleul dur inițial bazat pe top frecvență.

        FALLBACK: se folosește doar când scorerul n-a produs niciun scor
        (`_get_timesfm_pool` cu `scores` gol). Pe path-ul normal pool-ul vine din
        `select_pool_from_scores`.
        """
        logging.info(f"[INIT] Generare nucleu inițial de {pool_size} numere...")
        
        # Inițializăm blacklist dacă nu e furnizat
        if blacklist is None:
            blacklist = set()
        
        # Luăm top cele mai frecvente numere ca punct de plecare
        freq_scores = {
            int(i) + 1: float(freq[i])
            for i in range(len(freq))
            if freq[i] > 0 and (int(i) + 1) not in blacklist
        }
        pool = rank_by_score(freq_scores, pool_size)
        
        if filter_consecutives:
            pool = self._apply_consecutive_filter(pool, freq)
            self._consecutive_filter_applied = True
        else:
            self._consecutive_filter_applied = False
            
        # Salvăm statisticile inițiale
        self.hard_core_stats = {int(num): int(freq[num - 1]) for num in pool if num - 1 < len(freq)}
        logging.info(f"[INIT] Nucleu inițial: {pool}")
        return pool

    def _apply_consecutive_filter(self, pool: list, freq: np.ndarray, scores: dict | None = None,
                                  avoid: set | None = None) -> list:
        """STRICT (cerință utilizator): NU păstrăm NICIO pereche de numere consecutive
        în pool (nici 9-10, nici 38-39). Pentru fiecare adiacență (run de 2+): scoatem
        cel mai slab număr (după frecvență) și punem cea mai bine cotată rezervă care NU
        e adiacentă cu vreun număr din pool. Repetăm până nu mai rămâne niciun consecutiv.

        Înlocuirea folosește `scores` (bench-winner sau NQI) dacă e furnizat, altfel cade
        pe frecvența raw. (Anterior: doar 3+ erau sparte, perechile permise.)
        """
        if self.data is None or len(pool) < 2:
            return pool
            
        draw_sets = []
        if self._draw_matrix is not None and self._draw_matrix.size:
            draw_sets = [set(row) for row in self._draw_matrix]
        else:
            # Robust columns detection
            df = self.data
            n_cols = sorted(
                [c for c in df.columns if str(c).lower().startswith('n') and str(c).lower() != 'numbers'],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = primele 5 (Cat. I)
            if n_cols:
                for _, row in df.iterrows():
                    draw_sets.append(set(pd.to_numeric(row[n_cols], errors='coerce').dropna().astype(int)))
            elif "numbers" in df.columns:
                for _, row in df.iterrows():
                    try:
                        draw_sets.append(set(int(x) for x in str(row["numbers"]).split(",") if str(x).strip().isdigit()))
                    except (ValueError, TypeError) as exc:
                        logging.debug("anti-sequence: skip row (parse): %s", exc)
                        continue

        current_pool = sorted(pool.copy())
        # Ranking rezervelor: prefera bench-winner scoring daca disponibil,
        # altfel cade pe frecventa raw (legacy).
        if scores:
            ranked_reserves = sorted(
                range(1, len(freq) + 1),
                key=lambda n: scores.get(n, freq[n - 1] if n - 1 < len(freq) else 0),
                reverse=True,
            )
            # Convertim la indici 0-based pentru compatibilitate cu codul existent
            all_sorted_indices = np.array([n - 1 for n in ranked_reserves], dtype=np.int64)
            _replacement_signal = "bench-winner scores"
        else:
            all_sorted_indices = np.argsort(freq)[::-1]
            _replacement_signal = "raw frequency"
        modifications = []
        logging.info(f"[ANTI-SEQ] Replacement signal: {_replacement_signal}")

        # Trackăm numerele scoase ca să NU le re-adăugăm ca rezerve (altfel ai putea avea
        # "Scos 18 → adăugat 18"). Bug observat 2026-05-02.
        removed_nums: set[int] = set()
        # Instrumentare (audit): de câte ori garanția "zero consecutive" a trebuit să cadă
        # pe fallback (nicio rezervă neadiacentă disponibilă) sau bucla a fost întreruptă
        # de anti-loop guard — semnal că pool-ul rezultat poate încă avea o adiacență.
        fallback_any_count = 0
        guard_triggered = False

        def _forms_adjacency(pool_set: set, num: int) -> bool:
            """True dacă `num` e adiacent cu vreun număr din pool (ar crea consecutive)."""
            return (num - 1) in pool_set or (num + 1) in pool_set

        _iter_guard = 0
        while True:
            _iter_guard += 1
            if _iter_guard > 4 * max(len(pool), 1):  # anti-buclă (nu ar trebui atins)
                guard_triggered = True
                logging.warning(
                    "[ANTI-SEQ] iter_guard atins (%d iterații) — opresc filtrul înainte de "
                    "convergență completă; pool-ul poate încă conține numere consecutive.",
                    _iter_guard - 1,
                )
                break
            found_sequence = None
            # Căutăm orice CONSECUTIV (run de 2+) în pool — strict, fără perechi.
            for start_idx in range(len(current_pool) - 1):
                consecutive_nums = [current_pool[start_idx]]
                for next_idx in range(start_idx + 1, len(current_pool)):
                    if current_pool[next_idx] == consecutive_nums[-1] + 1:
                        consecutive_nums.append(current_pool[next_idx])
                    else:
                        break
                if len(consecutive_nums) >= 2:
                    found_sequence = tuple(consecutive_nums)
                    break
            
            if not found_sequence:
                break
                
            # STRICT (cerință utilizator): NU păstrăm NICIO pereche consecutivă în pool
            # (nici 2 adiacente). Spargem orice consecutiv găsit.
            seq_set = set(found_sequence)
            occurrences = sum(1 for d_set in draw_sets if seq_set.issubset(d_set))

            # Scoatem cel mai slab număr din secvență (după frecvență).
            weakest_num = min(found_sequence, key=lambda x: freq[x - 1])
            current_pool.remove(weakest_num)
            removed_nums.add(weakest_num)
            _pool_set = set(current_pool)

            # Alegem rezerva: cel mai bine cotat număr care NU e adiacent (fără consecutive);
            # dacă niciunul (improbabil), cădem pe cel mai bine cotat disponibil (păstrăm
            # dimensiunea pool-ului). Sărim numerele deja din pool sau scoase în rulare.
            chosen, chosen_any = None, None
            for idx in all_sorted_indices:
                num = int(idx) + 1
                if num in _pool_set or num in removed_nums or (avoid and num in avoid):
                    continue
                if chosen_any is None:
                    chosen_any = num
                if not _forms_adjacency(_pool_set, num):
                    chosen = num
                    break
            pick = chosen if chosen is not None else chosen_any
            if pick is None:
                logging.warning(
                    "[ANTI-SEQ] Nicio rezervă disponibilă (pool epuizat) — opresc filtrul cu "
                    "%d numere în pool (cerut %d); posibil consecutive rămase.",
                    len(current_pool), len(pool),
                )
                break  # nu mai există rezerve (improbabil) — oprim
            if chosen is None:
                # Fallback: NU am găsit rezervă neadiacentă → garanția "zero consecutive"
                # se poate încălca pentru acest pick (rezerva aleasă poate fi adiacentă).
                fallback_any_count += 1
                logging.warning(
                    "[ANTI-SEQ] Fallback chosen_any pentru secvența %s: nicio rezervă neadiacentă "
                    "disponibilă, aleg %d (poate reintroduce o adiacență).",
                    found_sequence, pick,
                )
            current_pool.append(pick)
            modifications.append(
                f"Scos {weakest_num} (frecvență {int(freq[weakest_num - 1])}) din secvența "
                f"{found_sequence} [a ieșit de {occurrences}× în istoric], adăugat {pick} "
                f"(frecvență {int(freq[pick - 1])})"
            )
            current_pool = sorted(current_pool)
            
        if modifications:
            self.audit['consecutive_filter'] = modifications
            logging.info(f"[FILTER] Modificări anti-secvență: {modifications}")

        # Verificare finală (doar audit/log — nu schimbă pool-ul): garanția "zero
        # consecutive" poate fi încălcată dacă fallback_any_count>0 sau guard_triggered.
        remaining_adjacent = [
            (a, b) for a, b in zip(current_pool, current_pool[1:]) if b == a + 1
        ]
        if fallback_any_count or guard_triggered or remaining_adjacent:
            self.audit['consecutive_filter_warnings'] = {
                "fallback_any_count": fallback_any_count,
                "iter_guard_triggered": guard_triggered,
                "remaining_adjacent_pairs": remaining_adjacent,
            }
            logging.warning(
                "[ANTI-SEQ] Garanție posibil incompletă: fallback_any=%d, iter_guard=%s, "
                "perechi adiacente rămase=%s",
                fallback_any_count, guard_triggered, remaining_adjacent,
            )

        return current_pool
        
    def _get_hard_core_joker(self, freq: np.ndarray, pool_size=3) -> list:
        """Selectează nucleul dur pentru Joker (1-based) și salvează statisticile."""
        freq_scores = {int(i) + 1: float(freq[i]) for i in range(len(freq))}
        pool = rank_by_score(freq_scores, pool_size)
        self.hard_core_joker_stats = {
            int(n): int(freq[n - 1]) for n in pool if n - 1 < len(freq)
        }
        return pool

    def _scores_via_bench_winner(self, is_joker_drum: bool = False) -> dict[int, float]:
        """Route scoring through the benchmark-winning method for this game/pool.

        Maps:
            self.game_type "6/49"  → game_key "loto_6_49"
            self.game_type "5/40"  → game_key "loto_5_40"
            self.game_type "joker" → "joker_urna2" if is_joker_drum else "joker_urna1"

        Reads best_methods.json via method_selector. Returns {} on any failure
        so the caller falls back to TimesFM.
        """
        try:
            from loto_enterprise.core.method_selector import get_ensemble_for_game, combine_ensemble_scores
        except Exception as exc:
            logging.warning("[ENGINE] method_selector import failed: %s", exc)
            return {}

        # Mapping (game_type, is_joker_drum) -> (game_key, max_num, pool_hint).
        # joker_urna2 e single-pick (draw_n=1) — pool_hint trebuie sa fie 1,
        # nu pool-size-ul UI care e pentru Urna 1.
        if self.game_type == "joker":
            if is_joker_drum:
                game_key = "joker_urna2"
                max_num = 20
                _pool_hint = 1
            else:
                game_key = "joker_urna1"
                max_num = int(self.params["max_n"])
                _pool_hint = int(self._winner_pool_hint)
        elif self.game_type == "5/40":
            game_key = "loto_5_40"
            max_num = int(self.params["max_n"])
            _pool_hint = int(self._winner_pool_hint)
        else:
            game_key = "loto_6_49"
            max_num = int(self.params["max_n"])
            _pool_hint = int(self._winner_pool_hint)

        if self._draw_matrix is None:
            return {}
        if is_joker_drum and self.data is not None and "joker" in self.data.columns:
            draws_2d = self.data["joker"].to_numpy(dtype=np.int64).reshape(-1, 1)
        else:
            draws_2d = self._draw_matrix.astype(np.int64)

        try:
            ensemble = get_ensemble_for_game(game_key, pool_size=_pool_hint)
            if not ensemble:
                return {}
            winner = ensemble[0][0]
            contributions = []
            for name, fn, weight in ensemble:
                try:
                    raw = fn(draws_2d, max_num)
                except Exception as exc_m:
                    logging.warning("[ENGINE] ensemble member %s a eșuat: %s — sar peste", name, exc_m)
                    raw = {}
                contributions.append((name, raw, weight))
            # Auditul primeşte compoziţia EFECTIVĂ (după filtrul de varianţă şi
            # decorelare), nu pe cea nominală din best_methods.json — altfel UI-ul
            # afişează 3 membri când blend-ul a folosit 2.
            _ens_audit: dict = {}
            scores = combine_ensemble_scores(contributions, audit=_ens_audit)
            if not scores:
                return {}
            _active = _ens_audit.get("ensemble_active") or []
            _n_act = len(_active) if _active else len(ensemble)
            logging.info(
                "[ENGINE] bench-winner scoring: game=%s pool=%d -> %s%s",
                game_key, _pool_hint, winner,
                (
                    f" (+ ensemble {_n_act} activi / {len(ensemble)} nominali)"
                    if len(ensemble) > 1 else ""
                ),
            )
            family = ""
            try:
                from loto_enterprise.benchmark.methods import METHODS as _METHODS
                meta = _METHODS.get(winner)
                if meta:
                    family = meta[1]
            except Exception as _exc_fam:
                logging.debug("[ENGINE] family lookup pt %s eșuat: %s", winner, _exc_fam)
            bench_winner_info = {
                "method": winner,
                "pool_hint": _pool_hint,
                "family": family,
            }
            if len(ensemble) > 1:
                # Membrii EFECTIV folosiţi (ponderi renormalizate după eliminări),
                # cu fallback la lista nominală dacă auditul lipseşte.
                _active = _ens_audit.get("ensemble_active")
                if _active:
                    bench_winner_info["ensemble"] = [
                        {"method": n, "weight": round(float(w), 4)} for n, w in _active
                    ]
                else:
                    bench_winner_info["ensemble"] = [
                        {"method": n, "weight": round(w, 4)} for n, _raw, w in contributions if _raw
                    ]
                _dropped: list = []
                for t in (_ens_audit.get("ensemble_dropped_correlated") or []):
                    if isinstance(t, (tuple, list)) and t:
                        _dropped.append((t[0], t[1] if len(t) > 1 else None,
                                         t[2] if len(t) > 2 else "correlated"))
                for d in (_ens_audit.get("ensemble_dropped") or []):
                    if isinstance(d, dict):
                        _dropped.append((d.get("method"), d.get("r"), d.get("reason")))
                    else:
                        _dropped.append((d, None, "flat_or_empty"))
                if _dropped:
                    bench_winner_info["ensemble_dropped"] = [
                        {"method": t[0], "vs": t[2], "r": t[1]} for t in _dropped
                    ]
            if game_key == "joker_urna2":
                bench_winner_info["single_pick_unbenched"] = True
                bench_winner_info["fallback"] = True
            self.audit.setdefault("bench_winner", {})[game_key] = bench_winner_info
            return {int(k): float(v) for k, v in scores.items()}
        except Exception as exc:
            logging.warning("[ENGINE] bench-winner scoring failed: %s", exc)
            return {}

    def _get_timesfm_scores(self, is_joker_drum: bool = False, context_len: int = 4096) -> dict[int, float]:
        """Scoruri per număr pentru selecția pool-ului.

        Sursă: metoda câștigătoare din benchmark pentru jocul/pool-ul curent
        (via method_selector, ensemble). Dacă aceasta nu produce scoruri →
        fallback determinist pe frecvență recency-weighted. Tot CPU.
        (`context_len` păstrat pentru compatibilitatea semnăturii cu apelanții.)
        """
        if self.use_bench_winner:
            scores = self._scores_via_bench_winner(is_joker_drum=is_joker_drum)
            if scores:
                return scores
            logging.warning("[ENGINE] bench-winner scoring returned empty — fallback frecvență")
            if is_joker_drum:
                _gk = "joker_urna2"
                _ph = 1
            elif self.game_type == "joker":
                _gk = "joker_urna1"
                _ph = int(getattr(self, "_winner_pool_hint", 11))
            elif self.game_type == "5/40":
                _gk = "loto_5_40"
                _ph = int(getattr(self, "_winner_pool_hint", 11))
            else:
                _gk = "loto_6_49"
                _ph = int(getattr(self, "_winner_pool_hint", 11))
            self.audit.setdefault("bench_winner", {}).setdefault(_gk, {
                "method": "frequency",
                "fallback": True,
                "reason": "bench-winner scoring empty",
                "pool_hint": _ph,
                "family": "baseline",
            })
        return self._frequency_fallback_scores(is_joker_drum=is_joker_drum)

    def _frequency_fallback_scores(self, is_joker_drum: bool = False) -> dict[int, float]:
        """Fallback determinist când câștigătorul bench nu produce scoruri:
        frecvență recency-weighted (exp-decay) pe istoric, normalizată [0,1]."""
        max_num = 20 if is_joker_drum else int(self.params["max_n"])
        if is_joker_drum and self.data is not None and "joker" in self.data.columns:
            draws_2d = self.data["joker"].to_numpy(dtype=np.int64).reshape(-1, 1)
        elif self._draw_matrix is not None:
            draws_2d = self._draw_matrix.astype(np.int64)
        else:
            return {}
        n = draws_2d.shape[0]
        if n == 0:
            return {i: 1.0 for i in range(1, max_num + 1)}
        weights = np.exp(np.linspace(-2.0, 0.0, n)).astype(np.float64)
        raw = np.zeros(max_num + 1, dtype=np.float64)
        for w, row in zip(weights, draws_2d):
            for v in row:
                vi = int(v)
                if 1 <= vi <= max_num:
                    raw[vi] += w
        vals = raw[1:]
        vmin, vmax = float(vals.min()), float(vals.max())
        rng = max(vmax - vmin, 1e-12)
        return {i: float((raw[i] - vmin) / rng) for i in range(1, max_num + 1)}

    def _get_timesfm_pool(self, scores: dict[int, float], pool_size: int, blacklist: set[int]) -> list[int]:
        """
        Metoda principală de selecție a numerelor (Pool) bazată pe TimesFM v3.
        Folosește selecție diversificată pe zone (low/mid/high) pentru maximizarea hit-urilor.
        """
        if not scores:
            # Fallback pe frecvență dacă TimesFM e indisponibil
            freq = self.analyze_frequency()
            return self._get_initial_hard_core(freq, pool_size=pool_size, blacklist=blacklist)

        max_num = int(self.params.get("max_n", 49))

        # Selector top-N după scor (aliniat bench / țintă 3+) — logică pură CPU.
        pool = select_pool_from_scores(
            scores, pool_size, blacklist, self.audit,
            max_num=max_num, draw_matrix=self._draw_matrix,
        )
        
        # Garanție pool complet: Dacă filtrele/blacklist-ul au fost prea agresive, completăm
        if len(pool) < pool_size:
            logging.warning(f"[TIMESFM] Pool incomplet ({len(pool)}/{pool_size}). Se completează cu cele mai bune numere din blacklist.")
            
            bl_scores = {
                int(num): float(score)
                for num, score in scores.items()
                if int(num) in blacklist and int(num) not in pool
            }
            needed = pool_size - len(pool)
            pool.extend(rank_by_score(bl_scores, needed))
                
            # Dacă tot nu avem destule (e.g. scores e incomplet), fallback final pe frecvență globală
            if len(pool) < pool_size:
                logging.warning(f"[TIMESFM] Pool încă incomplet ({len(pool)}/{pool_size}). Fallback final pe frecvență globală.")
                freq = getattr(self, 'freq', None)
                if freq is None:
                    freq = self.analyze_frequency()
                have = {int(n) for n in pool}
                freq_scores = {
                    int(i) + 1: float(freq[i])
                    for i in range(len(freq))
                    if (int(i) + 1) not in have
                }
                pool.extend(rank_by_score(freq_scores, pool_size - len(pool)))

        return sorted(pool[:pool_size])

