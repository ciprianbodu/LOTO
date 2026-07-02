"""Metode alternative de WHEELING (generatoare de bilete), pe lângă greedy-ul
existent din loto_engine.generate_combinatorial_wheel.

Toate au ACEEAȘI semnătură drop-in:
    fn(pool, pick, guarantee, max_variants, scores) -> (List[List[int]], coverage_pct)

și rezolvă același covering design: fiecare submulțime de `guarantee` numere din
pool trebuie să fie conținută în ≥1 bilet de `pick` numere.

  • wheel_ilp        — cover MINIM exact (scipy.optimize.milp). Optim, dar doar
                       pe dimensiuni moderate (guard pe nr. candidați/ținte).
  • wheel_annealing  — pornește din greedy și REDUCE biletele (remove redundante
                       + swap) prin simulated annealing, păstrând acoperirea.
  • wheel_genetic    — buget FIX de bilete; algoritm genetic care MAXIMIZEAZĂ
                       acoperirea (ponderată pe scoruri). Fitness pe GPU (torch).
  • wheel_lajolla    — designuri optime cunoscute (fișiere covering_designs/);
                       altfel cade pe ILP exact (mic) → greedy. „Best-known".

Selectabile prin env LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla.
Orice eșec/limită → fallback la greedy (sigur). Default = greedy (bit-identic).
"""
from __future__ import annotations

