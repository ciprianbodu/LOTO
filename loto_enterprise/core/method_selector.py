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
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "best_methods.json"
_CACHE: Dict[str, Callable] = {}
_CONFIG: Optional[Dict] = None
_CONFIG_MTIME: Optional[float] = None


def _load_config(path: Optional[str] = None) -> Dict:
    global _CONFIG, _CONFIG_MTIME
    # Cale explicită: încarcă proaspăt, fără a atinge cache-ul global (altfel un
    # apel cu path explicit ar polua configul default citit ulterior).
    if path is not None:
        p = Path(path)
        if not p.exists():
            logger.warning("[method_selector] %s missing — using frequency baseline", p)
            return {"games": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("[method_selector] failed to parse %s: %s", p, exc)
            return {"games": {}}

    # Cale default: cache cu invalidare pe mtime. După un re-bench fișierul se
    # schimbă → reîncărcăm și golim cache-ul de scorere, altfel un proces lung
    # (Streamlit) ar servi winneri stale până la restart.
    cfg_path = _DEFAULT_CONFIG_PATH
    try:
        mtime = cfg_path.stat().st_mtime if cfg_path.exists() else None
    except Exception:
        mtime = None
    if _CONFIG is not None and mtime == _CONFIG_MTIME:
        return _CONFIG
    _CONFIG_MTIME = mtime
    _CACHE.clear()
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
    pool_size: Optional[int] = None,
    config_path: Optional[str] = None,
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
    pool_size: Optional[int] = None,
    config_path: Optional[str] = None,
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
    pool_size: Optional[int] = None,
    config_path: Optional[str] = None,
) -> Callable:
    """Return the chosen scoring function for a given (game, pool_size)."""
    name = get_winner_name(game_key, pool_size, config_path)
    cache_key = f"{name}#{pool_size or 'overall'}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    try:
        from loto_enterprise.benchmark.methods import METHODS, method_meta
    except Exception as exc:
        logger.error("[method_selector] benchmark.methods import failed: %s", exc)
        raise

    if name not in METHODS:
        logger.warning("[method_selector] unknown winner %r, falling back to frequency", name)
        name = "frequency"
    fn, _family, _train, _notes = METHODS[name]
    # Capability-based: prinde și scorerele NeuralForecast/foundation care nu pot
    # rula în mediul curent (fără torch/CUDA) — altfel am rula tăcut un no-op care
    # returnează {} și ar degrada selecția top-K.
    _meta = method_meta(name)
    if not _meta["available"]:
        logger.warning(
            "[method_selector] winner %r unavailable (%s) — falling back to frequency",
            name, _meta.get("unavailable_reason", "?"),
        )
        fn = METHODS["frequency"][0]
        name = "frequency"
    _CACHE[cache_key] = fn
    return fn


def summary_line(game_key: str, pool_size: Optional[int] = None,
                 config_path: Optional[str] = None) -> str:
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


def all_per_pool_winners(game_key: str, config_path: Optional[str] = None) -> Dict[int, str]:
    """Map pool_size_int -> winner_method_name for this game."""
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    wpp = g.get("winners_per_pool", {}) or {}
    out: Dict[int, str] = {}
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
    config_path: Optional[str] = None,
) -> Dict:
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
    game_key: str, config_path: Optional[str] = None
) -> Dict[int, Dict]:
    """Return {pool_size: optimal_config} for this game - for UI rendering."""
    cfg = _load_config(config_path)
    g = cfg.get("games", {}).get(game_key, {})
    apm = g.get("auto_pilot_per_pool", {}) or {}
    out: Dict[int, Dict] = {}
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
