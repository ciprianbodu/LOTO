"""Cache one requested month of official Romanian prizes, without changing draws.

Example: python -m scripts.sync_official_prizes --year 2026 --month 10
The default is all three Romanian games. The database is per-machine runtime
state and must not be committed. This command never recalculates lottery scores.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from loto_enterprise.core.official_prizes import PrizeCache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--game", action="append", choices=("6/49", "5/40", "joker"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 1998 <= args.year <= 2100:
        parser.error("--year must be between 1998 and 2100")
    cache = PrizeCache(args.db)
    results = []
    for game in args.game or ("6/49", "5/40", "joker"):
        status = cache.ensure_month(game, args.year, args.month, force=args.force)
        results.append({"game": game, **asdict(status)})
    print(json.dumps({"database": str(cache.db_path), "results": results}, indent=2))
    return int(any(row["status"] in {"error", "busy"} for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
