"""Variante dispersate: cea mai mare sansa ca CEL PUTIN O varianta sa castige.

La acelasi numar de variante distincte, numarul MEDIU de variante cu >= t
numere este acelasi pentru orice aranjare (liniaritatea mediei). Geometria
schimba numai cat de des cade cel putin un castig: variantele care se suprapun
castiga impreuna, cele disjuncte castiga pe rand. Pentru orice set de B
variante, P(cel putin una >= t) <= B * P(o varianta >= t); limita este atinsa
exact cand doua variante nu pot avea simultan t numere (suprapunere
<= 2t - draw_n - 1), de exemplu la 6/49 pentru 4+ cu suprapuneri de cel mult 1.

`spread_variants` construieste variantele pe primele numere ale clasamentului
si minimizeaza suma probabilitatilor exacte ca doua variante sa castige
simultan (cautare locala determinista). `ticket_hit_probabilities` calculeaza
exact, prin enumerarea tuturor extragerilor, P(cel putin o varianta >= t).
Fara afirmatie predictiva: extragerea este presupusa uniforma.
"""

from __future__ import annotations

from functools import lru_cache
from math import comb

import numpy as np

_SAME = 1e6  # doua variante identice
_RUN = 1e3  # o varianta peste limita de consecutive
_RESTARTS = 4
_PASSES = 40


def pair_joint_probability(max_num: int, draw_n: int, pick: int, overlap: int, threshold: int) -> float:
    """P(doua variante cu `overlap` numere comune au ambele >= threshold)."""
    outside = max_num - 2 * pick + overlap
    if outside < 0:
        raise ValueError("Doua variante nu incap in univers cu aceasta suprapunere")
    acc = 0
    for x in range(min(overlap, draw_n) + 1):
        for y in range(min(pick - overlap, draw_n - x) + 1):
            for z in range(min(pick - overlap, draw_n - x - y) + 1):
                rest = draw_n - x - y - z
                if rest > outside or x + y < threshold or x + z < threshold:
                    continue
                acc += (
                    comb(overlap, x)
                    * comb(pick - overlap, y)
                    * comb(pick - overlap, z)
                    * comb(outside, rest)
                )
    return acc / comb(max_num, draw_n)


@lru_cache(maxsize=32)
def _pair_costs(max_num: int, draw_n: int, pick: int, target: int) -> tuple[float, ...]:
    costs = []
    for s in range(pick + 1):
        if max_num - 2 * pick + s < 0:
            costs.append(_SAME)
            continue
        higher = sum(
            pair_joint_probability(max_num, draw_n, pick, s, t)
            for t in range(target + 1, min(pick, draw_n) + 1)
        )
        # Pragurile inferioare doar departajeaza: cea mai mica diferenta la tinta
        # (o pereche, ~1e-6) ramane cu ordine de marime peste suma lor ponderata.
        lower = sum(
            pair_joint_probability(max_num, draw_n, pick, s, t) for t in range(2, target)
        )
        costs.append(
            pair_joint_probability(max_num, draw_n, pick, s, target)
            + 1e-3 * higher
            + 1e-9 * lower
        )
    costs[pick] = _SAME
    return tuple(costs)


def _run_excess(row: np.ndarray, numbers: np.ndarray, max_run: int) -> int:
    if max_run <= 0:
        return 0
    chosen = np.sort(numbers[row.astype(bool)])
    excess = run = 0
    for i in range(1, len(chosen) + 1):
        if i < len(chosen) and chosen[i] == chosen[i - 1] + 1:
            run += 1
            continue
        excess += max(0, run + 1 - max_run)
        run = 0
    return excess


