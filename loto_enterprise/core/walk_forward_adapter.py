"""Walk-forward backtest adapter — onest (FĂRĂ data leakage).

Înlocuieşte evaluate_variants (care folosea pool-ul azi vs istoric întreg) cu
run_retroactive_backtest care, pentru fiecare extragere t, regenerează pool-ul
folosind DOAR datele < t. Asta elimină recency bias şi reflectă cu acuratețe
puterea predictivă reală.

Cache:
    - Pe disc: D:\\_BUILD\\_LOTO\\.wf_cache\\walk_forward_<ver>_<game>_<csv_hash>
      _pool<N>_d<depth>_<dec_sig>.pkl. Override: ``LOTO_WF_CACHE_DIR``; pe un
      sistem fără directorul runtime Windows există fallback local ``.wf_cache``.
    - Reutilizat la următorul Auto-Pilot dacă (csv_hash, pool_size, decizie bench) match.
      dec_sig = semnătura deciziei (scorer/sim_depth/blacklist/ensemble/target) PLUS
      wheel-ul efectiv (algoritm + garanţia internă a WF) → un Re-Bench care schimbă
      câştigătorul, sau o schimbare de algoritm de wheeling, invalidează automat
      cache-ul. Wheel-ul contează pentru că determină numărul de variante, acoperirea
      şi hiturile per bilet; fără el, raportul ar evalua un wheel care nu mai există.
    - Un bump de CACHE_VERSION doar schimbă NUMELE fişierului: pickle-urile vechi
      rămân pe disc la nesfârşit. `purge_stale_wf_cache()` le inventariază (implicit
      dry-run) şi le poate şterge; `clear_walk_forward_cache()` şterge TOT, inclusiv
      versiunea curentă.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import scored_variant_numbers
from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path_atomic
from loto_enterprise.core.wf_sig import ensemble_sig as _ensemble_sig, lookback_pct
from runtime_paths import PROJECT_ROOT, WF_CACHE_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = WF_CACHE_DIR
LEGACY_CACHE_DIR = PROJECT_ROOT / "bench_results"
CACHE_VERSION = "v22"
# Changelog (cea mai nouă prima; bump = invalidare cache walk-forward):
# v22: Joker Urna 2 are decizie top-1 separată; semnătura WF include acum şi
#      scorerul/ensemble-ul ei, deoarece el schimbă numărul ataşat variantelor.
# v21: Urna 2 Joker validează strict valori întregi 1..20 şi nu mai ataşează o
#      bilă arbitrară când nu are observaţii valide. Lista `variant` din flat se
#      poate schimba pentru CSV-uri corupte, deci v20 nu mai este sigur.
# v20: cheia wheel include hash-ul fișierului covering-design folosit de La Jolla
#      (și union34). Un design actualizat schimbă lista de bilete, costul și
#      hiturile per bilet; cache-urile v19 nu îl puteau observa automat.
# v19: `wheel_union34` nu mai unește două covere redundante; pentru 3+/4+
#      folosește un singur C(v, pick, 4). Se schimbă lista de bilete, costul și
#      rezultatele `hits` per bilet, deci cache-ul v18 ar valida un wheel vechi.
# (v18, ADITIV — FĂRĂ bump): câmpul `wheel_coverage` pe `WalkForwardResult`.
#      E o ADĂUGIRE cu default, nu o schimbare de semantică a câmpurilor vechi:
#      înregistrările deja pe disc rămân corecte, doar că nu ştiu acoperirea
#      (→ `None` = necunoscut, raportat ca atare, NU 100%). Un bump ar fi aruncat
#      acoperirea WF acumulată între sesiuni pentru zero câştig.
# v18: `hits_union` = hit-uri de POOL (hard_core ∩ extragere), nu uniunea
#      numerelor de pe bilete. Un wheel incomplet omitea numere din pool și
#      WF raporta 3+ mai mic decât pool-ul real (UI/CLAUDE: Nucleu Dur).
#      `hits` (max pe un bilet) e neschimbat. La acoperire 100% și g≥3,
#      3+ pool ⇔ 3+ bilet; sub 100% hits_union supra-numără vs bilete;
#      5+/6 rămân plafon (g intern = max 4). (Acoperirea e persistată de
#      intrarea ADITIVĂ de mai sus — la scrierea acestei linii nu era.)
# v17: `_csv_hash` pe TOATE rândurile numerice + `len(df)` (nu doar tail 500).
#      Corecții/inserări mai vechi de 500, sau CSV mai lung la același tail,
#      serveau cache vechi pe alte date de antrenare/validare.
# v16: `_decision_sig` include lookback-ul efectiv (0 din UI = 100%) + serializare
#      stabilă a ensemble-ului (listă {method, weight}, nu str(dict)). Fără lookback
#      în cheie, un slider „ultimele X%” schimba pool-ul de producție dar WF
#      valida tot istoricul din cache.
# v15: score_sum_affinity rescris (nu mai produce pool consecutiv 18–28 pe Joker).
#      Pool-ul GENERAT se schimbă oriunde metoda e membru activ (Joker k11 = solo
#      winner) → pickle-urile v14 validează un pool care nu se mai generează.
# v14: `_decision_sig` include acum WHEEL-ul efectiv (algoritm + garanția internă a WF).
#      Cheia veche fixa doar scorer/ensemble/target, deci trecerea wheeling-ului de la
#      ILP la designurile La Jolla (6/49 pool 12: ~54 → 41 bilete) NU a invalidat nimic:
#      raportul continua să afișeze număr de variante și hituri pe wheel-ul vechi,
#      alături de rezultate regenerate cu wheel-ul nou.
#      Bump-ul de VERSIUNE (peste schimbarea de sig, care ar fi invalidat oricum) e ca
#      pickle-urile v13 să devină vizibile pentru `purge_stale_wf_cache`, altfel ar fi
#      rămas orfane la nesfârșit sub aceeași versiune.
# v13: (a) flat-ul NU mai conține omnius_hits/omnius_ticket (biletul OMNIUS scos din
#      WF/UI) — structură incompatibilă cu v12; (b) DECORELAREA membrilor de ensemble
#      (method_selector.MAX_MEMBER_CORR=0.95, pe scoruri) + filtrul de redundanță din
#      decizie pot schimba POOL-ul generat față de v12 — deci cache-urile v12 validează
#      un pool care nu se mai generează; (c) `_decision_sig` include acum ensemble-ul
#      (compoziție + ponderi), nu doar `scorer`.
#      Motivul (b) e cel important: (a) singur ar fi fost compatibil la unpickle
#      (dataclass-ul se dezserializează fără __init__), deci NU justifica singur bump-ul.
# v12: tie-break unificat + ponderi engine + registry fără duplicatul 649_top_autocorr (alias → autocorr).
# v11: prime_bias tie-break frecvență + _decision_sig include BENCH_HIT_TARGET + tie-break pool.
# v10: nefolosită (sărită la bump-ul v9→v11).
# v9:  pool top-N pur + OMNIUS top-N (optimizare hits 3+).
# v8:  649_katz12_gap88 scorer 6/49 k16.
# v7:  nefolosită (sărită).
# v6:  sufix legacy pentru cache-ul celei de-a doua treceri (funcție eliminată).
# v5:  iterare recent→vechi la oprire parțială (buget) — cache-urile v4 parțiale acopereau felia veche.
# v4:  flat-ul include omnius_hits/omnius_ticket per extragere.
# v3:  cheia include semnătura deciziei bench (scorer/sim_depth/blacklist).
# v2:  versiunea inițială.


@dataclass
class WalkForwardResult:
    """Per (draw, variant) entry — drop-in pentru UI care aşteaptă lista flat."""
    draw_index: int
    draw_date: str | None
    variant: list[int]
    hits: int
    hits_union: int  # POOL ∩ extragere (plafon pt 5+ când g=4; peste acoperire <100% e plafon vs bilete)
    target_draw_date: str | None = None  # alias pt RetroactivePrediction
    # % din ţintele de garanţie acoperite de biletele pasului. None = NECUNOSCUT
    # (înregistrare dintr-un cache scris înainte de introducerea câmpului), NU 0.
    # Contează fiindcă `hits_union` e hit de POOL: „3 în pool" ⇔ „3 pe un bilet"
    # doar la 100% acoperire (şi guarantee ≥ 3); sub 100% e un PLAFON.
    wheel_coverage: float | None = None

    def __post_init__(self):
        if self.target_draw_date is None:
            self.target_draw_date = self.draw_date


def _backfill_new_fields(records) -> None:
    """Completează IN-PLACE câmpurile adăugate în dataclass DUPĂ scrierea cache-ului.

    Unpickle-ul restaurează `__dict__`-ul obiectului vechi FĂRĂ să treacă prin
    `__init__`/`__post_init__`, deci un câmp nou lipseşte din instanţă.

    ⚠️ Pentru un câmp cu default SIMPLU (cazul lui `wheel_coverage: float | None =
    None`) CITIREA merge şi fără asta — cade pe atributul de CLASĂ, iar `hasattr`
    e deja True, deci bucla de mai jos îl SARE: pe câmpul de azi funcţia e un
    no-op deliberat. Ea contează pentru câmpurile care NU au atribut de clasă —
    `default_factory` sau fără default — unde accesul chiar ar da AttributeError.
    E plasa care ţine un câmp ADITIV departe de un bump de `CACHE_VERSION` (care
    ar arunca acoperirea WF acumulată). Acelaşi tipar ca în
    `benchmark/bench_cache.get_cached_fold`.
    """
    for obj in records or ():
        if not is_dataclass(obj):
            continue
        for fld in fields(obj):
            if hasattr(obj, fld.name):
                continue
            if fld.default is not MISSING:
                setattr(obj, fld.name, fld.default)
            elif fld.default_factory is not MISSING:  # type: ignore[misc]
                setattr(obj, fld.name, fld.default_factory())  # type: ignore[misc]
            else:
                setattr(obj, fld.name, None)


def wheel_coverage_summary(flat) -> dict:
    """Acoperirea wheel-ului peste paşii walk-forward, dedusă per EXTRAGERE.

    `flat` are o intrare per (extragere × bilet), iar acoperirea e o proprietate a
    PASULUI, nu a biletului → dedup pe `draw_index`, altfel paşii cu multe bilete
    ar domina numărătoarea.

    Întoarce `{n_draws, known, unknown, min, below_100}`. `min`/`below_100` se
    calculează DOAR pe paşii cu acoperire cunoscută; `unknown > 0` înseamnă
    înregistrări dintr-un cache mai vechi decât câmpul — necunoscut, nu 100%.
    """
    per: dict = {}
    for p in flat or ():
        di = int(getattr(p, "draw_index", 0) or 0)
        if di not in per:
            per[di] = getattr(p, "wheel_coverage", None)
    known = [float(v) for v in per.values() if v is not None]
    return {
        "n_draws": len(per),
        "known": len(known),
        "unknown": len(per) - len(known),
        "min": min(known) if known else None,
        "below_100": sum(1 for v in known if v < 100.0),
    }


def per_draw_hit_summary(flat) -> dict:
    """Agregă rezultatele WF o singură dată pentru fiecare extragere.

    Lista ``flat`` conține o intrare pentru fiecare bilet jucat la o extragere.
    ``hits_union`` este identic pe acele intrări, iar metrica de bilet utilă
    pentru 3+/4+ este maximul pe biletele acelei extrageri. A face media direct
    pe ``flat`` ar pondera artificial extragerile cu wheel-uri mai mari.
    """
    per: dict = {}
    for p in flat or ():
        draw_index = int(getattr(p, "draw_index", 0) or 0)
        row = per.get(draw_index)
        if row is None:
            per[draw_index] = {
                "pool": int(getattr(p, "hits_union", 0) or 0),
                "best_ticket": int(getattr(p, "hits", 0) or 0),
            }
        else:
            row["best_ticket"] = max(
                int(row["best_ticket"]), int(getattr(p, "hits", 0) or 0)
            )
    return per


def _csv_hash(df: pd.DataFrame, game_type: str) -> str:
    """MD5 stabil pe lungime + TOATE rândurile coloanelor de numere.

    Tail-only (500) lăsa corecții/inserări vechi și CSV-uri cu același coadă
    dar lungimi diferite să partajeze cache-ul — WF valida alte date decât
    cele pe care engine-ul antrenează (lookback 100%).
    """
    cols_map = {
        "6/49":   ["n1", "n2", "n3", "n4", "n5", "n6"],
        "5/40":   ["n1", "n2", "n3", "n4", "n5"],
        "joker":  ["n1", "n2", "n3", "n4", "n5", "joker"],
    }
    cols = [c for c in cols_map.get(game_type, []) if c in df.columns]
    if not cols:
        return hashlib.md5(str(len(df)).encode()).hexdigest()[:12]
    body = df[cols].to_csv(index=False, header=False).encode()
    h = hashlib.md5(f"{len(df)}|".encode() + body).hexdigest()
    return h[:12]


# Câte numere se extrag per joc (= `pick`-ul wheel-ului).
_WF_PICK = {"6/49": 6, "5/40": 5, "joker": 5}


def _wf_guarantee(pool_size: int, pick: int | None = None) -> int:
    """Garanția cu care walk-forward-ul regenerează wheel-ul la fiecare pas.

    SURSĂ UNICĂ: valoarea e pasată `run_retroactive_backtest(guarantee=...)` mai jos.
    Nu e garanția din setările UI — WF are propria formulă, independentă de ea.

    Plafonată la `pick - 1` când jocul e cunoscut. `guarantee == pick` e o cerere
    DEGENERATĂ: singurul cover 100% e sistemul complet (5/40 pool 15 → C(15,5) =
    3003 bilete), iar greedy-ul se oprește la 1000 de iterații → 1001 bilete la
    33% acoperire. Formula `max(4, pool//3)` atinge 5 de la pool 15, iar la 5/40 și
    Joker `pick` e tot 5 → degenerare la orice pool ≥ 15. Plafonul e intern (WF își
    alege singur garanția); garanția CERUTĂ DE UTILIZATOR în UI rămâne respectată
    întotdeauna, inclusiv `guarantee == pick` = sistem complet, deliberat.
    """
    g = max(4, int(pool_size) // 3)
    if pick:
        g = min(g, int(pick) - 1)
    return max(1, g)


def _wheel_sig(pool_size: int, game_type: str | None = None) -> str:
    """Semnătura wheel-ului EFECTIV pe care îl va folosi walk-forward-ul.

    WF rulează mereu cu `max_variants=0`, deci rezolvă aceeași ramură ca engine-ul
    (`loto_engine.generate_predictions`): override explicit din env `LOTO_WHEEL_METHOD`
    dacă e setat, altfel `lajolla`.

    DE CE intră în cheia de cache: numărul de BILETE, acoperirea şi hiturile per bilet
    depind de algoritmul de wheeling. Fără wheel în cheie, schimbarea ILP → La Jolla
    (doar 6/49 pool 12 / g4:
    ~54 → 41 bilete; `covering_designs/` are doar C_12_6_4 și C_12_5_4) a servit în
    continuare cache vechi, iar raportul amesteca numărul vechi de variante cu
    rezultate regenerate pe wheel-ul nou.
    """
    requested = os.environ.get("LOTO_WHEEL_METHOD", "").strip().lower()
    try:
        from wheeling_methods import WHEEL_METHODS, covering_design_source_signature
        method = requested if requested in WHEEL_METHODS or requested == "greedy" else "greedy"
        if not requested:
            method = "lajolla"
        guarantee = _wf_guarantee(pool_size, _WF_PICK.get(game_type))
        if method in {"lajolla", "union34"}:
            design_guarantee = 4 if method == "union34" and guarantee <= 4 else guarantee
            design_sig = covering_design_source_signature(
                int(pool_size), int(_WF_PICK.get(game_type) or 6), design_guarantee,
            )
            return f"{method}|g{guarantee}|cd{design_sig}"
        return f"{method}|g{guarantee}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WALK-FWD] Nu pot semna covering-design-ul: %s", exc)
        method = requested or "lajolla"
        return f"{method}|g{_wf_guarantee(pool_size, _WF_PICK.get(game_type))}|cd-error"


def _penalty_sig(recent_penalty_draws: int = 0, recent_penalty_factor: float = 0.5) -> str:
    """Sufix de cheie pentru penalizarea recentă; gol când e oprită (chei vechi valide)."""
    n = int(recent_penalty_draws or 0)
    if n <= 0:
        return ""
    return f"|rp{n}:{float(recent_penalty_factor):.3f}"


def _decision_sig(game_type: str, pool_size: int, lookback_percent: float = 100.0,
                  recent_penalty_draws: int = 0, recent_penalty_factor: float = 0.5) -> str:
    """Semnătură scurtă a deciziei bench (scorer + target + ensemble + wheel +
    lookback) pentru (joc, pool). La Joker include şi Urna 2, fiindcă bila ei
    este ataşată fiecărei variante şi îi poate schimba evaluarea retrospectivă.
    """
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET
        gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
              "joker": "joker_urna1"}.get(game_type, "loto_6_49")
        c = recommend_optimal_config(gk, int(pool_size))
        _ens_sig = _ensemble_sig(c.get("ensemble") or [])
        urna2_sig = ""
        if game_type == "joker":
            c2 = recommend_optimal_config("joker_urna2", 1)
            urna2_sig = (
                f"|u2:{c2.get('scorer', '?')}:{c2.get('hit_target', 1)}:"
                f"{_ensemble_sig(c2.get('ensemble') or [])}"
            )
        lb = lookback_pct(lookback_percent)
        # `use_blacklist` e INERT în producție (engine: blacklist=set()) — nu
        # intra în cheie, altfel un toggle de telemetrie invalida tot cache-ul WF
        # fără să schimbe pool-ul sau wheel-ul.
        raw = (f"{c.get('scorer', '?')}|{c.get('sim_depth_pct', 0)}|"
               f"{BENCH_HIT_TARGET}|{_ens_sig}{urna2_sig}|"
               f"{_wheel_sig(pool_size, game_type)}|lb{lb}"
               f"{_penalty_sig(recent_penalty_draws, recent_penalty_factor)}")
        return hashlib.md5(raw.encode()).hexdigest()[:8]
    except Exception as exc:
        logger.warning(f"[WALK-FWD] decizie bench indisponibilă ({exc}) — "
                       f"semnătură doar pe wheel")
        return "nd" + hashlib.md5(
            (_wheel_sig(pool_size, game_type)
             + _penalty_sig(recent_penalty_draws, recent_penalty_factor)).encode()
        ).hexdigest()[:6]


def migrate_legacy_wf_cache() -> dict:
    """Mută cache-urile WF vechi din ``bench_results`` în directorul runtime.

    Operația este idempotentă și restrânsă la ``walk_forward_*.pkl``. Dacă un
    nume există deja la destinație, ambele copii sunt păstrate: cea legacy este
    raportată ca ``skipped``, fără suprascriere sau pierdere de acoperire parțială.
    """
    source = LEGACY_CACHE_DIR
    target = CACHE_DIR
    result = {
        "source": str(source),
        "target": str(target),
        "found": 0,
        "moved": 0,
        "skipped": 0,
        "errors": [],
        "files": [],
    }
    try:
        if source.resolve() == target.resolve() or not source.exists():
            return result
    except OSError:
        return result

    files = sorted(source.glob("walk_forward_*.pkl"))
    result["found"] = len(files)
    if not files:
        return result
    target.mkdir(exist_ok=True, parents=True)
    for old_path in files:
        new_path = target / old_path.name
        if new_path.exists():
            result["skipped"] += 1
            continue
        try:
            shutil.move(str(old_path), str(new_path))
            result["moved"] += 1
            result["files"].append(old_path.name)
        except OSError as exc:
            result["errors"].append(f"{old_path.name}: {exc}")
    return result


def _cache_path(game_type: str, csv_hash: str, pool_size: int, depth: int, dec_sig: str) -> Path:
    safe = game_type.replace("/", "_")
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    return CACHE_DIR / f"walk_forward_{CACHE_VERSION}_{safe}_{csv_hash}_pool{pool_size}_d{depth}_{dec_sig}.pkl"


def expand_predictions_to_flat(
    preds: list[Any], game_type: str
) -> list[WalkForwardResult]:
    """RetroactivePrediction[N draws] → WalkForwardResult[N draws × M variants].

    Fiecare RetroactivePrediction conține lista de variante prezise pentru o
    extragere. UI consumă lista flat (variantă × extragere). Expand explicit.
    """
    flat: list[WalkForwardResult] = []
    for p in preds:
        actual = set(p.actual_numbers)
        for variant in p.variants:
            vset = set(scored_variant_numbers(variant, game_type))
            hits = len(vset & actual)
            flat.append(WalkForwardResult(
                draw_index=p.draw_index,
                draw_date=p.target_draw_date,
                variant=list(variant),
                hits=hits,
                hits_union=p.hits_union,
                target_draw_date=p.target_draw_date,
                wheel_coverage=getattr(p, "wheel_coverage", None),
            ))
    return flat


def _merge_partial_coverage(
    cached: dict,
    flat_new: list[WalkForwardResult],
    meta: dict,
) -> tuple[list[WalkForwardResult], dict]:
    """REUNIUNE (pe `draw_index`) între un cache PARȚIAL şi rularea nouă.

    De ce reuniune şi nu „câştigă cel mai lung": acoperirea parţială NU e garantat
    un prefix contiguu al cozii recente. Paşii se lansează în ordine recent→vechi,
    dar un pas care CRAPĂ e sărit (worker-ul loghează şi întoarce None), iar un
    pool fără NICIUN progres în `_WF_STEP_TIMEOUT_S` e oprit forţat cu validare
    parţială (paşii în curs se pierd), deci două rulări pot avea GĂURI diferite:
    o rulare mai lungă în total poate rata extrageri pe care cache-ul le avea.
    Regula veche („păstrez cache-ul dacă are mai multe extrageri") arunca exact
    felia acoperită doar de cealaltă rulare.

    Contract: cheia de cache fixează (csv_hash, joc, pool, depth, decizie), deci
    intrările pentru acelaşi `draw_index` sunt echivalente — la conflict păstrăm
    rularea NOUĂ. Rezultatul e monoton crescător prin construcţie.
    """
    cached_flat = list(cached.get("flat") or [])
    if not cached_flat:
        return flat_new, meta

    new_idx = {int(getattr(r, "draw_index", -1)) for r in flat_new}
    extra = [r for r in cached_flat if int(getattr(r, "draw_index", -1)) not in new_idx]
    if not extra:
        return flat_new, meta

    extra_idx = {int(getattr(r, "draw_index", -1)) for r in extra}
    merged = sorted(flat_new + extra, key=lambda r: int(getattr(r, "draw_index", -1)))
    meta["n_predictions"] = int(meta.get("n_predictions") or 0) + len(extra_idx)
    meta["n_test_draws"] = len(new_idx | extra_idx)
    meta["partial"] = meta["n_test_draws"] < int(meta.get("n_expected") or 0)
    meta["merged_partial_cache"] = len(extra_idx)
    # Dacă rularea nouă n-a produs nimic (anulare imediată), rezultatul e integral
    # din cache → UI-ul trebuie să vadă from_cache=True, nu o „rulare" fantomă.
    meta["from_cache"] = not flat_new
    logger.info(
        "[WALK-FWD] Reuniune cu cache-ul parţial: +%d extrageri din cache → %d/%d.",
        len(extra_idx), meta["n_test_draws"], meta.get("n_expected"),
    )
    return merged, meta


def run_honest_walk_forward(
    df_source: pd.DataFrame,
    game_type: str,
    pool_size: int,
    backtest_depth_percent: float = 5.0,
    lookback_percent: float = 100.0,
    use_cache: bool = True,
    force_refresh: bool = False,
    progress_cb=None,
    should_cancel=None,
    recent_penalty_draws: int = 0,
    recent_penalty_factor: float = 0.5,
) -> tuple[list[WalkForwardResult], dict]:
    """Run walk-forward backtest (or load from cache).

    recent_penalty_*: aceeași penalizare după ultimele extrageri ca în producție;
    intră în cheia de cache doar când e activă.

    Returns:
        (flat_results, meta_dict)
        meta_dict include: from_cache (bool), n_predictions, n_test_draws, csv_hash
    """
    _log_stale_wf_cache_once()
    csv_hash = _csv_hash(df_source, game_type)
    dec_sig = _decision_sig(game_type, pool_size, lookback_percent,
                            recent_penalty_draws, recent_penalty_factor)
    cache_file = _cache_path(game_type, csv_hash, pool_size, int(backtest_depth_percent), dec_sig)
    meta = {
        "csv_hash": csv_hash,
        "decision_sig": dec_sig,
        "cache_file": str(cache_file),
        "from_cache": False,
        "game_type": game_type,
        "pool_size": pool_size,
        "backtest_depth_percent": backtest_depth_percent,
        # Geometria internă WF poate diferi de garanția/bugetul producției.
        # Adăugare de metadate; calculată și pe cache-hit din aceeași cheie.
        "wheel_guarantee": _wf_guarantee(pool_size, _WF_PICK.get(game_type)),
        "wheel_condition": _wf_guarantee(pool_size, _WF_PICK.get(game_type)),
        "max_variants": 0,
    }

    cached = None
    if use_cache and not force_refresh and cache_file.exists():
        try:
            cached = pickle_load_path(cache_file)
            # Obiectele scrise înainte de un câmp ADITIV nu-l au deloc (unpickle-ul
            # nu trece prin __init__) → completează-l acum, o singură dată.
            _backfill_new_fields(cached.get("flat"))
            if not cached.get("partial", False):
                # COMPLET → servim direct (fast path neschimbat).
                meta["from_cache"] = True
                meta["n_predictions"] = cached["n_predictions"]
                meta["n_test_draws"] = cached["n_test_draws"]
                meta["n_expected"] = cached.get("n_expected", cached["n_test_draws"])
                meta["partial"] = False
                meta["wheel_coverage"] = wheel_coverage_summary(cached["flat"])
                logger.info(
                    f"[WALK-FWD] Cache hit pentru {game_type} pool={pool_size} "
                    f"hash={csv_hash} dec={dec_sig} ({len(cached['flat'])} entries)"
                )
                return cached["flat"], meta
            # PARȚIAL → NU-l servim orbește: re-rulăm ca să EXTINDEM acoperirea
            # (altfel un parțial rămâne înghețat până se schimbă CSV-ul, iar mărirea
            # bugetului WF din UI n-ar avea niciun efect). La final REUNIM rularea
            # nouă cu cache-ul pe `draw_index` (vezi `_merge_partial_coverage`) —
            # acoperirile parțiale sunt cozi RECENTE ale aceleiași ferestre, dar pot
            # avea găuri diferite (pași crăpați/timeout), deci „cel mai lung îl
            # conține pe cel mai scurt" NU e garantat.
            logger.info(
                f"[WALK-FWD] Cache PARȚIAL pentru {game_type} pool={pool_size} "
                f"({cached.get('n_test_draws')}/{cached.get('n_expected')}) → re-rulez "
                f"pentru a extinde acoperirea (bugetul curent poate fi mai mare)."
            )
        except Exception as exc:
            cached = None
            logger.warning(f"[WALK-FWD] Cache load failed: {exc} — re-run")

    # Cache miss → rulează walk-forward genuin
    from loto_enterprise.core.backtesting import LotoBacktester
    logger.info(
        f"[WALK-FWD] Cache miss — rulez walk-forward genuin pentru {game_type} "
        f"pool={pool_size} depth={backtest_depth_percent}%"
    )
    bt = LotoBacktester(df_source, game_type=game_type)
    predictions = bt.run_retroactive_backtest(
        pool_size=pool_size,
        guarantee=_wf_guarantee(pool_size, _WF_PICK.get(game_type)),
        lookback_percent=lookback_percent,
        backtest_depth_percent=backtest_depth_percent,
        filter_consecutives=False,
        max_variants=0,
        simulation_step=1,
        use_feedback=False,           # decuplat pentru a măsura PUR ce face engine-ul
        enable_hard_inversion=False,  # idem
        smart_reduction=False,
        progress_cb=progress_cb,      # frac 0..1 per simulare → bară de progres în UI
        should_cancel=should_cancel,  # oprire timpurie (anulare/buget timp) → validare parțială
        # Pașii deja validați în cache-ul PARȚIAL se sar: altfel rularea nouă
        # refăcea aceiași pași recent→vechi, se oprea la același buget în același
        # loc și reuniunea nu aducea nimic — bugetul WF se consuma fără câștig.
        skip_indices=(
            {int(getattr(r, "draw_index", -1)) for r in (cached.get("flat") or [])}
            if cached is not None else None
        ),
        recent_penalty_draws=int(recent_penalty_draws or 0),
        recent_penalty_factor=float(recent_penalty_factor),
    )

    # Câte simulări „ar fi trebuit" (pentru a marca validarea ca PARȚIALĂ în UI).
    # Pe EXTRAGERILE VALIDE (bt.draws), nu pe rândurile brute ale CSV-ului: cu un
    # rând invalid în CSV, backtester-ul simulează len(draws)*depth pași, deci un
    # n_expected calculat din len(df_source) era de neatins → cache-ul rămânea
    # marcat „partial" pentru totdeauna și se re-rula la fiecare Auto-Pilot.
    _n_valid = len(bt.draws) if getattr(bt, "draws", None) else len(df_source)
    n_expected = max(1, int(_n_valid * backtest_depth_percent / 100.0))
    flat = expand_predictions_to_flat(predictions, game_type)
    meta["n_predictions"] = len(predictions)
    meta["n_test_draws"] = len(set(p.draw_index for p in predictions))
    meta["n_expected"] = n_expected
    meta["partial"] = meta["n_test_draws"] < n_expected

    # Cache PARȚIAL existent → REUNIUNE, nu „câștigă cel mai lung": nicio extragere
    # deja validată nu se pierde, iar acoperirea se acumulează între sesiuni chiar
    # dacă rularea curentă a fost oprită devreme (buget/anulare) sau a sărit pași.
    if cached is not None:
        flat, meta = _merge_partial_coverage(cached, flat, meta)

    # Acoperirea wheel-ului pe paşii validaţi. Se calculează DUPĂ reuniune, ca să
    # acopere şi paşii veniţi din cache. Sub 100% (sau necunoscută), `hits_union`
    # (hit de POOL) nu mai e egal cu „hit pe un bilet" — UI-ul avertizează.
    meta["wheel_coverage"] = wheel_coverage_summary(flat)
    _cov_sum = meta["wheel_coverage"]
    if _cov_sum["below_100"]:
        logger.warning(
            "[WALK-FWD] %s pool=%s: wheel INCOMPLET la %d/%d paşi (min %.2f%%) — "
            "hiturile de POOL (3+/4+) sunt un PLAFON, nu ce prinde un bilet.",
            game_type, pool_size, _cov_sum["below_100"], _cov_sum["known"], _cov_sum["min"],
        )
    elif _cov_sum["unknown"]:
        logger.info(
            "[WALK-FWD] %s pool=%s: acoperire necunoscută la %d/%d paşi "
            "(cache scris înainte de câmpul `wheel_coverage`).",
            game_type, pool_size, _cov_sum["unknown"], _cov_sum["n_draws"],
        )

    # Save cache (rezultatul reunit ⊇ cache → suprascriem; scriere atomică anti-corupere
    # la UI-restart în mijlocul pickle.dump — un cache trunchiat ar crăpa la load).
    try:
        pickle_store_path_atomic(cache_file, {"flat": flat, **meta})
        logger.info(f"[WALK-FWD] Cache saved → {cache_file}")
    except Exception as exc:
        logger.warning(f"[WALK-FWD] Cache save failed: {exc}")

    return flat, meta


def _stale_wf_cache_files() -> list[Path]:
    """Cache-urile WF scrise de versiuni VECHI de CACHE_VERSION (inaccesibile azi)."""
    if not CACHE_DIR.exists():
        return []
    prefix_now = f"walk_forward_{CACHE_VERSION}_"
    return sorted(
        f for f in CACHE_DIR.glob("walk_forward_*.pkl")
        if not f.name.startswith(prefix_now)
    )


def purge_stale_wf_cache(dry_run: bool = True) -> dict:
    """Inventariază (şi opţional şterge) cache-urile WF de la versiuni VECHI.

    Un bump de CACHE_VERSION schimbă doar NUMELE fişierului de cache: pickle-urile
    versiunilor anterioare rămân pe disc la nesfârşit, permanent inaccesibile.
    Funcţia e DRY-RUN implicit: doar LISTEAZĂ şi însumează, ca decizia de ştergere
    să rămână a utilizatorului.

    Spre deosebire de `clear_walk_forward_cache()`, NU atinge versiunea curentă.

    Args:
        dry_run: True (implicit) = doar raportează; False = şterge efectiv.

    Returns:
        {"n_files", "bytes", "mb", "files" (list[str]), "deleted" (bool),
         "n_deleted", "version": CACHE_VERSION}
    """
    files = _stale_wf_cache_files()
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    n_deleted = 0
    if not dry_run:
        for f in files:
            try:
                f.unlink()
                n_deleted += 1
            except OSError as exc:
                logger.warning("[WALK-FWD] nu pot şterge %s: %s", f.name, exc)
    return {
        "n_files": len(files),
        "bytes": total,
        "mb": round(total / (1024 * 1024), 1),
        "files": [f.name for f in files],
        "deleted": not dry_run,
        "n_deleted": n_deleted,
        "version": CACHE_VERSION,
    }


_stale_cache_logged = False


def _log_stale_wf_cache_once() -> None:
    """Anunţă O SINGURĂ DATĂ pe proces cât spaţiu ocupă cache-urile WF vechi.

    Pur informativ — NU şterge nimic (vezi `purge_stale_wf_cache`). Eşecurile sunt
    înghiţite: igiena de disc nu are voie să pice un walk-forward.
    """
    global _stale_cache_logged
    if _stale_cache_logged:
        return
    _stale_cache_logged = True
    try:
        info = purge_stale_wf_cache(dry_run=True)
        if info["n_files"]:
            logger.info(
                "[WALK-FWD] %d cache-uri WF de la versiuni vechi (≠%s) ocupă %.1f MB în "
                "%s/ — inaccesibile. Curăţare opţională: "
                "walk_forward_adapter.purge_stale_wf_cache(dry_run=False).",
                info["n_files"], CACHE_VERSION, info["mb"], CACHE_DIR,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WALK-FWD] inventar cache vechi eşuat: %s", exc)


def clear_walk_forward_cache() -> int:
    """Şterge toate cache-urile walk-forward (TOATE versiunile, inclusiv cea curentă);
    returnează numărul de fişiere şterse. Pentru a păstra versiunea curentă și a
    șterge doar reziduurile vechi, folosește `purge_stale_wf_cache(dry_run=False)`."""
    if not CACHE_DIR.exists():
        return 0
    deleted = 0
    for f in CACHE_DIR.glob("walk_forward_*.pkl"):
        try:
            f.unlink()
            deleted += 1
        except Exception:
            pass
    return deleted
