"""Institutional pipeline mixin (was LotoEngine.run_institutional_pipeline)."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd

from covering.hypergeo import hypergeometric_hit_forecast
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance

try:
    from loto_enterprise.core.adaptive_feedback import (
        compute_post_draw_feedback,
        get_state_summary,
        load_adaptive_state,
        record_predicted_pool,
        save_adaptive_state,
    )

    _HAS_ADAPTIVE = True
except ImportError:
    _HAS_ADAPTIVE = False
    compute_post_draw_feedback = get_state_summary = load_adaptive_state = None
    record_predicted_pool = save_adaptive_state = None


class PipelineMixin:
    def run_institutional_pipeline(
        self,
        progress_cb=None,
        pool_size=12,
        guarantee=4,
        max_variants=0,
        lookback=0,
        sim_depth_pct=10,
        enable_adaptive_persistence=False,
        pure_bench_mode=False,
        track_pool_variation=True,
        wheel_condition=None,
        recent_penalty_draws=0,
        recent_penalty_factor=0.5,
        restrict_base_max=0,
        restrict_base_min=0,
        max_consecutive_run=0,
    ):
        """Rulează pipeline-ul complet de analiză.

        ⚠️ Fără filtre post-scoring automate (`self.audit["filters_disabled"] =
        True`): „Flow minimal" (cerere utilizator, 2026-07-08) a scos vechea
        anti-secvență, reducerea inteligentă și orice alt filtru automat din
        fluxul principal, iar codul lor mort a fost șters. Vechii parametri
        `filter_consecutives` / `smart_reduction` NU mai există în semnătură;
        un task vechi din coada SQLite care încă îi poartă e normalizat de
        worker.py într-un dict cu chei fixe, deci cheile în plus se ignoră fără
        eroare. Nu reintroduce filtre structurale deghizate în metode (paritate,
        sume, decade, poziție, secvențe): ele constrâng combinația, nu prezic un
        număr (AGENTS.md §4.3). Pool-ul e top-scor pur, cu excepția opțiunilor
        explicite ale utilizatorului de mai jos, fiecare consemnată în audit.

        `pure_bench_mode` rămâne acceptat pentru compatibilitatea contractului
        UI↔worker, la fel ca `should_use_blacklist`: telemetrie, nu buton de
        configurare activ.

        recent_penalty_draws / recent_penalty_factor: penalizare pe numerele
            extrase în ultimele N extrageri (scor × factor^aparitii). 0 = oprit.
            Se aplică identic în producție și în walk-forward, deci validarea
            măsoară exact pool-ul jucat.

        restrict_base_min / restrict_base_max: preferință OPȚIONALĂ a
            utilizatorului — restrânge candidații la intervalul [min, max]
            (0 pe oricare capăt = capătul rămâne liber; ambele 0 = fără
            restricție, implicit). Exemplu: min 10 și max 40 înseamnă „6 din
            10–40" în loc de „6 din 1–49".
            NU are avantaj statistic demonstrat: probabilitatea de hit a unui pool
            de dimensiune fixă e identică matematic (hipergeometric) indiferent de
            care numere îl compun — confirmat empiric pe istoricul acestei
            aplicații (scripts/analysis/pattern_base_reduction.py). E o preferință
            de compoziție, la fel ca `recent_penalty_draws`, aplicată identic în
            producție și walk-forward (intră în cheia de cache WF când e activă).

        max_consecutive_run: preferință OPȚIONALĂ a utilizatorului (cerere
            2026-09-26) — cel mult atâtea numere consecutive în pool; 2 = fără
            trei la rând (ex. 4-5-6), 0 = oprit (implicit în motor). Clasamentul
            metodei (după penalizarea recentă și restrângerea bazei) se parcurge
            în ordine; numărul care ar forma secvența e înlocuit cu următorul din
            clasament care încape (`pool_selection.apply_consecutive_limit`).
            Dacă baza restrânsă e prea îngustă pentru pool, limita se relaxează,
            nu pool-ul și nici intervalul; relaxarea apare în
            `audit["consecutive_limit"]`. NU are avantaj statistic demonstrat
            (probabilitate hipergeometrică identică pentru orice pool de mărime
            fixă). Nu se aplică Urnei 2 Joker (un singur număr). Aplicată identic
            în producție și walk-forward (intră în cheia de cache WF când e activă).

        wheel_condition: numărul de numere din pool care trebuie să cadă pentru
            ca garanția să se aplice (lotto design „guarantee dacă condition").
            None, 0 sau egal cu garanția = cover clasic „guarantee dacă guarantee".
            Se limitează la [guarantee, play_n] (numere pe bilet).

        enable_adaptive_persistence: Dacă True (live mode), încarcă/salvează
            adaptive_state.json — învățare persistentă din extrageri reale.
            Backtester-ul îl lasă False (își gestionează propriul state in-memory).

        track_pool_variation: Dacă True (implicit — producție), compară pool-ul
            cu ultimul pool salvat pentru același joc+dimensiune și
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

        # Garanția trebuie să fie în 1..draw_n. Workerul normalizează contractul
        # UI la 3..draw_n, însă engine-ul rămâne API public și apără inclusiv
        # apelurile directe: guarantee=0 ar acoperi formal doar mulțimea vidă și
        # ar raporta absurd 100%.
        # Biletul are `play_n` numere (5/40: 5, deși se extrag 6); garanția și
        # condiția lotto se raportează la BILET, nu la extragere.
        _draw_n = int(self.params.get("play_n", self.params["draw_n"]))
        if int(guarantee) < 1:
            logging.warning(
                "[PIPELINE] Garanție %s < 1 — imposibilă; o limitez la 1.",
                guarantee,
            )
            guarantee = 1
        if int(guarantee) > _draw_n:
            logging.warning(
                "[PIPELINE] Garanție %s > numere pe bilet (%s) — imposibil; "
                "o limitez la %s.",
                guarantee,
                _draw_n,
                _draw_n,
            )
            guarantee = _draw_n
        # Condiția lotto design: în [guarantee, draw_n]; lipsă/0 = cover clasic.
        try:
            _wc = int(wheel_condition) if wheel_condition is not None else 0
        except (TypeError, ValueError):
            _wc = 0
        if _wc <= 0:
            _wc = int(guarantee)
        if _wc < int(guarantee) or _wc > _draw_n:
            logging.warning(
                "[PIPELINE] Condiția wheel %s în afara [%s, %s] — o limitez.",
                wheel_condition,
                guarantee,
                _draw_n,
            )
            _wc = max(int(guarantee), min(_draw_n, _wc))
        wheel_condition = _wc

        # Garanția cerută în UI se respectă ÎNTOTDEAUNA — fără escaladare implicită.
        # (Istoric: pe pool=10 garanția era suprascrisă silențios cu draw_n → full
        # wheel C(10,draw_n), de ~10x mai scump decât coverul minim pentru garanția
        # cerută, iar UI-ul afișa în continuare garanția veche. Cine vrea full wheel
        # setează explicit Garanție = draw_n în UI.)

        # === ADAPTIVE FEEDBACK PRE-RUN: detectăm extrageri reale apărute de la
        # ultima predicție și actualizăm telemetria de regim (streak/mod) ÎNAINTE
        # de TimesFM. Nu mai ajustează niciun scor — vezi docstring-ul modulului
        # adaptive_feedback pentru eliminarea lui error_correction_map. ===
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
                        adaptive_event, adaptive_info = compute_post_draw_feedback(
                            last_pool=last_pool,
                            actual_draw=new_actual,
                            history=state.get("history", []),
                            game_type=self.game_type,
                            pool_size=pool_size,
                            streak_zero=int(rs.get("streak_zero", 0)),
                            prev_mode=rs.get("active_mode", "normal"),
                            reset_duration=int(rs.get("reset_duration", 0)),
                        )
                        # Persistăm istoricul actualizat
                        history = list(state.get("history", []))
                        history.append(
                            {
                                "date": self._extract_date_at_index(last_rows),
                                "pool_hits": int(adaptive_info["pool_hits"]),
                                "actual": [int(n) for n in new_actual],
                                "event": adaptive_event,
                            }
                        )
                        state["history"] = history[-50:]
                        state["regime_state"] = {
                            "streak_zero": int(adaptive_info["streak_zero"]),
                            "rolling_avg": (
                                float(adaptive_info["rolling_avg"])
                                if adaptive_info.get("rolling_avg") is not None
                                else None
                            ),
                            "last_reset": (
                                self._extract_date_at_index(last_rows)
                                if adaptive_info["active_mode"] == "reset"
                                else state.get("regime_state", {}).get("last_reset")
                            ),
                            "active_mode": adaptive_info["active_mode"],
                            "reset_duration": int(
                                adaptive_info.get("reset_duration", 0)
                            ),
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
                    adaptive_info["active_mode"]
                    if adaptive_info
                    else state.get("regime_state", {}).get("active_mode", "normal")
                )
                # Hard Inversion Temporară: ștearsă — blacklist-ul temporar se
                # calcula, se loga și se arunca (pool-ul rămâne top-scor pur de
                # la oprirea filtrelor, 2026-07-08).
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la procesarea feedback-ului: {e}")
                self._adaptive_event = None
                self._adaptive_info = None
                self._adaptive_mode = "normal"
        else:
            # NU suprascriem dacă au fost setate extern (e.g. de backtester
            # care îi pasează regime_mode în mod manual).
            if not hasattr(self, "_adaptive_event"):
                self._adaptive_event = None
            if not hasattr(self, "_adaptive_info"):
                self._adaptive_info = None
            if not hasattr(self, "_adaptive_mode"):
                self._adaptive_mode = "normal"

        logging.info(
            f"[PIPELINE] Inițializare scoring (câștigător bench CPU) [pool_size={pool_size}, guarantee={guarantee}, max_variants={max_variants}, lookback={lookback}%]..."
        )

        # Numarul de randuri INAINTE de trunchierea pe lookback: feedback-ul
        # adaptiv compara acest numar cu lungimea completa a CSV-ului la rularea
        # urmatoare, deci trebuie sa fie tot lungimea completa.
        full_rows = int(len(self.data)) if self.data is not None else 0
        if lookback > 0 and self.data is not None and not self.data.empty:
            effective_rows = int(len(self.data) * (lookback / 100.0))
            effective_rows = max(effective_rows, 1)
            actual_lookback = effective_rows
            if effective_rows == 0:
                actual_lookback = 1  # Măcar o extragere
            logging.info(
                f"[PIPELINE] Aplic limită de istoric: Ultimele {lookback}% ({actual_lookback} extrageri)."
            )
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

                _gk = self._bench_game_key()
                _wn = get_winner_name(
                    _gk, pool_size=int(getattr(self, "_winner_pool_hint", 16))
                )
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

        # Penalizare după ultimele extrageri (opțiune UI; neutră ca valoare
        # așteptată). Aplicată ÎNAINTE de top-N, pe scorurile validate.
        _rp_n = int(recent_penalty_draws or 0)
        self.audit["recent_penalty"] = {
            "draws": _rp_n,
            "factor": float(recent_penalty_factor),
            "penalized": {},
        }
        if _rp_n > 0 and tfm_scores:
            tfm_scores, _pen = self.apply_recent_penalty(
                tfm_scores,
                self._draw_matrix,
                _rp_n,
                recent_penalty_factor,
                int(self.params["max_n"]),
            )
            if not has_usable_score_variance(tfm_scores):
                logging.warning(
                    "[PIPELINE] Penalizarea recentă a aplatizat scorurile — o ignor."
                )
                tfm_scores = self._get_timesfm_scores(context_len=actual_lookback)
                self.audit["recent_penalty"]["skipped_flat"] = True
            else:
                self.audit["recent_penalty"]["penalized"] = {
                    int(k): int(v) for k, v in _pen.items()
                }
                logging.info(
                    "[PIPELINE] Penalizare recentă: ultimele %d extrageri × %.2f → %d numere afectate.",
                    _rp_n,
                    float(recent_penalty_factor),
                    len(_pen),
                )

        if progress_cb:
            progress_cb(
                f"{_score_lbl} complet ({score_time / 1000:.1f}s). Construiesc pool...",
                45,
            )

        if "performance" not in self.audit:
            self.audit["performance"] = {}
        self.audit["performance"]["score_time_ms"] = round(score_time, 2)
        logging.info(f"[PIPELINE] Scoring ({_score_lbl}) timp: {score_time:.2f}ms")

        # Feedback adaptiv pe scoruri — DEZACTIVAT (cerere user: fără filtre).
        # Pool-ul = scor pur al metodei câștigătoare / ensemble.
        blacklist = set()
        self.audit["sim_depth_pct"] = sim_depth_pct
        self.audit["reduction_filter"] = {
            "combined_blacklist": [],
            "total_blocked": 0,
            "model_used": "DISABLED_ALL_FILTERS",
            "sim_depth_pct": sim_depth_pct,
            "disabled_by_user": True,
        }
        logging.info("[PIPELINE] Filtre automate dezactivate — pool = top-scor.")

        # Restrângere bază: preferință OPȚIONALĂ a utilizatorului (0 = oprit,
        # implicit). Fără avantaj statistic — vezi docstring-ul funcției.
        _max_n = int(self.params["max_n"])
        restrict_max = int(restrict_base_max or 0)
        restrict_min = int(restrict_base_min or 0)
        restrict_max = min(restrict_max, _max_n) if restrict_max > 0 else _max_n
        restrict_min = max(restrict_min, 1) if restrict_min > 0 else 1
        # Interval inversat (min > max) ar goli complet baza de candidați și ar
        # lăsa pool-ul pe seama fallback-ului. Îl ignorăm și consemnăm motivul,
        # în loc să producem tăcut un pool care nu respectă nicio setare.
        _draw_n_ticket = int(self.params.get("play_n", self.params["draw_n"]))
        _span = restrict_max - restrict_min + 1
        _reason = ""
        if restrict_min > restrict_max:
            _reason = f"interval inversat ({restrict_min} > {restrict_max})"
        elif _span < _draw_n_ticket:
            # Un interval mai îngust decât un bilet nu poate produce un bilet
            # jucabil. Wheeling-ul trateaza `len(pool) < pick` drept sistem
            # complet cu un singur bilet, deci fara garda de aici pipeline-ul
            # raporta [47, 48, 49] ca bilet 6/49 cu acoperire 100%.
            _reason = (
                f"interval prea îngust ({_span} numere) pentru un bilet de "
                f"{_draw_n_ticket}"
            )
        if _reason:
            self.audit["restrict_base"] = {
                "ignored": True,
                "reason": _reason,
                "min": restrict_min,
                "max": restrict_max,
            }
            logging.warning(
                "[PIPELINE] Restrângere de bază ignorată: %s.",
                _reason,
            )
        elif restrict_min > 1 or restrict_max < _max_n:
            _excluded = set(range(1, restrict_min)) | set(
                range(restrict_max + 1, _max_n + 1)
            )
            blacklist |= _excluded
            self.audit["restrict_base"] = {
                "min": restrict_min,
                "max": restrict_max,
                "excluded": sorted(_excluded),
            }
            logging.info(
                "[PIPELINE] Bază restrânsă la %d..%d (preferință utilizator, "
                "fără avantaj statistic demonstrat) — %d numere excluse.",
                restrict_min,
                restrict_max,
                len(_excluded),
            )

        # Limita de consecutive: opțiune a utilizatorului (0 = oprit, implicit în
        # motor). Se aplică pe clasament, după penalizare și restrângerea bazei.
        _mcr = max(0, int(max_consecutive_run or 0))
        self.hard_core = self._get_timesfm_pool(
            tfm_scores,
            pool_size=pool_size,
            blacklist=blacklist,
            max_consecutive_run=_mcr,
        )
        _cl = self.audit.get("consecutive_limit") or {}
        if _cl.get("removed") or _cl.get("relaxed"):
            logging.info(
                "[PIPELINE] Limită de consecutive %d (aplicată %d): scoase %s, "
                "intrate %s (preferință utilizator, fără avantaj statistic).",
                _mcr,
                int(_cl.get("applied") or 0),
                [n for n, _r in _cl.get("removed") or []],
                [n for n, _r in _cl.get("added") or []],
            )
        if len(self.hard_core) < int(self.params.get("play_n", self.params["draw_n"])):
            raise ValueError("Pool insuficient pentru un bilet valid după selecție")

        # Transparența pipeline-ului: snapshot la fiecare etapă (pentru afișare în UI).
        # Cronologia e: NQI_raw → Smart → Anti-Seq → POST-HOC (final).
        self.audit["pipeline_stages"] = {
            "1_nqi_raw": sorted(self.hard_core.copy()),
        }

        # Flow minimal (cerere user 2026-07-08): scoring → pool top-N → wheel.
        # Fără POST-HOC, anomaly filter sau alte rafinări automate. Limita de
        # consecutive e opțiune a utilizatorului, consemnată separat în audit.
        self.audit["pure_bench_mode"] = True
        self.audit["filters_disabled"] = True
        if len(self.hard_core) > pool_size:
            logging.warning(
                f"[PIPELINE] Nucleul dur avea {len(self.hard_core)} numere. Trunchiere la {pool_size} după scor."
            )
            # Trunchiere prin regula canonică (nu sortare proprie): la scoruri egale
            # decide numărul mare, exact ca bench-ul. Ramură defensivă — selectorul
            # întoarce deja cel mult pool_size numere.
            ranked = rank_by_score(
                {int(n): float(tfm_scores.get(n, 0.0)) for n in self.hard_core},
                pool_size,
            )
            self.hard_core = sorted(ranked)
        elif len(self.hard_core) < pool_size:
            logging.warning(
                f"[PIPELINE] Nucleul dur avea doar {len(self.hard_core)} numere. Pool_size solicitat: {pool_size}."
            )
        logging.info(
            f"[PIPELINE] Nucleu (Pool) generat prin {_score_lbl}: {self.hard_core}"
        )
        self.audit["pipeline_stages"]["2_smart_selector"] = sorted(
            self.hard_core.copy()
        )
        self.audit["pipeline_stages"]["3_anti_sequence"] = sorted(self.hard_core.copy())
        self.audit["pipeline_stages"]["4_post_hoc_final"] = sorted(
            self.hard_core.copy()
        )

        if self.game_type == "joker":
            logging.info(
                f"[PIPELINE] Scoring Urna 2 (Joker — câștigător bench / TimesFM)..."
            )
            j_scores = self._get_timesfm_scores(
                is_joker_drum=True, context_len=actual_lookback
            )
            if _rp_n > 0 and j_scores:
                _jk_hist = self._valid_joker_values()
                _j_pen, _jp = self.apply_recent_penalty(
                    j_scores,
                    _jk_hist.reshape(-1, 1) if _jk_hist.size else None,
                    _rp_n,
                    recent_penalty_factor,
                    20,
                )
                if has_usable_score_variance(_j_pen):
                    j_scores = _j_pen
                    self.audit["recent_penalty"]["penalized_urna2"] = {
                        int(k): int(v) for k, v in _jp.items()
                    }
                else:
                    logging.warning(
                        "[PIPELINE] Penalizarea recentă a aplatizat scorurile Urnei 2 — o ignor."
                    )
                    self.audit["recent_penalty"]["skipped_flat_urna2"] = True
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
                logging.info(
                    f"[PIPELINE] Nucleu Joker (Urna 2): {self.hard_core_joker}"
                )
                self.audit["joker_predictions"] = {
                    int(n): round(float(j_scores[n]), 4) for n in ranked_j
                }
            else:
                freq_joker = self.analyze_joker_frequency()
                if int(freq_joker.sum()) == 0:
                    # Nu există o observație validă în Urna 2. Alegerea unui număr
                    # dintr-un vector de zerouri ar fi doar tie-break arbitrar.
                    self.hard_core_joker = []
                    self.audit["joker_urna2_unavailable"] = True
                    self.audit["joker_predictions"] = {}
                    logging.warning(
                        "[PIPELINE] Urna 2 Joker nu are valori valide (1..20) — "
                        "nu atașez un număr Joker arbitrar pe bilete."
                    )
                else:
                    self.hard_core_joker = self._get_hard_core_joker(
                        freq_joker, pool_size=1
                    )
                    # Candidați informativi (top-5 după frecvență) și pe fallback,
                    # ca UI-ul să aibă aceeași sursă indiferent de path-ul de scoring.
                    self.audit["joker_predictions"] = {
                        int(i) + 1: int(freq_joker[i])
                        for i in np.argsort(freq_joker)[-5:][::-1]
                    }
                    logging.info(
                        f"[PIPELINE] Nucleu Joker (Fallback Frecvență): {self.hard_core_joker}"
                    )
        if progress_cb:
            progress_cb("Generare predicții finale (Wheeling)...", 70)

        logging.info("[PIPELINE] Începe generarea predicțiilor (Wheeling Set Cover)...")
        # Folosim tfm_scores dacă sunt disponibile, altfel fallback pe frecvență pentru wheeling
        wheeling_scores = (
            tfm_scores if tfm_scores else {i + 1: float(f) for i, f in enumerate(freq)}
        )
        # Contract cu UI: garanția EFECTIV folosită la wheel (identică cu cea cerută
        # în UI — nu mai există nicio escaladare pe drum). UI-ul o afișează ca atare.
        self.audit["wheel_guarantee_used"] = int(guarantee)
        # Condiția EFECTIV folosită: egală cu garanția = cover clasic; mai mare =
        # lotto design „guarantee dacă condition" (mai puține bilete, garanție
        # declanșată doar când cad `condition` numere din pool).
        self.audit["wheel_condition_used"] = int(wheel_condition)
        lines, coverage_pct = self.generate_predictions(
            guarantee=guarantee,
            max_variants=max_variants,
            scores=wheeling_scores,
            condition=wheel_condition,
        )

        # Nu se aplică NICIUN filtru pe variante după wheeling: orice eliminare ar
        # putea sparge garanția de acoperire. Dacă vreodată se reintroduce unul,
        # foloseşte `wheeling_methods.filter_preserving_coverage` (scoate bilete doar
        # dacă rămân redundante) şi revalidează cu `compute_coverage_pct`.
        logging.info(
            f"[PIPELINE] S-au generat {len(lines)} variante de joc. Acoperire: {coverage_pct}%"
        )

        # Numere din pool care nu apar pe NICIUN bilet. Un lotto design „t dacă p"
        # nu are nevoie de toate cele v poziții ca să-și țină garanția (18 din cele
        # 99 de designuri L livrate chiar nu le folosesc), deci acoperirea rămâne
        # onest 100%. Dar hiturile de POOL (`hits_union` din backtest) numără și
        # numerele acelea, care nu se joacă — diferența trebuie să fie vizibilă,
        # nu dedusă. Nu le forțăm pe bilete: o substituție ar strica exact
        # garanția pentru care a fost ales designul.
        _played = {int(n) for line in (lines or []) for n in line}
        _unplayed = sorted(int(n) for n in self.hard_core if int(n) not in _played)
        self.audit["pool_numbers_not_on_tickets"] = _unplayed
        if _unplayed:
            logging.warning(
                "[PIPELINE] %d numere din pool nu apar pe niciun bilet (%s) — "
                "garanția designului rămâne validă, dar hiturile de POOL le "
                "numără, iar biletele nu le pot prinde.",
                len(_unplayed),
                _unplayed,
            )

        if progress_cb:
            progress_cb("Validare rezultate...", 90)

        # Recalculăm statisticile pentru afișare corectă în UI (procente)
        final_freq = self.analyze_frequency()
        self.hard_core_stats = {
            int(num): int(final_freq[num - 1])
            for num in self.hard_core
            if num - 1 < len(final_freq)
        }

        if self.game_type == "joker":
            final_j_freq = self.analyze_joker_frequency()
            self.hard_core_joker_stats = {
                int(num): int(final_j_freq[num - 1])
                for num in self.hard_core_joker
                if num - 1 < len(final_j_freq)
            }

        p10, p90 = (
            np.percentile(final_freq, [10, 90]) if final_freq.size else (0.0, 0.0)
        )
        _play_n = int(self.params.get("play_n", self.params["draw_n"]))
        g_range = [p10 * _play_n, p90 * _play_n]

        context = {"first_3": [], "last_3": []}
        if self.data is not None and not self.data.empty:
            draw_n = int(self.params.get("draw_n", 6))

            def extract_draws(df_subset):
                draws = []
                for _, row in df_subset.iterrows():
                    d = {
                        "date": str(row.get("date", "")).split()[0]
                        if "date" in row
                        else "N/A",
                        "numbers": [],
                        "joker": None,
                    }
                    n_cols = sorted(
                        [
                            c
                            for c in df_subset.columns
                            if str(c).lower().startswith("n")
                            and str(c).lower() != "numbers"
                        ],
                        key=lambda x: int(
                            "".join(ch for ch in str(x) if ch.isdigit()) or "0"
                        ),
                    )
                    nums = [row[c] for c in n_cols if pd.notna(row.get(c))]
                    d["numbers"] = [int(x) for x in nums][:draw_n]
                    if "joker" in df_subset.columns and pd.notna(row.get("joker")):
                        d["joker"] = int(row["joker"])
                    draws.append(d)
                return draws

            context["first_3"] = extract_draws(self.data.head(3))
            context["last_3"] = extract_draws(self.data.tail(3))

        context["coverage_pct"] = coverage_pct
        context["wheel_condition"] = int(wheel_condition)
        context["recent_penalty"] = {
            "draws": _rp_n,
            "factor": float(recent_penalty_factor),
        }
        context["max_consecutive_run"] = {
            "requested": _mcr,
            "applied": int(_cl.get("applied") or 0),
        }
        # Necesar UI-ului ca să atribuie corect cauza unei acoperiri <100%: limita
        # de variante SAU garanție degenerată. Fără el, mesajul acuza mereu limita,
        # inclusiv când era deja 0 (nelimitat), și sfătuia „pune 0" fără efect.
        context["max_variants"] = int(max_variants)

        if progress_cb:
            progress_cb("Pipeline complet!", 100)

        # --- TRACK POOL VARIATION ---
        # Pașii de walk-forward / backtest NU au voie să scrie aici: cheia e
        # `{joc}_{pool}` — EXACT cheia de producție. Cei ~1940 de pași ai
        # unui ciclu WF suprascriau intrarea reală, iar `pool_variation` din raport
        # compara pool-ul curent cu un pool dintr-un punct istoric arbitrar. În plus,
        # cele ~25 de procese WF scriau concurent același `.tmp` (nume fix în
        # ui_shared.atomic_write_*) → 216 linii „Eroare la tracker-ul de variație"
        # în loto.log între 2026-07-06 și 2026-08-08.
        if track_pool_variation:
            try:
                from pathlib import Path

                history_file = Path(
                    os.environ.get("LOTO_POOL_HISTORY_FILE", "pool_history.json")
                )
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
                            raise ValueError(
                                f"structură neașteptată: {type(history).__name__}"
                            )
                    except (
                        json.JSONDecodeError,
                        ValueError,
                        UnicodeDecodeError,
                    ) as exc:
                        logging.warning(
                            "[PIPELINE] pool_history.json corupt (%s) → îl reconstruiesc.",
                            exc,
                        )
                        history = {}

                hist_key = f"{self.game_type}_{pool_size}"
                legacy_key = f"{hist_key}_p1"
                last_pool = (history.get(hist_key, {}) or {}).get("pool", [])
                if not last_pool:
                    last_pool = (history.get(legacy_key, {}) or {}).get("pool", [])
                # Migrare unică: elimină intrările vechi cu sufix de fază.
                history = {
                    k: v
                    for k, v in history.items()
                    if not (str(k).endswith("_p1") or str(k).endswith("_p2"))
                }

                pool_variation = {}
                if last_pool:
                    added = sorted(list(set(self.hard_core) - set(last_pool)))
                    removed = sorted(list(set(last_pool) - set(self.hard_core)))
                    pool_variation = {
                        "added": added,
                        "removed": removed,
                        "changed": bool(added or removed),
                    }

                history[hist_key] = {
                    "pool": self.hard_core,
                    "date": datetime.now().isoformat(),
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
                    data_rows=full_rows,
                )
                summary = get_state_summary(self.game_type, pool_size)
                self.audit["adaptive_state"] = {
                    "event": adaptive_event,
                    "active_mode": self._adaptive_mode,
                    "last_hits": summary.get("last_hits"),
                    "streak_zero": summary.get("streak_zero"),
                    "rolling_avg": summary.get("rolling_avg"),
                    "baseline": round(summary.get("baseline", 0.0), 3),
                    "missed": (adaptive_info.get("missed") if adaptive_info else []),
                    "false_positives": (
                        adaptive_info.get("false_positives") if adaptive_info else []
                    ),
                }
            except Exception as e:
                logging.error(f"[ADAPTIVE] Eroare la persistarea pool-ului: {e}")

        # === Hit Forecast Diagnostic — baseline matematic + recomandare pool size ===
        # Calculează P(k+ hits) pentru pool RANDOM și recomandă pool minim
        # pentru ≥3 evenimente 3+/4+/5+. UI-ul citește audit.hit_forecast.
        try:
            n_recent_for_forecast = (
                max(int(len(self.data) * 0.05), 1) if self.data is not None else 100
            )
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
