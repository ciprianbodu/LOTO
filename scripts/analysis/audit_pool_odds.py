"""Audit real production pools 6..16 without changing production state.

Run from the repository with Python 3.14. Outputs are written only to --output;
best_methods, benchmark folds, the job queue and pool history are not rewritten.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.probability import wheel_hit_probabilities  # noqa: E402
from loto_engine import LotoEngine  # noqa: E402
from wheeling_methods import compute_coverage_pct, generate_wheel  # noqa: E402

GAMES = [('6/49', 'loto_6_49.csv', 6, 49), ('5/40', 'loto_5_40.csv', 5, 40),
         ('joker', 'joker.csv', 5, 45)]


def run(output: Path):
    logging.disable(logging.WARNING)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows, fingerprints = [], []
    geometries = 0
    for pick in (5, 6):
        for size in range(6, 17):
            pool = list(range(1, size + 1))
            for guarantee in range(3, pick + 1):
                for condition in range(guarantee, pick + 1):
                    wheel, cov = generate_wheel('lajolla', pool, pick, guarantee, condition=condition)
                    assert cov == compute_coverage_pct(wheel, pool, guarantee, condition) == 100.0
                    assert len(wheel) == len(set(map(tuple, wheel)))
                    assert all(len(t) == len(set(t)) == pick for t in wheel)
                    if guarantee == pick:
                        from math import comb
                        assert len(wheel) == comb(size, pick)
                    geometries += 1
    for game, filename, pick, max_num in GAMES:
        for size in range(6, 17):
            for guarantee, condition, budget in [(3, 3, 0), (4, 4, 0), (4, 4, 7), (4, 5, 0)]:
                engine = LotoEngine(game)
                assert engine.load_data(str(ROOT / '_ISTORIC' / filename))
                result = engine.run_institutional_pipeline(
                    pool_size=size, guarantee=guarantee, wheel_condition=condition,
                    max_variants=budget, track_pool_variation=False,
                    enable_adaptive_persistence=False,
                )
                lines, _, _, _, context, audit = result
                pool = list(engine.hard_core)
                tickets = [list(t[:pick]) for t in lines]
                assert len(pool) == len(set(pool)) == size
                assert all(len(t) == len(set(t)) == pick and set(t) <= set(pool) for t in tickets)
                assert len(tickets) == len(set(map(tuple, tickets)))
                assert not budget or len(tickets) <= budget
                actual = compute_coverage_pct(tickets, pool, guarantee, condition)
                assert actual == context['coverage_pct']
                assert budget or actual == 100
                odds = wheel_hit_probabilities(pool, tickets, pick, max_num)
                assert all(odds['ticket'][t] <= odds['pool'][t] for t in range(1, pick + 1))
                if not budget and condition == guarantee:
                    assert odds['ticket'][guarantee] == odds['pool'][guarantee]
                all_six = wheel_hit_probabilities(pool, tickets, 6, 40) if max_num == 40 else None
                rows.append(dict(
                    game=game, pool=size, guarantee=guarantee, condition=condition,
                    budget=budget, tickets=len(tickets), coverage_pct=actual,
                    pool_3_pct=100 * odds['pool'][3], ticket_3_pct=100 * odds['ticket'][3],
                    pool_4_pct=100 * odds['pool'][4], ticket_4_pct=100 * odds['ticket'][4],
                    jackpot_main_pct=100 * odds['ticket'][pick],
                    ticket_4_of_all6_pct=100 * all_six['ticket'][4] if all_six else '',
                ))
                fingerprints.append(dict(
                    game=game, pool_size=size, guarantee=guarantee, condition=condition,
                    budget=budget, pool=pool, scorer=audit.get('bench_winner'),
                    tickets_sha256=hashlib.sha256(json.dumps(lines).encode()).hexdigest(),
                ))
        print(f'{game}: pools 6..16 validated', flush=True)
    with (output / 'pool_odds.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(
        geometries=geometries, pipeline_runs=len(rows),
        seconds=round(time.perf_counter() - started, 2), fingerprints=fingerprints,
        history_sha256={name: hashlib.sha256((ROOT / '_ISTORIC' / name).read_bytes()).hexdigest()
                        for _, name, _, _ in GAMES},
    )
    (output / 'verification.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ['geometries', 'pipeline_runs', 'seconds']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
