"""Deterministic LR replay benchmark; never modifies histories or official schemes.

Run in an isolated copy, providing covering/lr_schemes.py from the baseline
revision as --baseline-source. Reports exact parity and local median times.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from random import Random
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from covering import lr_schemes as current
from loto_enterprise.core.generated_prize import estimate_generated_prize
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw

CASES = (("6/49",16,59,49,6), ("joker",15,12,45,5), ("5/40",19,48,40,6))
LABELS = {
    "6/49": ("6/6", "5/6", "4/6", "3/6"),
    "5/40": ("5/5", "5/6", "4/6"),
    "joker": ("5/5+J", "5/5", "4/5+J", "4/5", "3/5+J", "3/5", "2/5+J", "1/5+J"),
}
NAMES = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")


def time_pair(before, after, repeats):
    times = [[], []]
    outputs = [None, None]
    for repeat in range(repeats):
        for which in ((0,1) if repeat % 2 == 0 else (1,0)):
            start = perf_counter()
            outputs[which] = (before, after)[which]()
            times[which].append(perf_counter() - start)
        assert outputs[0] == outputs[1]
    a, b = (median(t) for t in times)
    return {"old_seconds": a, "new_seconds": b, "speedup": a / b,
            "repeats": repeats, "identical_outputs": True}


def run(baseline_path, repeats=5):
    spec = importlib.util.spec_from_file_location("lr_replay_before", baseline_path)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    rng = Random(20261104)
    result = []
    for game,n,code,maximum,draw_n in CASES:
        scheme = next(s for s in current.lr_schemes(game,n) if s.code == code)
        prior = old.LRScheme(scheme.game,scheme.code,scheme.n,scheme.variants)
        history = [(rng.sample(range(1,maximum+1),n),
                    rng.sample(range(1,maximum+1),draw_n)) for _ in range(1000)]
        hit_times = time_pair(
            lambda: [old.best_hits(prior,p,d) for p,d in history],
            lambda: [current.best_hits(scheme,p,d) for p,d in history], repeats)
        # Visible financial history is restricted to >=4 main pool hits.
        financial = []
        for index in range(120):
            pool = rng.sample(range(1,maximum+1),n)
            count = 4 + index % (draw_n - 3)
            outside = sorted(set(range(1,maximum+1)) - set(pool))
            drawn = rng.sample(pool,count) + rng.sample(outside,draw_n-count)
            rng.shuffle(drawn)
            categories = tuple(
                PrizeCategory(name,label,1,(i+1)*100,None,(i+1)*100)
                for i,(name,label) in enumerate(zip(NAMES,LABELS[game])))
            official = PrizeDraw(game,"2026-11-01",tuple(drawn),
                                 7 if game == "joker" else None,categories,
                                 "https://www.loto.ro/",order_known=True)
            financial.append((pool,drawn,official))

        def prizes(compact):
            outputs = []
            for pool,drawn,official in financial:
                kwargs = dict(target_numbers=drawn,target_date=official.draw_date,
                              expected_count=scheme.size,pool_numbers=pool)
                if compact:
                    grouped = current.scheme_prize_groups(
                        scheme,pool,drawn,joker=7)
                    value = estimate_generated_prize(
                        game,[],official,variant_prize_groups=grouped,**kwargs)
                else:
                    variants = old.scheme_tickets(prior,pool)
                    if game == "joker":
                        variants = [v + [7] for v in variants]
                    value = estimate_generated_prize(game,variants,official,**kwargs)
                outputs.append(value)
            return outputs

        prize_times = time_pair(lambda: prizes(False),lambda: prizes(True),repeats)
        group_counts = [len(current.scheme_prize_groups(scheme,p,d,joker=7)["groups"])
                        for p,d,_ in financial]
        row = {"game":game,"pool":n,"code":code,"variants":scheme.size,
               "hits_1000_rows":hit_times,"prizes_120_rows":prize_times,
               "groups_min":min(group_counts),"groups_max":max(group_counts),
               "odds":current.scheme_odds(scheme),
               "numerators":current.scheme_numerators(scheme)}
        result.append(row)
        print(json.dumps(row),flush=True)
    return {"python":sys.version,"seed":20261104,
            "baseline_source_sha256":hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "data":"Synthetic repeatable rows; all official block sets and terminal mapping retained.",
            "cases":result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--repeats",type=int,default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output.write_text(json.dumps(run(args.baseline_source,args.repeats),indent=2),
                           encoding="utf-8")
