"""Local-search wheels: annealing and genetic."""
from __future__ import annotations

import itertools
import logging
import math

import numpy as np

from covering.common import (
    _comb,
    _coverage_pct,
    _greedy_fallback,
    _order_by_scores,
    _sorted_pool,
    ensure_pool_numbers_on_tickets,
    filter_preserving_coverage,
)

logger = logging.getLogger(__name__)

def wheel_annealing(
    pool,
    pick,
    guarantee,
    max_variants=0,
    scores=None,
    iters: int = 4000,
    seed: int = 42,
):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [], 0.0
    base, _ = _greedy_fallback(
        pool, pick, guarantee, 0, scores
    )  # plecăm din greedy complet
    # ⚠️ Cheile ȚINTELOR se construiesc pe pool-ul sortat NUMERIC, fiindcă
    # `ticket_targets` caută cu `tuple(sorted(...))`. `_sorted_pool` reordonează
    # după SCOR, deci pe apelul de producție (care pasează mereu scoruri) cheile
    # ieșeau în ordinea scorurilor și fiecare căutare dădea KeyError — funcția era
    # moartă în producție, iar `test_wheeling.py` n-o prindea fiindcă testează
    # FĂRĂ scoruri, caz în care cele două ordini coincid.
    _pool_sorted = sorted(pool)
    targets = list(itertools.combinations(_pool_sorted, guarantee))
    if not targets:
        return base, 100.0
    tidx = {t: i for i, t in enumerate(targets)}
    nt = len(targets)

    def ticket_targets(tk) -> set:
        return {tidx[s] for s in itertools.combinations(sorted(tk), guarantee)}

    cur = [tuple(sorted(t)) for t in base]
    cur_tt = [ticket_targets(t) for t in cur]
    cover_count = np.zeros(nt, dtype=np.int32)
    for tt in cur_tt:
        for i in tt:
            cover_count[i] += 1

    def full() -> bool:
        return bool((cover_count > 0).all())

    rng = np.random.default_rng(seed)
    all_blocks = None  # lazy pt swap

    # 1) Eliminare bilete redundante (target-ele lor sunt acoperite de altele).
    changed = True
    while changed:
        changed = False
        order = sorted(range(len(cur)), key=lambda i: len(cur_tt[i]))  # întâi cele mici
        for i in order:
            if all(cover_count[t] > 1 for t in cur_tt[i]):
                for t in cur_tt[i]:
                    cover_count[t] -= 1
                cur.pop(i)
                cur_tt.pop(i)
                changed = True
                break

    # 2) Annealing: încearcă să înlocuiască un bilet cu altul aleator dacă scade
    #    redundanța (păstrând acoperirea completă) — relaxează spre minim local mai bun.
    if _comb(v, pick) <= 200000:
        all_blocks = list(itertools.combinations(_pool_sorted, pick))
    if all_blocks:
        T = 1.0
        for it in range(iters):
            T = max(0.01, 1.0 - it / iters)
            if len(cur) <= guarantee:
                break
            i = int(rng.integers(len(cur)))
            cand = all_blocks[int(rng.integers(len(all_blocks)))]
            cand_tt = ticket_targets(cand)
            old_tt = cur_tt[i]
            # simulează scoaterea lui i + adăugarea cand
            ok = True
            for t in old_tt:
                if cover_count[t] - 1 < 1 and t not in cand_tt:
                    ok = False
                    break
            if not ok:
                continue
            # acceptăm dacă cand acoperă ≥ target-e (redundanță mai utilă) sau prob. termică
            gain = len(cand_tt) - len(old_tt)
            if gain >= 0 or rng.random() < math.exp(gain / T):
                for t in old_tt:
                    cover_count[t] -= 1
                for t in cand_tt:
                    cover_count[t] += 1
                cur[i] = tuple(sorted(cand))
                cur_tt[i] = cand_tt
                # re-eliminare redundante după swap
                for j in range(len(cur) - 1, -1, -1):
                    if all(cover_count[t] > 1 for t in cur_tt[j]):
                        for t in cur_tt[j]:
                            cover_count[t] -= 1
                        cur.pop(j)
                        cur_tt.pop(j)

    wheel = [list(t) for t in cur]
    if max_variants > 0 and len(wheel) > max_variants:
        wheel = _order_by_scores(wheel, scores)[:max_variants]
    logger.info("[WHEEL-SA] %d bilete (din %d greedy)", len(wheel), len(base))
    ordered = _order_by_scores(wheel, scores)
    if max_variants > 0:
        ordered = ensure_pool_numbers_on_tickets(ordered, pool, pick)
    return ordered, _coverage_pct(ordered, pool, guarantee)