import itertools
import logging
import math
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilitare comune
# ---------------------------------------------------------------------------
def _comb(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _greedy_fallback(pool, pick, guarantee, max_variants, scores):
    """Apel lazy la greedy-ul canonic (evită import circular)."""
    from loto_engine import generate_combinatorial_wheel
    return generate_combinatorial_wheel(pool, pick, guarantee, max_variants, scores)


def _sorted_pool(pool, scores) -> List[int]:
    if scores:
        return sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    return sorted(list(pool))


def _coverage_pct(wheel: List[List[int]], pool: List[int], guarantee: int) -> float:
    targets = set(itertools.combinations(sorted(pool), guarantee))
    if not targets:
        return 100.0
    covered = set()
    for t in wheel:
        for sub in itertools.combinations(sorted(t), guarantee):
            covered.add(sub)
    return round(len(covered & targets) / len(targets) * 100.0, 2)


def compute_coverage_pct(wheel: List[List[int]], pool: List[int], guarantee: int) -> float:
    """API publică pt recalcularea acoperirii garanției pe un set de bilete DAT.

    Folosit din loto_engine.py pentru a revalida `coverage_pct` DUPĂ filtre
    post-wheeling (ex. anomaly filter) care pot elimina bilete fără să
    actualizeze procentul de acoperire raportat inițial de wheeling."""
    return _coverage_pct(wheel, pool, guarantee)


def filter_preserving_coverage(
    wheel: List[List[int]],
    pool: List[int],
    guarantee: int,
    removal_priority: List[int],
) -> Tuple[List[List[int]], int]:
    """Elimină bilete din `wheel`, în ordinea din `removal_priority` (indici în
    `wheel`, de la cel mai indezirabil la cel mai puțin dorit — ex. cele mai
    "anomale" statistic), PĂSTRÂND garanția combinatorică — un bilet e eliminat
    DOAR dacă toate țintele lui (subseturi de `guarantee` numere din pool) mai
    sunt acoperite de cel puțin un alt bilet rămas.

    Folosit ca să reconciliem filtrul anti-anomalie (bazat pe scoruri) cu
    garanția de wheeling (bazată pe covering design) — anterior, filtrul putea
    elimina bilete care erau UNICUL acoperitor al unei ținte, spărgând garanția
    promisă utilizatorului chiar și în modul "nelimitat" (max_variants=0).

    Returnează (wheel_filtrat, n_bilete_eliminate).
    """
    wheel = [list(t) for t in wheel]
    targets_per_ticket = [
        set(itertools.combinations(sorted(t), guarantee)) for t in wheel
    ]
    coverage_count: Dict[tuple, int] = {}
    for targets in targets_per_ticket:
        for t in targets:
            coverage_count[t] = coverage_count.get(t, 0) + 1

    keep = [True] * len(wheel)
    removed = 0
    for idx in removal_priority:
        if idx < 0 or idx >= len(wheel) or not keep[idx]:
            continue
        targets = targets_per_ticket[idx]
        # Sigur de eliminat DOAR dacă fiecare țintă a lui mai are ≥1 acoperitor
        # rămas (coverage_count>1 acum, înainte de a-l scădea pe al lui). Ținte
        # deja neacoperite (count=0, ex. dintr-un max_variants anterior) NU
        # forțează eliminarea — sunt tratate conservator, biletul e păstrat.
        if all(coverage_count.get(t, 0) > 1 for t in targets):
            keep[idx] = False
            for t in targets:
                coverage_count[t] -= 1
            removed += 1

    result = [w for w, k in zip(wheel, keep) if k]
    return result, removed


def _order_by_scores(wheel: List[List[int]], scores) -> List[List[int]]:
    if not scores:
        return [sorted(t) for t in wheel]
    return sorted([sorted(t) for t in wheel],
                  key=lambda t: sum(scores.get(n, 0) for n in t), reverse=True)


# ===========================================================================
# 1) ILP — cover minim EXACT (scipy.optimize.milp)
# ===========================================================================
_ILP_MAX_BLOCKS = 12000   # guard: peste asta, ILP devine prea greu → fallback
_ILP_MAX_TARGETS = 6000


def wheel_ilp(pool, pick, guarantee, max_variants=0, scores=None,
              time_limit: float = 15.0):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _ILP_MAX_BLOCKS or nt > _ILP_MAX_TARGETS:
        logger.info("[WHEEL-ILP] prea mare (blocuri=%d ținte=%d) → greedy", nb, nt)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
        blocks = list(itertools.combinations(pool, pick))
        targets = list(itertools.combinations(pool, guarantee))
        tidx = {t: i for i, t in enumerate(targets)}
        A = lil_matrix((nt, nb), dtype=np.float64)
        for j, b in enumerate(blocks):
            for sub in itertools.combinations(b, guarantee):
                A[tidx[sub], j] = 1.0
        res = milp(
            c=np.ones(nb),
            constraints=LinearConstraint(A.tocsr(), lb=1, ub=np.inf),
            integrality=np.ones(nb),
            bounds=Bounds(0, 1),
            options={"time_limit": time_limit},
        )
        if res.x is None:
            logger.warning("[WHEEL-ILP] fără soluție → greedy")
            return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
        chosen = [list(blocks[j]) for j in range(nb) if res.x[j] > 0.5]
        # HiGHS poate returna o soluție feasibilă NEoptimă la limita de timp →
        # comparăm cu greedy și păstrăm wheel-ul cu mai PUȚINE bilete (ambele 100%).
        g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
        if len(g_wheel) <= len(chosen):
            logger.info("[WHEEL-ILP] greedy (%d) ≤ ILP (%d) → păstrez greedy", len(g_wheel), len(chosen))
            chosen = [list(t) for t in g_wheel]
        else:
            logger.info("[WHEEL-ILP] cover minim ILP = %d bilete (greedy era %d)", len(chosen), len(g_wheel))
        if max_variants > 0 and len(chosen) > max_variants:
            chosen = _order_by_scores(chosen, scores)[:max_variants]
        return _order_by_scores(chosen, scores), _coverage_pct(chosen, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-ILP] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 2) Simulated annealing — reduce wheel-ul greedy păstrând acoperirea
