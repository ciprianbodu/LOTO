"""CSV freshness detection — decide if cached best_methods.json is still valid.

The benchmark is expensive (~50 min for full sweep). We don't want to re-run it
every time the user clicks Auto-Pilot. But if the user added 50+ new draws,
the winner per (game, pool) may have changed.

Strategy:
    1. Hash the CSV content (md5 of last 500 rows × number columns).
    2. Also record total row count.
    3. On Auto-Pilot click, compare:
        • Same hash → cached decision still 100% valid, instant.
        • Row delta < 5% → "data slightly fresher; cache likely still optimal".
        • Row delta 5-20% → "recommend quick re-bench".
        • Row delta > 20% OR hash radically different → "recommend full re-bench".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

GAMES_CSV_MAP = {
    "loto_6_49": ["ISTORIC/loto_6_49.csv", "_LOTO/istoric/loto_6_49.csv", "istoric/loto_6_49.csv"],
    "loto_5_40": ["ISTORIC/loto_5_40.csv", "_LOTO/istoric/loto_5_40.csv", "istoric/loto_5_40.csv"],
    "joker_urna1": ["ISTORIC/joker.csv", "_LOTO/istoric/joker.csv", "istoric/joker.csv"],
    "joker_urna2": ["ISTORIC/joker.csv", "_LOTO/istoric/joker.csv", "istoric/joker.csv"],
}


@dataclass
class FreshnessReport:
    game_key: str
    csv_path: Optional[str]
    cached_rows: int
    current_rows: int
    cached_hash: str
    current_hash: str
    row_delta_pct: float
    status: str  # "fresh" | "slight_drift" | "moderate_drift" | "stale" | "missing"
    recommendation: str  # "use_cache" | "quick_rebench" | "full_rebench" | "use_cache_no_csv"


def _resolve_csv(game_key: str) -> Optional[Path]:
    for candidate in GAMES_CSV_MAP.get(game_key, []):
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _content_hash(csv_path: Path, num_cols: Optional[List[str]] = None) -> Tuple[str, int]:
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


def compute_csv_signature(game_key: str) -> Tuple[Optional[str], str, int]:
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


def write_signatures_to_best_methods(best_methods_path: str = "best_methods.json") -> Dict[str, dict]:
    """Stamp the current CSV signatures into best_methods.json._meta.csv_signatures."""
    bm = Path(best_methods_path)
    if not bm.exists():
        return {}
    cfg = json.loads(bm.read_text(encoding="utf-8"))
    sigs: Dict[str, dict] = {}
    for gk in GAMES_CSV_MAP:
        path, h, n = compute_csv_signature(gk)
        sigs[gk] = {"csv_path": path, "hash": h, "rows": n}
    cfg.setdefault("_meta", {})["csv_signatures"] = sigs
    bm.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return sigs


def check_freshness(
    best_methods_path: str = "best_methods.json",
) -> Dict[str, FreshnessReport]:
    """Compare current CSV signatures against the cached ones."""
    bm = Path(best_methods_path)
    out: Dict[str, FreshnessReport] = {}
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
        if delta_pct < 2.0:
            status, rec = "slight_drift", "use_cache"
        elif delta_pct < 10.0:
            status, rec = "moderate_drift", "quick_rebench"
        else:
            status, rec = "stale", "full_rebench"

        out[gk] = FreshnessReport(
            game_key=gk, csv_path=path,
            cached_rows=cached_rows, current_rows=current_rows,
            cached_hash=cached_hash, current_hash=current_hash,
            row_delta_pct=delta_pct, status=status,
            recommendation=rec,
        )
    return out


def aggregate_recommendation(reports: Dict[str, FreshnessReport]) -> str:
    """Pick the strongest recommendation across all games."""
    priority = {"full_rebench": 3, "quick_rebench": 2, "use_cache": 1, "use_cache_no_csv": 0}
    best = "use_cache"
    for r in reports.values():
        if priority.get(r.recommendation, 0) > priority.get(best, 0):
            best = r.recommendation
    return best
