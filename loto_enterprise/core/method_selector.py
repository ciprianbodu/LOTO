"""Pick the winning scorer per game (and optionally per pool size).

best_methods.json schema (v2 — per-pool winners):

    {
      "_meta": {...},
      "games": {
        "loto_6_49": {
          "label": "Loto 6/49",
          "draw_n": 6,
          "overall_winner": "patchtst",
          "winners_per_pool": {"k6": "patchtst", "k7": "patchtst", ...},
          "winner_details": { "k6": {"winner":"patchtst","avg_hits":0.79,...}, ...}
        },
        ...
      }
    }

Old schema (v1, with `winner` field) is still supported as fallback.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "best_methods.json"
_CACHE: dict[str, Callable] = {}
_CONFIG: dict | None = None
_CONFIG_MTIME: float = -1.0
_CONFIG_PATH_USED: Path | None = None


def _load_config(path: str | None = None) -> dict:
    """Încarcă best_methods.json, cu reîncărcare automată la schimbarea fişierului.

    Cache-ul global e invalidat când mtime-ul fişierului se schimbă (ex. după un
    Re-Bench care rescrie decizia). Fără asta, worker-ul (Auto-Pilot) şi UI-ul
    (walk-forward) ar folosi decizia VECHE până la restart. `stat()` e ieftin.
    """
    global _CONFIG, _CONFIG_MTIME, _CONFIG_PATH_USED
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    try:
        mtime = cfg_path.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _CONFIG is not None and cfg_path == _CONFIG_PATH_USED and mtime == _CONFIG_MTIME:
        return _CONFIG
    _CONFIG_PATH_USED = cfg_path
    _CONFIG_MTIME = mtime
    if not cfg_path.exists():
        logger.warning("[method_selector] %s missing — using frequency baseline", cfg_path)
        _CONFIG = {"games": {}}
        return _CONFIG
    try:
        _CONFIG = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("[method_selector] failed to parse %s: %s", cfg_path, exc)
        _CONFIG = {"games": {}}
    return _CONFIG


# Re-Bench sare joker_urna2 (single-pick: 1 număr → rata 4+ e 0). Engine-ul tot
# cere un scorer pentru bila Joker — frequency e alegerea onestă, nu un WARNING
# la fiecare Generate (era dublu: get_ensemble fallback + get_scorer_for_game).
_UNBENCHED_SINGLE_PICK = frozenset({"joker_urna2"})


def _production_forbidden() -> frozenset[str]:
    """Metode care NU au voie să scocheze pool-ul de producție.

    = EXCLUDED_FROM_PRODUCTION (random) ∪ disabled_methods.json.
    best_methods.json vechi/manual putea totuși să le numească — fără gardă
    aici, pool-ul devenea nedeterminist sau reactiva o metodă legendată.
    """
    forbidden: set[str] = {"random"}
    try:
        from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
        forbidden |= {str(m) for m in EXCLUDED_FROM_PRODUCTION}
    except Exception:  # noqa: BLE001
        pass
    try:
        from loto_enterprise.benchmark.disabled import load_disabled
        forbidden |= {str(m) for m in load_disabled()}
    except Exception:  # noqa: BLE001
        pass
    return frozenset(forbidden)


def _sanitize_production_name(name: str | None, *, context: str) -> str | None:
    """None dacă numele e interzis / necunoscut; altfel numele curat din METHODS.

    Respinge: random, tombstone (disabled), alias-uri moarte (ml_xgb_cpu),
    orice nume care nu e în registry. Altfel UI/audit pretindea XGBoost când
    rulează frequency.
    """
    if not name:
        return None
    try:
        from loto_enterprise.benchmark.methods import METHODS, resolve_method_name
        name = resolve_method_name(str(name))
    except Exception:  # noqa: BLE001
        name = str(name)
        METHODS = {}
    if name in _production_forbidden():
        logger.warning(
            "[method_selector] %s %r interzis în producție (random/blacklist) — skip",
            context, name,
        )
        return None
    if name not in METHODS:
        logger.warning(
            "[method_selector] %s %r necunoscut (eliminat din METHODS) — skip",
            context, name,
        )
        return None
    return name


def _sanitize_ap_production(entry: dict) -> tuple[str | None, list[dict], bool]:
    """Aliniază scorer + ensemble dintr-o intrare auto_pilot (sursă unică).

    Returnează ``(scorer, clean_ensemble, scor_salvaged)``.
    Dacă scorer-ul din JSON e mort dar ensemble-ul are membri vii → scorer =
    primul membru (ca ``get_winner_name`` și engine-ul să spună același lucru).
    ``scor_salvaged`` e True când numele scorer-ului s-a schimbat față de JSON
    (UI poate marca fallback).
    """
    if not isinstance(entry, dict):
        return None, [], True
    raw_scorer = entry.get("scorer")
    scorer = _sanitize_production_name(raw_scorer, context="ap scorer")
    salvaged = bool(raw_scorer) and scorer is None

    clean_ens: list[dict] = []
    for item in entry.get("ensemble") or []:
        if not isinstance(item, dict):
            continue
        nm = _sanitize_production_name(item.get("method"), context="ap ensemble")
        if not nm:
            continue
        try:
            wt = float(item.get("weight", 0) or 0)
        except (TypeError, ValueError):
            wt = 0.0
        if wt <= 0:
            continue
        clean_ens.append({"method": nm, "weight": wt})
    if clean_ens:
        tw = sum(e["weight"] for e in clean_ens) or 1.0
        clean_ens = [{"method": e["method"], "weight": e["weight"] / tw} for e in clean_ens]

    if scorer is None and clean_ens:
        scorer = clean_ens[0]["method"]
        salvaged = True
    if scorer is not None and not clean_ens:
        clean_ens = [{"method": scorer, "weight": 1.0}]
    return scorer, clean_ens, salvaged


def _auto_pilot_entry(g: dict, pool_size: int | None) -> dict:
    """Intrarea auto_pilot_per_pool[kN], cu fallback la cel mai apropiat k."""
    if pool_size is None or not isinstance(g, dict):
        return {}
    apm = g.get("auto_pilot_per_pool") or {}
    if not isinstance(apm, dict):
        return {}
    entry = apm.get(f"k{int(pool_size)}") or {}
    if isinstance(entry, dict) and (entry.get("scorer") or entry.get("ensemble")):
        return entry
    avail = sorted(
        int(k[1:]) for k in apm
        if isinstance(k, str) and k.startswith("k") and k[1:].isdigit()
    )
    if not avail:
        return entry if isinstance(entry, dict) else {}
    nearest = min(avail, key=lambda k: abs(k - int(pool_size)))
    cand = apm.get(f"k{nearest}") or {}
    if isinstance(cand, dict) and (cand.get("scorer") or cand.get("ensemble")):
        logger.info(
            "[method_selector] pool k%d absent → folosesc k%d (cel mai apropiat decis)",
            int(pool_size), nearest,
        )
        return cand
    return entry if isinstance(entry, dict) else {}


def get_winner_name(
    game_key: str,
    pool_size: int | None = None,
    config_path: str | None = None,
) -> str:
    """Resolve the configured winner method for (game, pool_size).

    Priority order (v4 — Auto-Pilot consistency first):
      1. auto_pilot_per_pool[kN].scorer  (decision algorithm — preferred,
         consistent cu ce afișează butonul Auto-Pilot în UI)
      2. winners_per_pool_best (best of no-BL vs +BL)
      3. winners_per_pool (no-BL only)
      4. overall_winner / overall_winner_bl (game-level)
      5. legacy v1 'winner' field
      6. 'frequency' baseline (last resort)
    """
    if game_key in _UNBENCHED_SINGLE_PICK:
        return "frequency"

    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})

    def _ok(name) -> str | None:
        return _sanitize_production_name(name, context="winner")

    if pool_size is not None:
        ap = _auto_pilot_entry(g, pool_size)
        # Scorer + ensemble din aceeași sanitizare — altfel un scorer mort +
        # ensemble viu făcea get_winner_name→frequency dar engine→membru.
        if ap:
            n, _ens, _salv = _sanitize_ap_production(ap)
            if n:
                return n
        key = f"k{pool_size}"
        # v3 fallback: best of (no-bl, +bl)
        wpp_best = g.get("winners_per_pool_best", {})
        if isinstance(wpp_best, dict) and wpp_best.get(key, {}).get("winner"):
            n = _ok(wpp_best[key]["winner"])
            if n:
                return n
        wpp = g.get("winners_per_pool", {})
        if isinstance(wpp, dict) and wpp.get(key):
            n = _ok(wpp[key])
            if n:
                return n

    for fld in ("overall_winner", "overall_winner_bl", "winner"):
        if g.get(fld):
            n = _ok(g[fld])
            if n:
                return n
    logger.warning("[method_selector] no winner for %s — defaulting to frequency", game_key)
    return "frequency"


def should_use_blacklist(
    game_key: str,
    pool_size: int | None = None,
    config_path: str | None = None,
) -> bool:
    """Return True if the benchmark says blacklist helps for this (game, pool).

    Priority (consistent with get_winner_name):
      1. auto_pilot_per_pool[kN].use_blacklist  (v4 — preferat)
      2. winners_per_pool_best[kN].use_blacklist  (v3 fallback)
      3. default True (safe — production rulează blacklist-ul oricum)
    """
    if pool_size is None:
        return True
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    # v4: auto_pilot decision (consistent cu UI)
    apm = g.get("auto_pilot_per_pool", {})
    if isinstance(apm, dict):
        entry = apm.get(f"k{pool_size}", {})
        if "use_blacklist" in entry:
            return bool(entry["use_blacklist"])
    # v3 fallback
    wpp_best = g.get("winners_per_pool_best", {})
    if not isinstance(wpp_best, dict):
        return True
    entry = wpp_best.get(f"k{pool_size}", {})
    if "use_blacklist" not in entry:
        return True
    return bool(entry["use_blacklist"])


def get_scorer_for_game(
    game_key: str,
    pool_size: int | None = None,
    config_path: str | None = None,
) -> Callable:
    """Return the chosen scoring function for a given (game, pool_size)."""
    name = get_winner_name(game_key, pool_size, config_path)
    cache_key = f"{name}#{pool_size or 'overall'}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    try:
        from loto_enterprise.benchmark.methods import METHODS, resolve_method_name
    except Exception as exc:
        logger.error("[method_selector] benchmark.methods import failed: %s", exc)
        raise

    name = resolve_method_name(name)
    if name not in METHODS or name in _production_forbidden():
        if name in _production_forbidden():
            logger.warning("[method_selector] scorer %r interzis — falling back to frequency", name)
        else:
            logger.warning("[method_selector] unknown winner %r, falling back to frequency", name)
        name = "frequency"
    fn, _family, _train, _notes = METHODS[name]
    if getattr(fn, "_unavailable_reason", None):
        logger.warning(
            "[method_selector] winner %r unavailable (%s) — falling back to frequency",
            name, fn._unavailable_reason,
        )
        fn = METHODS["frequency"][0]
        name = "frequency"
    _CACHE[cache_key] = fn
    return fn


def get_ensemble_for_game(
    game_key: str,
    pool_size: int | None = None,
    config_path: str | None = None,
    max_methods: int = 3,
) -> list[tuple[str, Callable, float]]:
    """Return [(method_name, scorer_fn, weight), ...] — pondere ∝ limita Wilson
    a ratei T+ (scrisă de decision.py în auto_pilot_per_pool[kN].ensemble).

    Variance-reduction: combină scorurile mai multor metode CALIFICATE (nu doar
    câștigătorul unic) — loteria e aleatoare, diferența dintre metode e majoritar
    zgomot statistic, deci blend-ul reduce riscul ca o singură metodă "norocoasă"
    pe date de test să domine complet pool-ul.

    Fallback (best_methods.json vechi, fără câmp 'ensemble', sau toate metodele
    din ensemble indisponibile la runtime) → listă cu UN SINGUR membru
    (get_winner_name + get_scorer_for_game, weight=1.0) — identic cu
    comportamentul dinaintea ensemble-ului.
    """
    def _single_fallback() -> list[tuple[str, Callable, float]]:
        # Numele și callable-ul trebuie să coincidă (ambele după sanitizare).
        name = get_winner_name(game_key, pool_size, config_path)
        fn = get_scorer_for_game(game_key, pool_size, config_path)
        return [(name, fn, 1.0)]

    # Re-Bench sare single-pick (1 număr → rata 4+ e 0). Nu citi un ensemble
    # vechi din best_methods.json — frequency e scorer-ul onest.
    if game_key in _UNBENCHED_SINGLE_PICK:
        return _single_fallback()

    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    entry: dict = _auto_pilot_entry(g, pool_size) if pool_size is not None else {}

    raw_list = entry.get("ensemble") if isinstance(entry, dict) else None
    if not raw_list:
        return _single_fallback()

    try:
        from loto_enterprise.benchmark.methods import METHODS, resolve_method_name
    except Exception as exc:
        logger.error("[method_selector] benchmark.methods import failed: %s", exc)
        raise

    out: list[tuple[str, Callable, float]] = []
    for item in raw_list[:max_methods]:
        name = item.get("method") if isinstance(item, dict) else None
        weight = float(item.get("weight", 0.0)) if isinstance(item, dict) else 0.0
        if not name or weight <= 0:
            continue
        name = _sanitize_production_name(name, context="ensemble member")
        if not name:
            continue
        cache_key = f"{name}#{pool_size or 'overall'}"
        fn = _CACHE.get(cache_key)
        if fn is None:
            meta = METHODS.get(name)
            if meta is None:
                logger.warning("[method_selector] ensemble member %r unknown — skip", name)
                continue
            fn = meta[0]
            if getattr(fn, "_unavailable_reason", None):
                logger.warning("[method_selector] ensemble member %r unavailable (%s) — skip",
                               name, fn._unavailable_reason)
                continue
            _CACHE[cache_key] = fn
        out.append((name, fn, weight))

    if not out:
        logger.warning("[method_selector] toate metodele din ensemble %s indisponibile — fallback winner unic",
                        game_key)
        return _single_fallback()

    total_w = sum(w for _, _, w in out)
    if total_w <= 0:
        return [(n, fn, 1.0 / len(out)) for n, fn, _ in out]
    return [(n, fn, w / total_w) for n, fn, w in out]


def _has_variance(raw: dict) -> bool:
    """Scoruri PLATE (toate egale) = fără informație de ranking — filtrate din blend.

    Tot aici cad și scorurile NE-FINITE (NaN/inf): un singur NaN otrăvește
    min-max-ul membrului ȘI suma ponderată (numărul respectiv iese NaN în pool),
    iar `core.ranking.rank_by_score` devine dependent de ordinea de inserare pe
    NaN. Un membru cu scoruri ne-finite = metodă DEFECTĂ, nu semnal slab → afară
    din blend, la fel ca unul plat (același motiv raportat: „flat").
    """
    vals = [float(v) for v in raw.values()]
    if len(vals) < 2:
        return False
    if not all(math.isfinite(v) for v in vals):
        return False
    return (max(vals) - min(vals)) > 1e-12


# --- Decorelare membri ensemble -------------------------------------------
# Prag peste care doi membri sunt considerați REDUNDANȚI (aceeași informație de
# ranking) — auditul a arătat perechi cu |Spearman| ∈ {0.98, 1.00} în ensemble-
# urile scrise de decision.py (ex. 649_katz15_gap85 + 649_katz25_gap75 = blend-uri
# pe ACELEAȘI componente, cover_positional_bands + frequency la 5/40 → r=+0.98).
# Un asemenea "ensemble" e un no-op: nu reduce varianța, doar dublează un semnal.
MAX_MEMBER_CORR = 0.95
# Sub atâtea numere comune, Spearman e degenerat (cu 2 puncte e mereu ±1) —
# nu filtrăm nimic. Atenție: eșantionul e UNIVERSUL de numere scorate, nu
# pool-ul: 20 (joker_urna2, single-pick — loto_engine.py fixează max_num=20),
# 40 (5/40), 45 (joker_urna1), 49 (6/49). Deci pragul de 8 NU se atinge în
# producție (protejează apelurile sintetice: teste, scoruri parțiale), DAR pe
# universuri mici (20) un |Spearman| >= MAX_MEMBER_CORR se atinge mult mai ușor
# din întâmplare decât pe 49 — mai ales cu scorere care produc platouri de
# egalități. Dacă vreodată eliminările pe joker_urna2 par arbitrare, criteriul
# corect e un p-value (sau un prag scalat cu n), nu doar |r|.
_MIN_CORR_SAMPLE = 8

_SPEARMAN_FN: Callable | None = None
_SPEARMAN_TRIED = False


def _get_spearman() -> Callable | None:
    """scipy.stats.spearmanr dacă e disponibil (import LEAZY, o singură dată).

    scipy vine oricum cu sklearn, dar method_selector e importat și în contexte
    minimale → fără dependență obligatorie: la eșec cădem pe implementarea numpy.
    """
    global _SPEARMAN_FN, _SPEARMAN_TRIED
    if not _SPEARMAN_TRIED:
        _SPEARMAN_TRIED = True
        try:
            from scipy.stats import spearmanr
            _SPEARMAN_FN = spearmanr
        except Exception as exc:
            logger.debug("[method_selector] scipy.stats indisponibil (%s) — Spearman pe numpy", exc)
            _SPEARMAN_FN = None
    return _SPEARMAN_FN


def _rank_avg(vals: list[float]) -> list[float]:
    """Ranguri cu MEDIA pe egalități (echivalent rankdata(..., method='average')).

    Egalitățile sunt frecvente la scorurile de loto (multe metode dau platouri),
    deci rangul „competition" ar introduce corelații artificiale.
    """
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman_numpy(a: list[float], b: list[float]) -> float | None:
    """Fallback: Pearson pe ranguri (= Spearman) cu numpy; None dacă e degenerat."""
    ra, rb = _rank_avg(a), _rank_avg(b)
    try:
        import numpy as _np
        x = _np.asarray(ra, dtype=float)
        y = _np.asarray(rb, dtype=float)
        xc = x - x.mean()
        yc = y - y.mean()
        den = float(_np.sqrt(float((xc * xc).sum()) * float((yc * yc).sum())))
        if den <= 0.0:
            return None
        return float((xc * yc).sum() / den)
    except Exception:
        # nici numpy nu e disponibil → Python pur (≤49 numere, cost neglijabil)
        n = len(ra)
        if n < 2:
            return None
        ma, mb = sum(ra) / n, sum(rb) / n
        sxy = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
        sxx = sum((v - ma) ** 2 for v in ra)
        syy = sum((v - mb) ** 2 for v in rb)
        den = (sxx * syy) ** 0.5
        if den <= 0.0:
            return None
        return sxy / den


def _pair_corr(raw_a: dict, raw_b: dict) -> float | None:
    """Spearman SEMNAT între două seturi de scoruri, pe numerele COMUNE.

    Valoarea NU e în modul: semnul e semnificativ mai departe (apelantul aplică
    `abs()` pentru prag, iar `describe_ensemble` folosește semnul ca să eticheteze
    eliminarea „correlated" vs „anticorrelated").

    None = necalculabil → perechea nu poate justifica o eliminare:
      - mai puține de `_MIN_CORR_SAMPLE` numere comune;
      - valori ne-finite (NaN/inf) în oricare dintre seturi;
      - varianță nulă pe ranguri (numitor 0) sau eșec de calcul.
    """
    common = sorted(set(raw_a.keys()) & set(raw_b.keys()))
    if len(common) < _MIN_CORR_SAMPLE:
        return None
    a = [float(raw_a[k]) for k in common]
    b = [float(raw_b[k]) for k in common]
    # NaN/inf: scipy întoarce NaN (deci am cădea pe fallback-ul numpy), iar
    # `_rank_avg` sortează NaN-urile pe o poziție arbitrară dar STABILĂ → ar
    # rezulta o corelație FINITĂ și BOGUS (ex. 1.0000 pentru două seturi care
    # diferă doar printr-un NaN) care ar justifica o eliminare reală. Un scor
    # ne-finit înseamnă „metodă defectă", nu „membru redundant".
    if not all(math.isfinite(v) for v in a) or not all(math.isfinite(v) for v in b):
        logger.debug("[method_selector] corelație sărită: scoruri ne-finite pe %d numere comune", len(common))
        return None
    sp = _get_spearman()
    if sp is not None:
        try:
            res = sp(a, b)
            r = getattr(res, "statistic", None)
            if r is None:
                r = res[0]
            r = float(r)
            if r == r:  # nu NaN
                return r
        except Exception as exc:
            logger.debug("[method_selector] spearmanr a eșuat (%s) — fallback numpy", exc)
    r = _spearman_numpy(a, b)
    if r is None or r != r:
        return None
    return r


# Ordinea de lectură a etapelor = ordinea de execuție din `_resolve_members`:
#   1. `_split_active`        — filtru de varianță (goale/plate)
#   2. `_select_decorrelated` — decorelare greedy Spearman
#   3. `_resolve_members`     — pipeline-ul care le înlănțuie (+ memo + logare)


def _split_active(
    contributions: list[tuple[str, dict[int, float], float]],
) -> tuple[list[tuple[str, dict[int, float], float]], list[tuple[str, str]]]:
    """Împarte contribuțiile în (active, dropped) — logica UNICĂ de filtrare,
    partajată de combine_ensemble_scores și describe_ensemble (ca UI-ul să vadă
    EXACT aceeași listă pe care blend-ul o folosește efectiv).

    Ignoră scoruri goale SAU plate (toate egale) — altfel un Ridge defect
    (toate 0) + prime_bias (2 nivele) → pool = „cele mai mici compuse".
    „flat" acoperă și scorurile NE-FINITE (vezi `_has_variance`).
    dropped = [(nume, motiv)], motiv ∈ {"empty", "flat"}."""
    active: list[tuple[str, dict[int, float], float]] = []
    dropped: list[tuple[str, str]] = []
    for name, raw, w in contributions:
        if not raw:
            dropped.append((name, "empty"))
        elif not _has_variance(raw):
            dropped.append((name, "flat"))
        else:
            active.append((name, raw, w))
    return active, dropped


def _select_decorrelated(
    active: list[tuple[str, dict[int, float], float]],
) -> tuple[list[tuple[str, dict[int, float], float]], list[tuple[str, float, str]]]:
    """Selecție GREEDY decorelată peste membrii activi (deja filtrați de plate).

    Parcurge membrii în ordinea DESCRESCĂTOARE a ponderii și acceptă unul doar
    dacă |Spearman| față de TOȚI membrii deja acceptați e sub `MAX_MEMBER_CORR`.
    Două cazuri, ambele nocive:
      - r >= +0.95 → redundant: blend-ul e un no-op (aceeași informație de două ori);
      - r <= -0.95 → ANTI-corelat: tot redundant, dar din alt motiv. După min-max
        normalizare, y ≈ 1 - x, deci blend-ul devine w2 + (w1-w2)*x — o funcţie
        MONOTONĂ de x. Ranking-ul rezultat e identic cu al membrului mai greu, iar
        la ponderi egale (w1 == w2) degenerează în scor CONSTANT (pool degenerat).
        În ambele cazuri membrul nu aduce informaţie nouă → EXCLUS.

    NU loghează nimic: logarea e a apelantului (`_resolve_members`), ca
    `describe_ensemble` să poată reface calculul fără să reemită WARNING-urile.

    Returnează (kept, dropped) unde dropped = [(nume, r_max_semnat, față_de_cine)].
    `kept` păstrează ORDINEA ORIGINALĂ a listei `active` — când nu se elimină
    nimic, suma ponderată rămâne bit-identică cu cea dinaintea acestui filtru.
    """
    if len(active) < 2:
        return active, []
    # Tie-break pe NUME, nu pe indice: ponderile Wilson ies frecvent egale
    # (34/33/33), iar ordinea listei depinde de ordinea cheilor din
    # best_methods.json → acelaşi ensemble ar fi dat pool-uri diferite după o
    # simplă reserializare a JSON-ului. Numele e stabil între rulări.
    order = sorted(range(len(active)), key=lambda i: (-active[i][2], active[i][0]))
    kept_idx: list[int] = []
    dropped: list[tuple[str, float, str]] = []
    for i in order:
        name, raw, _w = active[i]
        worst_r = 0.0
        worst_vs = ""
        for j in kept_idx:
            r = _pair_corr(raw, active[j][1])
            if r is None:
                continue
            if abs(r) > abs(worst_r):
                worst_r, worst_vs = r, active[j][0]
        if worst_vs and abs(worst_r) >= MAX_MEMBER_CORR:
            dropped.append((name, round(worst_r, 4), worst_vs))
            continue
        kept_idx.append(i)
    kept = [active[i] for i in sorted(kept_idx)]
    return kept, dropped


# Perechile (eliminat, față_de_cine, tip) deja raportate la nivel WARNING/INFO.
# În walk-forward `combine_ensemble_scores` e apelat de sute de ori cu ACELAȘI
# ensemble → aceeași eliminare ar produce sute de linii identice în loto.log.
# Prima apariție se loghează normal, restul cad pe DEBUG.
_LOGGED_DROPS: set[tuple[str, str, str]] = set()

# Memo cu O SINGURĂ intrare pentru ultimul apel la `_resolve_members`:
# `describe_ensemble` (API pt UI) și `combine_ensemble_scores` refac exact
# același pipeline pe aceleași contribuții — fără memo s-ar plăti de două ori
# costul O(k²) de spearmanr. `logged` reține dacă rezultatul a fost deja
# raportat, ca un `describe` (silențios) urmat de un `combine` să nu piardă
# WARNING-ul, iar un `combine` urmat de `describe` să nu-l dubleze.
# Tuplu (nu dict): rebindarea unei singure variabile globale e atomică sub GIL,
# deci nu poate exista o stare intermediară „cheie nouă + valoare veche".
_RESOLVE_MEMO: tuple | None = None  # (key, (active, dropped, dropped_corr), logged)


def _memo_key(contributions: list[tuple[str, dict[int, float], float]]):
    """Cheie hashabilă pentru memo (None = necalculabilă → fără memo).

    Include scorurile, nu doar numele: în walk-forward același ensemble e
    rulat pe ferestre diferite, deci pool-ul rezultat DIFERĂ.
    """
    try:
        return tuple(
            (str(n), float(w), tuple(sorted((int(k), float(v)) for k, v in raw.items())))
            for n, raw, w in contributions
        )
    except Exception:
        return None


def _log_decorrelation(dropped_corr: list[tuple[str, float, str]]) -> None:
    """Raportează eliminările de corelație, o SINGURĂ dată per (pereche, tip)."""
    for name, r, vs in dropped_corr:
        anti = r <= -MAX_MEMBER_CORR
        key = (name, vs, "anti" if anti else "redundant")
        first = key not in _LOGGED_DROPS
        _LOGGED_DROPS.add(key)
        if anti:
            log = logger.warning if first else logger.debug
            log(
                "[method_selector] ensemble: %r ANTI-corelat cu %r (Spearman=%.4f) — "
                "EXCLUS (blend-ul devine monoton în celălalt membru; la ponderi "
                "egale → scor constant / pool degenerat)",
                name, vs, r,
            )
        else:
            log = logger.info if first else logger.debug
            log(
                "[method_selector] ensemble: %r redundant cu %r (Spearman=%.4f) — exclus",
                name, vs, r,
            )


def _resolve_members(
    contributions: list[tuple[str, dict[int, float], float]],
    log: bool = True,
) -> tuple[list[tuple[str, dict[int, float], float]], list[tuple[str, str]], list[tuple[str, float, str]]]:
    """Pipeline-ul COMPLET de selecție a membrilor: varianță → decorelare.

    Sursa UNICĂ de adevăr pentru combine_ensemble_scores și describe_ensemble
    (UI-ul trebuie să vadă exact membrii pe care blend-ul îi folosește efectiv).

    `log=False` (folosit de `describe_ensemble`) = calcul pur, fără efecte în
    log: afișarea în UI nu trebuie să pară o a doua decizie de excludere.
    """
    global _RESOLVE_MEMO
    key = _memo_key(contributions)
    memo = _RESOLVE_MEMO
    if key is not None and memo is not None and key == memo[0]:
        result = memo[1]
        if log and not memo[2]:
            _log_decorrelation(result[2])
            _RESOLVE_MEMO = (key, result, True)
        return result

    active, dropped = _split_active(contributions)
    active, dropped_corr = _select_decorrelated(active)
    result = (active, dropped, dropped_corr)
    if log:
        _log_decorrelation(dropped_corr)
    if key is not None:
        _RESOLVE_MEMO = (key, result, log)
    return result


def describe_ensemble(
    contributions: list[tuple[str, dict[int, float], float]],
) -> dict:
    """Metadata pt afișare (UI): cine e EFECTIV activ în blend vs. nominal.

    Aplică exact filtrarea din combine_ensemble_scores (goale/plate afară,
    apoi decorelarea greedy) FĂRĂ să combine scorurile — UI-ul o poate apela
    ca să afișeze membrii reali + ponderile efectiv folosite, nu cele nominale.
    Apel PUR: nu loghează nimic (vezi `_resolve_members(log=False)`) și
    refolosește memo-ul, deci nu re-plătește costul O(k²) de spearmanr.

    Returnează:
        active  — [{"method", "weight"}] ponderi RENORMALIZATE pe membrii activi
        dropped — [{"method", "reason", "r", "vs"}] TOATE eliminările, în
            ordinea etapelor. Schema e UNIFORMĂ: pentru „empty"/„flat" (unde
            corelația nici nu se calculează) "r" și "vs" sunt None, ca un
            consumator să poată itera fără garda de KeyError.
            reason ∈ {"empty", "flat", "correlated", "anticorrelated"}.
        dropped_correlated — doar eliminările de corelație, aceleași câmpuri
            (comod pt UI: „redundant cu X, r=0.98")
        nominal_count / active_count — dimensiunile listelor
        single_active_normalized — True când nominal >1 dar 1 activ (scorurile
            supraviețuitorului sunt min-max normalizate, nu brute)
        fallback_flat — True când NICIUN membru nu are varianță și blend-ul
            cade pe primul nevid (chiar plat)

    ⚠️ Vocabularul de `reason` NU e închis la nivel de proiect: decizia de bench
    (`decision._select_ensemble_members`) emite, cu ACELEAȘI chei
    {method, vs, r, reason}, o a cincea valoare — "perf_signature" — scrisă în
    best_methods.json sub `ensemble_dropped_redundant`. Sunt DOUĂ straturi cu
    axe diferite: aici se măsoară corelația între SCORURI (per număr, la
    generare), acolo între SEMNĂTURILE DE PERFORMANȚĂ (rate T+ pe folds). Un
    consumator de UI care traduce `reason` trebuie deci să acopere
    {"empty", "flat", "correlated", "anticorrelated", "perf_signature"}.
    """
    active, dropped, dropped_corr = _resolve_members(contributions, log=False)
    if len(active) == 1:
        active_out = [{"method": active[0][0], "weight": 1.0}]
    elif active:
        total_w = sum(w for _, _, w in active) or 1.0
        active_out = [{"method": n, "weight": w / total_w} for n, _raw, w in active]
    else:
        active_out = []
    corr_out = [
        {
            "method": n,
            "reason": "anticorrelated" if r <= -MAX_MEMBER_CORR else "correlated",
            "r": r,
            "vs": vs,
        }
        for n, r, vs in dropped_corr
    ]
    return {
        "active": active_out,
        # schemă uniformă: „empty"/„flat" primesc r/vs = None (nu s-a calculat
        # nicio corelație pentru ele), ca `dropped` să fie iterabilă uniform
        "dropped": (
            [{"method": n, "reason": reason, "r": None, "vs": None} for n, reason in dropped]
            + corr_out
        ),
        "dropped_correlated": corr_out,
        "nominal_count": len(contributions),
        "active_count": len(active),
        "single_active_normalized": len(contributions) > 1 and len(active) == 1,
        "fallback_flat": not active and any(raw for _n, raw, _w in contributions),
    }


def combine_ensemble_scores(
    contributions: list[tuple[str, dict[int, float], float]],
    audit: dict | None = None,
) -> dict[int, float]:
    """Combină scorurile brute ale mai multor metode într-un singur scor per
    număr, ponderat. Fiecare metodă e normalizată min-max la [0,1] ÎNAINTE de
    combinare — altfel o metodă cu scala de valori mai mare ar domina artificial
    suma, indiferent de calitatea ei relativă.

    `contributions` = [(method_name, {num: raw_score}, weight), ...] — ponderile
    NU trebuie să fie deja normalizate la sumă 1 (se renormalizează aici pe baza
    metodelor care au produs efectiv scoruri, ca metodele eșuate/goale să nu
    reducă artificial suma ponderilor active).

    DECORELARE: după filtrul de varianță se aplică o selecție GREEDY
    (`_select_decorrelated`) — un membru intră în blend doar dacă |Spearman|
    față de toți cei deja acceptați e sub `MAX_MEMBER_CORR`. Fără ea, „ensemble-
    ul" putea fi un no-op în două feluri: doi membri cu r=+0.98 = același semnal
    numărat de două ori; doi membri cu r=-1.0 → la ponderi ~EGALE blend-ul
    devine CONSTANT (pool degenerat), iar la ponderi inegale degenerează în
    ranking-ul membrului DOMINANT (blend = w2 + (w1-w2)·x, monoton în x) — în
    ambele cazuri al doilea membru nu aduce informație.
    Ponderile se renormalizează pe membrii RĂMAȘI.

    `audit` (opțional) — dict în care se scriu membrii EFECTIV folosiți, ca
    filtrarea să fie OBSERVABILĂ din UI, nu tăcută:
        audit["ensemble_active"]  = [(nume, pondere_renormalizată), ...]
        audit["ensemble_dropped"] = [nume, ...]  — doar goale/plate (semantică
            neschimbată față de versiunea dinaintea decorelării)
        audit["ensemble_dropped_correlated"] = [(nume, r_max, față_de_cine), ...]
        + flag-urile single_active_normalized / fallback_flat când e cazul.

    Bit-identitate: DOAR cu ensemble NOMINAL de exact 1 membru (decizia n-a
    construit un blend real) scorurile lui se întorc BRUTE, NEnormalizate —
    identic cu apelul direct al scorer-ului (CLAUDE.md regula 1); cu un singur
    membru nu se calculează nicio corelație. Când nominal >1 dar rămâne 1 singur
    membru ACTIV, scorurile lui sunt min-max normalizate (rank-preserving, pool
    identic) — exact „membru normalizat înainte de combinare", nu un scorer
    diferit pe tăcute.
    """
    active, dropped, dropped_corr = _resolve_members(contributions)

    if audit is not None:
        if len(active) == 1:
            audit["ensemble_active"] = [(active[0][0], 1.0)]
        elif active:
            _tw = sum(w for _, _, w in active) or 1.0
            audit["ensemble_active"] = [(n, w / _tw) for n, _raw, w in active]
        else:
            audit["ensemble_active"] = []
        audit["ensemble_dropped"] = [n for n, _reason in dropped]
        audit["ensemble_dropped_correlated"] = [(n, r, vs) for n, r, vs in dropped_corr]

    if not active:
        # Fallback doar pe scoruri FINITE și plate (toate egale). Un dict cu NaN
        # otrăvea rank_by_score (tie-break pe număr nu intră niciodată) și apărea
        # simultan în dropped ȘI active. Fără fallback finit → {} → frequency.
        for name, raw, w in contributions:
            if not raw:
                continue
            vals = [float(v) for v in raw.values()]
            if vals and all(math.isfinite(v) for v in vals):
                if audit is not None:
                    audit["ensemble_active"] = [(name, 1.0)]
                    audit["ensemble_fallback_flat"] = True
                return {int(k): float(v) for k, v in raw.items()}
        if audit is not None:
            audit["ensemble_active"] = []
            audit["ensemble_fallback_empty"] = True
        return {}
    if len(active) == 1:
        _name, raw, _w = active[0]
        if len(contributions) == 1:
            # ensemble NOMINAL cu 1 membru → scoruri BRUTE (bit-identic, vezi docstring)
            return {int(k): float(v) for k, v in raw.items()}
        # nominal >1 dar 1 activ → min-max normalize (monoton → același pool),
        # consecvent cu tratamentul membrilor dintr-un blend real
        if audit is not None:
            audit["ensemble_single_active_normalized"] = True
        vals = list(raw.values())
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        return {int(k): (float(v) - lo) / span for k, v in raw.items()}

    total_w = sum(w for _, _, w in active) or 1.0
    combined: dict[int, float] = {}
    for _name, raw, weight in active:
        w_norm = weight / total_w
        vals = list(raw.values())
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        for num, v in raw.items():
            scaled = (float(v) - lo) / span
            combined[int(num)] = combined.get(int(num), 0.0) + w_norm * scaled
    return combined


def summary_line(game_key: str, pool_size: int | None = None,
                 config_path: str | None = None) -> str:
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    label = g.get("label", game_key)
    if pool_size is not None:
        winner = get_winner_name(game_key, pool_size, config_path)
        details = g.get("winner_details", {}).get(f"k{pool_size}", {})
        suffix = f" (bench avg_hits={details['avg_hits']:.3f})" if "avg_hits" in details else ""
        return f"[{label} · pool K={pool_size}] scorer = {winner}{suffix}"
    winner = get_winner_name(game_key, config_path=config_path)
    return f"[{label}] overall scorer = {winner}"


def all_per_pool_winners(game_key: str, config_path: str | None = None) -> dict[int, str]:
    """Map pool_size_int -> winner_method_name for this game."""
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    wpp = g.get("winners_per_pool", {}) or {}
    out: dict[int, str] = {}
    for k, v in wpp.items():
        if not isinstance(k, str) or not k.startswith("k"):
            continue
        try:
            out[int(k[1:])] = v
        except ValueError:
            continue
    return out


def recommend_optimal_config(
    game_key: str,
    pool_size: int,
    config_path: str | None = None,
) -> dict:
    """Return the optimal (scorer, sim_depth_pct, use_blacklist) for (game, pool).

    Consumed by the auto-pilot - reads `auto_pilot_per_pool[kN]` from
    best_methods.json. The decision algorithm guarantees:
        - scorer beats random baseline in >=60% of regressive windows
        - sim_depth_pct is the window where avg_hits peaks (>=30 test draws)
        - use_blacklist = True only if +BL outperforms no-BL at that window

    Returns dict with keys: scorer, sim_depth_pct, use_blacklist, avg_hits,
    rationale, ensemble, rate_col_used, rate_col_mismatch, low_confidence,
    ensemble_dropped_redundant, fallback.

    E SINGURUL API prin care UI-ul citește decizia → lista de chei e o listă
    ALBĂ: orice câmp nou scris de `decision.py` care trebuie afișat se adaugă
    EXPLICIT aici, altfel rămâne invizibil (date moarte în best_methods.json).
    """
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    apm = g.get("auto_pilot_per_pool", {})
    entry = apm.get(f"k{pool_size}", {})

    # Pool cerut în afara gamei decise de bench (ex. k24, dar bench-ul a evaluat
    # doar k6..k20) → folosim cel mai APROPIAT pool decis, în loc de fallback.
    if not (entry and "scorer" in entry) and apm:
        avail = sorted(int(k[1:]) for k in apm if k.startswith("k") and k[1:].isdigit())
        if avail:
            nearest = min(avail, key=lambda k: abs(k - pool_size))
            cand = apm.get(f"k{nearest}", {})
            if cand and "scorer" in cand:
                logger.info("[method_selector] %s: pool k%d absent → folosesc k%d (cel mai apropiat decis)",
                            game_key, pool_size, nearest)
                entry = cand

    if entry and "scorer" in entry:
        scorer, clean_ens, salvaged = _sanitize_ap_production(entry)
        if not scorer:
            scorer = get_winner_name(game_key, pool_size=pool_size, config_path=config_path)
            clean_ens = [{"method": scorer, "weight": 1.0}]
            salvaged = True
        rationale = entry.get("rationale", "") or ""
        if salvaged:
            rationale = (
                (rationale + " | " if rationale else "")
                + "scorer/ensemble sanitizat (nume eliminat din METHODS)"
            ).strip(" |")
        return {
            "scorer": scorer,
            "sim_depth_pct": int(entry.get("sim_depth_pct", 40)),
            "use_blacklist": bool(entry.get("use_blacklist", False)),
            "avg_hits": float(entry.get("avg_hits", 0.0)),
            "rationale": rationale,
            "ensemble": clean_ens,
            "rate_col_used": entry.get("rate_col_used"),
            "rate_col_mismatch": bool(entry.get("rate_col_mismatch", False)),
            "low_confidence": bool(entry.get("low_confidence", False)) or salvaged,
            "ensemble_dropped_redundant": entry.get("ensemble_dropped_redundant") or [],
            "fallback": salvaged,
        }

    scorer = get_winner_name(game_key, pool_size=pool_size, config_path=config_path)
    use_bl = should_use_blacklist(game_key, pool_size=pool_size, config_path=config_path)
    return {
        "scorer": scorer,
        "sim_depth_pct": 40,
        "use_blacklist": use_bl,
        "avg_hits": 0.0,
        "rationale": "fallback: no auto_pilot_per_pool entry - using per-pool winner",
        "ensemble": [{"method": scorer, "weight": 1.0}],
        # nu există intrare de decizie pentru (joc, pool) → alegerea nu e
        # susținută de nicio măsurătoare: un fallback E prin definiție low confidence
        "low_confidence": True,
        "ensemble_dropped_redundant": [],
        "fallback": True,
    }


def get_all_auto_pilot_configs(
    game_key: str, config_path: str | None = None
) -> dict[int, dict]:
    """Return {pool_size: optimal_config} for this game - for UI rendering."""
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    apm = g.get("auto_pilot_per_pool", {}) or {}
    out: dict[int, dict] = {}
    for k, c in apm.items():
        if not isinstance(k, str) or not k.startswith("k"):
            continue
        try:
            ps = int(k[1:])
        except ValueError:
            continue
        if "error" in c:
            continue
        out[ps] = c
    return out