# ===========================================================================
# 3) Algoritm genetic — buget FIX, MAXIMIZEAZĂ acoperirea (fitness pe CPU/numpy)
# ===========================================================================
_GA_MAX_BLOCKS = 60000


def wheel_genetic(
    pool,
    pick,
    guarantee,
    max_variants=0,
    scores=None,
    pop: int = 200,
    gens: int = 80,
    seed: int = 42,
):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [], 0.0
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _GA_MAX_BLOCKS:
        logger.info("[WHEEL-GA] univers prea mare (blocuri=%d) → greedy", nb)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    # Greedy-ul ne dă atât bugetul implicit, cât și o sămânță bună (elitism).
    g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
    budget = min(max_variants, nb) if max_variants > 0 else min(len(g_wheel), nb)
    budget = max(1, budget)
    try:
        import numpy as np

        rng = np.random.default_rng(seed)
        blocks = list(itertools.combinations(pool, pick))
        # ⚠️ Cheile pe tuple SORTAT NUMERIC: elitismul de mai jos caută cu
        # `bidx[tuple(sorted(t))]`, iar `pool` e în ordinea SCORULUI. Cu scoruri
        # (cazul de producție) nicio cheie nu se potrivea → sămânța greedy se
        # pierdea tăcut, GA pornea aleator și întorcea sub 100% acoperire acolo
        # unde greedy dă 100% cu același număr de bilete. Valorile rămân indici
        # în `blocks`, deci M/`P` nu se schimbă.
        bidx = {tuple(sorted(b)): i for i, b in enumerate(blocks)}
        targets = list(itertools.combinations(pool, guarantee))
        tidx = {t: i for i, t in enumerate(targets)}
        # M[b, t] = 1 dacă blocul b acoperă ținta t (t ⊆ b)
        M = np.zeros((nb, nt), dtype=np.float32)
        for j, b in enumerate(blocks):
            for sub in itertools.combinations(b, guarantee):
                M[j, tidx[sub]] = 1.0
        # ponderi ținte după scoruri (ținte din numere bune cântăresc mai mult)
        if scores:
            tw = np.array(
                [sum(scores.get(n, 0.0) for n in t) for t in targets], dtype=np.float32
            )
            tw = tw / (tw.mean() + 1e-9)
        else:
            tw = np.ones(nt, dtype=np.float32)

        def fitness(P_idx):  # P_idx: (P, budget) int
            masks = M[P_idx]  # (P, budget, nt)
            cov = masks.max(axis=1)  # (P, nt) acoperit?
            return (cov * tw).sum(axis=1)  # (P,)

        # populație inițială: indici aleatori de blocuri
        P = rng.integers(0, nb, size=(pop, budget))
        # ELITISM: sămânță = soluția greedy (mapată pe indici), trunchiată/umplută la buget.
        g_idx = [bidx[tuple(sorted(t))] for t in g_wheel if tuple(sorted(t)) in bidx]
        if g_idx:
            if len(g_idx) >= budget:
                seed_row = np.array(g_idx[:budget], dtype=np.int64)
            else:
                pad = rng.integers(0, nb, size=(budget - len(g_idx),))
                seed_row = np.concatenate([np.array(g_idx, dtype=np.int64), pad])
            P[0] = (
                seed_row  # GA pornește de la ≥ acoperirea greedy → poate doar îmbunătăți
            )
        best_idx, best_fit = None, -1.0
        for _g in range(gens):
            fit = fitness(P)
            mx = int(np.argmax(fit))
            if float(fit[mx]) > best_fit:
                best_fit = float(fit[mx])
                best_idx = P[mx].copy()
            # selecție prin turnir
            a = rng.integers(0, pop, size=pop)
            b = rng.integers(0, pop, size=pop)
            winners = np.where(fit[a] >= fit[b], a, b)
            parents = P[winners]
            # crossover uniform între perechi consecutive
            perm = rng.permutation(pop)
            p2 = parents[perm]
            mask = rng.random(parents.shape) < 0.5
            child = np.where(mask, parents, p2)
            # mutație: înlocuiește ~8% din gene cu blocuri aleatoare
            mut = rng.random(child.shape) < 0.08
            rnd = rng.integers(0, nb, size=child.shape)
            child = np.where(mut, rnd, child)
            child[0] = best_idx  # elitism
            P = child

        chosen = sorted({int(i) for i in best_idx.tolist()})
        wheel = [list(blocks[j]) for j in chosen]
        logger.info(
            "[WHEEL-GA] buget=%d → %d bilete unice, fitness=%.1f",
            budget,
            len(wheel),
            best_fit,
        )
        ordered = _order_by_scores(wheel, scores)
        if max_variants > 0:
            ordered = ensure_pool_numbers_on_tickets(ordered, pool, pick)
        return ordered, _coverage_pct(ordered, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-GA] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)


