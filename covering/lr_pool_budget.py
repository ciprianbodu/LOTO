"""Affordable official LR zones drawn from one fixed main pool."""

from __future__ import annotations

from covering.lr_catalog_cache import catalog_cache
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from heapq import nlargest
from itertools import combinations
from math import comb, isfinite
from operator import index
from random import Random
from time import monotonic

import numpy as np

from covering.lr_budget import ANY_PRIZE
from covering.lr_portfolio import FEE_BANI, cost_bani
from covering.lr_schemes import lr_schemes
from covering.probability import MAX_EXACT_POOL, wheel_hit_profile
from loto_enterprise.core.lotteries import require_lottery


@dataclass(frozen=True, slots=True)
class _Zone:
    code: int
    positions: tuple[int, ...]
    variants: int
    wins: int
    touch: int


def _pack(values):
    return int.from_bytes(np.packbits(values, bitorder="little").tobytes(), "little")


@lru_cache(maxsize=32)
def _draw_space(size, drawn, universe):
    """An integer bitset over the attainable intersections with this pool."""
    masks = np.arange(1 << size, dtype=np.uint32)
    counts = np.bitwise_count(masks)
    valid = (counts <= drawn) & (counts >= drawn - (universe - size))
    masks, counts = masks[valid], counts[valid]
    balls = tuple(_pack(masks & (1 << i) != 0) for i in range(size))
    layers = tuple(
        (_pack(counts == k), comb(universe - size, drawn - k))
        for k in range(max(0, drawn - (universe - size)), min(size, drawn) + 1)
    )
    return balls, layers


def _weighted(events, layers):
    return sum((events & layer).bit_count() * weight for layer, weight in layers)


def _placements(size, width):
    positions = tuple(range(width))
    if comb(width, size) <= 64:
        return combinations(positions, size)
    # Include every cyclic block, then a reproducible sample of other layouts.
    # Only LR positions are sampled. The chosen balls already belong to the pool.
    layouts = {
        tuple(sorted((start + i) % width for i in range(size)))
        for start in positions
    }
    rng = Random(width * 100 + size)
    layouts.update(tuple(sorted(rng.sample(positions, size))) for _ in range(64))
    return sorted(layouts)


@catalog_cache(maxsize=32)
def _zone_catalog(game, size, threshold, use_bonus, codes):
    lot = require_lottery(game, "RO")
    balls, _ = _draw_space(size, lot.draw_n, lot.max_n)

    @lru_cache(maxsize=None)
    def covered(indices):
        events = balls[indices[0]]
        for i in indices[1:]:
            events &= balls[i]
        return events

    options = []
    for scheme in lr_schemes(game):
        if scheme.code not in codes:
            continue
        targets = {part for row in scheme.variants for part in combinations(row, threshold)}
        for placement in _placements(scheme.n, size):
            wins = touch = 0
            for part in targets:
                wins |= covered(tuple(placement[p - 1] for p in part))
            if use_bonus:
                for p in placement:
                    touch |= balls[p]
            options.append(_Zone(scheme.code, placement, scheme.size, wins, touch))
    return tuple(options)


def _pool_hits(size, lot, threshold):
    return sum(
        comb(size, k) * comb(lot.max_n - size, lot.draw_n - k)
        for k in range(threshold, min(size, lot.draw_n) + 1)
    )


def _layout_bound(plan, lot, size, threshold, bonus_count):
    bound = _pool_hits(size, lot, threshold)
    if bonus_count:
        bound = lot.geo.second.max_n * bound + bonus_count * (_pool_hits(size, lot, 1) - bound)
    if plan["upper"] is not None:
        bound = min(bound, plan["upper"])
    return bound