def spread_variants(
    universe,
    n_var: int,
    pick: int,
    draw_n: int,
    max_num: int,
    target: int,
    max_run: int = 0,
) -> list[list[int]]:
    """`n_var` variante distincte de `pick` numere, cu suprapuneri minime.

    `universe` este clasamentul (cel mai bun primul). Se folosesc primele
    min(len, n_var * pick) numere: cand incap, variantele sunt disjuncte; altfel
    fiecare numar apare de cel mult doua ori mai des decat altul, iar aparitiile
    suplimentare merg la numerele mai bine clasate. `max_run` > 0: nicio
    varianta nu contine mai mult de `max_run` numere consecutive, daca se poate.
    Determinist pentru aceleasi intrari.
    """
    ranked = []
    for n in universe:
        n = int(n)
        if 1 <= n <= max_num and n not in ranked:
            ranked.append(n)
    if n_var <= 0 or len(ranked) < pick:
        return []
    n_var = min(int(n_var), comb(len(ranked), pick))
    numbers = np.array(ranked[: min(len(ranked), n_var * pick)])
    u = len(numbers)
    table = np.array(_pair_costs(max_num, draw_n, pick, target))
    rank_w = 1e-9 * np.arange(u) / max(1, u)  # departajare: numarul mai bine clasat
    order = np.argsort(numbers)
    adjacent = np.zeros((u, u), dtype=bool)
    for a, b in zip(order[:-1], order[1:]):
        if numbers[b] == numbers[a] + 1:
            adjacent[a, b] = adjacent[b, a] = True

    def cost_of(member: np.ndarray) -> tuple[float, float]:
        overlap = member @ member.T
        iu = np.triu_indices(len(member), 1)
        pair = float(table[np.minimum(overlap[iu], pick)].sum())
        runs = sum(_run_excess(row, numbers, max_run) for row in member)
        return pair + _RUN * runs, float((member * rank_w).sum())

    def run_after_adding(row: np.ndarray) -> np.ndarray:
        """Excesul de consecutive al randului dupa adaugarea fiecarui numar."""
        if max_run <= 0:
            return np.zeros(u)
        out = np.full(u, float(_run_excess(row, numbers, max_run)))
        inside = np.flatnonzero(row)
        if not len(inside):
            return out
        for j in np.flatnonzero(adjacent[:, inside].any(axis=1) & (row == 0)):
            trial = row.copy()
            trial[j] = 1
            out[j] = _run_excess(trial, numbers, max_run)
        return out

    best = None
    best_cost = (float("inf"), float("inf"))
    for restart in range(_RESTARTS):
        rng = np.random.default_rng(restart)
        member = np.zeros((n_var, u), dtype=np.int64)
        usage = np.zeros(u)
        for i in range(n_var):
            row = np.zeros(u, dtype=np.int64)
            for _ in range(pick):
                if i:
                    cur = member[:i] @ row
                    cost = table[np.minimum(cur[:, None] + member[:i], pick)].sum(axis=0)
                else:
                    cost = np.zeros(u)
                key = cost + _RUN * run_after_adding(row) + 1e-6 * usage + rank_w
                if restart:
                    key = key + 1e-12 * rng.random(u)
                key[row == 1] = np.inf
                row[int(np.argmin(key))] = 1
            member[i] = row
            usage += row
        overlap = member @ member.T
        for _ in range(_PASSES):
            improved = False
            for i in range(n_var):
                others = np.arange(n_var) != i
                for a in np.flatnonzero(member[i]):
                    row = member[i].copy()
                    row[a] = 0
                    base = overlap[i] - member[:, a]
                    cur = table[np.minimum(overlap[i][others], pick)].sum()
                    new = table[np.minimum(base[others][:, None] + member[others], pick)].sum(axis=0)
                    run_now = _run_excess(member[i], numbers, max_run)
                    gain = (cur + _RUN * run_now) - (new + _RUN * run_after_adding(row))
                    gain[member[i] == 1] = -np.inf
                    better_rank = rank_w[a] - rank_w
                    # Castig strict; altfel, la cost egal, un numar mai bine clasat.
                    ok = (gain > 1e-15) | ((np.abs(gain) <= 1e-15) & (better_rank > 0))
                    cand = np.flatnonzero(ok)
                    if not len(cand):
                        continue
                    b = int(cand[np.lexsort((-better_rank[cand], -gain[cand]))[0]])
                    member[i, a] = 0
                    member[i, b] = 1
                    overlap = member @ member.T
                    improved = True
            if not improved:
                break
        cost = cost_of(member)
        if cost < best_cost:
            best_cost = cost
            best = member.copy()
    assert best is not None
    return [sorted(int(numbers[j]) for j in np.flatnonzero(row)) for row in best]


