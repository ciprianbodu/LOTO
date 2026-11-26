"""Time-bounded refinement of a complete paid Romanian LR portfolio."""

from __future__ import annotations

import logging
from itertools import combinations
from math import comb, isfinite
from random import Random
from time import monotonic

from covering.lr_pool_budget import _BudgetStopped, _Choice, _PortfolioSearch, _zone_catalog
from covering.lr_portfolio import cost_bani
from loto_enterprise.core.lotteries import require_lottery


ALGORITHM_VERSION = "expanded-placement-beam12-two-zone-v1"
EVALUATOR_VERSION = "lr-paid-union-protected-tails-v1"
_MAX_SECONDS = 2.0
_logger = logging.getLogger(__name__)


class _ExpandedSearch(_PortfolioSearch):
    """Use the frozen candidate's catalogue, starter order, beam and pair repairs."""

    def expanded_zones(self, schemes, generation_end):
        codes = tuple(scheme.code for scheme in schemes)
        by_code = {code: [] for code in codes}
        for zone in _zone_catalog(self.lot.game_id, self.width, self.threshold, self.bonus, codes):
            by_code[zone.code].append(zone)

        for scheme in schemes:
            self.stop()
            if monotonic() >= generation_end:
                break
            zones = by_code[scheme.code]
            known = {zone.positions for zone in zones}
            total = comb(self.width, scheme.n)
            seed = 0x5EED2026 + self.lot.max_n * 100000 + self.width * 1000 + scheme.code
            rng = Random(seed)
            added = 0
            for _ in range(4096):
                if added >= 64 or len(known) >= total:
                    break
                self.stop()
                if monotonic() >= generation_end:
                    break
                positions = tuple(sorted(rng.sample(range(self.width), scheme.n)))
                if positions not in known:
                    known.add(positions)
                    zones.append(self.make_zone(scheme.code, positions, scheme.size))
                    added += 1
        return by_code

    def search(self, incumbent):
        schemes = tuple(
            scheme for scheme in self.schemes.values()
            if scheme.n <= self.width
            and cost_bani(scheme.size, 1, self.unit, self.per_ticket) <= self.budget
        )
        if not schemes:
            return
        by_code = self.expanded_zones(schemes, min(self.deadline, self.start + 0.5))
        choices = tuple(dict.fromkeys((
            *incumbent.choices,
            *(_Choice(zone, slot) for zones in by_code.values() for zone in zones
              for slot in range(max(1, self.joker_slots))),
        )))
        starters = []
        for zones in by_code.values():
            count = min(8, len(zones))
            for i in range(count):
                index = i * (len(zones) - 1) // max(1, count - 1)
                starters.append(_Choice(zones[index], 0))
        self.evidence.update(
            candidate_choices=len(choices),
            candidate_zones=sum(map(len, by_code.values())),
            starter_choices=len(starters),
        )
        self.stop()
        seeds = [self.add(self.blank(), choice) for choice in starters]
        for seed in seeds:
            self.consider(seed)
        self.grow([self.blank(), *seeds], choices, 12, min(self.deadline, self.start + 1.2))
        self.stop()
        if self.best.score == self.upper:
            return
        repairs = 0
        for initial in dict.fromkeys((self.best.choices, incumbent.choices)):
            for first, second in combinations(range(len(initial)), 2):
                if repairs >= 10:
                    return
                self.stop()
                seed = self.portfolio(tuple(
                    choice for i, choice in enumerate(initial) if i != first and i != second
                ))
                repairs += 1
                self.grow((seed,), choices, 12, self.deadline)
                if self.best.score == self.upper:
                    return

    def played_slot(self, portfolio, index, choice):
        # The incoming paid plan can have explicit secondary assignments even
        # when its primary objective is four main hits. Preserve its exact bets.
        if portfolio is self.baseline:
            return self.incumbent_zones[index][3]
        return self.slot(index, choice)

    def actual_bets(self, portfolio):
        return tuple(
            (ticket, self.played_slot(portfolio, i, choice))
            for i, choice in enumerate(portfolio.choices)
            for ticket in self.variants(choice)
        )

    def optimize_current(self, zones, known):
        self.incumbent_zones = zones
        self.baseline = self.portfolio(tuple(
            _Choice(self.make_zone(code, positions, count), slot if slot is not None else 0)
            for code, positions, count, slot in zones
        ))
        score, main, duplicates = self.exact(self.baseline)
        paid = sum(self.schemes[code].size for code, _, _, _ in zones)
        if (score != self.baseline.score or score != known or self.baseline.price != self.budget
                or cost_bani(paid, len(zones), self.unit, self.per_ticket) != self.budget):
            raise ValueError("Costul sau numărătorul portofoliului de pornire diferă de biletele plătite.")
        self.floor = tuple(main[t - 1] for t in self.protected)
        self.best = self.baseline
        self.best_key = (score, -self.baseline.price, -duplicates)
        self.evidence = {}
        if self.baseline.choices and score != self.upper:
            try:
                self.stop()
                self.search(self.baseline)
            except _BudgetStopped:
                self.evidence["stopped"] = (
                    "cancelled" if self.cancelled is not None and self.cancelled() else "timeout"
                )
            except Exception:
                _logger.exception("Căutarea LR extinsă s-a întrerupt.")
                self.evidence["fallback"] = "search_error"

        selected = zones
        result_price = self.baseline.price
        if self.best is not self.baseline:
            try:
                new_score, new_main, new_duplicates = self.exact(self.best)
                paid = sum(self.schemes[c.zone.code].size for c in self.best.choices)
                paid_price = cost_bani(paid, len(self.best.choices), self.unit, self.per_ticket)
                if (new_score != self.best.score or new_score < score
                        or self.best.price != paid_price or paid_price > result_price
                        or (self.upper is not None and new_score > self.upper)
                        or any(new_main[t - 1] < main[t - 1] for t in self.protected)):
                    raise ValueError("Verificarea din variantele jucate a respins candidatul LR.")
                selected = tuple(
                    (c.zone.code, c.zone.positions, c.zone.variants, self.played_slot(self.best, i, c))
                    for i, c in enumerate(self.best.choices)
                )
                score, main, duplicates = new_score, new_main, new_duplicates
                result_price = self.best.price
            except Exception:
                _logger.exception("Candidatul LR nu a putut fi verificat. Se păstrează biletele inițiale.")
                self.evidence["fallback"] = "verification_error"
        return selected, score, result_price, {
            "candidate": ALGORITHM_VERSION,
            "certificate": "proven_bound" if score == self.upper else "feasible",
            "protected_numerators": {str(t): main[t - 1] for t in self.protected},
            "duplicate_paid_variants": duplicates,
            "search": self.evidence,
        }


def refine_expanded(game, width, objective, price, joker_count, threshold, upper, zones,
                    attained, time_limit=_MAX_SECONDS, cancelled=None):
    """Refine position/slot tuples, with the completed incumbent as cost ceiling."""
    seconds = float(time_limit)
    if not isfinite(seconds) or seconds < 0:
        raise ValueError("Durata căutării LR trebuie să fie finită și nenegativă.")
    start = monotonic()
    search = _ExpandedSearch(
        require_lottery(game, "RO"), width, objective, price, joker_count,
        threshold, upper, min(seconds, _MAX_SECONDS), cancelled,
    )
    search.start = start
    search.deadline = start + min(seconds, _MAX_SECONDS)
    return search.optimize_current(zones, attained)
