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
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})

    if pool_size is not None:
        key = f"k{pool_size}"
        # v4: auto_pilot decision (consistent cu UI Auto-Pilot button)
        apm = g.get("auto_pilot_per_pool", {})
        if isinstance(apm, dict) and apm.get(key, {}).get("scorer"):
            return apm[key]["scorer"]
        # v3 fallback: best of (no-bl, +bl)
        wpp_best = g.get("winners_per_pool_best", {})
        if isinstance(wpp_best, dict) and wpp_best.get(key, {}).get("winner"):
            return wpp_best[key]["winner"]
        wpp = g.get("winners_per_pool", {})
        if isinstance(wpp, dict) and wpp.get(key):
            return wpp[key]

    for fld in ("overall_winner", "overall_winner_bl", "winner"):
        if g.get(fld):
            return g[fld]
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
    if name not in METHODS:
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
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    entry: dict = {}
    if pool_size is not None:
        apm = g.get("auto_pilot_per_pool", {})
        if isinstance(apm, dict):
            entry = apm.get(f"k{pool_size}", {}) or {}
            if not entry.get("ensemble") and apm:
                avail = sorted(int(k[1:]) for k in apm if k.startswith("k") and k[1:].isdigit())
                if avail:
                    nearest = min(avail, key=lambda k: abs(k - pool_size))
                    cand = apm.get(f"k{nearest}", {}) or {}
                    if cand.get("ensemble"):
                        entry = cand

    def _single_fallback() -> list[tuple[str, Callable, float]]:
        name = get_winner_name(game_key, pool_size, config_path)
        fn = get_scorer_for_game(game_key, pool_size, config_path)
        return [(name, fn, 1.0)]

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
        name = resolve_method_name(name)
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
    """Scoruri PLATE (toate egale) = fără informație de ranking — filtrate din blend."""
    vals = [float(v) for v in raw.values()]
    if len(vals) < 2:
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
# nu filtrăm nimic. Pool-urile reale au 40..49 numere, deci pragul nu se atinge
# în producție; protejează doar apelurile sintetice (teste, scoruri parțiale).
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
    """|Spearman| între două seturi de scoruri, pe numerele COMUNE.

    None = necalculabil (prea puține numere comune, varianță nulă pe ranguri,
    NaN) → perechea nu poate justifica o eliminare.
    """
    common = sorted(set(raw_a.keys()) & set(raw_b.keys()))
    if len(common) < _MIN_CORR_SAMPLE:
        return None
    a = [float(raw_a[k]) for k in common]
    b = [float(raw_b[k]) for k in common]
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
        În ambele cazuri membrul nu aduce informaţie nouă → EXCLUS. Logat WARNING.

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
            if worst_r <= -MAX_MEMBER_CORR:
                logger.warning(
                    "[method_selector] ensemble: %r ANTI-corelat cu %r (Spearman=%.4f) — "
                    "EXCLUS (blend-ul devine monoton în celălalt membru; la ponderi "
                    "egale → scor constant / pool degenerat)",
                    name, worst_vs, worst_r,
                )
            else:
                logger.info(
                    "[method_selector] ensemble: %r redundant cu %r (Spearman=%.4f) — exclus",
                    name, worst_vs, worst_r,
                )
            continue
        kept_idx.append(i)
    kept = [active[i] for i in sorted(kept_idx)]
    return kept, dropped


def _resolve_members(
    contributions: list[tuple[str, dict[int, float], float]],
) -> tuple[list[tuple[str, dict[int, float], float]], list[tuple[str, str]], list[tuple[str, float, str]]]:
    """Pipeline-ul COMPLET de selecție a membrilor: varianță → decorelare.

    Sursa UNICĂ de adevăr pentru combine_ensemble_scores și describe_ensemble
    (UI-ul trebuie să vadă exact membrii pe care blend-ul îi folosește efectiv).
    """
    active, dropped = _split_active(contributions)
    active, dropped_corr = _select_decorrelated(active)
    return active, dropped, dropped_corr


