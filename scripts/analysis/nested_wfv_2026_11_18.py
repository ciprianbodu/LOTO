"""Compare winner replay with nested forward tests on identical targets and ticket budgets.

Run python -m scripts.analysis.nested_wfv_2026_11_18 --output scratch/nested.json
from the repository. No production decision or cache is written.
"""
from __future__ import annotations

import argparse
import itertools
import json
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_schemes import _best_position
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark import decision
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.lotteries import GAMES
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance

ROOT = Path(__file__).resolve().parents[2]
METHODS = (
    "frequency", "freq_window_50", "freq_window_200", "momentum_30_300",
    "cusum_burst", "runs_persistence", "spectral_phase", "repeat_last_draw", "neighbor_adjacent",
)
WARMUP_ROWS = 250
MIN_VALIDATION_DAYS = 100
BLOCK_DAYS = 26
WINDOWS = (10, 30, 70, 100)
BETS = (0.05, 0.15, 0.35, 0.60)
PROTOCOL = {
    "methods": METHODS, "scorer_warmup_rows": WARMUP_ROWS, "inner_minimum_days": MIN_VALIDATION_DAYS,
    "outer_test": "Last 30% of eligible distinct draw dates after warmup and minimum inner history. "
                  "Dates stay whole. Every arm faces exactly the same scored target rows.",
    "block_size_draw_dates": BLOCK_DAYS, "inner_percentages": WINDOWS,
    "prediction": "All methods from the fixed common bank use only rows strictly earlier than each date. "
                  "All outer arms update that scorer's input before each new draw date. "
                  "Only the time when scorer identity is selected differs within each family.",
    "geometry": "Official codes 48/16/24 in Romania. K10 positional reference schemes abroad, "
                "code 49 for six-number tickets, code 24 for five-number tickets. "
                "Maximum two consecutive pool numbers. Joker Urna 2 top-1. EuroMillions main urn only.",
    "tickets": "Choose method by largest count of 5+ on a ticket, then 4+, then sum of best-ticket hits. "
               "Urna 2 uses top-1. Ties prefer the earlier declared method. All prior eligible rows count.",
    "decision": "Run the production decision function on four synthesized suffixes of earlier scored draw dates, "
                "using raw canonical top-K pool hits and observed score ties for each common method. "
                "The validated ticket and final pool still use the limit of two consecutive values. "
                "This restricted candidate bank and complete-date folds are a controlled comparison, not an "
                "archived production Re-Bench. Secondary diagnostic only; curation comes from the checkout.",
    "arms": "Frequency fixed. In both families select once at the first test origin, every 26 draw dates, "
            "or once at archive end and replay retrospectively. For direct ticket selection also reselect "
            "each date. End-of-archive methods use future outcomes and are display-only comparators.",
    "evidence": "For the five causal selection policies vs fixed frequency, primary success is best ticket 5+ "
                "or single ball top-1. One-sided e-value under uniform independent draws, mean of products "
                "of 1+b*s with b=0.05/0.15/0.35/0.60 and s=+1,-1,0 according to paired success difference. "
                "Multiplicity threshold = 5 * number of urns / 0.05. No test for retrospective winners.",
    "archive": "Exploratory, previously reused source histories. No scorer promotion.",
}


def call_method(*args, **kwargs):
    """Use the archived scorers for the dated CLI and its companion studies."""
    from scripts.analysis.legacy_methods_2026_11_20 import call_method as archived_call

    return archived_call(*args, **kwargs)


def active_methods(key):
    from loto_enterprise.benchmark.curated import apply_curation, resolve_methods_per_game
    from loto_enterprise.benchmark.methods import list_methods, require_complete_catalog

    require_complete_catalog()
    available, _ = apply_curation(list_methods())
    names = resolve_methods_per_game(available, [key]).get(key, available)
    # Frequency wins exact ties. Random is evaluated on the same dates but
    # remains outside the selectable methods for every policy.
    return ("frequency", *(name for name in names if name not in ("frequency", "random")), "random")


