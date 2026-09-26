"""
Loto Engine - Motor principal de analiză și predicție (vectorizat unde e posibil).
"""

from __future__ import annotations

import hashlib
import json
import warnings
from datetime import datetime

import itertools
import math
import numpy as np
import pandas as pd
import time
import logging
import os
import sys

# Scoring = câștigătorul benchmark (metode CPU) → fallback frecvență.
# Tot suportul GPU/neural (TimesFM/torch/foundation) a fost eliminat din aplicație.
# Selecția pool-ului (top-N pur după scor, aliniat bench / țintă 3+) e logică pură CPU.
from loto_enterprise.core.pool_selection import (
    apply_consecutive_limit,
    select_pool_from_scores,
)
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history
from loto_enterprise.core.score_validation import has_usable_score_variance

# Tie-break CANONIC „top-N după scor" (regula de aur 8 din AGENTS.md): orice
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
    datefmt="%Y-%m-%d %H:%M:%S",
)
VERSION = "1.1.2"
warnings.filterwarnings("ignore")


from covering.greedy import generate_combinatorial_wheel
from covering.hypergeo import hypergeometric_hit_forecast
from loto_enterprise.engine.pipeline import PipelineMixin
from loto_enterprise.engine.scoring import ScoringMixin


class LotoEngine(PipelineMixin, ScoringMixin):
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
                # 5/40: se extrag 6 numere (hiturile se numără pe toate 6;
                # categoria I = 5 din primele 5 nu e modelată separat), biletul
                # are 5. draw_n = extragere, play_n = bilet.
                "draw_n": 6,
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
                "max_joker": 20,
            },
        }
        return params.get(game_type, params["6/49"])

    def load_data(self, csv_path: str) -> bool:
        """Încarcă date din CSV."""
        try:
            self.data = pd.read_csv(csv_path)
            raw_rows = len(self.data)
            self._build_draw_matrix()
            self.audit["rows_loaded"] = raw_rows
            self.audit["game_detected"] = self.game_type
            # Hash pe CONȚINUT, nu pe pointeri. `self.data.values` pe un DataFrame
            # cu dtype-uri MIXTE (coloana `date` = str + n1..n6 = int64) dă un
            # array `dtype=object`, iar `.tobytes()` serializează ADRESELE
            # obiectelor Python, nu valorile — deci trei citiri ale ACELUIAȘI
            # fișier dădeau trei hash-uri diferite (verificat), exact
            # nedeterminismul pe care comentariul de dinainte pretindea că l-a
            # rezolvat. `hash_pandas_object` hash-uiește valorile.
            self.audit["hash"] = hashlib.sha256(
                pd.util.hash_pandas_object(self.data, index=True).values.tobytes()
            ).hexdigest()[:16]
            # Un CSV care se PARSEAZĂ nu înseamnă date UTILIZABILE: pandas citește
            # fericit un fișier de text arbitrar ca DataFrame cu 1 rând, iar
            # `load_data` întorcea True. Pipeline-ul rula apoi până la capăt și
            # producea POOL GOL / 0 bilete, iar jobul era marcat COMPLETED —
            # utilizatorul vedea „gata" și niciun bilet, fără nicio eroare.
            # Cerem cel puțin o extragere valid parsată (matricea de extrageri).
            if self._draw_matrix is None or len(self._draw_matrix) == 0:
                self.audit["rows_valid"] = 0
                logging.error(
                    "[LOAD] %s: nicio extragere validă după parsare (%s rânduri "
                    "citite) — date inutilizabile pentru %s.",
                    csv_path,
                    len(self.data),
                    self.game_type,
                )
                self.data = None
                return False
            self.audit["rows_valid"] = len(self.data)
            return True
        except Exception as e:
            logging.error("[LOAD] Eroare la încărcare date din %s: %s", csv_path, e)
            self.data = None
            self._draw_matrix = None
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
                [
                    c
                    for c in self.data.columns
                    if str(c).lower().startswith("n") and str(c).lower() != "numbers"
                ],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = toate cele 6 extrase
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
        """Construiește matricea validă ``rows x draw_n`` pentru scorere.

        Rândurile invalide se elimină împreună din ``data`` și matrice: fără
        asta indecșii din walk-forward ar antrena pe un rând și ar evalua altul.
        """
        if self.data is None:
            self._draw_matrix = None
            return
        df = self.data = chronological_history(self.data)
        if "numbers" in df.columns:
            self._draw_matrix = None
            return
        n_cols = [
            f"n{i}"
            for i in range(1, int(self.params["draw_n"]) + 1)
            if f"n{i}" in df.columns
        ]
        draw_n = int(self.params["draw_n"])
        if len(n_cols) != draw_n:
            logging.error(
                "[LOAD] %s: sunt necesare %d coloane n1..n%d, găsite %s.",
                self.game_type,
                draw_n,
                draw_n,
                [str(c) for c in n_cols],
            )
            self._draw_matrix = None
            return
        try:
            matrix, valid_mask = valid_draw_matrix(
                df,
                n_cols,
                draw_n=draw_n,
                max_num=int(self.params["max_n"]),
            )
        except ValueError as exc:
            logging.error(
                "[LOAD] %s: validare extrageri eșuată: %s", self.game_type, exc
            )
            self._draw_matrix = None
            return

        rejected = int(len(df) - int(valid_mask.sum()))
        if rejected:
            logging.warning(
                "[LOAD] %s: elimin %d rând(uri) invalide (numere lipsă, duplicate, "
                "zecimale sau în afara intervalului).",
                self.game_type,
                rejected,
            )
            self.data = df.loc[valid_mask].reset_index(drop=True)
        self._draw_matrix = matrix.astype(np.int32, copy=False)
        self.audit["draw_number_columns"] = [str(c) for c in n_cols]
        self.audit["invalid_draw_rows_dropped"] = (
            int(self.audit.get("invalid_draw_rows_dropped", 0)) + rejected
        )

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
                [c for c in self.data.columns if str(c).lower().startswith("n")],
                key=lambda x: int("".join(ch for ch in str(x) if ch.isdigit()) or "0"),
            )[: int(self.params["draw_n"])]  # 5/40 = toate cele 6 extrase
            if n_cols:
                raw_vals = self.data[n_cols].values.ravel()
                all_numbers = raw_vals[~np.isnan(raw_vals)].astype(int).tolist()
            elif "numbers" in self.data.columns:
                for _, row in self.data.iterrows():
                    try:
                        nums = [
                            int(x)
                            for x in str(row["numbers"]).split(",")
                            if str(x).strip().isdigit()
                        ]
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
        joker_vals = self._valid_joker_values()
        if joker_vals.size == 0:
            return np.zeros(20, dtype=np.int64)
        freq = np.bincount(joker_vals, minlength=21)
        return freq[1:21]

    def _valid_joker_values(self) -> np.ndarray:
        """Valorile valide din Urna 2, fără conversii tăcute de zecimale.

        Urna 1 este deja validată prin ``_build_draw_matrix``. Pentru Joker,
        coloana separată ``joker`` trebuie să respecte același contract strict:
        întreg finit în intervalul 1..20. Un ``4.7`` nu este bila 4, iar un
        câmp lipsă sau 21 nu trebuie să ajungă într-un scorer.
        """
        if self.data is None or "joker" not in self.data.columns:
            return np.empty(0, dtype=np.int64)
        try:
            matrix, valid_mask = valid_draw_matrix(
                self.data,
                ["joker"],
                draw_n=1,
                max_num=20,
            )
        except ValueError as exc:
            logging.warning("[JOKER] Urna 2 nu poate fi validată: %s", exc)
            self.audit["joker_urna2_validation_error"] = str(exc)
            return np.empty(0, dtype=np.int64)

        total = len(self.data)
        valid = int(valid_mask.sum())
        dropped = total - valid
        self.audit["joker_urna2_rows_total"] = total
        self.audit["joker_urna2_rows_valid"] = valid
        self.audit["joker_urna2_invalid_rows_dropped"] = dropped
        if dropped and not self.audit.get("joker_urna2_invalid_warning_logged"):
            logging.warning(
                "[JOKER] ignor %d valoare(i) Urna 2 invalidă(e) din %d "
                "(accept doar întregi 1..20).",
                dropped,
                total,
            )
            self.audit["joker_urna2_invalid_warning_logged"] = True
        return matrix[:, 0].astype(np.int64, copy=False)

    def generate_predictions(
        self, guarantee=4, max_variants=0, scores=None, condition=None
    ):
        """Generează predicții bazate pe analiză.

        `condition` (> guarantee) = lotto design „guarantee dacă condition": garanția
        se aplică numai când cad `condition` numere din pool, cu mult mai puține
        bilete. None sau egal cu garanția = cover clasic (comportament neschimbat).
        """
        if not hasattr(self, "hard_core") or not self.hard_core:
            return [], 0.0
        _cond = int(condition) if condition is not None else int(guarantee)
        if _cond != int(guarantee):
            from wheeling_methods import generate_wheel

            variants, coverage_pct = generate_wheel(
                "lotto",
                pool=self.hard_core,
                pick=self.params["play_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores,
                condition=_cond,
            )
            self._attach_joker(variants)
            return variants, coverage_pct

        # Wheeling: implicit **lajolla** când max_variants == 0 (setarea implicită
        # a UI-ului), altfel greedy. Comentariul de dinainte zicea „implicit greedy
        # (bit-identic)", ceea ce contrazicea codul de 5 rânduri mai jos.
        # Alternative selectabile prin env
        # LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla|union34
        # (necunoscut → greedy). Lista completă: wheeling_methods.WHEEL_METHODS.
        _wheel_method_env = os.environ.get("LOTO_WHEEL_METHOD", "").strip().lower()
        if _wheel_method_env:
            # Override explicit — comportament neschimbat (backward-compat).
            _wheel_method = _wheel_method_env
        elif max_variants == 0:
            # Implicit, fără cap de bilete ("garanție completă"): design de acoperire
            # PRECALCULAT și validat 100% din covering_designs/ pentru geometriile UI: pool 6-16,
            # pick 5/6 și garanție 3..pick-1. Orice geometrie fără fișier cade pe
            # ILP, apoi pe greedy. Exemplu: 6/49 pool 12 / g4, 54→41 bilete;
            # 5/40+Joker pool 12 / g4, 123→113.
            _wheel_method = "lajolla"
        else:
            # Buget de bilete fix (max_variants>0): greedy + packing numere
            # din pool pe bilete (ensure_pool_numbers_on_tickets). Default
            # max_variants=0 e neschimbat.
            _wheel_method = "greedy"
        if _wheel_method and _wheel_method != "greedy":
            from wheeling_methods import generate_wheel

            variants, coverage_pct = generate_wheel(
                _wheel_method,
                pool=self.hard_core,
                pick=self.params["play_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores,
            )
        else:
            variants, coverage_pct = generate_combinatorial_wheel(
                pool=self.hard_core,
                pick=self.params["play_n"],
                guarantee=guarantee,
                max_variants=max_variants,
                scores=scores,
            )

        self._attach_joker(variants)
        return variants, coverage_pct

    def _attach_joker(self, variants: list) -> None:
        """Atașează Joker-ul din nucleul dur de Urna 2 pe fiecare variantă (in place)."""
        if (
            self.game_type == "joker"
            and hasattr(self, "hard_core_joker")
            and self.hard_core_joker
        ):
            # Atașăm jokerul favorit pe fiecare variantă de Urna 1. Codul rămâne
            # ciclic (generic pe lungimea listei), dar cu urna2 single-pick
            # (pool 1, aliniat bench) toate variantele primesc ACELAȘI joker.
            jokers = self.hard_core_joker
            joker_pool_size = len(jokers)
            for idx, variant in enumerate(variants):
                assigned_joker = jokers[idx % joker_pool_size]
                variant.append(assigned_joker)  # Elementul 6 este Joker-ul

    @staticmethod
    def apply_recent_penalty(
        scores: dict, draws_2d, n_draws: int, factor: float, max_num: int
    ) -> tuple[dict, dict]:
        """Penalizează numerele extrase în ultimele `n_draws` extrageri.

        Scorul unui număr apărut de k ori în ultimele `n_draws` rânduri scade
        cu `(1 - factor**k) * |scor|`: pentru scoruri pozitive e exact înmulțirea
        cu `factor**k`, iar un scor negativ coboară, nu urcă. Preferință a utilizatorului, neutră ca
        valoare așteptată (vezi AGENTS.md §6): nu schimbă probabilitatea
        extragerii, doar compoziția pool-ului. Întoarce (scoruri_noi,
        {numar: aparitii}) — al doilea dict conține doar numerele penalizate.
        """
        n = int(n_draws or 0)
        try:
            f = float(factor)
        except (TypeError, ValueError):
            f = 0.5
        if n <= 0 or not scores or draws_2d is None or len(draws_2d) == 0 or f >= 1.0:
            return dict(scores), {}
        f = max(0.0, f)
        recent = np.asarray(draws_2d)[-n:]
        counts: dict[int, int] = {}
        for row in recent:
            for v in row:
                vi = int(v)
                if 1 <= vi <= int(max_num):
                    counts[vi] = counts.get(vi, 0) + 1
        out = {}
        for num, sc in scores.items():
            k = counts.get(int(num), 0)
            v = float(sc)
            if k:
                # La v < 0, v*f**k ar URCA scorul (-1 * 0.5 = -0.5). Ramura pozitiva
                # pastreaza exact inmultirea veche, bit cu bit (pool si cache WF).
                v = v * (f**k) if v >= 0 else v * (2.0 - f**k)
            out[num] = v
        return out, {k_: v_ for k_, v_ in sorted(counts.items())}

    def _get_initial_hard_core(
        self, freq: np.ndarray, pool_size=12, blacklist=None, max_consecutive_run=0
    ) -> list:
        """Selectează nucleul dur inițial bazat pe top frecvență.

        FALLBACK: se folosește doar când scorerul n-a produs niciun scor
        (`_get_timesfm_pool` cu `scores` gol). Pe path-ul normal pool-ul vine din
        `select_pool_from_scores`. Limita de consecutive a utilizatorului se
        aplică și aici, ca opțiunea să nu depindă de traseul pe care s-a ajuns.
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
        pool = apply_consecutive_limit(
            rank_by_score(freq_scores, len(freq_scores)),
            pool_size,
            max_consecutive_run,
            getattr(self, "audit", None),
        )

        # Fără filtre post-scoring automate: pool-ul rămâne top-scor pur, cu o
        # singură excepție, opțiunea explicită `max_consecutive_run` de mai sus
        # (AGENTS.md §4.2: un filtru structural deghizat în metodă e interzis).

        # Salvăm statisticile inițiale
        self.hard_core_stats = {
            int(num): int(freq[num - 1]) for num in pool if num - 1 < len(freq)
        }
        logging.info(f"[INIT] Nucleu inițial: {pool}")
        return pool

    def _get_hard_core_joker(self, freq: np.ndarray, pool_size=3) -> list:
        """Selectează nucleul dur pentru Joker (1-based) și salvează statisticile."""
        freq_scores = {int(i) + 1: float(freq[i]) for i in range(len(freq))}
        pool = rank_by_score(freq_scores, pool_size)
        self.hard_core_joker_stats = {
            int(n): int(freq[n - 1]) for n in pool if n - 1 < len(freq)
        }
        return pool

