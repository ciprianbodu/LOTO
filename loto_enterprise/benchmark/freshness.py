"""CSV freshness detection — decide if cached best_methods.json is still valid.

The benchmark is expensive (~50 min for full sweep). We don't want to re-run it
every time the user clicks Auto-Pilot. But if the user added 50+ new draws,
the winner per (game, pool) may have changed.

Strategy:
    1. Hash all number columns and dates (dates define training boundaries).
    2. Also record total row count.
    3. Stamp the MOTOR alongside the data: versiunea cache-ului de bench și
       setul de metode din registry (vezi `compute_engine_signature`).
    4. On Auto-Pilot click, compare:
        • Același hash ȘI același motor → decizia din cache rămâne validă.
        • Hash diferit → s-au schimbat datele (numărul de rânduri poate fi
          neschimbat, ex. o extragere corectată in loc), deci „use_cache" nu se
          mai oferă; minimul e „quick re-bench" și urcă la „full re-bench" când
          delta de rânduri e mare (>=10%).
        • Motor diferit → decizia din cache a fost calculată cu ALTE scoruri sau
          cu alt set de metode, deci e stale indiferent de date: full re-bench.

⚠️ Punctul 3 a fost adăugat la 15.09.2026. Înainte, semnătura acoperea DOAR
conținutul CSV, iar docstring-ul promitea „Same hash → cached decision still 100%
valid". Fals la orice bump de `bench_cache.CACHE_VERSION` (care se face tocmai
pentru că scorurile s-au schimbat) și la orice ștergere/redenumire de metodă: cu
CSV-urile neatinse, freshness raporta „fresh / use_cache" pentru o decizie
calculată cu alt motor. S-a văzut concret la v18 -> v19 și la curarea din
14.09.2026, când ~183 de metode au dispărut din registry fără ca vreo semnătură
să se schimbe.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loto_enterprise.core.history import canonical_history_columns

logger = logging.getLogger(__name__)

GAMES_CSV_MAP = {
    "loto_6_49": [
        "_ISTORIC/loto_6_49.csv",
        "ISTORIC/loto_6_49.csv",
        "istoric/loto_6_49.csv",
        "_LOTO/istoric/loto_6_49.csv",
    ],
    "loto_5_40": [
        "_ISTORIC/loto_5_40.csv",
        "ISTORIC/loto_5_40.csv",
        "istoric/loto_5_40.csv",
        "_LOTO/istoric/loto_5_40.csv",
    ],
    "joker_urna1": [
        "_ISTORIC/joker.csv",
        "ISTORIC/joker.csv",
        "istoric/joker.csv",
        "_LOTO/istoric/joker.csv",
    ],
    "joker_urna2": [
        "_ISTORIC/joker.csv",
        "ISTORIC/joker.csv",
        "istoric/joker.csv",
        "_LOTO/istoric/joker.csv",
    ],
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
    recommendation: (
        str  # "use_cache" | "quick_rebench" | "full_rebench" | "use_cache_no_csv"
    )


def _resolve_csv(game_key: str) -> Path | None:
    for candidate in GAMES_CSV_MAP.get(game_key, []):
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _content_hash(csv_path: Path, num_cols: list[str] | None = None) -> tuple[str, int]:
    """Hash CSV content using the number columns (or all if not provided)."""
    df = canonical_history_columns(pd.read_csv(csv_path))
    n_rows = len(df)
    if num_cols:
        cols = [c for c in num_cols if c in df.columns]
        if "date" in df.columns:
            cols.append("date")
        if cols:
            df = df[cols]
    body = df.to_csv(index=False, header=False).encode("utf-8")
    h = hashlib.md5(body).hexdigest()[:16]
    return h, n_rows


def compute_engine_signature() -> dict[str, str | int]:
    """Semnătura MOTORULUI care a produs decizia: versiune de cache + registry.

    Nu include configurația de rulare (percentile, pool_extra): aceea schimbă cât
    de fin e măsurată decizia, nu ce ar măsura. Aici stă doar ce face un
    `folds.csv` vechi INCOMPARABIL cu unul nou: scoruri schimbate (bump de
    `CACHE_VERSION`) și alt set de metode.
    """
    try:
        from loto_enterprise.benchmark.bench_cache import CACHE_VERSION
    except Exception:  # noqa: BLE001
        CACHE_VERSION = "?"
    try:
        from loto_enterprise.benchmark.methods import METHODS

        names = sorted(str(n) for n in METHODS)
    except Exception:  # noqa: BLE001
        names = []
    digest = hashlib.md5("\n".join(names).encode("utf-8")).hexdigest()[:16]
    return {
        "bench_cache_version": str(CACHE_VERSION),
        "methods_hash": digest,
        "n_methods": len(names),
    }


def compute_csv_signature(game_key: str) -> tuple[str | None, str, int]:
    """Return (csv_path, hash, n_rows). Path is None if CSV missing."""
    p = _resolve_csv(game_key)
    if p is None:
        return None, "", 0
    cols_map = {
        "loto_6_49": ["n1", "n2", "n3", "n4", "n5", "n6"],
        "loto_5_40": ["n1", "n2", "n3", "n4", "n5", "n6"],
        "joker_urna1": ["n1", "n2", "n3", "n4", "n5"],
        "joker_urna2": ["joker"],
    }
    h, n = _content_hash(p, cols_map.get(game_key))
    return str(p), h, n


def write_signatures_to_best_methods(
    best_methods_path: str = "best_methods.json",
) -> dict[str, dict]:
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
        cfg["_meta"]["engine_signature"] = compute_engine_signature()
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
                game_key=gk,
                csv_path=None,
                cached_rows=0,
                current_rows=0,
                cached_hash="",
                current_hash="",
                row_delta_pct=0.0,
                status="missing",
                recommendation="full_rebench",
            )
        return out
    cfg = json.loads(bm.read_text(encoding="utf-8"))
    cached_sigs = cfg.get("_meta", {}).get("csv_signatures", {})

    # Motorul se compară ÎNAINTE de date: dacă scorurile sau setul de metode
    # s-au schimbat, `folds.csv` și decizia din el descriu alt sistem, oricât de
    # neatinse ar fi CSV-urile. O semnătură lipsă (best_methods.json scris
    # înainte de 15.09.2026) nu se tratează ca nepotrivire — n-avem cu ce
    # compara, iar o alarmă la fiecare pornire ar fi zgomot; se stampilează la
    # primul bench.
    _cached_engine = cfg.get("_meta", {}).get("engine_signature")
    _engine_changed = False
    if isinstance(_cached_engine, dict) and _cached_engine:
        _current_engine = compute_engine_signature()
        _engine_changed = any(
            str(_cached_engine.get(k, "")) != str(v)
            for k, v in _current_engine.items()
        )
        if _engine_changed:
            logger.warning(
                "[freshness] decizia din cache a fost calculată cu alt motor "
                "(cache %s / %s metode, acum %s / %s) — full re-bench.",
                _cached_engine.get("bench_cache_version"),
                _cached_engine.get("n_methods"),
                _current_engine.get("bench_cache_version"),
                _current_engine.get("n_methods"),
            )

    for gk in GAMES_CSV_MAP:
        path, current_hash, current_rows = compute_csv_signature(gk)
        cached = cached_sigs.get(gk, {})
        cached_hash = str(cached.get("hash", ""))
        cached_rows = int(cached.get("rows", 0))

        if path is None:
            out[gk] = FreshnessReport(
                game_key=gk,
                csv_path=None,
                cached_rows=cached_rows,
                current_rows=0,
                cached_hash=cached_hash,
                current_hash="",
                row_delta_pct=0.0,
                status="missing",
                recommendation="use_cache_no_csv",
            )
            continue

        # If no cached signature exists, treat as stale (first benchmark run)
        if not cached_hash:
            out[gk] = FreshnessReport(
                game_key=gk,
                csv_path=path,
                cached_rows=cached_rows,
                current_rows=current_rows,
                cached_hash="",
                current_hash=current_hash,
                row_delta_pct=100.0,
                status="stale",
                recommendation="full_rebench",
            )
            continue

        if cached_hash == current_hash:
            # Date identice, dar motorul poate fi altul: atunci decizia din cache
            # e stale chiar cu hash-ul neschimbat.
            out[gk] = FreshnessReport(
                game_key=gk,
                csv_path=path,
                cached_rows=cached_rows,
                current_rows=current_rows,
                cached_hash=cached_hash,
                current_hash=current_hash,
                row_delta_pct=0.0,
                status="stale" if _engine_changed else "fresh",
                recommendation="full_rebench" if _engine_changed else "use_cache",
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
        if delta_pct >= 10.0 or _engine_changed:
            # Motor schimbat = re-bench complet: un quick re-bench ar amesteca
            # folduri calculate cu scoruri vechi cu unele calculate cu cele noi.
            status, rec = "stale", "full_rebench"
        else:
            status, rec = "moderate_drift", "quick_rebench"

        out[gk] = FreshnessReport(
            game_key=gk,
            csv_path=path,
            cached_rows=cached_rows,
            current_rows=current_rows,
            cached_hash=cached_hash,
            current_hash=current_hash,
            row_delta_pct=delta_pct,
            status=status,
            recommendation=rec,
        )
    return out


def aggregate_recommendation(reports: dict[str, FreshnessReport]) -> str:
    """Pick the strongest recommendation across all games."""
    priority = {
        "full_rebench": 3,
        "quick_rebench": 2,
        "use_cache": 1,
        "use_cache_no_csv": 0,
    }
    best = "use_cache"
    for r in reports.values():
        if priority.get(r.recommendation, 0) > priority.get(best, 0):
            best = r.recommendation
    return best