def _search(options, lotsize, unit, budget, layers, bonus_slots, urn_size):
    """Grow affordable portfolios from each standalone code and an empty start."""
    best, best_value, best_price = (), 0, 0

    def extend(zone, main, extras, slot):
        wins = main | zone.wins
        value = _weighted(wins, layers)
        if bonus_slots:
            extras = extras[:slot] + (extras[slot] | zone.touch,) + extras[slot + 1:]
            value *= urn_size
            value += sum(_weighted(touch & ~wins, layers) for touch in extras if touch)
        return value, wins, extras

    seeds = {}
    for zone in options:
        seeds.setdefault(zone.code, zone)
    for first in (None, *seeds.values()):
        selected, size, main, value, price = (), 0, 0, 0, 0
        extras = (0,) * bonus_slots
        while True:
            slot = len(selected) % max(1, bonus_slots)
            winner = None
            for zone in options if selected or first is None else (first,):
                cost = cost_bani(size + zone.variants, len(selected) + 1, unit, lotsize)
                if cost > budget:
                    continue
                attained, wins, touches = extend(zone, main, extras, slot)
                gain = attained - value
                if gain > 0:
                    key = (Fraction(gain, cost - price), gain, -cost)
                    if winner is None or key > winner[0]:
                        winner = key, zone, cost, attained, wins, touches
            if winner is None:
                break
            _, zone, price, value, main, extras = winner
            size += zone.variants
            selected += (zone,)
            if (value, -price, -len(selected)) > (best_value, -best_price, -len(best)):
                best, best_value, best_price = selected, value, price
    return best, best_value, best_price


@catalog_cache(maxsize=128)
def _layout(game, size, budget_bani, objective, bonus_count):
    lot = require_lottery(game, "RO")
    width = min(size, MAX_EXACT_POOL)
    unit = round(lot.price * 100)
    use_bonus = game == "joker" and objective == ANY_PRIZE
    threshold = 3 if objective == ANY_PRIZE and game != "5/40" else 4
    codes = tuple(
        s.code for s in lr_schemes(game)
        if s.n <= width and cost_bani(s.size, 1, unit, lot.per_ticket) <= budget_bani
    )
    if not codes:
        return (), 0, 0
    _, layers = _draw_space(width, lot.draw_n, lot.max_n)
    options = _zone_catalog(game, width, threshold, use_bonus, codes)
    return _search(
        options, lot.per_ticket, unit, budget_bani, layers,
        bonus_count if use_bonus else 0, lot.geo.second.max_n if use_bonus else 0,
    )

