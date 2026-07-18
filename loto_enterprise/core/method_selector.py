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


def combine_ensemble_scores(
    contributions: list[tuple[str, dict[int, float], float]],
) -> dict[int, float]:
    """Combină scorurile brute ale mai multor metode într-un singur scor per
    număr, ponderat. Fiecare metodă e normalizată min-max la [0,1] ÎNAINTE de
    combinare — altfel o metodă cu scala de valori mai mare ar domina artificial
    suma, indiferent de calitatea ei relativă.

    `contributions` = [(method_name, {num: raw_score}, weight), ...] — ponderile
    NU trebuie să fie deja normalizate la sumă 1 (se renormalizează aici pe baza
    metodelor care au produs efectiv scoruri, ca metodele eșuate/goale să nu
    reducă artificial suma ponderilor active).

    Cu UN SINGUR membru cu scoruri nevide, returnează scorurile lui BRUTE,
    NEnormalizate — comportament bit-identic cu apelul direct al scorer-ului
    (fără ensemble), ca să nu schimbăm nimic când decizia n-a construit un
    blend real (ensemble cu 1 membru).
    """
    active = [(name, raw, w) for name, raw, w in contributions if raw]
    if not active:
        return {}
    if len(active) == 1:
        _name, raw, _w = active[0]
        return {int(k): float(v) for k, v in raw.items()}

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
