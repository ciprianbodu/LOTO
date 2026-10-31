"""Bounded-memory covering for pools beyond the local design catalogue.

No candidate table of C(v, pick) tickets or array of 2**v subsets is built.
Every accepted ticket covers a previously uncovered target; an uncapped
construction therefore terminates with a verified classical cover. This is a
constructive cover, rather than a claim to find a minimum covering design.
"""

from __future__ import annotations

import itertools
import logging
import math
import random
from functools import lru_cache

from covering.common import _coverage_ratio_pct, _sorted_pool

logger = logging.getLogger(__name__)

# Existing designs, optimizers and their cache identities remain untouched.
LEGACY_MAX_POOL = 19
MAX_UNCAPPED_FULL_TICKETS = 100_000
MAX_UNCAPPED_TARGETS = 250_000
MAX_BUDGET_TICKETS = 100_000
MAX_CONDITIONAL_TARGETS = 20_000_000
_COVERAGE_BATCH = 32_768
_TARGET_CANDIDATES = 6
_GLOBAL_CANDIDATES = 6
# Rafinarea locala a coverului cu plafon (2026-10-31): schimbari de cate un
# bilet care cresc lexicografic (grupele de g acoperite, grupele de g+1
# acoperite), pastrand numarul de bilete, biletele distincte si pozitiile
# jucate. Determinista (RNG local, seed din geometrie), cu buget de evaluari.
_REFINE_MAX_CAP = 4096
_REFINE_MAX_PASSES = 24
_REFINE_CANDIDATES = 48
_REFINE_MAX_EVALS = 400_000


def validate_large_pool_request(v, pick, guarantee, max_variants=0, condition=None):
    """Reject a huge unlimited system before allocating its combinations.

    A finite budget bounds both the returned tickets and the construction's
    memory. The defaults (4-cover of 49/40/45 numbers) fit the unlimited target
    limit. A six-number guarantee on all 49 numbers does not.
    """
    v, pick, guarantee = int(v), int(pick), int(guarantee)
    cap = int(max_variants or 0)
    if pick < 1 or guarantee < 1 or guarantee > pick:
        raise ValueError(f"Invalid covering geometry: pick={pick}, guarantee={guarantee}")
    if cap < 0:
        raise ValueError("Plafonul de variante trebuie să fie zero sau pozitiv.")
    if cap > MAX_BUDGET_TICKETS:
        raise ValueError(
            f"Alege un plafon de cel mult {MAX_BUDGET_TICKETS:,} variante pentru pool-ul extins."
        )
    c = guarantee if condition is None else int(condition)
    if c < guarantee or c > v:
        raise ValueError(f"Invalid condition={c} for guarantee={guarantee}, pool={v}")
    if v < pick:
        return
    count = math.comb(v, guarantee)
    limit = MAX_UNCAPPED_FULL_TICKETS if guarantee == pick else MAX_UNCAPPED_TARGETS
    if cap == 0 and count > limit:
        label = "variante" if guarantee == pick else "grupe de acoperit"
        raise ValueError(
            f"Pool-ul de {v} numere cu garanția {guarantee} cere {count:,} {label}. "
            "Stabilește un plafon pozitiv de variante pentru acest sistem."
        )
    conditional = math.comb(v, c)
    if c != guarantee and conditional > MAX_CONDITIONAL_TARGETS:
        raise ValueError(
            f"Acoperirea exactă «{guarantee} dacă {c}» are {conditional:,} grupe; "
            "alege o condiție mai mică."
        )


def _subset_masks(ticket_bits, guarantee):
    return tuple(sum(part) for part in itertools.combinations(ticket_bits, guarantee))


