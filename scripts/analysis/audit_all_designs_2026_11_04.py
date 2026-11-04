"""Read-only exhaustive audit of every stored design and every budget section.

Run from any directory:
    python scripts/analysis/audit_all_designs_2026_11_04.py --output report.json

For every intersection size h=0..min(pool, 6), enumerate ALL h-subsets and
calculate the best physical ticket intersection directly, independently of the
subset-DP profile implementation. All threshold counts must agree exactly.
These are all possible pool intersections for currently registered games;
this audit does not enumerate intersections larger than six.

Also verify manifest completeness/hashes, valid distinct tickets, all claimed
complete C/L/R/RL guarantees, and all LR printed win indices. Budget, extended,
and reduced LR designs are not incorrectly required to have 100% coverage.
No design, manifest, decision, history, queue, or application state is changed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import combinations
import json
from math import comb
from pathlib import Path
import platform
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.probability import wheel_hit_profile
from loto_enterprise.core.lotteries import GEOMETRIES
from scripts.analysis.import_lr_schemes import index_errors, rows_sha256

MAX_DRAW = 6
BATCH_SIZE = 512


def _sections(data):
    sections = []
    rows = []
    budget = None
    for line in data.decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("B "):
            if rows:
                sections.append((budget, rows))
            budget = int(line.split()[1])
            rows = []
        else:
            rows.append(tuple(map(int, line.replace(",", " ").split())))
    if rows:
        sections.append((budget, rows))
    return sections


def run_audit():
    assert all(g.draw_n <= MAX_DRAW for g in GEOMETRIES.values())
    root = ROOT / "covering_designs"
    manifest_path = root / "MANIFEST.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    started = time.perf_counter()
    official = {
        (e["game"], e["code"]): e
        for e in json.loads((root / "lr/index_oficial.json").read_text())
    }
    report = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "scope": "all subsets for h=0..min(pool,6), all hit thresholds",
        "files": 0,
        "entries": 0,
        "complete_designs": 0,
        "lr_indices": 0,
        "exact_count_comparisons": 0,
        "max_count_difference": 0,
        "rows": [],
    }
    kinds = Counter()
    subset_cache = {}
    assert {e["file"] for e in manifest["entries"]} == {
        p.relative_to(root).as_posix() for p in root.rglob("*.txt")
    }
    for entry in manifest["entries"]:
        path = root / entry["file"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], path
        v = entry["v"]
        pick = entry.get("pick") or (6 if entry.get("game") == "649" else 5)
        kind = entry["kind"]
        sections = _sections(data)
        assert sections, path
        assert sum(len(rows) for _, rows in sections) == entry["tickets"], path
        if kind == "H":
            assert [(b, len(r)) for b, r in sections] == [
                (s["budget"], s["tickets"]) for s in entry["sections"]
            ]
        for budget, rows in sections:
            assert all(
                len(r) == len(set(r)) == pick and min(r) >= 1 and max(r) <= v
                for r in rows
            ), (path, budget)
            assert len({tuple(sorted(r)) for r in rows}) == len(rows), (path, budget)
            profile = wheel_hit_profile(range(1, v + 1), rows)
            ticket_masks = np.array(
                [sum(1 << (n - 1) for n in r) for r in rows], dtype=np.uint32
            )
            for h in range(min(v, MAX_DRAW) + 1):
                key = (v, h)
                if key not in subset_cache:
                    subset_cache[key] = np.fromiter(
                        (sum(1 << n for n in s) for s in combinations(range(v), h)),
                        dtype=np.uint32,
                        count=comb(v, h),
                    )
                subsets = subset_cache[key]
                expected = np.zeros(v + 1, dtype=np.int64)
                for start in range(0, len(subsets), BATCH_SIZE):
                    intersections = (
                        subsets[start : start + BATCH_SIZE, None]
                        & ticket_masks[None, :]
                    )
                    best = np.bitwise_count(intersections).max(axis=1)
                    for threshold in range(h + 1):
                        expected[threshold] += np.count_nonzero(best >= threshold)
                difference = max(
                    abs(int(a) - b) for a, b in zip(expected, profile[h])
                )
                report["max_count_difference"] = max(
                    report["max_count_difference"], difference
                )
                report["exact_count_comparisons"] += v + 1
                assert difference == 0, (path, budget, h)
            if kind in ("C", "L", "R", "RL"):
                guarantee = entry["guarantee"]
                condition = entry.get("condition", guarantee)
                assert condition <= MAX_DRAW, (path, "extend independent enumeration")
                assert profile[condition][guarantee] == comb(v, condition), (
                    path, "incomplete"
                )
                report["complete_designs"] += 1
            if kind == "LR":
                printed = official[(entry["game"], entry["code"])]
                assert index_errors(printed["game"], v, rows, printed["index"]) == [], path
                assert rows_sha256(rows) == printed["rows_sha256"], path
                report["lr_indices"] += 1
            report["entries"] += 1
            report["rows"].append({
                "file": entry["file"],
                "budget": budget,
                "tickets": len(rows),
                "validated_intersection_sizes": list(range(min(v, MAX_DRAW) + 1)),
                "exact_profile_match": True,
            })
        report["files"] += 1
        kinds[kind] += 1
        if report["files"] % 40 == 0:
            print(
                f"Validated {report['files']} files / {report['entries']} entries "
                f"in {time.perf_counter() - started:.1f}s",
                flush=True,
            )
    report["kinds"] = dict(kinds)
    report["seconds"] = time.perf_counter() - started
    report["all_hashes_match"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