def read_draws(lot, second):
    # One read ties the identity reported in the UI to the exact input we scored.
    content = (ROOT / lot.csv).read_bytes()
    frame = chronological_history(pd.read_csv(BytesIO(content))).reset_index(drop=True)
    universe = lot.geo.second.max_n if second else lot.max_n
    n = lot.geo.second.draw_n if second else lot.draw_n
    columns = list(lot.geo.second.columns) if second else [f"n{i}" for i in range(1, n + 1)]
    matrix, valid = valid_draw_matrix(frame, columns, draw_n=n, max_num=universe)
    dates = history_dates(frame)
    if dates is None or not np.all(valid):
        raise ValueError(f"Invalid dated history for {lot.csv}")
    cutoffs = np.asarray(training_cutoffs(frame), dtype=np.int64)
    days = np.flatnonzero((cutoffs == np.arange(len(matrix))) & (cutoffs >= WARMUP_ROWS))
    if len(days) <= MIN_VALIDATION_DAYS + 2:
        raise ValueError(f"Insufficient prior validation dates for {lot.csv}")
    return matrix, dates.to_numpy().astype("datetime64[D]"), cutoffs, days, universe, sha256(content).hexdigest()


def scheme_definition(lot, second, code):
    if code is not None and (not lot.is_romanian or second):
        raise ValueError("An explicit LR code requires a Romanian main urn")
    if second:
        return 1, None, None
    if lot.is_romanian:
        geometry = lot.geometry
        code = code if code is not None else {"6/49": 48, "5/40": 16, "joker": 24}[geometry]
    else:
        geometry, code = ("6/49", 49) if lot.pick_n == 6 else ("joker", 24)
    scheme = resolve_lr_scheme(geometry, code)
    metadata = {"label": geometry, "code": scheme.code, "variants": scheme.size, "official_for_game": lot.is_romanian}

    @cache
    def best_ticket_hits(mask):
        return _best_position(scheme, mask)[1]

    return scheme.n, metadata, best_ticket_hits


def outcome_matrices(matrix, cutoffs, days, universe, k, lookup, methods, caller, fallback_days):
    """Evaluate a shared date prefix, with visible frequency substitution in active reports."""
    first = int(days[0])
    shape = (len(matrix) - first, len(methods))
    raw_hits = np.empty(shape, dtype=np.uint8)
    pool_hits = np.empty_like(raw_hits)
    ticket_hits = np.empty_like(raw_hits)
    ties = np.empty((len(days), len(methods)), dtype=np.uint8)
    balls = set(range(1, universe + 1))
    for day, (start, end) in enumerate(zip(days, np.r_[days[1:], len(matrix)], strict=True)):
        targets = matrix[start:end]
        rows = slice(start - first, end - first)
        frequency_scores = None
        for col, method in enumerate(methods):
            try:
                scores, _ = caller(method, matrix[:start], universe, history_cutoffs=cutoffs[:start])
                if scores.keys() != balls or any(not np.isfinite(v) or v < 0 for v in scores.values()):
                    raise ValueError(f"Incomplete or invalid scores for {method} at {start}")
                if not has_usable_score_variance(scores) and not (fallback_days is not None and method == "frequency"):
                    raise ValueError(f"Unusable scores for {method} at {start}")
            except Exception:
                if fallback_days is None or method in ("frequency", "random") or frequency_scores is None:
                    raise
                fallback_days[method] += 1
                scores = frequency_scores
            if method == "frequency":
                frequency_scores = scores
            rank = rank_by_score(scores, k + 1)
            ties[day, col] = scores[rank[k - 1]] == scores[rank[k]]
            raw_mask = np.zeros(universe + 1, dtype=np.uint8)
            raw_mask[rank[:k]] = 1
            raw_hits[rows, col] = raw_mask[targets].sum(axis=1)
            numbers = select_pool_from_scores(scores, k, set(), max_num=universe, max_consecutive_run=2)
            if len(numbers) != k or numbers != sorted(numbers):
                raise ValueError("Invalid positional pool for the reduced scheme")
            positions = np.zeros(universe + 1, dtype=np.uint32)
            positions[numbers] = 1 << np.arange(k)
            masks = positions[targets].sum(axis=1)
            pool_hits[rows, col] = np.bitwise_count(masks)
            if lookup is None:
                ticket_hits[rows, col] = pool_hits[rows, col]
            else:
                ticket_hits[rows, col] = [lookup(int(mask)) for mask in masks]
    return raw_hits, pool_hits, ticket_hits, ties


def cumulative(values):
    return np.vstack((np.zeros((1, values.shape[1]), dtype=np.int64),
                      np.cumsum(values, axis=0, dtype=np.int64)))