def max_consecutive_on_variant(variant) -> int:
    nums = sorted({int(n) for n in variant})
    best = run = 1 if nums else 0
    for a, b in zip(nums, nums[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


def _masks(max_num: int, size: int) -> np.ndarray:
    level = [np.array([np.uint64(1) << np.uint64(e)], dtype=np.uint64) for e in range(max_num)]
    for _ in range(1, size):
        acc: list[np.ndarray] = []
        nxt = []
        for e in range(max_num):
            bit = np.uint64(1) << np.uint64(e)
            nxt.append(np.concatenate(acc) | bit if acc else np.zeros(0, dtype=np.uint64))
            acc.append(level[e])
        level = nxt
    return np.concatenate(level)


@lru_cache(maxsize=3)
def _draw_chunks(max_num: int, draw_n: int):
    """Submultimile de draw_n-1 numere, ordonate dupa cel mai mic element."""
    subs = _masks(max_num, draw_n - 1)
    low = np.log2((subs & (~subs + np.uint64(1))).astype(np.float64)).astype(np.int64)
    order = np.argsort(low, kind="stable")
    subs, low = subs[order], low[order]
    starts = np.searchsorted(low, np.arange(max_num + 1), side="left")
    return subs, starts


def ticket_hit_probabilities(tickets, draw_n: int, max_num: int) -> dict[int, float]:
    """P(cel putin o varianta >= t), t = 1..draw_n, exact, extragere uniforma.

    Enumera toate cele C(max_num, draw_n) extrageri, pe bucati dupa cel mai mic
    numar extras (memorie limitata). Variantele repetate se numara o data.
    """
    draw_n, max_num = int(draw_n), int(max_num)
    if not 1 <= draw_n <= max_num <= 64:
        raise ValueError("Geometrie invalida")
    masks = []
    for ticket in {tuple(sorted({int(n) for n in v})) for v in tickets}:
        if not ticket or any(not 1 <= n <= max_num for n in ticket):
            raise ValueError("Varianta in afara universului")
        masks.append(np.uint64(sum(1 << (n - 1) for n in ticket)))
    hist = np.zeros(draw_n + 1, dtype=np.int64)
    if draw_n == 1:
        draws = (np.uint64(1) << np.arange(max_num, dtype=np.uint64))
        chunks = [draws]
    else:
        subs, starts = _draw_chunks(max_num, draw_n)
        chunks = (
            subs[starts[e + 1] :] | (np.uint64(1) << np.uint64(e))
            for e in range(max_num - draw_n + 1)
        )
    total = 0
    block = 1 << 17  # ~1 MB de extrageri: ramane in cache pentru toate variantele
    tmp = np.empty(block, dtype=np.uint64)
    cnt = np.empty(block, dtype=np.uint8)
    best = np.empty(block, dtype=np.uint8)
    for chunk in chunks:
        for lo in range(0, len(chunk), block):
            draws = chunk[lo : lo + block]
            n = len(draws)
            b, t_, c = best[:n], tmp[:n], cnt[:n]
            b.fill(0)
            for m in masks:
                np.bitwise_and(draws, m, out=t_)
                np.bitwise_count(t_, out=c)
                np.maximum(b, c, out=b)
            hist += np.bincount(b, minlength=draw_n + 1)[: draw_n + 1]
            total += n
    assert total == comb(max_num, draw_n)
    tail = np.cumsum(hist[::-1])[::-1]
    return {t: float(tail[t] / total) for t in range(1, draw_n + 1)}
