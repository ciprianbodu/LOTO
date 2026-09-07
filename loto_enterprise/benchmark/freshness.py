"""CSV freshness detection — decide if cached best_methods.json is still valid.

The benchmark is expensive (~50 min for full sweep). We don't want to re-run it
every time the user clicks Auto-Pilot. But if the user added 50+ new draws,
the winner per (game, pool) may have changed.

Strategy:
    1. Hash the CSV content (md5 of last 500 rows × number columns).
    2. Also record total row count.
    3. On Auto-Pilot click, compare:
        • Same hash → cached decision still 100% valid, instant.
        • Different hash → content changed (row count may be unchanged, e.g. one
          historical draw corrected in place), so "use_cache" is never offered;
          severity floors at "quick re-bench" and escalates to "full re-bench"
          once the row-count delta itself is large (>=10%).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

GAMES_CSV_MAP = {
    "loto_6_49": ["_ISTORIC/loto_6_49.csv", "ISTORIC/loto_6_49.csv", "istoric/loto_6_49.csv", "_LOTO/istoric/loto_6_49.csv"],
    "loto_5_40": ["_ISTORIC/loto_5_40.csv", "ISTORIC/loto_5_40.csv", "istoric/loto_5_40.csv", "_LOTO/istoric/loto_5_40.csv"],
    "joker_urna1": ["_ISTORIC/joker.csv", "ISTORIC/joker.csv", "istoric/joker.csv", "_LOTO/istoric/joker.csv"],
    "joker_urna2": ["_ISTORIC/joker.csv", "ISTORIC/joker.csv", "istoric/joker.csv", "_LOTO/istoric/joker.csv"],
}


@dataclass
class FreshnessReport:
    game_key: str
    csv_path: str | None
    cached_rows: int
    current_rows: int
    cached_hash: str
    current_hash: str
    row_delta_pct: float
    status: str  # "fresh" | "moderate_drift" | "stale" | "missing"
    recommendation: str  # "use_cache" | "quick_rebench" | "full_rebench" | "use_cache_no_csv"


def _resolve_csv(game_key: str) -> Path | None:
    for candidate in GAMES_CSV_MAP.get(game_key, []):
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _content_hash(csv_path: Path, num_cols: list[str] | None = None) -> tuple[str, int]:
    """Hash CSV content using the number columns (or all if not provided)."""
    df = pd.read_csv(csv_path)
    n_rows = len(df)
    if num_cols:
        cols = [c for c in num_cols if c in df.columns]
        if cols:
            df = df[cols]
    body = df.to_csv(index=False, header=False).encode("utf-8")
    h = hashlib.md5(body).hexdigest()[:16]
    return h, n_rows


def compute_csv_signature(game_key: str) -> tuple[str | None, str, int]:
    """Return (csv_path, hash, n_rows). Path is None if CSV missing."""
    p = _resolve_csv(game_key)
    if p is None:
        return None, "", 0
    cols_map = {
        "loto_6_49": ["n1", "n2", "n3", "n4", "n5", "n6"],
        "loto_5_40": ["n1", "n2", "n3", "n4", "n5"],
        "joker_urna1": ["n1", "n2", "n3", "n4", "n5"],
        "joker_urna2": ["joker"],
    }
    h, n = _content_hash(p, cols_map.get(game_key))
    return str(p), h, n


def write_signatures_to_best_methods(best_methods_path: str = "best_methods.json") -> dict[str, dict]:
    """Stamp the current CSV signatures into best_methods.json._meta.csv_signatures."""
    bm = Path(best_methods_path)
    if not bm.exists():
        return {}
    from ui_shared import atomic_write_json, file_lock

    # Read-modify-write protejat: decision.py și UI pot actualiza același fișier,
    # iar OneDrive nu tolerează bine scrieri parțiale. Versiunea veche făcea
    # `write_text` direct și putea trunchia tocmai decizia scrisă atomic anterior.
    with file_lock(bm):
        cfg = json.loads(bm.read_text(encoding="utf-8"))
        sigs: dict[str, dict] = {}
        for gk in GAMES_CSV_MAP:
            path, h, n = compute_csv_signature(gk)
            sigs[gk] = {"csv_path": path, "hash": h, "rows": n}
        cfg.setdefault("_meta", {})["csv_signatures"] = sigs
        atomic_write_json(bm, cfg)
    return sigs


def check_freshness(
    best_methods_path: str = "best_methods.json",
) -> dict[str, FreshnessReport]:
    """Compare current CSV signatures against the cached ones."""
    bm = Path(best_methods_path)
    out: dict[str, FreshnessReport] = {}
    if not bm.exists():
        for gk in GAMES_CSV_MAP:
            out[gk] = FreshnessReport(
                game_key=gk, csv_path=None, cached_rows=0, current_rows=0,
                cached_hash="", current_hash="",
                row_delta_pct=0.0, status="missing",
                recommendation="full_rebench",
            )
        return out
    cfg = json.loads(bm.read_text(encoding="utf-8"))
    cached_sigs = cfg.get("_meta", {}).get("csv_signatures", {})

    for gk in GAMES_CSV_MAP:
        path, current_hash, current_rows = compute_csv_signature(gk)
        cached = cached_sigs.get(gk, {})
        cached_hash = str(cached.get("hash", ""))
        cached_rows = int(cached.get("rows", 0))

        if path is None:
            out[gk] = FreshnessReport(
                game_key=gk, csv_path=None,
                cached_rows=cached_rows, current_rows=0,
                cached_hash=cached_hash, current_hash="",
                row_delta_pct=0.0, status="missing",
                recommendation="use_cache_no_csv",
            )
            continue

        # If no cached signature exists, treat as stale (first benchmark run)
        if not cached_hash:
            out[gk] = FreshnessReport(
                game_key=gk, csv_path=path,
                cached_rows=cached_rows, current_rows=current_rows,
                cached_hash="", current_hash=current_hash,
                row_delta_pct=100.0, status="stale",
                recommendation="full_rebench",
            )
            continue

        if cached_hash == current_hash:
            out[gk] = FreshnessReport(
                game_key=gk, csv_path=path,
                cached_rows=cached_rows, current_rows=current_rows,
                cached_hash=cached_hash, current_hash=current_hash,
                row_delta_pct=0.0, status="fresh",
                recommendation="use_cache",
            )
            continue

        delta = abs(current_rows - cached_rows)
        delta_pct = (delta / max(cached_rows, 1)) * 100
        # Am ajuns aici DOAR dacă hash-ul diferă (ramura `cached_hash == current_hash`
        # a ieșit deja mai sus) — deci datele s-au schimbat cu certitudine, chiar dacă
        # numărul de rânduri e aproape neschimbat (ex. o extragere istorică corectată
        # in loc, fără să adauge/scoată rânduri). Un `delta_pct` mic nu mai poate
        # coborî recomandarea la "slight_drift"/use_cache — asta ar contrazice exact
        # ce am verificat deja (hash diferit); minimul e moderate_drift/quick_rebench.
        if delta_pct >= 10.0:
            status, rec = "stale", "full_rebench"
        else:
            status, rec = "moderate_drift", "quick_rebench"

        out[gk] = FreshnessReport(
            game_key=gk, csv_path=path,
            cached_rows=cached_rows, current_rows=current_rows,
            cached_hash=cached_hash, current_hash=current_hash,
            row_delta_pct=delta_pct, status=status,
            recommendation=rec,
        )
    return out


def aggregate_recommendation(reports: dict[str, FreshnessReport]) -> str:
    """Pick the strongest recommendation across all games."""
    priority = {"full_rebench": 3, "quick_rebench": 2, "use_cache": 1, "use_cache_no_csv": 0}
    best = "use_cache"
    for r in reports.values():
        if priority.get(r.recommendation, 0) > priority.get(best, 0):
            best = r.recommendation
    return best