def forecast_methods(ends, first_day, block_days, choose):
    """Set the method before the first row of a block; an entire date stays together."""
    predictions = np.empty(ends[-1] - ends[first_day], dtype=np.int16)
    choices = []
    for day in range(first_day, len(ends) - 1, block_days):
        next_day = min(day + block_days, len(ends) - 1)
        method = choose(day)
        predictions[ends[day] - ends[first_day]:ends[next_day] - ends[first_day]] = method
        choices.append(method)
    return predictions, choices


def fold_decision(day, ends, raw_sums, event_sums, tie_sums, key, k, draw_n, universe,
                  hit_target, methods, decision_candidates):
    threshold = 1 if k == 1 else hit_target
    records = []
    for pct in WINDOWS:
        days_in_fold = (pct * day + 99) // 100
        first_day = day - days_in_fold
        start, stop = ends[first_day], ends[day]
        n = int(stop - start)
        rates = (event_sums[stop] - event_sums[start]) / n
        means = (raw_sums[stop] - raw_sums[start]) / n
        fractions = (tie_sums[day] - tie_sums[first_day]) / days_in_fold
        for index, method in enumerate(methods):
            records.append({
                "game": key, "method": method, "percentile": pct, "failed": False,
                "is_random": False, "runtime_sec": np.nan, "n_eval": n, "n_test": n, "blocks": days_in_fold,
                f"k{k}": means[index], f"rate_{threshold}plus_k{k}": rates[index],
                f"tiebreak_k{k}": fractions[index],
            })
    response = decision.decide_optimal_config_for_pool(
        pd.DataFrame(records), key, k, draw_n, universe,
        hit_target=hit_target, candidate_methods=decision_candidates,
    )
    if (
        response.get("hit_target") != threshold
        or response.get("expected_windows") != list(WINDOWS)
        or response.get("scorer") not in decision_candidates
    ):
        raise ValueError(f"Unexpected decision contract for {key}: {response}")
    return methods.index(response["scorer"])


def summary(indices, choices, pool, tickets, threshold, *, methods, pool_max, ticket_max, primary_pool=False):
    row = np.arange(len(tickets))
    selected_tickets = tickets[row, indices]
    selected_pool = pool[row, indices]
    counts = {
        "selection_count": len(choices),
        "changes": sum(a != b for a, b in itertools.pairwise(choices)),
        "method_draws": dict(zip(methods, map(int, np.bincount(indices, minlength=len(methods))), strict=True)),
        "pool_5plus": int((selected_pool >= 5).sum()), "pool_4plus": int((selected_pool >= 4).sum()),
        "ticket_5plus": int((selected_tickets >= 5).sum()), "ticket_4plus": int((selected_tickets >= 4).sum()),
        "primary": int(((selected_pool if primary_pool else selected_tickets) >= threshold).sum()),
        "mean_ticket_hits": float(selected_tickets.mean()),
    }
    if pool_max >= 6:
        counts["pool_6plus"] = int((selected_pool >= 6).sum())
    if ticket_max >= 6:
        counts["ticket_6plus"] = int((selected_tickets >= 6).sum())
    return counts


def paired(candidate, baseline):
    wins = int((candidate & ~baseline).sum())
    losses = int((baseline & ~candidate).sum())
    log_e = [wins * np.log1p(b) + losses * np.log1p(-b) for b in BETS]
    return {"wins": wins, "losses": losses, "e_value": float(np.exp(np.logaddexp.reduce(log_e) - np.log(len(BETS))))}