# ===========================================================================
def wheel_annealing(pool, pick, guarantee, max_variants=0, scores=None,
                    iters: int = 4000, seed: int = 42):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    base, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)  # plecăm din greedy complet
    targets = list(itertools.combinations(pool, guarantee))
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
                cur.pop(i); cur_tt.pop(i)
                changed = True
                break

    # 2) Annealing: încearcă să înlocuiască un bilet cu altul aleator dacă scade
    #    redundanța (păstrând acoperirea completă) — relaxează spre minim local mai bun.
    if _comb(v, pick) <= 200000:
        all_blocks = list(itertools.combinations(pool, pick))
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
                cur[i] = tuple(sorted(cand)); cur_tt[i] = cand_tt
                # re-eliminare redundante după swap
                for j in range(len(cur) - 1, -1, -1):
                    if all(cover_count[t] > 1 for t in cur_tt[j]):
                        for t in cur_tt[j]:
                            cover_count[t] -= 1
                        cur.pop(j); cur_tt.pop(j)

    wheel = [list(t) for t in cur]
    if max_variants > 0 and len(wheel) > max_variants:
        wheel = _order_by_scores(wheel, scores)[:max_variants]
    logger.info("[WHEEL-SA] %d bilete (din %d greedy)", len(wheel), len(base))
    return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)


# ===========================================================================
# 3) Algoritm genetic — buget FIX, MAXIMIZEAZĂ acoperirea (fitness pe GPU)
# ===========================================================================
_GA_MAX_BLOCKS = 60000


def wheel_genetic(pool, pick, guarantee, max_variants=0, scores=None,
                  pop: int = 200, gens: int = 80, seed: int = 42):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _GA_MAX_BLOCKS:
        logger.info("[WHEEL-GA] univers prea mare (blocuri=%d) → greedy", nb)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    # Greedy-ul ne dă atât bugetul implicit, cât și o sămânță bună (elitism).
    g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
    budget = min(max_variants, nb) if max_variants > 0 else min(len(g_wheel), nb)
    budget = max(1, budget)
    try:
        import torch
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        blocks = list(itertools.combinations(pool, pick))
        bidx = {b: i for i, b in enumerate(blocks)}
        targets = list(itertools.combinations(pool, guarantee))
        tidx = {t: i for i, t in enumerate(targets)}
        # M[b, t] = 1 dacă blocul b acoperă ținta t (t ⊆ b)
        M = torch.zeros((nb, nt), dtype=torch.float32, device=dev)
        for j, b in enumerate(blocks):
            for sub in itertools.combinations(b, guarantee):
                M[j, tidx[sub]] = 1.0
        # ponderi ținte după scoruri (ținte din numere bune cântăresc mai mult)
        if scores:
            tw = torch.tensor([sum(scores.get(n, 0.0) for n in t) for t in targets],
                              dtype=torch.float32, device=dev)
            tw = tw / (tw.mean() + 1e-9)
        else:
            tw = torch.ones(nt, device=dev)

        gen = torch.Generator(device=dev); gen.manual_seed(seed)

        def fitness(P_idx):  # P_idx: (P, budget) long
            masks = M[P_idx]                # (P, budget, nt)
            cov = masks.amax(dim=1)         # (P, nt) acoperit?
            return (cov * tw).sum(dim=1)    # (P,)

        # populație inițială: indici aleatori de blocuri
        P = torch.randint(0, nb, (pop, budget), generator=gen, device=dev)
        # ELITISM: sămânță = soluția greedy (mapată pe indici), trunchiată/umplută la buget.
        g_idx = [bidx[tuple(sorted(t))] for t in g_wheel if tuple(sorted(t)) in bidx]
        if g_idx:
            if len(g_idx) >= budget:
                seed = torch.tensor(g_idx[:budget], device=dev, dtype=torch.long)
            else:
                pad = torch.randint(0, nb, (budget - len(g_idx),), generator=gen, device=dev)
                seed = torch.cat([torch.tensor(g_idx, device=dev, dtype=torch.long), pad])
            P[0] = seed  # GA pornește de la ≥ acoperirea greedy → poate doar îmbunătăți
        best_idx, best_fit = None, -1.0
        for _g in range(gens):
            fit = fitness(P)
            mx = int(torch.argmax(fit))
            if float(fit[mx]) > best_fit:
                best_fit = float(fit[mx]); best_idx = P[mx].clone()
            # selecție prin turnir
            a = torch.randint(0, pop, (pop,), generator=gen, device=dev)
            b = torch.randint(0, pop, (pop,), generator=gen, device=dev)
            winners = torch.where(fit[a] >= fit[b], a, b)
            parents = P[winners]
            # crossover uniform între perechi consecutive
            perm = torch.randperm(pop, generator=gen, device=dev)
            p2 = parents[perm]
            mask = torch.rand(parents.shape, generator=gen, device=dev) < 0.5
            child = torch.where(mask, parents, p2)
            # mutație: înlocuiește ~8% din gene cu blocuri aleatoare
            mut = torch.rand(child.shape, generator=gen, device=dev) < 0.08
            rnd = torch.randint(0, nb, child.shape, generator=gen, device=dev)
            child = torch.where(mut, rnd, child)
            child[0] = best_idx  # elitism
            P = child

        chosen = sorted({int(i) for i in best_idx.tolist()})
        wheel = [list(blocks[j]) for j in chosen]
        logger.info("[WHEEL-GA] buget=%d → %d bilete unice, fitness=%.1f", budget, len(wheel), best_fit)
        return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-GA] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 4) La Jolla — designuri optime cunoscute (fișiere) → ILP → greedy