class _BudgetStopped(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Choice:
    zone: _Zone
    joker: int


@dataclass(frozen=True, slots=True)
class _Portfolio:
    choices: tuple[_Choice, ...]
    variants: int
    price: int
    main_wins: int
    joker_hits: tuple[int, ...]
    score: int


def _profile_counts(tickets, drawn, universe):
    """Evaluate distinct bets independently, by maximum hits by pool subset."""
    positions = sorted(set().union(*map(set, tickets)))
    counts = wheel_hit_profile(positions, tickets) if positions else ()
    return tuple(
        sum(row[target] * comb(universe - len(positions), drawn - size)
            for size, row in enumerate(counts)
            if target <= len(positions) and 0 <= drawn - size <= universe - len(positions))
        for target in range(1, drawn + 1)
    )


class _PortfolioSearch:
    def __init__(self, lot, width, objective, price, jokers, threshold, upper, time_limit, cancelled):
        self.lot = lot
        self.width = width
        self.unit = round(lot.price * 100)
        self.per_ticket = lot.per_ticket
        self.budget = price
        self.threshold = threshold
        self.upper = upper
        self.jokers = jokers
        self.bonus = lot.game_id == "joker" and objective == ANY_PRIZE
        self.urn_size = lot.geo.second.max_n if self.bonus else 0
        self.balls, self.layers = _draw_space(width, lot.draw_n, lot.max_n)
        self.protected = tuple(range(4, lot.pick_n + 1))
        self.schemes = {s.code: s for s in lr_schemes(lot.game_id)}
        self.deadline = monotonic() + time_limit
        self.cancelled = cancelled
        self.joker_slots = jokers if self.bonus else 0
        self.guard_cache = {}
        self.template_cache = {}
        self.floor = ()
        self.best = None
        self.best_key = None
        self.catalogue_optimization = None

    def stop(self):
        if (self.cancelled is not None and self.cancelled()) or monotonic() >= self.deadline:
            raise _BudgetStopped

    def blank(self):
        return _Portfolio((), 0, 0, 0, (0,) * self.joker_slots, 0)

    def events(self, code, positions, threshold):
        key = (code, positions, threshold)
        if key not in self.guard_cache:
            parts = {
                part for ticket in self.schemes[code].variants
                for part in combinations(ticket, threshold)
            }
            wins = 0
            for part in parts:
                events = self.balls[positions[part[0] - 1]]
                for p in part[1:]:
                    events &= self.balls[positions[p - 1]]
                wins |= events
            self.guard_cache[key] = wins
        return self.guard_cache[key]

    def make_zone(self, code, positions, variants):
        wins = self.events(code, positions, self.threshold)
        touch = 0
        if self.bonus:
            for p in positions:
                touch |= self.balls[p]
        return _Zone(code, positions, variants, wins, touch)

    def add(self, current, choice):
        zone = choice.zone
        main = current.main_wins | zone.wins
        touches = current.joker_hits
        score = _weighted(main, self.layers)
        if self.bonus:
            slot = choice.joker
            touches = touches[:slot] + (touches[slot] | zone.touch,) + touches[slot + 1:]
            score *= self.urn_size
            score += sum(_weighted(bits & ~main, self.layers) for bits in touches if bits)
        variants = current.variants + zone.variants
        choices = (*current.choices, choice)
        price = cost_bani(variants, len(choices), self.unit, self.per_ticket)
        return _Portfolio(choices, variants, price, main, touches, score)

    def portfolio(self, choices):
        current = self.blank()
        for choice in choices:
            current = self.add(current, choice)
        return current

    def variants(self, choice):
        zone = choice.zone
        key = (zone.code, zone.positions)
        if key not in self.template_cache:
            self.template_cache[key] = tuple(
                tuple(zone.positions[p - 1] + 1 for p in row)
                for row in self.schemes[zone.code].variants
            )
        return self.template_cache[key]

    def actual_bets(self, current):
        return tuple((ticket, self.slot(i, c)) for i, c in enumerate(current.choices)
                     for ticket in self.variants(c))

    def slot(self, index, choice):
        if self.bonus:
            return choice.joker
        return index % self.jokers if self.jokers else None

    def exact(self, current):
        """Recount the completed portfolio using the independent wheel DP."""
        bets = self.actual_bets(current)
        tickets = tuple(sorted({ticket for ticket, _ in bets}))
        main = _profile_counts(tickets, self.lot.draw_n, self.lot.max_n)
        score = main[self.threshold - 1]
        if self.bonus:
            score *= self.urn_size
            for slot in range(self.joker_slots):
                touch = {n for ticket, played in bets if played == slot for n in ticket}
                remaining = tuple(row for ticket in tickets
                                  if (row := tuple(n for n in ticket if n not in touch)))
                outside = self.lot.max_n - len(touch)
                outside_wins = _profile_counts(remaining, self.lot.draw_n, outside)[self.threshold - 1]
                score += (
                    comb(self.lot.max_n, self.lot.draw_n) - comb(outside, self.lot.draw_n)
                    - main[self.threshold - 1] + outside_wins
                )
        return score, main, len(bets) - len(set(bets))

    def passes(self, portfolio):
        for threshold, floor in zip(self.protected, self.floor, strict=True):
            union = 0
            for c in portfolio.choices:
                union |= self.events(c.zone.code, c.zone.positions, threshold)
            if _weighted(union, self.layers) < floor:
                return False
        return True

    def consider(self, portfolio):
        if portfolio.price > self.budget or portfolio.score < self.best.score:
            return
        if portfolio.score == self.best.score and portfolio.price > self.best.price:
            return
        if not self.passes(portfolio):
            return
        bets = self.actual_bets(portfolio)
        key = (portfolio.score, -portfolio.price, len(set(bets)) - len(bets))
        if key > self.best_key:
            self.best, self.best_key = portfolio, key

    def grow(self, frontier, candidates, beam_width, slice_end):
        """Keep several nonidentical affordable continuations at every depth."""
        for _ in range(self.budget // (self.unit * min(c.zone.variants for c in candidates))):
            self.stop()
            if monotonic() >= slice_end:
                break
            following = {}
            for current in frontier:
                for choice in candidates:
                    if monotonic() >= slice_end:
                        break
                    if choice in current.choices:
                        continue
                    price = cost_bani(
                        current.variants + choice.zone.variants,
                        len(current.choices) + 1, self.unit, self.per_ticket,
                    )
                    if price > self.budget:
                        continue
                    built = self.add(current, choice)
                    if built.score > current.score:
                        following.setdefault(frozenset(built.choices), built)
            frontier = nlargest(beam_width, following.values(), key=lambda p: (p.score, -p.price))
            if not frontier:
                break
            for current in frontier:
                self.consider(current)
            if self.best.score == self.upper:
                break

    def solve(self, candidates):
        from covering.lr_pool_milp import solve_zone_coverage

        # Identical abstract Joker labels are interchangeable. Each individual
        # option nonetheless represents a concrete placement and one valid label.
        def main_events(threshold=None, slot=None):
            return tuple(
                c.zone.wins | (c.zone.touch if slot == c.joker else 0)
                if threshold is None else self.events(c.zone.code, c.zone.positions, threshold)
                for c in candidates
            )

        target = [(main_events(slot=None), self.urn_size - self.joker_slots if self.bonus else 1)]
        if self.bonus:
            target.extend((main_events(slot=slot), 1) for slot in range(self.joker_slots))
        groups = [[(ev, m) for ev, m in target if m]]
        groups.extend([(main_events(t), 1)] for t in self.protected)
        indexes, bound, complete = solve_zone_coverage(
            tuple(c.zone.variants for c in candidates), self.unit, FEE_BANI, self.per_ticket,
            self.budget, groups, self.floor, self.layers, self.stop,
            lambda: min(0.6, max(0.0, self.deadline - monotonic())),
        )
        potential = self.portfolio(tuple(candidates[i] for i in indexes))
        verified = (
            bool(indexes)
            and potential.price <= self.budget
            and self.passes(potential)
            and self.exact(potential)[0] == potential.score
        )
        self.catalogue_optimization = {
            "candidates": len(candidates),
            "upper": bound,
            "optimal": bool(complete and verified and bound == potential.score),
        }
        if verified:
            self.consider(potential)

    def optimize(self, incumbent, known_value):
        baseline = self.portfolio(tuple(
            _Choice(self.make_zone(code, positions, variants), i % max(1, self.joker_slots))
            for i, (code, positions, variants) in enumerate(incumbent)
        ))
        exact_score, exact_main, duplicates = self.exact(baseline)
        if exact_score != baseline.score or exact_score != known_value:
            raise ValueError("Probabilitatea exactă a portofoliului LR diferă de valoarea memorată.")
        self.floor = tuple(exact_main[t - 1] for t in self.protected)
        self.best, self.best_key = baseline, (exact_score, -baseline.price, -duplicates)
        if baseline.score != self.upper and baseline.choices:
            try:
                self.stop()
                self.search(baseline)
            except _BudgetStopped:
                pass

        score, main, duplicates = self.exact(self.best)
        if score != self.best.score or (self.upper is not None and score > self.upper):
            raise ValueError("Verificarea independentă a portofoliului LR nu coincide cu optimizarea.")
        if any(main[t - 1] < floor for t, floor in zip(self.protected, self.floor, strict=True)):
            raise ValueError("Portofoliul LR reduce probabilitatea unui prag protejat.")
        zones = tuple((c.zone.code, c.zone.positions, c.zone.variants, self.slot(i, c))
                      for i, c in enumerate(self.best.choices))
        return zones, score, self.best.price, {
            "certificate": "proven_bound" if score == self.upper else "feasible",
            "candidate_milp": self.catalogue_optimization,
            "protected_numerators": {str(t): main[t - 1] for t in self.protected},
            "duplicate_paid_variants": duplicates,
        }

    def search(self, incumbent):
        codes = tuple(
            s.code for s in self.schemes.values()
            if s.n <= self.width and cost_bani(s.size, 1, self.unit, self.per_ticket) <= self.budget
        )
        if not codes:
            return
        base = _zone_catalog(self.lot.game_id, self.width, self.threshold, self.bonus, codes)
        by_code = {}
        for zone in base:
            by_code.setdefault(zone.code, []).append(zone)

        # Different layouts of each code seed the beam. Evenly spaced indices
        # retain positional diversity after sorting the deterministic catalogue.
        starter_zones = [
            zones[i * (len(zones) - 1) // max(1, min(3, len(zones)) - 1)]
            for zones in by_code.values()
            for i in range(min(3, len(zones)))
        ]
        seeds = [_Choice(z, 0) for z in starter_zones]
        all_choices = tuple(dict.fromkeys((
            *incumbent.choices,
            *(_Choice(z, s) for z in base for s in range(max(1, self.joker_slots))),
        )))
        compact = tuple(dict.fromkeys((
            *incumbent.choices,
            *(_Choice(z, s) for z in starter_zones for s in range(max(1, self.joker_slots))),
            *all_choices[::max(1, len(all_choices) // 72)],
        )))
        self.stop()
        if not compact:
            return

        start = monotonic()
        remaining = self.deadline - start
        # A wider beam is useful when substantial headroom remains. Keep a
        # larger time slice for two-zone repair on portfolios near their bound.
        gap = self.upper - self.best.score if self.upper is not None else self.best.score
        beam_width = 5 if gap * 10 > self.best.score else 3
        beam_end = start + remaining * 0.30
        seed_plans = [self.add(self.blank(), c) for c in seeds]
        for starter in seed_plans:
            self.consider(starter)
        self.grow([self.blank(), *seed_plans], compact, beam_width, beam_end)
        self.stop()
        if self.best.score == self.upper:
            return

        # Remove at least two complete zones together; all their resources are
        # available at once when rebuilding, possibly with different codes.
        repair_end = start + remaining * 0.63
        for initial in dict.fromkeys((self.best.choices, incumbent.choices)):
            if len(initial) < 2:
                continue
            pairs = list(combinations(range(len(initial)), 2))
            for first, second in pairs[:min(len(pairs), 5)]:
                if monotonic() >= repair_end:
                    break
                seed = self.portfolio(tuple(c for i, c in enumerate(initial) if i not in (first, second)))
                self.grow((seed,), compact, 3, repair_end)
                self.stop()

        if self.best.score == self.upper:
            return
        # Limit the number of candidate/outcome cells before constructing the
        # solver matrix. Large geometries spend the reserve on cheaper repair.
        outcomes = max(mask.bit_length() for mask, _ in self.layers)
        groups = 1 + self.joker_slots + len(self.protected)
        limit = min(96, 150_000 // (outcomes * groups))
        mandatory = tuple(dict.fromkeys((*self.best.choices, *incumbent.choices)))
        if limit >= max(12, len(mandatory)) and self.deadline - monotonic() > 0.10:
            source = all_choices if len(all_choices) <= limit else (*mandatory, *compact)
            milp_options = tuple(dict.fromkeys(source))[:limit]
            try:
                self.solve(milp_options)
            except ImportError:
                pass
        elif self.deadline - monotonic() > 0.06:
            self.grow((self.best,), all_choices, beam_width, self.deadline)


def _refine_layout(game, width, objective, price, joker_count, threshold, upper, zones, attained,
                   time_limit, cancelled):
    lot = require_lottery(game, "RO")
    search = _PortfolioSearch(lot, width, objective, price, joker_count, threshold, upper, time_limit, cancelled)
    return search.optimize(zones, attained)


def _default_search_seconds(width):
    # Keep the larger geometries responsive with or without a cancellation hook.
    return 1.05 if width < 15 else 0.65


def _layout_rules_signature():
    from loto_enterprise.benchmark.lr_provenance import layout_formulas

    return layout_formulas()


@catalog_cache(maxsize=128, semantic_key=_layout_rules_signature)
def _refine_cached(game, width, objective, price, joker_count, threshold, upper, zones, attained):
    return _refine_layout(
        game, width, objective, price, joker_count, threshold, upper, zones, attained,
        _default_search_seconds(width), None,
    )


def _attach(plan, lot, zones, pool, jokers, price, attained, upper):
    rows = [
        {
            "code": code,
            "pool_size": len(positions),
            "numbers": [pool[p] for p in positions],
            "joker": jokers[slot] if slot is not None else None,
        }
        for code, positions, _, slot in zones
    ]
    plan.pop("anchor_size", None)
    plan.update(
        tickets=[rows[i:i + lot.per_ticket] for i in range(0, len(rows), lot.per_ticket)],
        price_bani=price,
        variants=sum(count for _, _, count, _ in zones),
        attained=attained,
        upper=upper,
        optimal=attained == upper,
    )
    return plan


def required_pool_size(plan):
    """Width of the budget template, whose positions use contiguous labels."""
    return max((n for ticket in plan["tickets"] for zone in ticket for n in zone["numbers"]), default=0)


def restrict_to_pool(plan, objective, pool, jokers=(), *, time_limit_s=None, cancelled=None):
    """Choose whole official LR zones inside the pool within the incumbent cost.

    The original layout remains eligible throughout. The optional time limit
    bounds improvement work after the incumbent exists. A callable can stop
    subsequent improvement work without discarding a feasible result.
    """
    lot = require_lottery(plan["game"], "RO")
    if lot.geo.second is not None:
        try:
            jokers = tuple(jokers)
            if any(isinstance(number, (bool, np.bool_)) for number in jokers):
                raise TypeError("Indicator boolean în loc de bilă Joker.")
            jokers = tuple(map(index, jokers))
        except TypeError as exc:
            raise ValueError("Urna 2 Joker cere bile eligibile din intervalul 1–20.") from exc
        if not jokers or len(jokers) != len(set(jokers)) or any(
            not 1 <= number <= lot.geo.second.max_n for number in jokers
        ):
            raise ValueError("Urna 2 Joker cere bile distincte din intervalul 1–20.")
    values = tuple(pool)
    if any(isinstance(number, (bool, np.bool_)) for number in values):
        raise ValueError("Poolul principal cere numere întregi distincte.")
    try:
        pool = tuple(sorted(map(index, values)))
    except TypeError as exc:
        raise ValueError("Poolul principal cere numere întregi distincte.") from exc
    if len(set(pool)) != len(pool) or any(not 1 <= number <= lot.max_n for number in pool):
        raise ValueError("Poolul principal cere numere întregi distincte din geometria jocului.")
    template = [zone for ticket in plan["tickets"] for zone in ticket]
    use_bonus = plan["game"] == "joker" and objective == ANY_PRIZE
    threshold = 3 if objective == ANY_PRIZE and plan["game"] != "5/40" else 4
    upper = _layout_bound(plan, lot, len(pool), threshold, len(jokers) if use_bonus else 0)
    template_fits = (
        template and required_pool_size(plan) <= len(pool)
        and (not use_bonus or len(template) <= len(jokers))
    )
    plan["template_adjusted"] = not template_fits

    if template_fits:
        sizes = {scheme.code: scheme.size for scheme in lr_schemes(plan["game"])}
        zones = tuple((z["code"], tuple(n - 1 for n in z["numbers"]), sizes[z["code"]]) for z in template)
        price, attained = plan["price_bani"], plan["attained"]
    else:
        options, attained, price = _layout(plan["game"], len(pool), plan["budget_bani"], objective, len(jokers))
        zones = tuple((z.code, z.positions, z.variants) for z in options)

    width = min(len(pool), MAX_EXACT_POOL)
    if not zones or max(max(positions) for _, positions, _ in zones) >= width:
        selected = tuple(
            (code, positions, count, i % len(jokers) if jokers else None)
            for i, (code, positions, count) in enumerate(zones)
        )
        report = {"certificate": "proven_bound" if attained == upper else "feasible"}
    else:
        args = (plan["game"], width, objective, price, len(jokers), threshold, upper, zones, attained)
        if time_limit_s is None and cancelled is None:
            selected, attained, price, report = _refine_cached(*args)
        else:
            seconds = _default_search_seconds(width) if time_limit_s is None else float(time_limit_s)
            if not isfinite(seconds) or seconds < 0:
                raise ValueError("Limita de timp a optimizării LR trebuie să fie finită și nenegativă.")
            selected, attained, price, report = _refine_layout(*args, seconds, cancelled)

    plan["optimization"] = deepcopy(report) | {
        "global_upper": upper,
        "global_gap": upper - attained,
    }
    return _attach(plan, lot, selected, pool, jokers, price, attained, upper)
