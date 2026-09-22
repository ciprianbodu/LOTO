"""Benchmark-winner scoring mixin (was LotoEngine._scores_via_bench_winner+)."""
from __future__ import annotations

import logging
import os

import numpy as np

from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance

class ScoringMixin:
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
            from loto_enterprise.core.method_selector import (
                get_ensemble_for_game,
                combine_ensemble_scores,
            )
            from loto_enterprise.benchmark.decision import ENSEMBLE_MAX_METHODS
        except Exception as exc:
            logging.warning("[ENGINE] method_selector import failed: %s", exc)
            return {}

        # Mapping (game_type, is_joker_drum) -> (game_key, max_num, pool_hint).
        # joker_urna2 e single-pick (draw_n=1) — pool_hint trebuie sa fie 1,
        # nu pool-size-ul UI care e pentru Urna 1. game_key vine din
        # `_bench_game_key` (sursă unică, aceeași folosită mai jos la fallback).
        game_key = self._bench_game_key(is_joker_drum)
        if is_joker_drum:
            max_num = 20
            _pool_hint = 1
        else:
            max_num = int(self.params["max_n"])
            _pool_hint = int(self._winner_pool_hint)

        if self._draw_matrix is None:
            return {}
        if is_joker_drum:
            # Urna 2 se scoreaza NUMAI pe coloana `joker` validata (1..20). Fara
            # coloana, cazul cade pe {} si callerul marcheaza Urna 2 indisponibila;
            # altfel numerele Urnei 1 (1..45) ar fi scorate ca bile 1..20.
            _jk = self._valid_joker_values()
            if _jk.size == 0:
                return {}
            draws_2d = _jk.reshape(-1, 1)
        else:
            draws_2d = self._draw_matrix.astype(np.int64)

        try:
            # max_methods explicit din decision.ENSEMBLE_MAX_METHODS (azi 1), NU
            # default-ul funcției (3, gândit pentru afișarea nominală din UI —
            # app_nicegui.py folosește explicit max_methods=3 acolo). best_methods.json
            # respectă azi plafonul la SCRIERE (decision.py), dar producția nu avea
            # nicio gardă proprie la CITIRE: un fișier editat manual, restaurat dintr-un
            # backup vechi sau scris de o regresie viitoare cu >1 membri ar fi fost
            # blendat tăcut aici — exact regresia măsurată în §5 pct. 8 (Joker k11:
            # blend 6.73% sub random 8.53%, față de 11.16% pentru câștigătorul unic).
            ensemble = get_ensemble_for_game(
                game_key, pool_size=_pool_hint, max_methods=ENSEMBLE_MAX_METHODS
            )
            if not ensemble:
                return {}
            contributions = []
            for name, fn, weight in ensemble:
                try:
                    raw = fn(draws_2d, max_num)
                except Exception as exc_m:
                    logging.warning(
                        "[ENGINE] ensemble member %s a eșuat: %s — sar peste",
                        name,
                        exc_m,
                    )
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
            # Cap de listă = primul membru ACTIV (după filtrare), nu ensemble[0]
            # nominal care putea fi sărit ca plat/corelat.
            winner = _active[0][0] if _active else ensemble[0][0]
            _n_act = len(_active) if _active else len(ensemble)
            logging.info(
                "[ENGINE] bench-winner scoring: game=%s pool=%d -> %s%s",
                game_key,
                _pool_hint,
                winner,
                (
                    f" (+ ensemble {_n_act} activi / {len(ensemble)} nominali)"
                    if len(ensemble) > 1
                    else ""
                ),
            )
            family = ""
            try:
                from loto_enterprise.benchmark.methods import METHODS as _METHODS

                meta = _METHODS.get(winner)
                if meta:
                    family = meta[1]
            except Exception as _exc_fam:
                logging.debug(
                    "[ENGINE] family lookup pt %s eșuat: %s", winner, _exc_fam
                )
            bench_winner_info = {
                "method": winner,
                "pool_hint": _pool_hint,
                "family": family,
            }
            if len(ensemble) > 1:
                # Membrii EFECTIV folosiţi (ponderi renormalizate după eliminări),
                # cu fallback la lista nominală dacă auditul lipseşte.
                if _active:
                    bench_winner_info["ensemble"] = [
                        {"method": n, "weight": round(float(w), 4)} for n, w in _active
                    ]
                else:
                    bench_winner_info["ensemble"] = [
                        {"method": n, "weight": round(w, 4)}
                        for n, _raw, w in contributions
                        if _raw
                    ]
                _dropped: list = []
                for t in _ens_audit.get("ensemble_dropped_correlated") or []:
                    if isinstance(t, (tuple, list)) and t:
                        _dropped.append(
                            {
                                "method": t[0],
                                "r": t[1] if len(t) > 1 else None,
                                "vs": t[2] if len(t) > 2 else None,
                                "reason": "correlated",
                            }
                        )
                for d in _ens_audit.get("ensemble_dropped") or []:
                    if isinstance(d, dict):
                        _dropped.append(
                            {
                                "method": d.get("method"),
                                "r": d.get("r"),
                                "vs": d.get("vs"),
                                "reason": d.get("reason") or "flat_or_empty",
                            }
                        )
                    else:
                        _dropped.append(
                            {
                                "method": d,
                                "r": None,
                                "vs": None,
                                "reason": "flat_or_empty",
                            }
                        )
                if _dropped:
                    bench_winner_info["ensemble_dropped"] = _dropped
            # Flagurile documentate în CLAUDE.md (ex. ensemble_single_active_normalized)
            # se pierdeau: combine_ensemble_scores le scria doar în _ens_audit local.
            for _flag in (
                "ensemble_single_active_normalized",
                "ensemble_fallback_flat",
                "ensemble_fallback_empty",
            ):
                if _ens_audit.get(_flag):
                    bench_winner_info[_flag] = True
            if game_key == "joker_urna2":
                bench_winner_info["single_pick"] = True
                bench_winner_info["target_metric"] = "rate_1plus_k1"
            self.audit.setdefault("bench_winner", {})[game_key] = bench_winner_info
            return {int(k): float(v) for k, v in scores.items()}
        except Exception as exc:
            logging.warning("[ENGINE] bench-winner scoring failed: %s", exc)
            return {}

    def _get_timesfm_scores(
        self, is_joker_drum: bool = False, context_len: int = 4096
    ) -> dict[int, float]:
        """Scoruri per număr pentru selecția pool-ului.

        Sursă: metoda câștigătoare din benchmark pentru jocul/pool-ul curent
        (via method_selector, ensemble). Dacă aceasta nu produce scoruri →
        fallback determinist pe frecvență recency-weighted. Tot CPU.
        (`context_len` păstrat pentru compatibilitatea semnăturii cu apelanții.)
        """
        if self.use_bench_winner:
            scores = self._scores_via_bench_winner(is_joker_drum=is_joker_drum)
            # Un dict NEVID dar PLAT (toate scorurile egale) nu e un rezultat, e un
            # eșec deghizat: un scorer poate semnala eroarea internă cu
            # `normalize({}, max_num)`, care întoarce {n: 0.0 …} — TRUTHY, deci
            # trecea de `if scores`. Tie-break-ul canonic e „număr mare întâi", așa
            # că pool-ul devenea [49, 48, 47, …]: cele mai mari numere, pur artefact,
            # fără niciun avertisment pentru utilizator. Tratăm platul ca pe gol →
            # cade pe fallback-ul determinist de frecvență.
            if scores and not has_usable_score_variance(scores):
                logging.warning(
                    "[ENGINE] bench-winner a întors scoruri inutilizabile (%d numere; "
                    "plate, ne-numerice sau ne-finite) — cad pe frecvență.",
                    len(scores),
                )
                self.audit["bench_winner_flat_scores"] = True
                self.audit["bench_winner_unusable_scores"] = True
                self._bench_winner_unusable_attempt = (
                    (self.audit.get("bench_winner", {}) or {})
                    .get(self._bench_game_key(is_joker_drum), {})
                    .get("method")
                )
                scores = {}
            if scores:
                return scores
            logging.warning(
                "[ENGINE] bench-winner scoring returned empty — fallback frecvență"
            )
            _gk = self._bench_game_key(is_joker_drum)
            _ph = 1 if is_joker_drum else int(getattr(self, "_winner_pool_hint", 11))
            # Suprascriem (nu setdefault): pe scoruri inutilizabile intrarea a fost
            # deja scrisa cu numele metodei moarte, iar UI-ul ar fi afisat-o ca
            # activa desi pool-ul vine din frecventa.
            _attempted = getattr(self, "_bench_winner_unusable_attempt", None)
            self._bench_winner_unusable_attempt = None
            _fb_info = {
                "method": "frequency",
                "fallback": True,
                "reason": (
                    "bench-winner scoring unusable"
                    if _attempted
                    else "bench-winner scoring empty"
                ),
                "pool_hint": _ph,
                "family": "baseline",
            }
            if _attempted:
                _fb_info["attempted"] = _attempted
            self.audit.setdefault("bench_winner", {})[_gk] = _fb_info
        return self._frequency_fallback_scores(is_joker_drum=is_joker_drum)

    def _bench_game_key(self, is_joker_drum: bool = False) -> str:
        """Cheia de joc din best_methods.json pentru (game_type, is_joker_drum)."""
        if is_joker_drum:
            return "joker_urna2"
        return {"6/49": "loto_6_49", "5/40": "loto_5_40", "joker": "joker_urna1"}.get(
            self.game_type, "loto_6_49"
        )

    def _frequency_fallback_scores(
        self, is_joker_drum: bool = False
    ) -> dict[int, float]:
        """Fallback determinist când câștigătorul bench nu produce scoruri:
        frecvență recency-weighted (exp-decay) pe istoric, normalizată [0,1]."""
        max_num = 20 if is_joker_drum else int(self.params["max_n"])
        if is_joker_drum:
            _jk = self._valid_joker_values()
            if _jk.size == 0:
                # Nu fabricăm un clasament plat / o bilă arbitrară când Urna 2
                # lipsește sau e complet invalidă. Callerul o semnalizează și
                # lasă biletul fără al șaselea număr.
                return {}
            draws_2d = _jk.reshape(-1, 1)
        elif self._draw_matrix is not None:
            draws_2d = self._draw_matrix.astype(np.int64)
        else:
            return {}
        from loto_enterprise.benchmark.methods import score_frequency

        scores = score_frequency(draws_2d, max_num)
        # Istoric gol: `score_frequency` normalizează un vector constant la 0.
        # Fără a doua gardă, `rank_by_score` umple pool-ul cu cele mai mari
        # numere — exact eșecul pe care poarta de varianță îl oprește la
        # câștigătorul de bench.
        if not has_usable_score_variance(scores):
            return {}
        return scores

    def _get_timesfm_pool(
        self, scores: dict[int, float], pool_size: int, blacklist: set[int]
    ) -> list[int]:
        """Selectează top-N după scor (aliniat bench). Numele e istoric (TimesFM)."""
        if not scores:
            # Fallback pe frecvență dacă TimesFM e indisponibil
            freq = self.analyze_frequency()
            return self._get_initial_hard_core(
                freq, pool_size=pool_size, blacklist=blacklist
            )

        max_num = int(self.params.get("max_n", 49))

        # Selector top-N după scor (aliniat bench / țintă 3+) — logică pură CPU.
        pool = select_pool_from_scores(
            scores,
            pool_size,
            blacklist,
            self.audit,
            max_num=max_num,
            draw_matrix=self._draw_matrix,
        )

        # Gardă defensivă: dacă selectorul întoarce prea puține numere,
        # completăm numai din candidații ne-excluși.
        if len(pool) < pool_size:
            logging.warning(
                f"[TIMESFM] Pool incomplet ({len(pool)}/{pool_size}). "
                "Completez din numere ne-excluse (nu din blacklist)."
            )
            have = {int(n) for n in pool}
            blocked = set(int(n) for n in blacklist)
            extra_scores = {
                int(num): float(score)
                for num, score in scores.items()
                if int(num) not in have
                and int(num) not in blocked
                and 1 <= int(num) <= max_num
            }
            needed = pool_size - len(pool)
            pool.extend(rank_by_score(extra_scores, needed))

            if len(pool) < pool_size:
                logging.warning(
                    f"[TIMESFM] Pool încă incomplet ({len(pool)}/{pool_size}). "
                    "Fallback frecvență, tot fără blacklist."
                )
                freq = getattr(self, "freq", None)
                if freq is None:
                    freq = self.analyze_frequency()
                have = {int(n) for n in pool}
                freq_scores = {
                    int(i) + 1: float(freq[i])
                    for i in range(len(freq))
                    if (int(i) + 1) not in have and (int(i) + 1) not in blocked
                }
                pool.extend(rank_by_score(freq_scores, pool_size - len(pool)))
            if len(pool) < pool_size:
                logging.warning(
                    f"[TIMESFM] Pool final {len(pool)}/{pool_size} — universul "
                    "rămas nu acoperă pool_size (blacklist prea mare)."
                )

        return sorted(pool[:pool_size])