def _refine_capped_cover(v, pick, guarantee, cap, tickets):
    """Rafinare determinista a unui cover cu plafon (partial).

    Cautare locala peste schimbari de cate un bilet: se accepta doar mutari cu
    (grupe de ``guarantee`` noi acoperite, grupe de ``guarantee+1`` noi acoperite)
    lexicografic pozitive, deci acoperirea garantiei nu scade niciodata si creste
    strict impreuna cu pragul urmator. Numarul de bilete, distinctia biletelor si
    multimea pozitiilor jucate raman neschimbate. Fara scoruri, fara date: doar
    geometrie pozitionala, asa ca designul ramane o functie pura a geometriei.
    """
    g = int(guarantee)
    g1 = g + 1
    if g1 > pick or cap <= 1 or len(tickets) != cap:
        return tuple(tickets)
    rng = random.Random((v << 24) | (pick << 16) | (g << 8) | 0x5EED)
    sel = [tuple(t) for t in tickets]
    sel_set = set(sel)
    cnt_g: dict = {}
    cnt_g1: dict = {}
    pos: dict = {}
    uncovered_g: set = set(itertools.combinations(range(v), g))
    for t in sel:
        for c in itertools.combinations(t, g):
            cnt_g[c] = cnt_g.get(c, 0) + 1
            uncovered_g.discard(c)
        for c in itertools.combinations(t, g1):
            cnt_g1[c] = cnt_g1.get(c, 0) + 1
        for p in t:
            pos[p] = pos.get(p, 0) + 1
    universe = range(v)
    evals = 0

    def delta(i, j):
        ti = sel[i]
        tj = tuple(sorted(j))
        if tj in sel_set:
            return None
        for p in ti:
            if pos.get(p, 0) == 1 and p not in tj:
                return None
        dg = 0
        for c in itertools.combinations(tj, g):
            if c not in cnt_g:
                dg += 1
        for c in itertools.combinations(ti, g):
            if cnt_g.get(c, 0) == 1:
                dg -= 1
        dg1 = 0
        for c in itertools.combinations(tj, g1):
            if c not in cnt_g1:
                dg1 += 1
        for c in itertools.combinations(ti, g1):
            if cnt_g1.get(c, 0) == 1:
                dg1 -= 1
        return (dg, dg1)

    def apply(i, j):
        ti = sel[i]
        tj = tuple(sorted(j))
        sel_set.discard(ti)
        sel_set.add(tj)
        for c in itertools.combinations(ti, g):
            cnt_g[c] -= 1
            if cnt_g[c] == 0:
                del cnt_g[c]
                uncovered_g.add(c)
        for c in itertools.combinations(tj, g):
            if c not in cnt_g:
                uncovered_g.discard(c)
            cnt_g[c] = cnt_g.get(c, 0) + 1
        for c in itertools.combinations(ti, g1):
            cnt_g1[c] -= 1
            if cnt_g1[c] == 0:
                del cnt_g1[c]
        for c in itertools.combinations(tj, g1):
            cnt_g1[c] = cnt_g1.get(c, 0) + 1
        for p in ti:
            pos[p] -= 1
        for p in tj:
            pos[p] = pos.get(p, 0) + 1
        sel[i] = tj

    for _pas in range(_REFINE_MAX_PASSES):
        improved = False
        pool_cands = list(uncovered_g)
        rng.shuffle(pool_cands)
        cands = []
        for tgt in pool_cands[: _REFINE_CANDIDATES // 2]:
            rest = [x for x in universe if x not in tgt]
            cands.append(tuple(sorted(tgt + tuple(rng.sample(rest, pick - g)))))
        for _ in range(_REFINE_CANDIDATES - len(cands)):
            cands.append(tuple(sorted(rng.sample(universe, pick))))
        for i in range(len(sel)):
            best = None
            for j in cands:
                evals += 1
                if evals > _REFINE_MAX_EVALS:
                    return tuple(sel)
                d = delta(i, j)
                if d is not None and d > (0, 0) and (best is None or d > best[0]):
                    best = (d, j)
            if best is not None:
                apply(i, best[1])
                improved = True
        if not improved:
            break
    return tuple(sel)


@lru_cache(maxsize=8)
def _canonical_cover(v, pick, guarantee, cap, condition=0):
    """Immutable position design, reused for the same geometry in WF steps.

    `condition` > guarantee (design lotto „g dacă c”) nu se rafinează: căutarea
    locală optimizează acoperirea clasică g, iar `counts[c][g]` nu e monotonie
    în numărul de grupe g acoperite.
    """
    n_full = math.comb(v, pick)
    if guarantee == pick and (cap == 0 or cap >= n_full):
        return tuple(itertools.combinations(range(v), pick))

    bits = tuple(1 << i for i in range(v))
    universe = range(v)
    targets = itertools.combinations(universe, guarantee)
    covered = set()
    selected = set()
    tickets = []
    total = math.comb(v, guarantee)
    # A local RNG produces the same position design on every process. It does
    # not touch a scorer's random state, depend on labels, or learn from draws.
    rng = random.Random((v << 24) | (pick << 16) | (guarantee << 8) | 1)

    def add(ticket):
        ticket = tuple(sorted(ticket))
        if ticket in selected:
            return False
        selected.add(ticket)
        tickets.append(ticket)
        covered.update(_subset_masks(tuple(bits[i] for i in ticket), guarantee))
        return True

    # Include every pool position whenever the ticket capacity permits it.
    # In particular cap=1 keeps the best-ranked pick, and a tiny cap never
    # advertises that all v positions were played.
    for start in range(0, v, pick):
        if cap and len(tickets) >= cap:
            break
        add((start + j) % v for j in range(pick))

    target = None
    while len(covered) < total and (cap == 0 or len(tickets) < cap):
        while target is None or sum(bits[i] for i in target) in covered:
            target = next(targets, None)
            if target is None:
                return tuple(tickets)
        target_set = set(target)
        remaining = [i for i in universe if i not in target_set]
        candidates = [tuple((*target, *remaining[:pick - guarantee]))]
        for _ in range(_TARGET_CANDIDATES - 1):
            candidates.append(tuple((*target, *rng.sample(remaining, pick - guarantee))))
        for _ in range(_GLOBAL_CANDIDATES):
            candidates.append(tuple(rng.sample(universe, pick)))
        best, best_masks, best_gain = None, (), -1
        for candidate in candidates:
            candidate = tuple(sorted(candidate))
            if candidate in selected:
                continue
            masks = _subset_masks(tuple(bits[i] for i in candidate), guarantee)
            gain = sum(mask not in covered for mask in masks)
            if gain > best_gain:
                best, best_masks, best_gain = candidate, masks, gain
        # The first candidate contains the uncovered target, hence gain >= 1.
        if best is None or best_gain < 1:
            raise RuntimeError("Large-pool construction lost its uncovered target")
        selected.add(best)
        tickets.append(best)
        covered.update(best_masks)
    if (
        cap
        and len(tickets) == cap
        and guarantee < pick
        and cap <= _REFINE_MAX_CAP
        and not (condition and int(condition) > guarantee)
    ):
        # Cover partial (oprit de plafon): rafinare locala pentru mai multe
        # grupe de g si g+1 acoperite la acelasi numar de bilete.
        return _refine_capped_cover(v, pick, guarantee, cap, tuple(tickets))
    return tuple(tickets)


def _covered_masks(wheel, pool, guarantee):
    positions = {int(n): 1 << i for i, n in enumerate(pool)}
    covered = set()
    for ticket in wheel:
        bits = tuple(sorted({positions[int(n)] for n in ticket if int(n) in positions}))
        covered.update(_subset_masks(bits, guarantee))
    return covered


def large_pool_coverage_pct(wheel, pool, guarantee, condition=None):
    """Exact coverage without enumerating the classical target universe.

    For ``g if c``, a c-subset is covered iff one of its g-subsets occurs on a
    ticket. A combinadic lookup table and fixed-size NumPy batches count those
    targets exactly; memory is independent of C(v,c). No sampling is used.
    """
    pool = tuple(dict.fromkeys(int(n) for n in pool))
    v, g = len(pool), int(guarantee)
    c = g if condition is None else int(condition)
    if c < g:
        raise ValueError(f"condition={c} < guarantee={g}")
    if not wheel or g < 1 or c > v:
        return 0.0
    covered = _covered_masks(wheel, pool, g)
    n_classical = math.comb(v, g)
    if c == g:
        return _coverage_ratio_pct(len(covered), n_classical)
    if len(covered) == n_classical:
        return 100.0
    n_targets = math.comb(v, c)
    if n_targets > MAX_CONDITIONAL_TARGETS:
        raise ValueError(
            f"Acoperirea exactă «{g} dacă {c}» are {n_targets:,} grupe; "
            "alege o condiție mai mică."
        )
    import numpy as np

    choose = np.array(
        [[math.comb(n, k) if n >= k else 0 for n in range(v)] for k in range(g + 1)],
        dtype=np.int64,
    )
    lookup = np.zeros(n_classical, dtype=bool)
    for mask in covered:
        rank, ordinal = 0, 1
        while mask:
            bit = mask & -mask
            rank += int(choose[ordinal, bit.bit_length() - 1])
            ordinal += 1
            mask ^= bit
        lookup[rank] = True
    targets = itertools.combinations(range(v), c)
    columns = tuple(itertools.combinations(range(c), g))
    count = 0
    while rows := list(itertools.islice(targets, _COVERAGE_BATCH)):
        batch = np.asarray(rows, dtype=np.int16)
        hits = np.zeros(len(rows), dtype=bool)
        for subset in columns:
            ranks = choose[1, batch[:, subset[0]]].copy()
            for ordinal, column in enumerate(subset[1:], 2):
                ranks += choose[ordinal, batch[:, column]]
            hits |= lookup[ranks]
            if hits.all():
                break
        count += int(np.count_nonzero(hits))
    return _coverage_ratio_pct(count, n_targets)


def generate_large_pool_wheel(pool, pick=6, guarantee=4, max_variants=0, scores=None,
                              condition=None):
    """Construct a deterministic wheel for the complete requested pool."""
    pool = _sorted_pool(pool, scores)
    if len(set(pool)) != len(pool):
        raise ValueError("Pool-ul trebuie să conțină numere distincte.")
    pick, guarantee, cap = int(pick), int(guarantee), int(max_variants or 0)
    validate_large_pool_request(len(pool), pick, guarantee, cap, condition)
    if len(pool) < pick:
        return [], 0.0
    positions = _canonical_cover(len(pool), pick, guarantee, cap, int(condition or 0))
    wheel = [sorted(pool[i] for i in ticket) for ticket in positions]
    coverage = large_pool_coverage_pct(wheel, pool, guarantee, condition)
    logger.info(
        "[WHEEL-LARGE] pool=%d pick=%d guarantee=%d condition=%s cap=%d: %d tickets, %.2f%%",
        len(pool), pick, guarantee, condition, cap, len(wheel), coverage,
    )
    return wheel, coverage
