"""Recoacere simulată pe șansa exactă de hituri pe bilete, la buget fix.

Etapa de după hitcover și schimburile de profil, pentru wheel-urile cu plafon
de 1..64 bilete. Pornește de la biletele deja construite și mută câte un
număr al unui bilet pe alt număr din pool, acceptând și mutări care pierd
puțin, ca să iasă din optimul local în care rămân schimburile una câte una.
Obiectivul e probabilitatea EXACTĂ, pe o extragere uniformă de ``draw_n``
numere din ``max_num``, de cel puțin ``t`` hituri pe un bilet, cu ținta
``guarantee`` întâi și celelalte praguri după ea; o scădere sub nivelul
biletelor de plecare la orice prag afișat (3+ și mai sus) sau la acoperirea
garanției e penalizată tare în căutare.

Rezultatul intră în producție numai certificat din nou, exact, cu
``covering.probability``: același număr de bilete distincte, numerele jucate
din pool rămân jucate, P(cel puțin t pe un bilet) nu scade la NICIUN prag de
la 3 la ``draw_n`` și crește strict la cel puțin unul, iar acoperirea clasică
a garanției nu scade. Altfel biletele de plecare rămân neschimbate.

Numai geometrie: pozițiile din pool urmează ordinea canonică a scorurilor;
niciun istoric, nicio valoare de scor și nicio afirmație predictivă nu intră
în căutare. Determinist: buget fix de mutări, generator seedat din geometrie,
rezultat memorat pe geometrie și pe biletele de plecare (în poziții), deci
același pe toți pașii walk-forward cu aceeași construcție.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from math import comb

import numpy as np

from covering.budget_climb import _subsets
from covering.probability import wheel_hit_probabilities, wheel_hit_profile

MAX_POOL = 16
MAX_TICKETS = 64
MIN_THRESHOLD = 3
MOVES = 20000
MIN_MOVES = 4000
WORK_BUDGET = 6_000_000_000  # subseturi × bilete evaluate pe o căutare (~2 s)
PENALTY = 100.0


def _move_budget(n_subsets: int, n_tickets: int) -> int:
    return max(MIN_MOVES, min(MOVES, WORK_BUDGET // max(1, n_subsets * n_tickets)))


def _weights(thresholds: list[int], primary: int) -> dict[int, float]:
    """Ținta întâi, apoi pragurile mai mari, apoi cele mai mici (lexicografic)."""
    out = {}
    for t in thresholds:
        if t == primary:
            out[t] = 1.0
        elif t > primary:
            out[t] = 1e-3 ** (t - primary)
        else:
            out[t] = 1e-4 ** (primary - t)
    return out


@lru_cache(maxsize=64)
def _anneal(
    v: int,
    pick: int,
    draw_n: int,
    max_num: int,
    guarantee: int,
    start: tuple[int, ...],
) -> tuple[tuple[int, ...], int, int]:
    """(bilete găsite ca măști pe poziții, mutări acceptate, mutări încercate)."""
    masks, sizes, weights, _member = _subsets(v, draw_n, max_num)
    masks = masks.astype(np.int64)
    prob = weights.astype(np.float64) / comb(max_num, draw_n)
    top = min(pick, draw_n)
    thresholds = list(range(MIN_THRESHOLD, top + 1))
    primary = min(max(int(guarantee), MIN_THRESHOLD), top)
    w = _weights(thresholds, primary)
    guarantee_rows = np.flatnonzero(sizes == guarantee)

    def column(ticket: int) -> np.ndarray:
        return np.bitwise_count(masks & np.int64(ticket)).astype(np.int8)

    def measure(best_row: np.ndarray):
        # P(cel mai bun bilet >= t) = suma cozii distribuției celui mai bun bilet.
        tail = np.bincount(best_row.astype(np.intp), weights=prob, minlength=top + 1)
        cum = np.cumsum(tail[::-1])[::-1]
        p = {t: float(cum[t]) for t in thresholds}
        cov = int(np.count_nonzero(best_row[guarantee_rows] >= guarantee))
        return p, cov

    def objective(p, cov, base_p, base_cov) -> float:
        value = sum(w[t] * p[t] for t in thresholds)
        loss = sum(max(0.0, base_p[t] - p[t]) for t in thresholds)
        loss += max(0, base_cov - cov) / max(1, comb(v, guarantee))
        return value - PENALTY * loss

    current = list(start)
    # Un rând pe bilet: maximul pe bilete e o reducere pe axa 0, contiguă pe
    # subseturi (pe cealaltă așezare costa de ~40 de ori mai mult).
    H = np.stack([column(m) for m in current], axis=0)
    base_p, base_cov = measure(H.max(axis=0))
    cur_f = objective(base_p, base_cov, base_p, base_cov)
    best_f, best_masks = cur_f, tuple(current)
    played = [sum((m >> p) & 1 for m in current) for p in range(v)]
    present = set(current)
    moves = _move_budget(len(masks), len(current))
    t0 = max(0.05 * base_p[primary], 1e-7)
    t1 = t0 / 1000.0
    seed = hashlib.sha256(
        f"anneal-hits|{v}|{pick}|{draw_n}|{max_num}|{guarantee}|{start}".encode()
    ).digest()
    rng = np.random.default_rng(int.from_bytes(seed[:8], "little"))
    accepted = 0
    n_tickets = len(current)
    for k in range(moves):
        temp = t0 * (t1 / t0) ** (k / max(1, moves - 1))
        i = int(rng.integers(n_tickets))
        mask = current[i]
        inside = [p for p in range(v) if (mask >> p) & 1]
        outside = [p for p in range(v) if not (mask >> p) & 1]
        x = inside[int(rng.integers(len(inside)))]
        y = outside[int(rng.integers(len(outside)))]
        if played[x] == 1:
            continue  # numărul ar rămâne fără bilet
        moved = (mask ^ (1 << x)) | (1 << y)
        if moved in present:
            continue
        H[i] = column(moved)
        p, cov = measure(H.max(axis=0))
        f = objective(p, cov, base_p, base_cov)
        delta = f - cur_f
        if delta >= 0.0 or rng.random() < float(np.exp(delta / temp)):
            present.discard(mask)
            present.add(moved)
            current[i] = moved
            played[x] -= 1
            played[y] += 1
            cur_f = f
            accepted += 1
            if cur_f > best_f + 1e-15:
                best_f, best_masks = cur_f, tuple(current)
        else:
            H[i] = column(mask)
    return best_masks, accepted, moves


def improve_ticket_chances(ordered_pool, wheel, guarantee: int, draw_n: int, max_num: int):
    """Return ``(wheel, audit)``; biletele de plecare rămân dacă nu e certificat mai bine.

    ``ordered_pool`` e ordinea canonică a scorurilor (cel mai bun întâi).
    Auditul spune de ce s-a oprit, câte mutări a încercat și a acceptat și, la
    aplicare, câștigul exact pe fiecare prag.
    """
    audit = {"applied": False, "reason": "", "moves": 0, "accepted": 0, "gain": {}}
    pool = [int(n) for n in ordered_pool]
    v = len(pool)
    tickets = [sorted(int(n) for n in t) for t in wheel]
    if not tickets:
        audit["reason"] = "no tickets"
        return wheel, audit
    pick = len(tickets[0])
    distinct = {tuple(t) for t in tickets}
    guarantee, draw_n, max_num = int(guarantee), int(draw_n), int(max_num)
    if not (
        1 <= len(tickets) <= MAX_TICKETS
        and MIN_THRESHOLD <= pick <= v <= MAX_POOL
        and len(set(pool)) == v
        and len(distinct) == len(tickets)
        and all(len(set(t)) == len(t) == pick for t in tickets)
        and 1 <= guarantee <= pick
        and MIN_THRESHOLD <= draw_n
        and v < max_num
    ):
        audit["reason"] = "outside limits"
        return wheel, audit
    index = {n: i for i, n in enumerate(pool)}
    if any(n not in index for t in tickets for n in t):
        audit["reason"] = "ticket outside pool"
        return wheel, audit
    start = tuple(sum(1 << index[n] for n in t) for t in tickets)
    found, accepted, moves = _anneal(v, pick, draw_n, max_num, guarantee, start)
    audit.update(moves=moves, accepted=accepted)
    if found == start:
        audit["reason"] = "no better design"
        return wheel, audit
    candidate = [sorted(pool[p] for p in range(v) if (m >> p) & 1) for m in found]
    if len({tuple(t) for t in candidate}) != len(tickets):
        audit["reason"] = "duplicate tickets"
        return wheel, audit
    played_before = {n for t in tickets for n in t}
    played_after = {n for t in candidate for n in t}
    if not played_before <= played_after:
        audit["reason"] = "pool number dropped"
        return wheel, audit
    before = wheel_hit_probabilities(pool, tickets, draw_n, max_num)["ticket"]
    after = wheel_hit_probabilities(pool, candidate, draw_n, max_num)["ticket"]
    top = min(pick, draw_n)
    thresholds = range(MIN_THRESHOLD, top + 1)
    if any(after[t] < before[t] - 1e-15 for t in thresholds):
        audit["reason"] = "chance falls at a threshold"
        return wheel, audit
    if not any(after[t] > before[t] + 1e-15 for t in thresholds):
        audit["reason"] = "no strict gain"
        return wheel, audit
    cov_before = wheel_hit_profile(pool, tickets)[guarantee][guarantee]
    cov_after = wheel_hit_profile(pool, candidate)[guarantee][guarantee]
    if cov_after < cov_before:
        audit["reason"] = "guarantee coverage falls"
        return wheel, audit
    audit.update(
        applied=True,
        reason="",
        gain={t: after[t] - before[t] for t in thresholds},
    )
    return candidate, audit
