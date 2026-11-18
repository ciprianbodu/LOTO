"""Offline causal LR study. Budgets and candidate rules are fixed in PROTOCOL.

The mixture chooses one past co-drawn pair, then completes the draw uniformly.
Exact LR event probabilities account for overlap across official tickets.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
from math import comb
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.stats import binom

from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core.history import chronological_history
from loto_enterprise.core.pool_selection import select_pool_from_scores
from scripts.analysis.lr_pool_study_2026_11_04 import exact_lookup, load_source, safe_scores


ROOT = Path(__file__).resolve().parents[2]
GAMES = (
    ("loto_6_49", "6/49", 49, 6, 9, 48, "naive_bayes_last"),
    ("joker", "joker", 45, 5, 10, 24, "runs_persistence"),
    ("loto_5_40", "5/40", 40, 6, 8, 16, "croston_interval"),
)
PROTOCOL = {
    "version": "lr_pair_mixture1",
    "games": GAMES,
    "warmup": 250,
    "half_life_draw_days": 256,
    "uniform_prior_draws": 100,
    "pair_mixture_weight": 0.20,
    "max_swaps": 2,
    "consecutive_limit": 2,
    "selection": "Start from frozen incumbent pool. Maximize exact model LR 5+ via all single swaps, require non-decreasing model LR 4+ versus preceding pool. Tie: 4+, then lexicographically smallest pool.",
    "primary_window": "Last 30% of eligible distinct dates. All eligible dates are secondary. Archive previously used; all results exploratory.",
    "inference": "One-sided exact binomial vs uniform for the candidate on LR 5+ at each game in primary window, Holm across three games. Paired discordant counts descriptive. Log likelihood ratio evaluates full draw law separately.",
    "joker": "Same causal cumulative-frequency Urna 2 top-1 for all rules. Main-urn 5+ is separate from 5+J and any official prize.",
}


def choose(n, k):
    return comb(n, k) if 0 <= k <= n else 0


@dataclass(frozen=True)
class PairEvent:
    """Conditional event counts by where the planted pair falls."""

    uniform_count: int
    both_outside: int
    one_inside: np.ndarray
    both_inside: np.ndarray
    pos_left: np.ndarray
    pos_right: np.ndarray

    @classmethod
    def build(cls, lookup, pool_size, universe, draw_n, target):
        outside = universe - pool_size
        left, right = np.triu_indices(pool_size, 1)
        pairs = (1 << left) | (1 << right)
        one = np.zeros(pool_size, dtype=np.int64)
        both = np.zeros(len(left), dtype=np.int64)
        none = 0
        uniform = 0
        for mask, best in enumerate(lookup):
            inside = mask.bit_count()
            if best < target or inside > draw_n:
                continue
            outside_draws = draw_n - inside
            uniform += choose(outside, outside_draws)
            none += choose(outside - 2, outside_draws - 2)
            one += ((mask >> np.arange(pool_size)) & 1) * choose(outside - 1, outside_draws - 1)
            both += ((mask & pairs) == pairs) * choose(outside, outside_draws)
        return cls(uniform, none, one, both, left, right)

    def weighted_counts(self, pools, weights):
        """Weighted union count under all possible planted pairs, no ticket overlap."""
        row_sums = weights.sum(axis=1)
        inside = row_sums[pools] @ (self.one_inside - self.both_outside)
        i, j = self.pos_left, self.pos_right
        extra = self.both_inside - self.one_inside[i] - self.one_inside[j] + self.both_outside
        correction = weights[pools[:, i], pools[:, j]] @ extra
        return self.both_outside * row_sums.sum() / 2 + inside + correction


def one_swaps(pool, universe):
    outside = np.setdiff1d(np.arange(1, universe + 1), pool, assume_unique=True)
    alternatives = np.broadcast_to(pool, (len(pool) * len(outside), len(pool))).copy()
    alternatives[np.arange(len(alternatives)), np.repeat(np.arange(len(pool)), len(outside))] = np.tile(outside, len(pool))
    alternatives.sort(axis=1)
    gaps = np.diff(alternatives, axis=1)
    clean = ~np.any((gaps[:, :-1] == 1) & (gaps[:, 1:] == 1), axis=1)
    return alternatives[clean]


def optimize(initial, universe, weights, event4, event5):
    current = np.asarray(initial, dtype=np.int64)
    old4 = event4.weighted_counts(current[None, :], weights)[0]
    old5 = event5.weighted_counts(current[None, :], weights)[0]
    moves = 0
    for _ in range(PROTOCOL["max_swaps"]):
        options = one_swaps(current, universe)
        count4 = event4.weighted_counts(options, weights)
        count5 = event5.weighted_counts(options, weights)
        tol = 1e-12
        eligible = (count4 >= old4 - tol * max(1.0, abs(old4))) & (count5 > old5 + tol * max(1.0, abs(old5)))
        choices = np.flatnonzero(eligible)
        if not len(choices):
            break
        # Round only the tie comparison at numerical noise scale. The gain
        # guard above always uses unrounded event counts.
        pool = options[choices]
        p4 = count4[choices]
        p5 = count5[choices]
        key4 = np.round(p4 / max(1.0, abs(old4)), 12)
        key5 = np.round(p5 / max(1.0, abs(old5)), 12)
        ranked = np.lexsort((*pool.T[::-1], -key4, -key5))
        j = choices[ranked[0]]
        current, old4, old5 = options[j], count4[j], count5[j]
        moves += 1
    return current, moves


def best_hits(pool, row, lookup):
    inside = np.isin(pool, row)
    mask = sum(1 << int(i) for i in np.flatnonzero(inside))
    return int(inside.sum()), int(lookup[mask])


def binomial_power(n, p0, alpha):
    critical = int(binom.isf(alpha, n, p0)) + 1
    return {"critical": critical, "power_at_twice_baseline": float(binom.sf(critical - 1, n, 2 * p0))}


def source_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_game(config):
    started = perf_counter()
    key, label, universe, draw_n, k, code, incumbent = config
    scheme = resolve_lr_scheme(label, code, k)
    lookup = exact_lookup(k, scheme.variants)
    targets = tuple(range(3, min(draw_n, len(scheme.variants[0])) + 1))
    events = {t: PairEvent.build(lookup, k, universe, draw_n, t) for t in targets}
    denominator = comb(universe, draw_n)
    baseline = {str(t): event.uniform_count / denominator for t, event in events.items()}
    matrix, dates, cutoffs, digest = load_source(key, universe, draw_n)
    starts = np.flatnonzero(cutoffs == np.arange(len(dates)))
    ends = np.r_[starts[1:], len(dates)]
    eligible = starts >= PROTOCOL["warmup"]
    primary_start = starts[eligible][int(0.70 * eligible.sum())]
    joker = None
    joker_counts = np.zeros(21, dtype=np.int64)
    if label == "joker":
        frame = chronological_history(pd.read_csv(ROOT / "_ISTORIC" / "joker.csv"))
        second = pd.to_numeric(frame["joker"], errors="raise").to_numpy()
        if not np.all((1 <= second) & (second <= 20) & (second == np.floor(second))):
            raise ValueError("Invalid Joker second-urn history")
        joker = second.astype(np.int64)
    pair_i, pair_j = np.triu_indices(draw_n, 1)
    pair_count = comb(draw_n, 2)
    pair_universe = comb(universe, 2)
    prior = PROTOCOL["uniform_prior_draws"] * pair_count / pair_universe
    counts = np.zeros((universe + 1, universe + 1), dtype=np.float64)
    uniform_pairs = np.full_like(counts, prior)
    uniform_pairs[0] = uniform_pairs[:, 0] = 0
    np.fill_diagonal(uniform_pairs, 0)
    decay = 2 ** (-1 / PROTOCOL["half_life_draw_days"])
    lam = PROTOCOL["pair_mixture_weight"]
    history_counts = np.zeros(universe + 1, dtype=np.int64)
    output = []
    fallbacks = 0
    for start, end in zip(starts, ends, strict=True):
        if start >= PROTOCOL["warmup"]:
            history = matrix[:start]
            freq = {n: float(history_counts[n]) for n in range(1, universe + 1)}
            scores, failure, _ = safe_scores(incumbent, history, universe, cutoffs[:start], freq)
            fallbacks += int(failure is not None)
            reference = select_pool_from_scores(scores, k, set(), max_num=universe, max_consecutive_run=2)
            frequency = select_pool_from_scores(freq, k, set(), max_num=universe, max_consecutive_run=2)
            weights = counts + uniform_pairs
            candidate, moves = optimize(reference, universe, weights, events[4], events[5])
            mass = weights.sum() / 2
            normalizer = mass * comb(universe - 2, draw_n - 2)
            pools = np.array([reference, frequency, candidate])
            estimate = [(1 - lam) * baseline["5"] + lam * x / normalizer for x in events[5].weighted_counts(pools, weights)]
            j_pick = 20 - int(np.argmax(joker_counts[20:0:-1])) if joker is not None else None
            for i in range(start, end):
                draw = matrix[i]
                likelihood = (1 - lam) + lam * pair_universe / pair_count * weights[draw[pair_i], draw[pair_j]].sum() / mass
                row = {"date": str(dates[i])[:10], "primary": bool(i >= primary_start), "moves": moves,
                       "model_p5": estimate, "full_draw_log_lr": float(np.log(likelihood)), "rules": {}}
                for name, pool in zip(("incumbent", "frequency", "pair_lr"), pools, strict=True):
                    pool_hits, ticket = best_hits(pool, draw, lookup)
                    hits = {"pool_hits": pool_hits, "ticket_hits": ticket}
                    if joker is not None:
                        j_hit = j_pick == joker[i]
                        hits.update(top_with_j=bool(j_hit and ticket == 5), any_prize=bool(ticket >= 3 or (j_hit and ticket >= 1)))
                    if label == "5/40":
                        hits["category_1"] = best_hits(pool, draw[:5], lookup)[1] == 5
                    row["rules"][name] = hits
                output.append(row)
        # Each forecast is frozen for the whole date. Account for every row
        # only after predictions for that date have been evaluated.
        counts *= decay
        for draw in matrix[start:end]:
            np.add.at(counts, (draw[pair_i], draw[pair_j]), 1)
            np.add.at(counts, (draw[pair_j], draw[pair_i]), 1)
            np.add.at(history_counts, draw, 1)
        if joker is not None:
            np.add.at(joker_counts, joker[start:end], 1)
    windows = {}
    for name, rows in (("all", output), ("primary", [r for r in output if r["primary"]])):
        n = len(rows)
        games = {}
        for method in ("incumbent", "frequency", "pair_lr"):
            outcomes = [r["rules"][method] for r in rows]
            games[method] = {"ticket_tail": {str(t): sum(o["ticket_hits"] >= t for o in outcomes) for t in targets},
                             "pool5": sum(o["pool_hits"] >= 5 for o in outcomes)}
            for extra in ("top_with_j", "any_prize", "category_1"):
                if extra in outcomes[0]:
                    games[method][extra] = sum(o[extra] for o in outcomes)
        discordant = {}
        for threshold in targets:
            a = [r["rules"]["incumbent"]["ticket_hits"] >= threshold for r in rows]
            b = [r["rules"]["pair_lr"]["ticket_hits"] >= threshold for r in rows]
            discordant[str(threshold)] = {"gain": sum(y and not x for x, y in zip(a,b)), "loss": sum(x and not y for x,y in zip(a,b))}
        got5 = games["pair_lr"]["ticket_tail"]["5"]
        windows[name] = {"n":n,"start":rows[0]["date"],"end":rows[-1]["date"],"rules":games,
                         "expected_uniform":{k:p*n for k,p in baseline.items()},
                         "5plus_binomial_p":float(binom.sf(got5-1,n,baseline["5"])),
                         "5plus_power_bonferroni":binomial_power(n,baseline["5"],0.05/len(GAMES)),
                         "discordant_vs_incumbent":discordant,"changed_pools":sum(r["moves"]>0 for r in rows),
                         "mean_model_p5":np.mean([r["model_p5"] for r in rows],axis=0).tolist(),
                         "full_draw_log_lr":sum(r["full_draw_log_lr"] for r in rows)}
    return {"game":key,"pool":k,"code":code,"tickets":scheme.size,"incumbent":incumbent,"history_sha256":digest,
            "lr_rows_sha256":hashlib.sha256(json.dumps(scheme.variants).encode()).hexdigest(),
            "fallback_days":fallbacks,"seconds":perf_counter()-started,"denominator":denominator,
            "uniform_exact_numerators":{str(t):e.uniform_count for t,e in events.items()},"windows":windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {"protocol":PROTOCOL,"script_sha256":source_digest(Path(__file__)),"results":[]}
    for game in GAMES:
        result = run_game(game)
        report["results"].append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    ordered = sorted(report["results"], key=lambda result: result["windows"]["primary"]["5plus_binomial_p"])
    running_max = 0.0
    for index, result in enumerate(ordered):
        primary = result["windows"]["primary"]
        running_max = max(running_max, min(1.0, (len(ordered)-index)*primary["5plus_binomial_p"]))
        primary["5plus_holm_p"] = running_max
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