def _split_active(
    contributions: list[tuple[str, dict[int, float], float]],
) -> tuple[list[tuple[str, dict[int, float], float]], list[tuple[str, str]]]:
    """Împarte contribuțiile în (active, dropped) — logica UNICĂ de filtrare,
    partajată de combine_ensemble_scores și describe_ensemble (ca UI-ul să vadă
    EXACT aceeași listă pe care blend-ul o folosește efectiv).

    Ignoră scoruri goale SAU plate (toate egale) — altfel un Ridge defect
    (toate 0) + prime_bias (2 nivele) → pool = „cele mai mici compuse".
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


def describe_ensemble(
    contributions: list[tuple[str, dict[int, float], float]],
) -> dict:
    """Metadata pt afișare (UI): cine e EFECTIV activ în blend vs. nominal.

    Aplică exact filtrarea din combine_ensemble_scores (goale/plate afară,
    apoi decorelarea greedy) FĂRĂ să combine scorurile — UI-ul o poate apela
    ca să afișeze membrii reali + ponderile efectiv folosite, nu cele nominale.

    Returnează:
        active  — [{"method", "weight"}] ponderi RENORMALIZATE pe membrii activi
        dropped — [{"method", "reason", ...}] TOATE eliminările, în ordinea
            etapelor; reason ∈ {"empty", "flat", "correlated", "anticorrelated"}.
            Pentru cele două motive de corelație se adaugă și "r" (Spearman
            semnat) + "vs" (membrul păstrat față de care s-a măsurat).
        dropped_correlated — doar eliminările de corelație, aceleași câmpuri
            (comod pt UI: „redundant cu X, r=0.98")
        nominal_count / active_count — dimensiunile listelor
        single_active_normalized — True când nominal >1 dar 1 activ (scorurile
            supraviețuitorului sunt min-max normalizate, nu brute)
        fallback_flat — True când NICIUN membru nu are varianță și blend-ul
            cade pe primul nevid (chiar plat)
    """
    active, dropped, dropped_corr = _resolve_members(contributions)
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
        "dropped": [{"method": n, "reason": r} for n, r in dropped] + corr_out,
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
    ul" putea fi un no-op (doi membri cu r=0.98 = același semnal numărat de două
    ori) sau chiar DEGENERAT (doi membri cu r=-1.0 se anulează → scor constant).
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
        # fallback: primul nevid, chiar dacă e plat (mai bun decât pool gol);
        # plat = toate valorile egale, deci scalarea nu ar schimba nimic în ranking
        for name, raw, w in contributions:
            if raw:
                if audit is not None:
                    audit["ensemble_active"] = [(name, 1.0)]
                    audit["ensemble_fallback_flat"] = True
                return {int(k): float(v) for k, v in raw.items()}
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
    rationale, fallback.
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
        return {
            "scorer": entry["scorer"],
            "sim_depth_pct": int(entry.get("sim_depth_pct", 40)),
            "use_blacklist": bool(entry.get("use_blacklist", False)),
            "avg_hits": float(entry.get("avg_hits", 0.0)),
            "rationale": entry.get("rationale", ""),
            # membrii ensemble (nominali, din decizie) + semnalizarea fallback-ului
            # de metrică (3+→4+) — altfel panoul de status din UI nu le poate afișa
            "ensemble": entry.get("ensemble") or [],
            "rate_col_used": entry.get("rate_col_used"),
            "rate_col_mismatch": bool(entry.get("rate_col_mismatch", False)),
            "fallback": False,
        }

    scorer = get_winner_name(game_key, pool_size=pool_size, config_path=config_path)
    use_bl = should_use_blacklist(game_key, pool_size=pool_size, config_path=config_path)
    return {
        "scorer": scorer,
        "sim_depth_pct": 40,
        "use_blacklist": use_bl,
        "avg_hits": 0.0,
        "rationale": "fallback: no auto_pilot_per_pool entry - using per-pool winner",
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