def study_game(key, *, scheme_code=None, hit_target=5, selection_basis="ticket", active_catalog=False):
    """Evaluate the registered CSV. The dated CLI keeps its original ticket protocol."""
    lot = next((g for g in GAMES if key in g.bench_keys), None)
    if lot is None:
        raise ValueError(f"Unknown validation stream: {key}")
    if hit_target not in (4, 5) or selection_basis not in ("ticket", "pool"):
        raise ValueError("Chronological comparison requires a 4+ or 5+ pool or ticket target.")
    second = key == lot.bench_key_urna2
    primary_pool = selection_basis == "pool" and not second
    k, scheme, lookup = scheme_definition(lot, second, scheme_code)
    matrix, dates, cutoffs, days, universe, digest = read_draws(lot, second)
    threshold = 1 if second else hit_target
    draw_n = 1 if second else lot.draw_n
    ticket_max = min(draw_n, 1 if second else lot.pick_n)
    if active_catalog:
        from loto_enterprise.benchmark import methods as registry

        methods = active_methods(key)
        candidates = methods[:-1]
        decision_candidates = candidates
        caller = registry.call_method
        fallback_days = dict.fromkeys(candidates[1:], 0)
    else:
        from scripts.analysis.legacy_methods_2026_11_20 import eligible_methods_for_game

        methods = candidates = METHODS
        # The original decision applied the per-game curation order only in Romania.
        decision_candidates = (
            tuple(m for m in eligible_methods_for_game(key) if m in methods)
            if lot.is_romanian else methods
        )
        caller = call_method
        fallback_days = None
    raw, pools, tickets, ties = outcome_matrices(matrix, cutoffs, days, universe, k, lookup, methods, caller, fallback_days)
    ends = np.r_[days - days[0], len(raw)]
    split_day = MIN_VALIDATION_DAYS + (len(days) - MIN_VALIDATION_DAYS) * 7 // 10
    start = int(ends[split_day])
    test_len = len(raw) - start
    ticket_objectives = [cumulative(tickets >= threshold)]
    if threshold == 5:
        ticket_objectives.extend((cumulative(tickets >= 4), cumulative(tickets)))

    def pick_ticket(day):
        return max(range(len(candidates)), key=lambda idx: tuple(v[ends[day], idx] for v in ticket_objectives))

    raw_sums = cumulative(raw)
    raw_events = cumulative(raw >= threshold)
    tie_sums = cumulative(ties)

    def pick_decision(day):
        return fold_decision(day, ends, raw_sums, raw_events, tie_sums, key, k, draw_n, universe,
                             hit_target, methods, decision_candidates)

    entries = {"frequency": (np.zeros(test_len, dtype=np.int16), [0])}
    if active_catalog:
        control_index = methods.index("random")
        entries["random"] = (np.full(test_len, control_index, dtype=np.int16), [control_index])
    for family, choose in (("ticket", pick_ticket), ("decision", pick_decision)):
        winner = choose(len(days))
        entries[family + "_hindsight"] = (np.full(test_len, winner, dtype=np.int16), [winner])
        entries[family + "_frozen"] = forecast_methods(ends, split_day, len(days), choose)
        entries[family + "_block26"] = forecast_methods(ends, split_day, BLOCK_DAYS, choose)
    entries["ticket_daily"] = forecast_methods(ends, split_day, 1, pick_ticket)
    if primary_pool:
        # Count eligible draws on the actual constrained pool. The prefix ends
        # before every draw on this day; argmax retains method order on ties.
        pool_events = cumulative(pools >= threshold)

        def pick_pool(day):
            return int(np.argmax(pool_events[ends[day], :len(candidates)]))

        for suffix, block in (("frozen", len(days)), ("block26", BLOCK_DAYS), ("daily", 1)):
            entries["pool_" + suffix] = forecast_methods(ends, split_day, block, pick_pool)
    test_pool, test_ticket = pools[start:], tickets[start:]
    row = np.arange(test_len)
    primary_events = (test_pool if primary_pool else test_ticket) >= threshold
    control = primary_events[:, 0]
    result = {}
    for name, (indices, choices) in entries.items():
        metrics = summary(indices, choices, test_pool, test_ticket, threshold,
                          methods=methods, pool_max=min(draw_n, k), ticket_max=ticket_max, primary_pool=primary_pool)
        if name != "frequency" and not name.endswith("_hindsight"):
            metrics["vs_frequency"] = paired(primary_events[row, indices], control)
        if active_catalog and name != "random" and not name.endswith("_hindsight"):
            metrics["vs_random"] = paired(primary_events[row, indices], primary_events[:, control_index])
        result[name] = metrics
    print(f"{key}: {test_len} target rows", flush=True)
    report = {"key": key, "n": test_len, "n_dates": len(days) - split_day, "k": k, "scheme": scheme, "hit_target": threshold,
              "start": str(dates[start + days[0]]), "end": str(dates[-1]), "sha256": digest, "arms": result}
    if active_catalog:
        report.update(catalog_version=registry.CATALOG_VERSION, candidate_methods=list(candidates),
                      comparator="random", fallback_days=fallback_days, scored_days=len(days))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--protocol-only", action="store_true")
    args = parser.parse_args()
    keys = [key for lot in GAMES for key in lot.bench_keys]
    document = {"protocol": PROTOCOL, "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    if not args.protocol_only:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            document["games"] = list(executor.map(study_game, keys))
        document["family_size"] = 5 * len(keys)
        document["evidence_threshold"] = document["family_size"] / 0.05
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
