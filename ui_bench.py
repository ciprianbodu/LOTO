"""Bench leaderboard rendering."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from nicegui import ui

from ui_runtime import *
from ui_shared import PROJECT_ROOT, render_html_safe

logger = logging.getLogger("app_nicegui")

# Familia din registry (`METHODS[name][1]`) → etichetă lizibilă în clasament.
# TOATE metodele din registry sunt numpy/scipy pur: nicio familie nu mai are voie
# să pretindă scikit-learn, statsmodels, XGBoost, LightGBM sau CatBoost — acele
# dependențe nu mai sunt importate de nicio metodă. Lista trebuie ținută sincron
# cu familiile reale; una necunoscută se afișează ca atare, nu se maschează.
_FAMILY_LIBRARY = {
    "baseline": "baseline (numpy)",
    "recency": "recență (numpy)",
    "gap": "goluri (numpy)",
    "timeseries": "serii de timp (numpy)",
    "transition": "tranziții (numpy)",
    "cooccurrence": "co-apariție (numpy)",
    "graph": "graf (numpy)",
    "similarity": "similaritate (numpy)",
    "structure": "vecinătate numerică (numpy)",
    "learning": "învățare (numpy+scipy)",
    "ensemble": "ansamblu (mix de metode)",
}


def _method_library(name: str, family: str = "") -> str:
    """Librăria/categoria lizibilă a metodei. Din `family` (preferat) sau din nume (fallback)."""
    f = (family or "").strip().lower()
    if f:
        return _FAMILY_LIBRARY.get(f, family)  # familia brută dacă n-o recunoaștem
    # Fallback pe nume: rândurile vechi din folds.csv pot avea `family` gol.
    # Îl rezolvăm din registry, nu din prefixe lexicale — vechile ramuri `ml_*`,
    # `croston_classic`, `croston_sba` numeau metode care nu mai există.
    n = (name or "").strip().lower()
    if n:
        try:
            from loto_enterprise.benchmark.methods import METHODS as _METHODS_NOW

            meta = _METHODS_NOW.get(n)
        except Exception:  # noqa: BLE001
            meta = None
        if meta:
            return _FAMILY_LIBRARY.get(meta[1], meta[1])
    return "necunoscută (metodă absentă din registry)"


# Eticheta UI/worker → cheia exactă din folds.csv / best_methods.json
_LABEL_TO_FOLDS_GAME = {
    "6/49": "loto_6_49",
    "5/40": "loto_5_40",
    "joker": "joker_urna1",
}
# Pool-ul de BAZĂ al fiecărui joc (= numere pe bilet; coloana fără `_kN`), nu
# numărul de numere extrase: 5/40 extrage 6, dar biletul și pool-ul de bază au 5.
_BENCH_DRAW_N = {
    "loto_6_49": 6,
    "loto_5_40": 5,
    "joker_urna1": 5,
    "joker_urna2": 1,
}


def _baseline_methods() -> frozenset[str]:
    """Metodele care sunt DOAR baseline de referință, NU candidați de producție.

    Trebuie să rămână SINCRON cu excluderea din decizie (decision.py:
    `methods = [m for m in ... if m not in EXCLUDED_FROM_PRODUCTION]`). Preferăm
    constanta din decision.py dacă există; altfel fallback identic cu ce face
    decizia azi.
    NB: `frequency` are family="baseline" în folds.csv, DAR decizia NU o exclude
    (e și fallback-ul de scoring în producție) → rămâne candidat aici."""
    try:
        from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION as _EX

        return frozenset(str(m) for m in _EX)
    except Exception:  # noqa: BLE001
        return frozenset(
            {
                "random",
                "neighbor_adjacent",
                "repeat_last_draw",
                "rwr_last_draw",
                "haar_multiscale",
            }
        )


def _decision_entry(folds_game_key: str, pool: int) -> dict:
    """Intrarea deciziei pentru (joc, pool) din best_methods.json (`auto_pilot_per_pool[kN]`).

    Folosește aceeași substituire nearest-k ca producția; altfel scorerul venea
    de la k10, dar badge-urile low_confidence/consistency din UI căutau exact k11
    și afișau metadate goale. `_load_config` invalidează cache-ul pe mtime."""
    try:
        from loto_enterprise.core.method_selector import _auto_pilot_entry, _load_config

        g = (_load_config().get("games") or {}).get(folds_game_key) or {}
        e = _auto_pilot_entry(g, int(pool))
        return e if isinstance(e, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _decision_low_confidence(entry: dict) -> bool | None:
    """Decizia a căzut pe ramura de FALLBACK (nicio metodă n-a bătut random consistent)?

    True/False, sau None dacă nu se poate ști. Citim `low_confidence` (cheie nouă)
    și, pentru best_methods.json scrise de decizia VECHE (fără cheia asta),
    deducem din `qualifying_methods == 0` — decision.py scrie 0 exact pe ramurile
    de fallback (`len(qualifying)` e 0 acolo)."""
    if not entry:
        return None
    if "low_confidence" in entry:
        return bool(entry["low_confidence"])
    if "qualifying_methods" in entry:
        try:
            return int(entry["qualifying_methods"]) == 0
        except Exception:  # noqa: BLE001
            return None
    return None


def _consistency_pct(entry: dict) -> int:
    """Pragul de consistență al deciziei, în %, ca ÎNTREG (60 = „≥60% din ferestre").

    Preferăm valoarea stampilată în best_methods.json (`consistency_threshold`,
    scrisă chiar de decizia care a produs intrarea), apoi constanta din
    decision.py; ultimul resort 60 = `CONSISTENCY_THRESHOLD` actual."""
    try:
        v = entry.get("consistency_threshold")
        if v is not None:
            return int(round(float(v) * 100))
    except Exception:  # noqa: BLE001
        pass
    try:
        from loto_enterprise.benchmark.decision import CONSISTENCY_THRESHOLD

        return int(round(float(CONSISTENCY_THRESHOLD) * 100))
    except Exception:  # noqa: BLE001
        return 60


def _bench_structural_exclusion(
    grp: pd.DataFrame,
    metric: str,
    pool: int,
    expected_pcts: set[int],
) -> str:
    """Motivul pentru care o metodă măsurată nu este candidat de decizie.

    Oglindește cele două porți structurale din ``decision.py``: toate ferestrele
    comune trebuie să existe, iar top-K nu trebuie să fie ales preponderent de
    tie-break-ul numeric. Returnează text gol când metoda rămâne eligibilă.
    """
    reasons: list[str] = []
    if expected_pcts and "percentile" in grp.columns and metric in grp.columns:
        valid = grp[pd.to_numeric(grp[metric], errors="coerce").notna()]
        have = {
            int(p) for p in pd.to_numeric(valid["percentile"], errors="coerce").dropna()
        }
        missing = sorted(expected_pcts - have)
        if missing:
            reasons.append("ferestre lipsă: " + ", ".join(f"{p}%" for p in missing))

    tie_col = f"tiebreak_k{int(pool)}"
    if tie_col in grp.columns:
        vals = pd.to_numeric(grp[tie_col], errors="coerce").dropna()
        if not vals.empty:
            frac = float(vals.mean())
            try:
                from loto_enterprise.benchmark.decision import TIEBREAK_MAX_FRACTION

                limit = float(TIEBREAK_MAX_FRACTION)
            except Exception:  # noqa: BLE001
                limit = 0.5
            if frac >= limit:
                reasons.append(
                    f"scoruri egale la limita top-{int(pool)}: {frac * 100:.1f}% "
                    f"blocuri, în medie pe ferestre (maxim admis < {limit * 100:.0f}%)"
                )
    return "; ".join(reasons)


def _wilson_pooled_rate(grp, metric: str) -> float | None:
    """Limita inferioară Wilson: rata POOLED pe ferestre, dovada = extrageri DISTINCTE.

    ACEEAȘI metrică pe care decision.py alege câștigătorul: TOATĂ agregarea
    (rata pooled Σ rate·n / Σ n + n EFECTIV Kish, ferestrele fiind sufixe
    CUIBĂRITE) e IMPORTATĂ din `decision.pooled_wilson_distinct`, sursa unică de
    adevăr — nu re-implementa aici. None = incalculabil (fără coloane n / import
    eșuat)."""
    try:
        from loto_enterprise.benchmark.decision import pooled_wilson_distinct
    except Exception:  # noqa: BLE001
        return None
    try:
        v = pooled_wilson_distinct(grp, metric)
    except Exception:  # noqa: BLE001
        return None
    return float(v) if v is not None else None


def _last_generation_bench_info(folds_game_key: str, pool: int | None = None) -> dict:
    """Ensemble-ul EFECTIV din ultima generare (`audit.bench_winner`), dacă există.

    Clasamentul citea `get_ensemble_for_game` cu plafon 3, deci lista afișată
    putea avea mai mulți membri decât cei jucați: producția cere
    `ENSEMBLE_MAX_METHODS` (azi 1) în `engine/scoring.py`, iar din cei rămași
    intră în pool doar membrii ACTIVI după combine. Preferăm auditul ultimei
    generări; `{}` dacă n-a rulat încă Generate.

    Dacă `pool` e dat, acceptăm numai aceeași geometrie; altfel prima potrivire.
    """
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return {}
    bundle = results[0]
    if not bundle:
        return {}
    for _fname, outs in bundle:
        if not isinstance(outs, dict):
            continue
        for _g, data in outs.items():
            if not isinstance(data, dict):
                continue
            blob = _primary_pool_data(data)
            bw = (blob.get("audit") or {}).get("bench_winner") or {}
            info = bw.get(folds_game_key)
            if not (
                isinstance(info, dict) and (info.get("ensemble") or info.get("method"))
            ):
                continue
            if pool is None:
                return info
            ph = info.get("pool_hint")
            if ph is None:
                ph = 1 if folds_game_key == "joker_urna2" else blob.get("pool_size")
            try:
                if ph is not None and int(ph) == int(pool):
                    return info
            except (TypeError, ValueError):
                pass
    return {}


def _render_bench_leaderboard_slice(
    df: pd.DataFrame,
    folds_game_key: str,
    pool: int,
    section_label: str,
    top_n: int = 10,
) -> None:
    """Un clasament bench pentru (joc bench, pool K) — fără amestec între joker urna1/urna2."""
    sub = df[df["game"].astype(str) == folds_game_key]
    if "is_random" in sub.columns:
        sub = sub[sub["is_random"] == False]  # noqa: E712
    if "failed" in sub.columns:
        sub = sub[sub["failed"] != True]  # noqa: E712
    if sub.empty:
        return
    try:
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET as _T
    except Exception:  # noqa: BLE001
        _T = 3

    # Urna 2 este strict top-1, separat de ținta configurabilă 3+/4+ pentru
    # jocurile de pool. Coloana fără `_kN` este rata pool-ului de bază și nu e
    # comparabilă cu un alt pool.
    def _has(c):
        return c in sub.columns and sub[c].notna().any()

    _draw_n = _BENCH_DRAW_N.get(folds_game_key)
    _is_single_pick = _draw_n == 1
    if _is_single_pick:
        _T = 1
    else:
        # Aceeași țintă per joc ca decizia: 5/40 rămâne pe 4+.
        from loto_enterprise.benchmark.hit_target import game_hit_target

        _T = game_hit_target(folds_game_key, _T)
    _shown_t, metric = _T, None
    _target_candidates = [f"rate_{_T}plus_k{pool}"]
    if _draw_n is not None and int(pool) == int(_draw_n):
        _target_candidates.append(f"rate_{_T}plus")
    for _c in _target_candidates:
        if _has(_c):
            metric = _c
            break
    if metric is None and not _is_single_pick:
        _fallback_candidates = [f"rate_4plus_k{pool}"]
        if _draw_n is not None and int(pool) == int(_draw_n):
            _fallback_candidates.append("rate_4plus")
        for _c in _fallback_candidates:
            if _has(_c):
                _shown_t, metric = 4, _c
                break
    has_target_rate = metric is not None
    if metric is None:
        metric = "avg_hits_topk"
    if metric not in sub.columns:
        return
    has_family = "family" in sub.columns

    def _rate_for(grp, n):
        """Rata de ≥n pentru o metodă, POOLED pe extragerile evaluate (coloana pe pool).

        Ponderarea e obligatorie, nu cosmetică: ferestrele sunt CUIBĂRITE (fiecare e
        ultimele P% din istoric — vezi `runner.run_benchmark`), deci au dimensiuni de
        ordine de mărime diferite (6/49: 258 / 772 / 1544 / 2492 extrageri). O medie
        neponderată dă aceeași greutate ferestrei de 258 ca celei de 2492 și fabrică
        astfel „lift-uri" care nu există în datele pooled — exact metrica pe care se
        ia decizia (`_wilson_pooled_rate` / decision.py) e pooled pe același n.
        Agregarea e IMPORTATĂ din `decision.pooled_rate_and_neff` (sursa unică):
        n_eval per rând cu fallback pe n_test — o alegere pe FRAME ar arunca tăcut
        rândurile vechi dintr-un folds.csv mixt. Fallback pe media neponderată doar
        dacă lipsesc ambele coloane de n (folds foarte vechi), ca înainte."""
        try:
            from loto_enterprise.benchmark.decision import pooled_rate_and_neff as _prn
        except Exception:  # noqa: BLE001
            _prn = None
        candidates = [f"rate_{n}plus_k{pool}"]
        if _draw_n is not None and int(pool) == int(_draw_n):
            candidates.append(f"rate_{n}plus")
        for c in candidates:
            if c not in grp.columns:
                continue
            if _prn is not None:
                try:
                    got = _prn(grp, c)
                except Exception:  # noqa: BLE001
                    got = None
                if got is not None:
                    return float(got[0])
            v = float(grp[c].mean())
            if v == v:  # nu e NaN
                return v
        return None

    # TIE-BREAK IDENTIC cu decizia (decision.py: `qualifying.sort(key=(Wilson_lb,
    # w_lift, consistență))`). Egalitățile EXACTE pe Wilson sunt masive (succesele
    # sunt întregi → multe metode au aceeași proporție pooled), deci fără aceleași
    # chei secundare ordinea din UI ar diverge de decizie pe ~un sfert din poziții.
    # Gate-ul folosește chiar rata T+ care produce Wilson-ul, nu media kN:
    # altfel o metodă cu mai multe 2-hit-uri putea fi declarată „consistentă”
    # deși pierdea față de random exact la pragul 3+/4+ configurat.
    _base_col = f"k{pool}"
    _gate_col = metric if has_target_rate else None
    _lift_fn = _beat_fn = None
    _pooled_mean_fn = None
    _rnd_frame = None
    # Referința porții = ACEEAȘI ca în decision.py: rata așteptată hipergeometric
    # a unui pool aleator (determinist), nu realizarea `random` din folds.csv.
    _gate_baseline = None
    if _gate_col is not None:
        try:
            from loto_enterprise.benchmark.decision import (
                _weighted_mean_lift as _lift_fn,
                _windows_method_beats_random as _beat_fn,
                pooled_mean as _pooled_mean_fn,
            )

            _rnd_frame = sub[sub["method"] == "random"]
            _gate_baseline = _random_rate_hypergeo(folds_game_key, pool, _shown_t)
            if _rnd_frame.empty and _gate_baseline is None:
                _lift_fn = _beat_fn = None
        except Exception:  # noqa: BLE001
            _lift_fn = _beat_fn = None

    _BASE = _baseline_methods()
    try:
        from loto_enterprise.benchmark.methods import METHODS as _METHODS_NOW
        from loto_enterprise.benchmark.curated import load_per_game as _load_pg

        _alive_methods = set(_METHODS_NOW)
        _pg_only = set(_load_pg().get(folds_game_key) or [])
    except Exception:  # noqa: BLE001
        _alive_methods = None
        _pg_only = set()
    rows = []
    _expected_pcts: set[int] = set()
    if has_target_rate and "percentile" in sub.columns:
        _valid_target = sub[pd.to_numeric(sub[metric], errors="coerce").notna()]
        _expected_pcts = {
            int(p)
            for p in pd.to_numeric(
                _valid_target["percentile"], errors="coerce"
            ).dropna()
        }
    _conf_ok = False  # măcar o metodă are Wilson calculabil → sortăm ca decizia
    _lift_ok = False  # lift+consistență calculabile → tie-break identic cu decizia
    _current_dec = {}
    # Rândurile (Wilson, lift, consistență per metodă) se recalculează DOAR când
    # folds.csv s-a schimbat: tick-ul de 1s re-randa clasamentul în timpul
    # bench-ului și refăcea ~0,6 s de pandas pe event-loop la fiecare secundă.
    _memo_key = (
        _BENCH_FOLDS_CACHE.get("signature"),
        folds_game_key,
        int(pool),
        metric,
        _shown_t,
        int(len(sub)),
        tuple(sorted(_alive_methods or ())),
        tuple(sorted(_pg_only)),
    )
    _memo = _LB_ROWS_MEMO.get(_memo_key) if _memo_key[0] is not None else None
    if _memo is not None:
        rows, _conf_ok, _lift_ok, _current_dec = (
            list(_memo[0]),
            _memo[1],
            _memo[2],
            _memo[3],
        )
    else:
        for m, grp in sub.groupby("method"):
            # Folds vechi pot lista metode eliminate — nu le arăta în clasament
            # (decizia le sare deja; UI trebuie să rămână aliniat).
            if _alive_methods is not None and str(m) not in _alive_methods:
                continue
            # Clasament per joc = lista curated per_game (plus baseline random).
            if _pg_only and str(m) not in _pg_only and str(m) not in _BASE:
                continue
            score = float(grp[metric].mean())
            # Afișăm media la pool-ul CERUT (k{pool}), nu `avg_hits_topk`, care
            # este mereu pool-ul de bază k5/k6. Ferestrele au dimensiuni diferite,
            # deci media este pooled pe n_eval, ca ratele și decizia.
            _avg_pool = (
                _pooled_mean_fn(grp, _base_col) if _pooled_mean_fn is not None else None
            )
            if _avg_pool is None:
                _avg_pool = (
                    float(grp[_base_col].mean())
                    if _base_col in grp.columns
                    else float(grp["avg_hits_topk"].mean())
                    if "avg_hits_topk" in grp.columns
                    else score
                )
            avg = float(_avg_pool)
            fam = ""
            if has_family:
                _f = grp["family"].dropna().astype(str)
                fam = _f.iloc[0] if not _f.empty else ""
            # Wilson doar pe RATE (proporții). Când metrica e fallback-ul avg_hits_topk
            # (folds vechi fără coloane rate_*), nu e proporție → nu are sens.
            conf = _wilson_pooled_rate(grp, metric) if has_target_rate else None
            if conf is not None:
                _conf_ok = True
            w_lift = cons = None
            if _lift_fn is not None:
                try:
                    _nb, _nt = _beat_fn(grp, _rnd_frame, _gate_col, _gate_baseline)
                    if _nt > 0:
                        w_lift = float(
                            _lift_fn(grp, _rnd_frame, _gate_col, _gate_baseline)
                        )
                        cons = _nb / _nt
                        _lift_ok = True
                except Exception:  # noqa: BLE001
                    w_lift = cons = None
            # media pe coloana pool-ului (k{pool}) — EXACT cheia secundară a ramurii
            # de fallback din decision.py (base_col), nu avg_hits_topk (K = draw_n).
            _base_avg = avg
            _structural_reason = _bench_structural_exclusion(
                grp,
                metric,
                pool,
                _expected_pcts,
            )
            rows.append(
                (
                    m,
                    score,
                    avg,
                    _method_library(m, fam),
                    _rate_for(grp, _shown_t if _is_single_pick else 3),
                    _rate_for(grp, 4),
                    conf,
                    w_lift,
                    cons,
                    _base_avg,
                    _structural_reason,
                )
            )
        if _draw_n is not None and _base_col in sub.columns:
            try:
                from loto_enterprise.benchmark.decision import (
                    decide_optimal_config_for_pool,
                )

                _current_dec = decide_optimal_config_for_pool(
                    sub,
                    folds_game_key,
                    pool,
                    _draw_n,
                )
            except Exception:  # noqa: BLE001
                _current_dec = {}
        if _memo_key[0] is not None:
            # Doar semnătura curentă a fișierului rămâne în memo (3 felii × pool).
            for _k in [k for k in _LB_ROWS_MEMO if k[0] != _memo_key[0]]:
                _LB_ROWS_MEMO.pop(_k, None)
            _LB_ROWS_MEMO[_memo_key] = (list(rows), _conf_ok, _lift_ok, _current_dec)
    # ORDONARE = ACEEAȘI metrică ȘI aceleași chei secundare ca decizia (Wilson pe
    # rata pooled cu n efectiv → lift mediu ponderat vs random → consistență). Fără
    # decision.py / fără coloana k{pool} / fără rândurile `random` → cădem pe
    # (Wilson, rată brută, avg_hits) și eticheta o spune explicit.
    _dec = _current_dec or _decision_entry(folds_game_key, pool)
    _dec_low = _decision_low_confidence(_dec)
    _cons_pct = _consistency_pct(_dec)
    _fail_gate: set[str] = set()
    _structural_fail = {
        str(r[0]): str(r[10]) for r in rows if r[10] and r[0] not in _BASE
    }
    _gate_applied = False

    def _sort_key_lift(r):
        return (
            (r[6] if r[6] is not None else -1.0),
            (r[7] if r[7] is not None else -1e18),
            (r[8] if r[8] is not None else -1.0),
        )

    def _sort_key_fallback(r):
        # Ramura de fallback din decision.py: (Wilson, media k{pool}) — fără lift.
        # r[9] = media pe coloana pool-ului (base_col), aceeași cheie ca decizia;
        # înainte se folosea r[2] (avg la K=draw_n) → la egalitate de Wilson
        # ordinea afișată diverge de decizia reală.
        return ((r[6] if r[6] is not None else -1.0), r[9])

    if _lift_ok and _dec_low is True:
        rows.sort(key=_sort_key_fallback, reverse=True)
    elif _lift_ok:
        rows.sort(key=_sort_key_lift, reverse=True)
    else:
        rows.sort(
            key=lambda r: ((r[6] if r[6] is not None else -1.0), r[1], r[2]),
            reverse=True,
        )
    # Ordinea PE SCOR, înainte ca poarta de consistență să rearanjeze lista.
    # Poziția baseline-ului se calculează pe ASTA: reordonarea de mai jos pune
    # baseline-urile la coadă NECONDIȚIONAT, deci calculată pe lista rearanjată
    # ieșea mereu „ultimul din N+1", indiferent de cât de bine punctase random —
    # exact invers față de mesajul pentru care există rândul ăla.
    rows_by_score = list(rows)
    # Porțile STRUCTURALE preced poarta de consistență și sortarea în decizie.
    # O metodă cu Wilson mare, dar cu ferestre lipsă ori cu ≥50% tăieturi top-K
    # în scoruri egale, rămâne vizibilă ca diagnostic, însă nu primește rang și
    # nu poate deveni capul clasamentului eligibil.
    _bases_r = [r for r in rows if r[0] in _BASE]
    _eligible_r = [
        r for r in rows if r[0] not in _BASE and r[0] not in _structural_fail
    ]
    _structural_r = [r for r in rows if r[0] not in _BASE and r[0] in _structural_fail]
    # Poarta de consistență CA LA DECIZIE: calificatele (bat random în ≥60%
    # ferestre) întâi. Fără asta, #1 din listă putea fi o metodă cu Wilson mare
    # care n-a trecut gate-ul, iar 🏆 era altcineva.
    if _lift_ok and _dec_low is not True:
        _thresh = float(_cons_pct) / 100.0
        _qual = [r for r in _eligible_r if r[8] is not None and r[8] >= _thresh]
        _fail = [r for r in _eligible_r if r[8] is None or r[8] < _thresh]
        if _qual:
            _qual.sort(key=_sort_key_lift, reverse=True)
            _fail.sort(key=_sort_key_lift, reverse=True)
            _structural_r.sort(key=_sort_key_lift, reverse=True)
            _bases_r.sort(key=_sort_key_lift, reverse=True)
            rows = _qual + _fail + _structural_r + _bases_r
            _fail_gate = {r[0] for r in _fail}
            _gate_applied = True
        else:
            rows = _eligible_r + _structural_r + _bases_r
    else:
        # Inclusiv ramura low-confidence: fallback-ul din decision.py sare
        # aceleași metode structural invalide înainte să aleagă după Wilson.
        rows = _eligible_r + _structural_r + _bases_r
    if "ranked_methods" in _current_dec:
        # Sursa de adevăr pentru eligibilitate și ordonare este chiar decizia
        # pe snapshot-ul afișat, nu best_methods.json dintr-un bench anterior.
        _positions = {m: i for i, m in enumerate(_current_dec["ranked_methods"])}
        _ranked = sorted(
            (r for r in _eligible_r if r[0] in _positions),
            key=lambda r: _positions[r[0]],
        )
        _unqualified = [r for r in _eligible_r if r[0] not in _positions]
        rows = _ranked + _unqualified + _structural_r + _bases_r
        _fail_gate = {r[0] for r in _unqualified}
        _gate_applied = True
    if not rows:
        return
    # Baseline-urile („random") NU sunt candidați: rămân vizibile ca reper, dar nu
    # primesc rang și nu intră în „Top N din M metode".
    competitors = [
        r for r in rows if r[0] not in _BASE and r[0] not in _structural_fail
    ]
    measured_methods = [r for r in rows if r[0] not in _BASE]
    if not competitors:
        with ui.expansion(f"Clasament bench — {section_label}", value=True).classes(
            "w-full"
        ):
            ui.label(
                "Nicio metodă eligibilă în acest snapshot; decizia folosește fallback."
            ).classes("text-warning")
            for r in measured_methods[:top_n]:
                ui.label(f"⛔ {r[0]}: {_structural_fail[r[0]]}").classes("text-caption")
        return
    # Slice afișat: primele `top_n` CANDIDATE + baseline-urile care cad printre ele.
    top_idx: list[int] = []
    _n_comp = 0
    for _i, rec in enumerate(rows):
        top_idx.append(_i)
        if rec[0] not in _BASE:
            _n_comp += 1
            if _n_comp >= top_n:
                break
    top_rows = [rows[i] for i in top_idx]
    _n_shown = _n_comp
    label = (
        "rata top-1 (1/1)"
        if _is_single_pick and has_target_rate
        else f"rata {_shown_t}+ @ pool {pool}"
        if has_target_rate and metric.endswith(f"_k{pool}")
        else f"rata {_shown_t}+ numere ghicite"
        if has_target_rate
        else "medie hituri / extragere"
    )
    # Eticheta spune EXACT cât face ordonarea: „ca decizia" doar când și tie-break-ul
    # secundar e cel al deciziei (lift + consistență), altfel nu promite identitate.
    if _conf_ok and _lift_ok and _dec_low is True:
        label += (
            " · fallback ca decizia (eligibilitate structurală → Wilson → "
            "medie hituri; nicio metodă n-a trecut poarta)"
        )
    elif _conf_ok and _lift_ok:
        if _gate_applied:
            label += (
                f" · sortat ca decizia (eligibilitate structurală → poartă "
                f"≥{_cons_pct}% vs random pe aceeași rată T+ → Wilson → "
                "lift T+ → consistență)"
            )
        else:
            label += (
                " · sortat ca decizia (eligibilitate structurală → Wilson → "
                "lift T+ → consistență)"
            )
    elif _conf_ok:
        label += " · sortat după Wilson (tie-break ≠ decizia: rată brută, nu lift)"
    else:
        label += " · sortat după rata brută"
    # Baseline-ul PUR aleator (hipergeometric) la acest pool — afișat O DATĂ în titlu
    # + multiplicator pe fiecare rată. Onestitate: „3+: 10%" pare edge, dar hazardul
    # singur dă ~9% la pool 10 pe 6/49 → diferența reală e mică (zgomot).
    _rnd3 = _random_rate_hypergeo(
        folds_game_key, pool, _shown_t if _is_single_pick else 3
    )
    _rnd4 = _random_rate_hypergeo(folds_game_key, pool, 4)
    _rnd_t = _random_rate_hypergeo(folds_game_key, pool, _shown_t)
    if has_target_rate and _rnd_t is not None:
        label += f" · baseline random = {_rnd_t * 100:.2f}%"
    if _structural_fail:
        label += f" · {len(_structural_fail)} excluse structural"
    winner = competitors[
        0
    ]  # capul clasamentului (doar candidați, baseline-urile excluse)
    # Metoda EFECTIV aleasă pentru pool (best_methods.json) — poate diferi de #1 din
    # mai multe motive (ramura de fallback, decizie mai veche decât folds.csv).
    try:
        from loto_enterprise.core.method_selector import get_winner_name

        chosen_name = get_winner_name(folds_game_key, pool)
    except Exception:  # noqa: BLE001
        chosen_name = winner[0]
    # Dacă există generare recentă, 🎯 = membrul ACTIV din audit (nu scorer
    # stale din best_methods.json eliminat din METHODS). Un audit vechi care
    # numește un filtru EXCLUDED nu are voie să devină 🎯.
    _gen_early = _last_generation_bench_info(folds_game_key, pool)
    _gen_usable = (
        isinstance(_gen_early, dict)
        and _gen_early.get("method")
        and str(_gen_early["method"]) not in _BASE
    )
    if _gen_usable:
        chosen_name = str(_gen_early["method"])
    _saved_entry = _decision_entry(folds_game_key, pool)
    if _gen_usable:
        _chosen_caption = "Metoda din ultima generare"
    elif _saved_entry:
        _chosen_caption = "Metoda din decizia salvată"
    else:
        _chosen_caption = "Fără decizie salvată — fallback frequency"

    def _row(i, rec):
        """`i=None` → rând de BASELINE (referință, fără rang și fără pretenția de candidat)."""
        m, score, avg, lib = rec[:4]
        r3, r4, conf = rec[4], rec[5], rec[6]
        if has_target_rate:
            parts = []
            # Primul = criteriul REAL de ordonare/decizie (Wilson pooled); ratele brute
            # rămân ca informație secundară.
            if conf is not None:
                parts.append(
                    f"Wilson {'top-1' if _is_single_pick else f'{_shown_t}+'}: {conf * 100:.2f}%"
                )
            if _is_single_pick and r3 is not None:
                _m1 = f" ({r3 / _rnd3:.2f}x random)" if _rnd3 else ""
                parts.append(f"brut top-1: {r3 * 100:.1f}%{_m1}")
            elif r3 is not None:
                _m3 = f" ({r3 / _rnd3:.2f}x random)" if _rnd3 else ""
                parts.append(f"brut 3+: {r3 * 100:.1f}%{_m3}")
            if r4 is not None and not _is_single_pick:
                _m4 = f" ({r4 / _rnd4:.2f}x random)" if _rnd4 else ""
                parts.append(f"brut 4+: {r4 * 100:.1f}%{_m4}")
            sc_txt = " · ".join(parts) if parts else f"medie: {score:.3f}"
        else:
            sc_txt = f"medie: {score:.3f}"
        is_base = m in _BASE
        is_excluded = (not is_base) and m in _structural_fail
        is_chosen = (not is_base) and (m == chosen_name)
        _gate_txt = ""
        if is_excluded:
            _gate_txt = f" · EXCLUSĂ din decizie: {_structural_fail[m]}"
        elif (not is_base) and m in _fail_gate:
            _gate_txt = (
                f" · necalificată (minimum 3 ferestre; ≥{_cons_pct}% peste random)"
            )
        with ui.row().classes("items-center gap-2 w-full"):
            _rank_badge = (
                "🎲"
                if is_base
                else "⛔"
                if is_excluded
                else "🏆"
                if i == 1
                else f"{i}."
            )
            ui.label(_rank_badge).classes("text-bold text-grey w-6")
            ui.label(("🎯 " + m) if is_chosen else m).classes(
                "text-bold text-positive"
                if is_chosen
                else "text-bold text-orange"
                if is_base
                else "text-bold text-grey"
                if is_excluded
                else "text-bold"
            )
            _pref = "baseline (referință, NU e candidat) · " if is_base else ""
            ui.label(
                f"· {_pref}{lib} · {sc_txt} · medie/extragere {avg:.2f}{_gate_txt}"
            ).classes("text-caption text-grey")

    title = f"🏆 Clasament bench — {section_label} ({label})"
    with ui.expansion(title, value=True).classes("w-full"):
        ui.label(
            "🏆 = primul în clasamentul eligibil; 🎯 = metoda din ultima generare sau decizia salvată. "
            "⛔ = exclusă structural: la tăietura top-K scorurile sunt egale în ≥50% din blocuri, "
            "deci pool-ul ar fi ales după număr, nu după semnal — nu e un defect al metodei. "
            "Necalificată = a bătut random în <60% din ferestre (zgomot, nu evidență)."
        ).classes("text-caption text-grey")
        if _is_single_pick:
            ui.label(
                "Urna 2: potrivire exactă a unei bile din 20; random = 5%."
            ).classes("text-caption text-grey")
        _chosen_lib = next((r[3] for r in rows if r[0] == chosen_name), "")
        _chosen_suffix = f" · {_chosen_lib}" if _chosen_lib else ""
        # Ensemble: preferă membrii ACTIVI din ultima generare (după decorelare
        # pe scoruri). Fără generare, arătăm ensemble-ul NOMINAL din best_methods
        # și îl etichetăm ca atare — nu mai pretindem că 3 membri = pool-ul.
        _ens_names: list[tuple[str, float]] = []
        _ens_source = ""
        _gen_info = _gen_early if isinstance(_gen_early, dict) else {}
        _gen_ens = _gen_info.get("ensemble") if isinstance(_gen_info, dict) else None
        _gen_dropped = (
            _gen_info.get("ensemble_dropped") if isinstance(_gen_info, dict) else None
        ) or []
        if _gen_ens:
            _ens_names = [
                (e.get("method"), float(e.get("weight", 0) or 0))
                for e in _gen_ens
                if isinstance(e, dict) and e.get("method")
            ]
            _ens_source = "efectiv (după decorelare pe scoruri, ultima generare)"
        else:
            try:
                from loto_enterprise.core.method_selector import get_ensemble_for_game

                _ens_names = [
                    (nm, float(wt))
                    for nm, _fn, wt in get_ensemble_for_game(
                        folds_game_key, pool, max_methods=3
                    )
                ]
                _ens_source = (
                    "nominal, plafon 3 (producția aplică "
                    "ENSEMBLE_MAX_METHODS, azi 1)"
                )
            except Exception:  # noqa: BLE001
                _ens_names = []
        if len(_ens_names) > 1:
            _n_ens = len(_ens_names)
            _ens_str = " + ".join(f"{nm} ({wt * 100:.0f}%)" for nm, wt in _ens_names)
            ui.html(
                render_html_safe(
                    t"🎯 <b style='color:#22c55e'>{_chosen_caption}: {chosen_name}</b>"
                    t"{_chosen_suffix} <span style='opacity:.75'>— pool-ul folosește "
                    t"ensemble-ul de {_n_ens} metode de mai jos ({_ens_source})</span>"
                )
            ).classes("text-caption")
            ui.html(
                render_html_safe(
                    t"<span style='opacity:.6;font-size:.85em'>⚖️ ensemble (variance-reduction): {_ens_str}</span>"
                )
            ).classes("text-caption")
        else:
            ui.html(
                render_html_safe(
                    t"🎯 <b style='color:#22c55e'>{_chosen_caption}: {chosen_name}</b>{_chosen_suffix}"
                )
            ).classes("text-caption")
        if _gen_dropped:
            _dparts = []
            for d in _gen_dropped:
                if isinstance(d, dict):
                    nm = d.get("method") or "?"
                    vs = d.get("vs")
                    extra = f" vs {vs}" if vs else ""
                    _dparts.append(f"{nm}{extra}")
                else:
                    _dparts.append(str(d))
            ui.label(
                "ℹ️ Săriți la generare (corelație/plat): " + "; ".join(_dparts)
            ).classes("text-caption text-grey")
        _dec_dropped = (_dec or {}).get("ensemble_dropped_redundant") or []
        if _dec_dropped:
            _dparts = []
            for d in _dec_dropped:
                if isinstance(d, dict):
                    nm = d.get("method") or "?"
                    vs = d.get("vs")
                    extra = f" vs {vs}" if vs else ""
                    _dparts.append(f"{nm}{extra}")
                else:
                    _dparts.append(str(d))
            ui.label(
                "ℹ️ Decizia a sărit ca redundanți (semnătură de performanță): "
                + "; ".join(_dparts)
            ).classes("text-caption text-grey")
        if chosen_name != winner[0]:
            # De ce diferă ALEASĂ de #1 — enumerăm doar cauzele care chiar există.
            if _conf_ok and _lift_ok:
                _ord = (
                    f"aceleași chei ca decizia (limita Wilson a ratei {_shown_t}+ pooled, pe "
                    f"extrageri efective → lift mediu vs random → consistență)"
                )
            elif _conf_ok:
                _ord = (
                    f"limita Wilson a ratei {_shown_t}+ (pooled, pe extrageri efective); "
                    f"tie-break-ul secundar diferă de decizie (rată brută, nu lift)"
                )
            else:
                _ord = f"rata brută {_shown_t}+ (fără coloane n → fără Wilson)"
            if chosen_name in _structural_fail:
                _why = (
                    f"selecția folosită provine dintr-o decizie anterioară, iar bench-ul curent "
                    f"o exclude structural: {_structural_fail[chosen_name]}"
                )
            elif not _gen_usable and not _saved_entry:
                _why = (
                    "nu există decizie salvată (best_methods.json lipsește sau fără acest "
                    "joc/pool) — producția folosește frequency până la Re-Bench"
                )
            elif _dec_low is True:
                _why = (
                    "nicio metodă n-a bătut random consistent (≥"
                    f"{_cons_pct}% din ferestre) → decizia a căzut pe "
                    "ramura CONSERVATOARE de fallback: alegerea nu e o dovadă de "
                    "superioritate, diferențele sunt zgomot"
                )
            elif _gate_applied:
                _why = (
                    "decizia e dintr-un bench mai vechi decât folds.csv, sau "
                    "ensemble-ul de scoring a rămas pe alt cap de listă"
                )
            elif _dec_low is False:
                _why = (
                    f"decizia aplică ÎN PLUS filtrul de consistență (să bată random în ≥"
                    f"{_cons_pct}% din ferestre), pe care clasamentul "
                    f"nu-l aplică; iar la scoring pool-ul folosește ensemble-ul, din care "
                    f"membrii redundanți/corelați sunt eliminați"
                )
            else:
                _why = (
                    "best_methods.json nu spune pe ce ramură s-a luat decizia (fișier scris "
                    "de o versiune veche) — poate fi filtrul de consistență, ramura de "
                    "fallback sau pur și simplu o decizie mai veche decât folds.csv"
                )
            ui.label(
                f"ℹ️ Lista e sortată după {_ord}; cap: {winner[0]}. Metoda ALEASĂ "
                f"({chosen_name}, marcată 🎯) diferă fiindcă {_why}."
            ).classes("text-caption text-grey")
        elif _dec_low is True:
            # Chiar și când ALEASĂ == #1, ramura de fallback trebuie spusă: „câștigătorul"
            # nu a bătut hazardul consistent.
            ui.label(
                f"⚠️ Decizia pentru acest pool e pe ramura de FALLBACK: nicio metodă n-a "
                f"bătut random în ≥{_cons_pct}% din ferestre. "
                f"Alegerea e conservatoare — diferențele dintre metode sunt zgomot."
            ).classes("text-caption text-warning")
        if not has_family:
            ui.label(
                "ℹ️ Librăria e estimată din nume (folds.csv vechi). Rulează un Re-Bench "
                "pentru etichete exacte."
            ).classes("text-caption text-orange")
        _n_qual = sum(1 for r in competitors if r[0] not in _fail_gate)
        _n_fail = len(_fail_gate)
        if _gate_applied and _n_fail:
            ui.label(
                f"Top {_n_shown} din {len(measured_methods)} metode măsurate "
                f"({_n_qual} calificate, {_n_fail} sub poarta de consistență, "
                f"{len(_structural_fail)} excluse structural)"
            ).classes("text-bold text-blue mt-2")
        else:
            ui.label(
                f"Top {_n_shown} din {len(measured_methods)} metode măsurate "
                f"({len(competitors)} eligibile, {len(_structural_fail)} excluse structural)"
            ).classes("text-bold text-blue mt-2")
        _cats = sorted(
            {rec[3] for rec in measured_methods if rec[3]}
        )  # categorii REALE (din folds)
        if _cats:
            ui.label("Categorii: " + " · ".join(_cats)).classes(
                "text-caption text-grey"
            )
        _rank = 0
        for rec in top_rows:
            if rec[0] in _BASE:
                _row(None, rec)  # baseline: vizibil ca reper, fără rang
            elif rec[0] in _structural_fail:
                _row(None, rec)  # diagnostic structural, fără rang
            else:
                _rank += 1
                _row(_rank, rec)
        # Baseline-urile care NU au intrat în slice: spune unde ar cădea (informativ),
        # fără să pară competitor.
        _shown_names = {rec[0] for rec in top_rows}
        for _bi, _brec in enumerate(rows_by_score):
            if _brec[0] not in _BASE or _brec[0] in _shown_names:
                continue
            # PE `rows_by_score` (ordinea Wilson), nu pe `rows`: acolo baseline-ul
            # e împins la coadă de poarta de consistență, deci ieșea mereu ultimul.
            _better = sum(1 for r in rows_by_score[:_bi] if r[0] not in _BASE)
            # Numitorul include toate metodele măsurate și baseline-ul însuși — altfel
            # poziția poate ajunge la N+1 „din N" (contradicție) când baseline-ul
            # e sub TOȚI candidații.
            # Poziția rândului `random` este o REALIZARE empirică a scorerului,
            # nu baseline-ul teoretic din titlu. Numărul „sub hazard” calculat
            # anterior din această poziție amesteca cele două referințe.
            _target_idx = 4 if _shown_t in (1, 3) else 5
            _below_theory = None
            if _rnd_t is not None:
                _below_theory = sum(
                    1
                    for r in competitors
                    if r[_target_idx] is not None
                    and float(r[_target_idx]) <= float(_rnd_t)
                )
            _theory_txt = (
                f" Față de pragul teoretic din titlu: {_below_theory} metode eligibile au "
                f"rata brută ≤ {_rnd_t * 100:.2f}%."
                if _below_theory is not None
                else ""
            )
            ui.label(
                f"🎲 baseline «{_brec[0]}» (realizare empirică, NU candidat) — locul "
                f"{_better + 1} din {len(measured_methods) + 1} după Wilson."
                f"{_theory_txt}"
            ).classes("text-caption text-grey")
        # SIMETRIC cu baseline-ul: dacă metoda EFECTIV folosită la generare nu apare în
        # slice, spune unde cade. Altfel 🎯 lipsește complet din listă, fără niciun
        # indiciu — exact metoda despre care utilizatorul vrea să știe cel mai mult.
        if chosen_name and chosen_name not in _shown_names:
            _ci = next((i for i, r in enumerate(rows) if r[0] == chosen_name), None)
            if _ci is None:
                ui.label(
                    f"🎯 metoda ALEASĂ «{chosen_name}» nu apare în folds.csv pentru acest "
                    f"(joc, pool) — decizia e mai veche decât bench-ul curent."
                ).classes("text-caption text-orange")
            elif chosen_name in _structural_fail:
                ui.label(
                    f"🎯 «{chosen_name}» este exclusă: {_structural_fail[chosen_name]}"
                ).classes("text-warning text-caption")
            else:
                _cbetter = sum(
                    1
                    for r in rows[:_ci]
                    if r[0] not in _BASE and r[0] not in _structural_fail
                )
                ui.label(
                    f"🎯 metoda ALEASĂ «{chosen_name}» — locul {_cbetter + 1} din "
                    f"{len(competitors)} metode candidate (în afara top-{top_n} afișat)."
                ).classes("text-caption text-positive")


def draw_vs_production_pool(draw_nums, pool):
    """Hit-urile ultimei extrageri pe nucleul de PRODUCȚIE (nu pool-ul WF).

    Walk-forward antrenează doar pe extragerile ANTERIOARE datei evaluate.
    Nucleul afișat la generare include și ultima linie din CSV. Cele două
    seturi nu trebuie să coincidă; UI-ul le raportează separat.
    """
    pool_set = {int(x) for x in (pool or [])}
    hits = [int(n) for n in draw_nums if int(n) in pool_set]
    miss = [int(n) for n in draw_nums if int(n) not in pool_set]
    return hits, miss


def _last_csv_draw(fname: str):
    """(date_str, [numere], joker|None) din ULTIMA linie a CSV-ului încărcat pentru
    acest fișier; None dacă lipsește. Faithful la CSV (exact ultima extragere)."""
    df = next((d for f, d in STATE.get("datasets", []) if f == fname), None)
    if df is None or len(df) == 0:
        return None
    try:
        last = df.iloc[-1]
    except Exception:  # noqa: BLE001
        return None
    cols = [str(c) for c in df.columns]
    num_cols = sorted(
        (c for c in cols if len(c) > 1 and c[0] == "n" and c[1:].isdigit()),
        key=lambda c: int(c[1:]),
    )
    nums = []
    for c in num_cols:
        try:
            nums.append(int(last[c]))
        except Exception:  # noqa: BLE001
            pass
    if not nums:
        return None
    # 5/40 extrage 6 numere și hiturile se numără pe toate 6 (engine-ul citește
    # n1..n6); Joker are 5 în Urna 1, plus jokerul afișat separat.
    label = _game_label_for(fname)
    draw_n = 5 if label == "joker" else 6
    nums = nums[:draw_n]
    joker = None
    if "joker" in cols:
        try:
            joker = int(last["joker"])
        except Exception:  # noqa: BLE001
            joker = None
    date_str = ""
    for dc in ("date", "Data", "data", "Date"):
        if dc in cols:
            try:
                date_str = str(last[dc])
            except Exception:  # noqa: BLE001
                date_str = ""
            break
    return (date_str, nums, joker)


def _fmt_score_time(ms) -> str:
    """Timp de scoring lizibil: sub 100 ms afișăm milisecunde, nu «0.0s»."""
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return "?"
    return f"{v:.0f}ms" if v < 100 else f"{v / 1000:.1f}s"


def _csv_last_date(df) -> str:
    """Data ultimei extrageri dintr-un DataFrame încărcat (ultimul rând, aceeași
    convenție ca `_last_csv_draw`: CSV-urile sunt cronologice, ultimul rând e cel
    mai recent). Șir gol dacă nu există coloană de dată."""
    if df is None or len(df) == 0:
        return ""
    cols = [str(c) for c in df.columns]
    for dc in ("date", "Data", "data", "Date"):
        if dc in cols:
            try:
                val = str(df.iloc[-1][dc]).strip()
            except Exception:  # noqa: BLE001
                return ""
            return "" if val.lower() in ("", "nan", "nat", "none") else val
    return ""


def _render_last_csv_draw(fname: str, pool=None, joker_pick=None) -> None:
    """Reper lângă clasament: ULTIMA extragere reală din CSV-ul încărcat (data + numere
    + joker dacă există), plus câte din ele sunt în nucleul de producție."""
    info = _last_csv_draw(fname)
    if not info:
        return
    date_str, nums, joker = info
    txt = "  ".join(str(n) for n in nums)
    if joker is not None:
        txt += f"   ·   joker {joker}"
    cap = "📅 Ultima extragere din CSV" + (f" ({date_str})" if date_str else "") + ":"
    with ui.row().classes("items-center gap-2"):
        ui.label(cap).classes("text-caption text-grey")
        ui.label(txt).classes("text-bold text-info")
    if pool:
        hits, miss = draw_vs_production_pool(nums, pool)
        hit_txt = ", ".join(str(n) for n in hits) if hits else "—"
        miss_txt = ", ".join(str(n) for n in miss) if miss else "—"
        ui.label(
            f"vs nucleul de PRODUCȚIE ({len({int(x) for x in pool})} nr.): "
            f"{len(hits)}/{len(nums)} în pool ({hit_txt}) · afară: {miss_txt}"
        ).classes("text-caption")
        ui.label(
            "Producție = pool-ul de joc, antrenat și pe această extragere. "
            "Tabelul 🔥 din walk-forward folosește pool-ul de DINAINTEA fiecărei date "
            "— numerele nimerite acolo NU trebuie să coincidă cu rândul de mai sus."
        ).classes("text-caption text-grey")
    if joker is not None and joker_pick:
        pred = [int(x) for x in joker_pick]
        if not pred:
            return
        pred_txt = ", ".join(str(n) for n in pred)
        if joker in pred:
            ui.label(
                f"Joker urna 2 (producție): extras {joker} — hit pe {pred_txt}."
            ).classes("text-caption text-positive")
        else:
            ui.label(
                f"Joker urna 2 (producție): extras {joker}, prezis {pred_txt} — miss."
            ).classes("text-caption text-grey")


def _render_urna2_benchmark_note() -> None:
    """Explică metrica dedicată Urnei 2, fără a o confunda cu hiturile +3/+4."""
    with ui.expansion("Despre benchmark — Joker Urna 2 (1/20)", value=True).classes(
        "w-full"
    ):
        ui.label(
            "Urna 2 este evaluată separat top-1 (1/1): o predicție este hit doar când "
            "bila aleasă coincide exact cu cea extrasă. Baseline aleator: 1/20 = 5%."
        ).classes("text-caption")
        ui.label(
            "Re-Bench compară metodele CPU pe ferestre walk-forward, cu aceeași poartă "
            "de consistență și limită Wilson. Dacă nu există semnal peste random, aplicația "
            "marchează low confidence și alege conservator dintre metodele eligibile; "
            "frequency rămâne fallback-ul dacă nu există date utilizabile."
        ).classes("text-caption text-grey")


def _render_bench_leaderboard(
    game_label: str, top_n: int = 20, pool_size: int | None = None
) -> None:
    """Top-N metode din ULTIMUL bench pentru acest joc (folds.csv). Joker = urne separate."""
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        if game_label == "joker":
            _render_urna2_benchmark_note()
        return
    try:
        df = _read_bench_folds_cached(fp)
    except Exception:  # noqa: BLE001
        return
    if df.empty or "method" not in df.columns or "game" not in df.columns:
        return
    pool = int(pool_size) if pool_size is not None else _int_setting("pool_size_val")
    if game_label == "joker":
        _render_bench_leaderboard_slice(
            df, "joker_urna1", pool, "Joker Urna 1 (5/45)", top_n=top_n
        )
        _render_bench_leaderboard_slice(
            df, "joker_urna2", 1, "Joker Urna 2 (1/20)", top_n=top_n
        )
        if not (df["game"].astype(str) == "joker_urna2").any():
            _render_urna2_benchmark_note()
        return
    folds_key = _LABEL_TO_FOLDS_GAME.get(game_label, game_label)
    _render_bench_leaderboard_slice(
        df, folds_key, pool, game_label.upper(), top_n=top_n
    )


_BENCH_FOLDS_CACHE: dict[str, object] = {"signature": None, "df": None}


_LB_ROWS_MEMO: dict = {}  # (semnătură folds.csv, joc, pool, metrică, T, n) → rânduri clasament


def _read_bench_folds_cached(path: Path) -> pd.DataFrame:
    """Citește folds.csv doar când fișierul atomic a fost înlocuit.

    Tick-ul UI rulează la 1s, dar benchmark-ul face flush mult mai rar. Fără
    cache, aceeași mie de rânduri era reparsată de zeci de ori între două
    flush-uri. Semnătura include mtime_ns + size; dacă fișierul se schimbă chiar
    în timpul citirii, rezultatul este folosit o dată dar nu cache-uit.
    """
    before = path.stat()
    signature = (before.st_mtime_ns, before.st_size)
    cached = _BENCH_FOLDS_CACHE.get("df")
    if _BENCH_FOLDS_CACHE.get("signature") == signature and isinstance(
        cached, pd.DataFrame
    ):
        return cached
    df = pd.read_csv(path)
    after = path.stat()
    if signature == (after.st_mtime_ns, after.st_size):
        _BENCH_FOLDS_CACHE["signature"] = signature
        _BENCH_FOLDS_CACHE["df"] = df
    return df


def _render_bench_live_leaderboard(bench_start=None, progress=None) -> None:
    """Clasament PARȚIAL în timpul bench-ului — din folds.csv (flush-uit periodic la
    ~100 rezultate). Metodele apar pe măsură ce TERMINĂ. Câștigătorul final + Auto-Pilot
    se decid abia la sfârșit. Citește folds.csv O DATĂ pe render (parțial → mic).
    `progress` (0..1) — la ≥1.0 testele-s gata, dar procesul încă scrie decizia/raportul
    → titlul NU mai zice „PARȚIAL" (inadvertență văzută în UI la 100%)."""
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return
    # Până la primul flush al rulării CURENTE, folds.csv încă are rezultatele bench-ului
    # ANTERIOR → nu le arăta ca „live".
    if bench_start:
        try:
            if fp.stat().st_mtime < float(bench_start) - 2:
                ui.label(
                    "⏳ Se calculează primele rezultate… (clasamentul parțial apare după primul flush)."
                ).classes("text-caption text-grey")
                return
        except Exception:  # noqa: BLE001
            pass
    try:
        df = _read_bench_folds_cached(fp)
    except Exception:  # noqa: BLE001
        return  # mid-flush / gol → reîncearcă la următorul tick
    if df.empty or "method" not in df.columns or "game" not in df.columns:
        return
    pool = _int_setting("pool_size_val")
    _done = progress is not None and float(progress) >= 1.0
    _title = (
        "🏆 Clasament COMPLET (teste 100% — se scrie decizia/raportul...)"
        if _done
        else "🏆 Clasament PARȚIAL (live — în timpul bench-ului)"
    )
    with ui.expansion(_title, value=True).classes("w-full"):
        if _done:
            ui.label(
                "✅ Toate testele au rulat. Procesul de bench finalizează decizia "
                "(best_methods.json) + raportul — câștigătorul final și Auto-Pilot "
                "pornesc în câteva momente."
            ).classes("text-caption text-positive")
        else:
            ui.label(
                "⏳ Se completează pe măsură ce metodele termină. "
                "Câștigătorul final + Auto-Pilot se stabilesc abia la sfârșitul bench-ului."
            ).classes("text-caption text-grey")
        ui.label(
            "ℹ️ Walk-forward: istoricul listează +3 și +4; targetul bench/alerte rămâne "
            f"≥{_bench_target()}."
        ).classes("text-caption text-grey")
        for fk, kp, sect in [
            ("loto_6_49", pool, "6/49"),
            ("joker_urna1", pool, "Joker Urna 1 (5/45)"),
            ("loto_5_40", pool, "5/40"),
        ]:
            _render_bench_leaderboard_slice(df, fk, kp, sect, top_n=20)


# NOTĂ: `_render_bench_winner_only` a fost ȘTEARSĂ (2026-07). Era cod MORT (zero
# call-site-uri) și rămăsese pe calea VECHE, divergentă: nu filtra `is_random`/`failed`,
# nu excludea baseline-urile (`EXCLUDED_FROM_PRODUCTION`), sorta după media BRUTĂ (nu
# Wilson) și nu citea deloc best_methods.json — deci putea anunța drept „câștigătoare"
# altă metodă decât cea folosită efectiv la generare (și putea pune `random` pe podium).
# Sursa UNICĂ de adevăr pentru „cine e câștigătorul" e `_render_bench_leaderboard_slice`.

