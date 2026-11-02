"""Nested chronological evaluation of a FROZEN, declared selection procedure.

At each outer target: build prequential inner folds from earlier dates, run
the production method decision (including Holm), select generation parameters
on those inner dates, freeze tickets, then reveal the target. No global
best_methods.json, folds.csv, adaptive state or production cache is read/written.

This retrospective replay cannot undo historical development of the methods.
It is not an external prospective holdout and proves no predictive advantage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from math import comb

import numpy as np
import pandas as pd

from loto_enterprise.benchmark.decision import decide_optimal_config_for_pool
from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance
from loto_enterprise.core.ticket_experiment import (
    GAMES, compare_probabilities, equal_budget_pool, integer, optimize_universe,
    proportion, random_tickets, realized, validate_tickets,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Parameters:
    pool_size: int
    lookback: int = 0
    guarantee: int = 4
    wheel_condition: int | None = None
    max_consecutive_run: int = 0
    recent_penalty_draws: int = 0
    recent_penalty_factor: float = .5
    restrict_base_min: int = 0
    restrict_base_max: int = 0
    wheel_method: str = "hitcover"


@dataclass(frozen=True)
class Protocol:
    game: str
    budget: int
    parameters: tuple[Parameters, ...]
    objective: str = "4plus"
    hit_target: int = 4
    inner_draws: int = 30
    minimum_training: int = 30
    variant_price_bani: int = 800
    ticket_fees_bani: int | None = None
    probability_samples: int = 20_000
    design_samples: int = 4000
    universe_proposals: int = 128
    seed: int = 104729

    def validate(self):
        game = GAMES[self.game]
        integer(self.budget, minimum=1)
        if self.budget > 100_000:
            raise ValueError("physical experiment limit is 100000 variants; declare a smaller budget")
        integer(self.inner_draws, minimum=5)
        integer(self.minimum_training, minimum=5)
        integer(self.variant_price_bani, minimum=1)
        if self.ticket_fees_bani is not None:
            integer(self.ticket_fees_bani)
        integer(self.probability_samples, minimum=1)
        integer(self.design_samples, minimum=1)
        if self.budget * self.design_samples > 20_000_000:
            raise ValueError("design sample matrix exceeds 20 million entries; reduce design_samples or budget explicitly")
        integer(self.universe_proposals)
        integer(self.seed)
        if self.objective not in ("4plus", "any_prize") or self.hit_target not in (3, 4):
            raise ValueError("invalid explicit objective or method hit target")
        if not self.parameters or len(set(self.parameters)) != len(self.parameters):
            raise ValueError("declare a nonempty grid without duplicate parameters")
        for p in self.parameters:
            if not game.pick <= integer(p.pool_size) <= game.maximum:
                raise ValueError("pool outside game geometry")
            if comb(p.pool_size, game.pick) < self.budget:
                raise ValueError("each pool must supply the SAME distinct-variant budget")
            for n in (p.lookback, p.max_consecutive_run, p.recent_penalty_draws,
                      p.restrict_base_min, p.restrict_base_max):
                integer(n)
            if p.lookback and p.lookback < 5:
                raise ValueError("lookback needs at least five prior draws")
            if not 1 <= integer(p.guarantee) <= game.pick:
                raise ValueError("invalid ticket guarantee")
            if p.wheel_condition is not None:
                condition = integer(p.wheel_condition)
                if condition and not p.guarantee <= condition <= game.pick:
                    raise ValueError("invalid wheel condition")
            if not 0 <= p.recent_penalty_factor <= 1:
                raise ValueError("invalid recent penalty")
            from wheeling_methods import WHEEL_METHODS

            if p.wheel_method not in WHEEL_METHODS:
                raise ValueError("unknown explicit wheel method")
            lo, hi = p.restrict_base_min or 1, p.restrict_base_max or game.maximum
            if not 1 <= lo <= hi <= game.maximum or hi - lo + 1 < p.pool_size:
                raise ValueError("declared interval cannot supply pool")


class ChronologicalEvaluator:
    def __init__(self, frame: pd.DataFrame, protocol: Protocol, *, prize_lookup=None, cancel_check=None):
        protocol.validate()
        self.protocol, self.game = protocol, GAMES[protocol.game]
        self.frame = chronological_history(frame).reset_index(drop=True)
        dates = history_dates(self.frame)
        if dates is None:
            raise ValueError("dated history is required for selection validation")
        self.dates = dates.dt.strftime("%Y-%m-%d").tolist()
        self.draws, valid = valid_draw_matrix(
            self.frame, [f"n{i}" for i in range(1, self.game.drawn + 1)],
            draw_n=self.game.drawn, max_num=self.game.maximum,
        )
        if not bool(np.asarray(valid).all()):
            raise ValueError("invalid historical draws; repair input before evaluation")
        self.cutoffs = training_cutoffs(self.frame)
        self.jokers = None
        if self.game.joker:
            joker_matrix, valid = valid_draw_matrix(self.frame, ["joker"], draw_n=1, max_num=20)
            if not bool(np.asarray(valid).all()):
                raise ValueError("complete Joker history required for both-drum evaluation")
            self.jokers = joker_matrix[:, 0]
        # Freeze registry membership at experiment start, including witnesses.
        self.methods = tuple(sorted(METHODS))
        if not {"random", "frequency"} <= set(self.methods):
            raise ValueError("registry must retain random and frequency")
        self.prize_lookup = prize_lookup
        self.cancel_check = cancel_check or (lambda: False)
        self._scores, self._pools = {}, {}
        self.failures = {}

    def _raw_scores(self, method, cutoff, parameters, drum=False):
        if self.cancel_check():
            raise InterruptedError("Chronological evaluation cancelled")
        key = (method, cutoff, parameters.lookback, drum)
        if key not in self._scores:
            start = max(0, cutoff - parameters.lookback) if parameters.lookback else 0
            source = self.jokers[:, None] if drum else self.draws
            prefix = source[start:cutoff].copy()
            boundaries = tuple(max(0, c - start) for c in self.cutoffs[start:cutoff])
            try:
                scores, _ = call_method(method, prefix, 20 if drum else self.game.maximum,
                                        history_cutoffs=boundaries)
                if not has_usable_score_variance(scores):
                    raise ValueError("empty, flat or non-finite scores")
                if any(not np.isfinite(float(s)) for s in scores.values()):
                    raise ValueError("non-finite scores")
                self._scores[key] = scores
            except InterruptedError:
                raise
            except Exception as exc:
                self.failures[str(key)] = f"{type(exc).__name__}: {exc}"
                self._scores[key] = None
        result = self._scores[key]
        if result is None:
            raise ValueError(f"method unavailable: {method}")
        return result

    def _pool(self, method, cutoff, p, drum=False):
        key = (method, cutoff, p, drum)
        if key not in self._pools:
            scores = dict(self._raw_scores(method, cutoff, p, drum))
            audit = {}
            if p.recent_penalty_draws:
                from loto_engine import LotoEngine

                start = max(0, cutoff - p.lookback) if p.lookback else 0
                source = self.jokers[:, None] if drum else self.draws
                scores, audit["recent_penalty"] = LotoEngine.apply_recent_penalty(
                    scores, source[start:cutoff], p.recent_penalty_draws,
                    p.recent_penalty_factor, 20 if drum else self.game.maximum,
                )
            if not drum:
                lo, hi = p.restrict_base_min or 1, p.restrict_base_max or self.game.maximum
                scores = {n: s for n, s in scores.items() if lo <= n <= hi}
            size, maximum = (1, 20) if drum else (p.pool_size, self.game.maximum)
            ranked = rank_by_score(scores, maximum)
            pool = select_pool_from_scores(scores, size, set(), audit, maximum,
                                           max_consecutive_run=0 if drum else p.max_consecutive_run)
            if len(pool) != size:
                raise ValueError("incomplete selected pool")
            tie = len(ranked) > size and scores[ranked[size - 1]] == scores[ranked[size]]
            self._pools[key] = (pool, scores, bool(tie), audit)
        return self._pools[key]

    def _decision(self, indices, p, drum=False):
        size = 1 if drum else p.pool_size
        key = "joker_urna2" if drum else ("joker_urna1" if self.game.joker else self.game.key)
        rows = []
        for method in self.methods:
            hits, ties = [], []
            for i in indices:
                try:
                    pool, _, tie, _ = self._pool(method, self.cutoffs[i], p, drum)
                    actual = {int(self.jokers[i])} if drum else set(map(int, self.draws[i]))
                    hits.append(len(set(pool) & actual))
                    ties.append(tie)
                except ValueError:
                    hits.append(-1)
                    ties.append(False)
            for percentile in (10, 30, 60, 100):
                count = max(1, int(len(indices) * percentile / 100))
                values = np.asarray(hits[-count:])
                valid = values >= 0
                evaluated = values[valid]
                row = {"game": key, "method": method, "percentile": percentile,
                       "is_random": False, "failed": not len(evaluated),
                       "n_test": count, "n_eval": len(evaluated), "runtime_sec": 0.,
                       f"k{size}": float(evaluated.mean()) if len(evaluated) else np.nan,
                       f"tiebreak_k{size}": float(np.asarray(ties[-count:])[valid].mean()) if len(evaluated) else np.nan}
                for threshold in (1, 3, 4, 5, 6):
                    row[f"rate_{threshold}plus_k{size}"] = float((evaluated >= threshold).mean()) if len(evaluated) else np.nan
                rows.append(row)
        decision = decide_optimal_config_for_pool(
            pd.DataFrame(rows), key, size, 1 if drum else self.game.drawn,
            20 if drum else self.game.maximum, 1 if drum else self.game.pick,
            hit_target=self.protocol.hit_target, use_curated=False,
        )
        if "error" in decision:
            raise ValueError(decision["error"])
        return decision

    def _tickets(self, cutoff, p, decision, joker_decision=None):
        if self.cancel_check():
            raise InterruptedError("Chronological evaluation cancelled")
        # Production currently selects a single scorer. Fail visibly if the
        # production decision later starts selecting a genuinely new ensemble:
        # that ranking needs its own nested inner predictions, not member means.
        members = decision.get("ensemble") or []
        if len(members) > 1:
            raise ValueError("multi-member selection requires nested ensemble evaluation")
        method = decision["scorer"]
        pool, scores, _, audit = self._pool(method, cutoff, p)
        lines, wheel = equal_budget_pool(self.game, pool, scores, self.protocol.budget,
                                        p.guarantee, p.wheel_condition,
                                        seed=self.protocol.seed + cutoff, method=p.wheel_method)
        if self.game.joker:
            ball = self._pool(joker_decision["scorer"], cutoff, p, True)[0][0]
            lines = [line + [ball] for line in lines]
        return validate_tickets(self.game, lines), {"pool": pool, "method": method,
                "joker_method": joker_decision["scorer"] if joker_decision else None,
                "parameters": asdict(p), "wheel": wheel, "filters": audit}

    def freeze_before(self, target_index):
        """Select only on earlier completed DAYS; target row is never accessed."""
        cutoff = self.cutoffs[target_index]
        available = [i for i in range(cutoff) if self.cutoffs[i] >= self.protocol.minimum_training]
        inner = available[-self.protocol.inner_draws:]
        if len(inner) < self.protocol.inner_draws:
            raise ValueError("insufficient prior inner predictions")
        candidates, rejected = [], []
        for p in self.protocol.parameters:
            try:
                decision = self._decision(inner, p)
                joker_decision = self._decision(inner, p, True) if self.game.joker else None
                successes = 0
                for i in inner:
                    lines, _ = self._tickets(self.cutoffs[i], p, decision, joker_decision)
                    actual = realized(self.game, lines, self.draws[i], self.dates[i],
                                      joker=int(self.jokers[i]) if self.game.joker else None,
                                      variant_price_bani=self.protocol.variant_price_bani,
                                      ticket_fees_bani=self.protocol.ticket_fees_bani)
                    successes += bool(actual[self.protocol.objective])
                candidates.append((successes, p, decision, joker_decision))
            except ValueError as exc:
                rejected.append({"parameters": asdict(p), "reason": str(exc)})
        if not candidates:
            raise ValueError(f"no complete eligible procedure: {rejected}")
        # Declared grid order is the deterministic tie-break; no outer result.
        winner = max(candidates, key=lambda item: item[0])
        successes, p, decision, joker_decision = winner
        lines, info = self._tickets(cutoff, p, decision, joker_decision)
        info.update(training_end_exclusive=cutoff, training_last_date=self.dates[cutoff - 1],
                    inner_indices=inner, selection_last_date=self.dates[inner[-1]],
                    decision=decision, joker_decision=joker_decision,
                    inner_objective_successes=successes, rejected_parameters=rejected,
                    candidate_parameter_scores=[{"parameters": asdict(c[1]), "successes": c[0]} for c in candidates],
                    tickets_sha256=digest(lines))
        return lines, info

    def run(self, *, outer_draws=10, progress=None):
        outer_draws = integer(outer_draws, minimum=1)
        if outer_draws > len(self.frame):
            raise ValueError("outer count exceeds history")
        # All construction settings fixed before any outer outcome is examined.
        universe, search = optimize_universe(self.game, self.protocol.budget,
            objective=self.protocol.objective, seed=self.protocol.seed,
            design_samples=self.protocol.design_samples, proposals=self.protocol.universe_proposals)
        reference = random_tickets(self.game, self.protocol.budget, self.protocol.seed + 17)
        rows = []
        for i in range(len(self.frame) - outer_draws, len(self.frame)):
            if progress:
                progress(len(rows), outer_draws, self.dates[i])
            pool, frozen = self.freeze_before(i)
            portfolios = {"pool": pool, "universe": universe, "random_reference": reference}
            # Reveal the target only after freeze_before has returned the tickets.
            numbers = list(map(int, self.draws[i]))
            joker = int(self.jokers[i]) if self.game.joker else None
            official = self.prize_lookup(self.game.key, self.dates[i], numbers, joker) if self.prize_lookup else None
            outcomes = {name: realized(self.game, lines, numbers, self.dates[i], joker=joker,
                official=official, variant_price_bani=self.protocol.variant_price_bani,
                ticket_fees_bani=self.protocol.ticket_fees_bani) for name, lines in portfolios.items()}
            probabilities = compare_probabilities(self.game, portfolios,
                samples=self.protocol.probability_samples, seed=self.protocol.seed + 1_000_003)
            rows.append({"target_index": i, "target_date": self.dates[i], "numbers": numbers,
                         "joker": joker, "selection": frozen, "tickets": portfolios,
                         "probabilities": probabilities, "outcomes": outcomes})
        summary = {}
        for name in ("pool", "universe", "random_reference"):
            results = [row["outcomes"][name] for row in rows]
            known = [r for r in results if r["net_bani"] is not None]
            summary[name] = {
                "any_prize_frequency": proportion(sum(r["any_prize"] for r in results), len(results)),
                "4plus_frequency": proportion(sum(r["4plus"] for r in results), len(results)),
                "cost_recovery_frequency_known_only": proportion(sum(r["recovered_cost"] for r in known), len(known)),
                "financially_resolved_draws": len(known), "financially_missing_draws": len(results) - len(known),
                "variant_cost_bani": sum(r["variant_cost_bani"] for r in results),
                "full_cost_bani": sum(r["full_cost_bani"] for r in results) if all(r["full_cost_bani"] is not None for r in results) else None,
                "gross_bani": sum(r["gross_bani"] for r in results) if all(r["gross_bani"] is not None for r in results) else None,
                "net_bani": sum(r["net_bani"] for r in results) if len(known) == len(results) else None,
            }
        return {"schema": "chronological_selection_v1", "protocol": asdict(self.protocol),
                "protocol_sha256": digest(asdict(self.protocol)), "registry": list(self.methods),
                "registry_sha256": digest(list(self.methods)), "universe_search": search,
                "selection_validation": "nested_chronological_procedure",
                "inference": "retrospective_replay_no_demonstrated_predictive_advantage",
                "interval_scope": "MC intervals measure sampling error; historical frequency intervals are descriptive, not independent predictive tests",
                "financial_note": "Missing prize amounts/fees are unknown, never zero. Published prizes are gross estimates before tax and may change with additional winners.",
                "method_failures": self.failures, "summary": summary, "rows": rows}