# ===========================================================================
_LAJOLLA_DIRS = [Path("covering_designs"), Path("_ISTORIC/covering_designs")]


def _load_lajolla(v: int, pick: int, guarantee: int) -> Optional[List[List[int]]]:
    """Citește un design C(v, pick, guarantee) dintr-un fișier local dacă există.
    Format La Jolla: fiecare linie = un bloc de `pick` numere (1-based, 1..v)."""
    for d in _LAJOLLA_DIRS:
        f = d / f"C_{v}_{pick}_{guarantee}.txt"
        if f.exists():
            try:
                blocks = []
                for line in f.read_text().splitlines():
                    nums = [int(x) for x in line.replace(",", " ").split() if x.strip()]
                    if len(nums) == pick:
                        blocks.append(nums)
                if blocks:
                    logger.info("[WHEEL-LaJolla] folosesc design cunoscut %s (%d blocuri)", f, len(blocks))
                    return blocks
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WHEEL-LaJolla] citire %s eșuată: %s", f, exc)
    return None


def wheel_lajolla(pool, pick, guarantee, max_variants=0, scores=None):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    design = _load_lajolla(v, pick, guarantee)
    if design is not None:
        # mapăm indicii 1..v ai design-ului pe pool-ul sortat după scor (numerele bune
        # primesc pozițiile cu apariții multiple → mai des în bilete)
        wheel = []
        for blk in design:
            mapped = [pool[i - 1] for i in blk if 1 <= i <= v]
            if len(mapped) == pick:
                wheel.append(sorted(mapped))
        if wheel:
            if max_variants > 0 and len(wheel) > max_variants:
                wheel = _order_by_scores(wheel, scores)[:max_variants]
            return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)
    # fără fișier → încearcă ILP exact (mic), altfel greedy
    logger.info("[WHEEL-LaJolla] fără design local pt C(%d,%d,%d) → ILP/greedy", v, pick, guarantee)
    return wheel_ilp(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# Dispatcher
# ===========================================================================
WHEEL_METHODS = {
    "ilp": wheel_ilp,
    "annealing": wheel_annealing,
    "genetic": wheel_genetic,
    "lajolla": wheel_lajolla,
}


def generate_wheel(method: str, pool, pick, guarantee, max_variants=0, scores=None):
    """Selectează algoritmul de wheeling. 'greedy' (sau necunoscut) → canonic."""
    fn = WHEEL_METHODS.get((method or "greedy").strip().lower())
    if fn is None:
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    return fn(pool, pick, guarantee, max_variants, scores)
