"""Refine stored 6-number / guarantee-4 budget covers offline.

Reuse good covers across budgets: remove the ticket with the smallest exact
marginal loss, or add the best marginal ticket. Every promoted candidate must
retain coverage and all 3+..6+ probabilities on BOTH supported geometries,
6/49 and 6/45. No scoring, historical draws, randomness, or native compiler.

Example (writes only to the explicit output directory):
    python scripts/analysis/refine_budget_designs.py --input-dir covering_designs/budget \
        --seed-dir covering_designs --output-dir scratch/refined_budget --pools 16

Complete covers in --seed-dir are extra pruning seeds and set the exclusive
upper budget. The input files are never modified. Outputs retain unchanged
sections below that upper budget, and report exact integer favorable-draw
counts, probabilities, hashes, and elapsed/CPU time. Re-running with identical
inputs and parameters produces identical design files (timings may differ).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from itertools import combinations
from math import comb
from pathlib import Path

# This standalone offline search does not need BLAS parallelism.
if __name__ == "__main__":
    for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[_name] = "1"

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from covering.anneal_hits import _certify
from covering.budget_designs import _parse
from covering.probability import wheel_hit_profile, wheel_hit_probabilities

UNIVERSES = (45, 49)
PICK, GUARANTEE = 6, 4


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dominates(new: tuple[int, ...], old: tuple[int, ...]) -> bool:
    return len(new) == len(old) and all(a >= b for a, b in zip(new, old)) and any(a > b for a, b in zip(new, old))


def _read_budget(path: Path, v: int) -> dict[int, list[list[int]]]:
    text = path.read_text(encoding="utf-8-sig")
    entries = _parse(text, v, PICK)
    headers = [line.strip() for line in text.splitlines() if line.strip().upper().startswith("B ")]
    if not entries or len(headers) != len(entries):
        raise ValueError(f"Missing, duplicate, or malformed budget sections: {path}")
    return {b: [list(t) for t in value[0]] for b, value in entries.items()}


def _read_complete(path: Path, v: int) -> list[list[int]]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    tickets = [sorted(map(int, line.split())) for line in lines if line and not line.startswith("#")]
    pool = list(range(1, v + 1))
    if (not tickets or len({tuple(t) for t in tickets}) != len(tickets)
            or any(len(t) != PICK or len(set(t)) != PICK or not set(t) <= set(pool) for t in tickets)
            or wheel_hit_profile(pool, tickets)[GUARANTEE][GUARANTEE] != comb(v, GUARANTEE)):
        raise ValueError(f"Invalid complete cover: {path}")
    return tickets


class Search:
    def __init__(self, v: int, designs: dict[int, list[list[int]]], complete=None):
        self.v = v
        self.pool = list(range(1, v + 1))
        self.original = designs
        self.best = {b: [t[:] for t in rows] for b, rows in designs.items()}
        masks = np.arange(1, 1 << v, dtype=np.uint32)
        sizes = np.bitwise_count(masks)
        self.masks = masks[sizes <= PICK]
        self.sizes = sizes[sizes <= PICK]
        self.weights = {n: np.array([comb(n-v, PICK-int(s)) for s in self.sizes], dtype=np.int64) for n in UNIVERSES}
        self.quartet = self.sizes == GUARANTEE
        self.original_metrics = {b: self.metrics(self.matrix(t)) for b, t in designs.items()}
        self.best_metrics = dict(self.original_metrics)
        self.complete = complete
        self.limit = len(complete) if complete else max(designs) + 1
        self.origins = {}

    def matrix(self, tickets):
        masks = np.array([sum(1 << (n-1) for n in t) for t in tickets], dtype=np.uint32)
        return np.bitwise_count(masks[:, None] & self.masks)

    def metrics(self, matrix):
        best = matrix.max(axis=0)
        return tuple(int(self.weights[n][best >= t].sum()) for n in UNIVERSES for t in (3, 4, 5, 6)) + (int(((best >= 4) & self.quartet).sum()),)

    def consider(self, tickets, metrics, origin):
        budget = len(tickets)
        if budget not in self.best or budget >= self.limit or not _dominates(metrics, self.best_metrics[budget]):
            return False
        if {n for t in tickets for n in t} != set(self.pool):
            return False
        # Independent production certificate, including distinct tickets and
        # played positions. Integer metrics above also forbid rounding losses.
        if any(_certify(self.pool, self.best[budget], tickets, 4, 6, n)[0] for n in UNIVERSES):
            return False
        self.best[budget] = [t[:] for t in tickets]
        self.best_metrics[budget] = metrics
        self.origins[budget] = origin
        return True

    def prune(self, stride: int):
        available = sorted(self.best, reverse=True)
        sources = sorted(set(available[::stride] + [available[-1]]), reverse=True)
        if self.complete is not None:
            sources = sorted(set(sources + [len(self.complete)]), reverse=True)
        for source in sources:
            tickets = self.complete if self.complete is not None and source == len(self.complete) else self.best[source]
            tickets = [t[:] for t in tickets]
            matrix = self.matrix(tickets)
            cover = [matrix >= t for t in (3, 4, 5)]
            counts = [c.sum(axis=0) for c in cover]
            while len(tickets) > min(self.best):
                losses = [c[:, cnt == 1].astype(np.int64) @ self.weights[49][cnt == 1] for c, cnt in zip(cover, counts)]
                covloss = cover[1][:, (counts[1] == 1) & self.quartet].sum(axis=1)
                i = min(range(len(tickets)), key=lambda j: (losses[0][j], losses[1][j], losses[2][j], covloss[j], j))
                tickets.pop(i)
                for c, cnt in zip(cover, counts):
                    cnt -= c[i]
                cover = [np.delete(c, i, axis=0) for c in cover]
                matrix = np.delete(matrix, i, axis=0)
                self.consider(tickets, self.metrics(matrix), f"prune:{source}")
            print(f"pool {self.v}: pruned source {source}", flush=True)

    def extend(self, step: int):
        # For these large budgets all stored designs already cover every triple.
        # A subset's exact 4+/5+ gain is then sufficient to rank additions;
        # consider() still verifies every 3+..6+ threshold independently.
        if any(wheel_hit_profile(self.pool, t)[3][3] != comb(self.v, 3) for t in self.best.values()):
            raise ValueError("Extension requires incumbent designs with complete triple coverage")
        tickets = list(combinations(self.pool, PICK))
        index = {t: i for i, t in enumerate(tickets)}
        masks = np.array([sum(1 << (n-1) for n in t) for t in tickets], dtype=np.uint32)
        subsets = {s: np.array([sum(1 << (n-1) for n in ss) for ss in combinations(self.pool, s)], dtype=np.uint32) for s in (4, 5, 6)}
        covered = []
        for s, threshold in ((4, 4), (5, 4), (6, 4), (5, 5), (6, 5)):
            bits = np.bitwise_count(masks[:, None] & subsets[s]) >= threshold
            covered.append([int.from_bytes(row.tobytes(), "little") for row in np.packbits(bits, axis=1, bitorder="little")])

        def union(ids):
            result = [0] * 5
            for i in ids:
                for j in range(5):
                    result[j] |= covered[j][i]
            return result

        def partial_metrics(parts, n):
            a, b, c, d, e = parts
            return a * comb(n-self.v, 2) + b * (n-self.v) + c, d * (n-self.v) + e, a

        eligible = sorted(b for b in self.best if b < self.limit)
        if not eligible:
            return
        starts = sorted({eligible[0]} | {b for b in eligible if b % step == 0})
        for start in starts:
            ids = [index[tuple(t)] for t in self.best[start]]
            present = set(ids)
            sets = union(ids)
            for budget in range(start+1, min(max(self.best)+1, self.limit)):
                oldparts = [x.bit_count() for x in sets]
                old = {n: partial_metrics(oldparts, n) for n in UNIVERSES}
                goal = self.best_metrics.get(budget)
                needed = {n: (goal[k*4+1], goal[k*4+2], goal[8]) for k, n in enumerate(UNIVERSES)} if goal is not None else {}
                best_key, chosen = None, None
                for i in range(len(tickets)):
                    if i in present:
                        continue
                    parts = [(covered[j][i] & ~sets[j]).bit_count() for j in range(5)]
                    gains = {n: partial_metrics(parts, n) for n in UNIVERSES}
                    feasible = not needed or all(p+q >= r for n in UNIVERSES for p, q, r in zip(old[n], gains[n], needed[n]))
                    key = feasible, gains[49][0], gains[49][1], parts[0], -i
                    if best_key is None or key > best_key:
                        best_key, chosen = key, i
                if chosen is None:
                    break
                ids.append(chosen)
                present.add(chosen)
                for j in range(5):
                    sets[j] |= covered[j][chosen]
                if best_key[0]:
                    candidate = [list(tickets[i]) for i in ids]
                    self.consider(candidate, self.metrics(self.matrix(candidate)), f"extend:{start}")
            print(f"pool {self.v}: extended source {start}", flush=True)

    def write(self, destination: Path):
        filename = destination / f"H_{self.v}_6_4.txt"
        lines = [f"# Budget designs C({self.v},6,4), positions 1..{self.v}.",
                 "# Refined offline by scripts/analysis/refine_budget_designs.py.",
                 "# Certified against incumbents at every 3+..6+ threshold on 6/49 and 6/45."]
        rows = []
        for budget in sorted(self.best):
            if budget >= self.limit:
                continue
            candidate, original = self.best[budget], self.original[budget]
            lines.append(f"B {budget}")
            lines.extend(" ".join(map(str, ticket)) for ticket in candidate)
            if candidate == original:
                continue
            # Final certification is against the original file, not merely
            # the latest intermediate candidate.
            for n in UNIVERSES:
                why = _certify(self.pool, original, candidate, 4, 6, n)[0]
                if why:
                    raise AssertionError((self.v, budget, n, why))
            rows.append({"pool": self.v, "budget": budget, "origin": self.origins[budget],
                         "counts_before": self.original_metrics[budget], "counts_after": self.best_metrics[budget],
                         "coverage_before": self.original_metrics[budget][-1], "coverage_after": self.best_metrics[budget][-1],
                         "relative_gain_4plus": self.best_metrics[budget][5]/self.original_metrics[budget][5]-1,
                         "probabilities": {str(n): {"before": wheel_hit_probabilities(self.pool, original, 6, n)["ticket"],
                                                     "after": wheel_hit_probabilities(self.pool, candidate, 6, n)["ticket"]} for n in UNIVERSES}})
        filename.write_text("\n".join(lines)+"\n", encoding="utf-8", newline="\n")
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "covering_designs/budget")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-dir", type=Path)
    parser.add_argument("--pools", type=int, nargs="+", default=[16])
    parser.add_argument("--source-stride", type=int, default=5, help="Try every Nth descending budget as pruning seed")
    parser.add_argument("--extension-step", type=int, default=10, help="Try multiples of N as extension seeds; 0 disables")
    args = parser.parse_args()
    source, destination = args.input_dir.resolve(), args.output_dir.resolve()
    if source == destination or destination == (ROOT / "covering_designs/budget").resolve():
        parser.error("Choose a separate output directory; production and input files must not be overwritten")
    if args.source_stride < 1 or args.extension_step < 0 or any(not 6 < v <= 16 for v in args.pools):
        parser.error("Expected positive source stride, nonnegative extension step, and pools 7..16")
    destination.mkdir(parents=True, exist_ok=True)
    wall, cpu = time.perf_counter(), time.process_time()
    report = {"universes": UNIVERSES, "pick": PICK, "guarantee": GUARANTEE,
              "source_stride": args.source_stride, "extension_step": args.extension_step,
              "inputs": {}, "outputs": {}, "changed": []}
    input_paths = {}
    for v in sorted(set(args.pools)):
        path = source / f"H_{v}_6_4.txt"
        report["inputs"][path.name] = _sha(path)
        input_paths[path] = _sha(path)
        complete = None
        if args.seed_dir is not None:
            seed = args.seed_dir.resolve() / f"C_{v}_6_4.txt"
            complete = _read_complete(seed, v)
            report["inputs"][seed.name] = _sha(seed)
            input_paths[seed] = _sha(seed)
        search = Search(v, _read_budget(path, v), complete)
        search.prune(args.source_stride)
        if args.extension_step:
            search.extend(args.extension_step)
        report["changed"].extend(search.write(destination))
        output = destination / path.name
        report["outputs"][output.name] = _sha(output)
        print(f"pool {v}: {len(search.origins)} improved sections", flush=True)
    if any(_sha(path) != digest for path, digest in input_paths.items()):
        raise RuntimeError("Input changed during refinement; discard generated files and retry")
    report["wall_seconds"] = time.perf_counter()-wall
    report["cpu_seconds"] = time.process_time()-cpu
    (destination / "report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(f"Finished: {len(report['changed'])} improved sections in {report['wall_seconds']:.1f}s", flush=True)


if __name__ == "__main__":
    main()
