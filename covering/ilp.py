"""ILP exact cover."""
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
)

logger = logging.getLogger(__name__)

_ILP_MAX_BLOCKS = 12000  # guard: peste asta, ILP devine prea greu → fallback
_ILP_MAX_TARGETS = 6000

# Cache de PROCES pentru coverul ILP, keyed pe (v, pick, guarantee).
# De ce e legitim: obiectivul ILP-ului e `c=ones(nb)` — minimizează NUMĂRUL de
# bilete — deci NU depinde de scoruri, iar matricea de constrângeri se
# construiește pe POZIȚII (0..v-1), fiind identică pentru orice pool de aceeași
# dimensiune. Coverul e deci invariant la reetichetare, exact ca un design
# La Jolla (verificat: același cover pozițional pe `1..10`, pe `21..30`, pe un
# set împrăștiat și pe același set CU scoruri).
# Ce NU se cache-uiește: comparația cu greedy de mai jos — `generate_combinatorial_wheel`
# ordonează țintele după SUMA SCORURILOR, deci depinde de scoruri și se reface la
# fiecare apel (costă < 0.5 s măsurat, față de ~15 s cât ia solver-ul).
# Motivul optimizării: fără el, fiecare pas de walk-forward (~1940/rulare) plătea
# `time_limit` întreg (15 s) ca să re-deducă exact același cover.
_ILP_COVER_CACHE: dict[tuple[int, int, int], list[tuple[int, ...]] | None] = {}


def _ilp_cover_positions(
    v: int, pick: int, guarantee: int, time_limit: float
) -> list[tuple[int, ...]] | None:
    """Coverul ILP pentru C(v, pick, guarantee) ca POZIȚII 0..v-1 (memoizat).

    None = ILP indisponibil pentru configurația asta (prea mare / fără soluție /
    scipy lipsă) → apelantul cade pe greedy, ca înainte.
    """
    key = (int(v), int(pick), int(guarantee))
    if key in _ILP_COVER_CACHE:
        return _ILP_COVER_CACHE[key]
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _ILP_MAX_BLOCKS or nt > _ILP_MAX_TARGETS:
        logger.info("[WHEEL-ILP] prea mare (blocuri=%d ținte=%d) → greedy", nb, nt)
        _ILP_COVER_CACHE[key] = None
        return None
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix

        idxs = range(v)
        blocks = list(itertools.combinations(idxs, pick))
        targets = list(itertools.combinations(idxs, guarantee))
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
            # TIMEOUT / infeasible raportat de solver. NU memoiza: `time_limit` e o
            # limită de CEAS, nu o proprietate a geometriei — o mașină încărcată o
            # ratează o dată și o prinde data viitoare. Memoizat, un singur timeout
            # dezactiva ILP-ul pentru TOT restul procesului (adică tot walk-forward-ul,
            # ~1940 de pași) și trecea tăcut pe greedy, cu bilete mai multe.
            logger.warning(
                "[WHEEL-ILP] fără soluție în %.1fs → greedy "
                "(NU memoizez: e limită de timp, nu geometrie)",
                time_limit,
            )
            return None
    except Exception as exc:  # noqa: BLE001
        # Idem: scipy lipsă, MemoryError, orice excepție = eșec de MEDIU, nu de
        # geometrie. Singurul „nu se poate niciodată" memoizabil e pragul de
        # dimensiune de mai sus (`_ILP_MAX_BLOCKS` / `_ILP_MAX_TARGETS`).
        logger.warning("[WHEEL-ILP] eșec (%s) → greedy (NU memoizez)", exc)
        return None
    cover = [blocks[j] for j in range(nb) if res.x[j] > 0.5]
    covered = {
        sub for blk in cover for sub in itertools.combinations(blk, guarantee)
    }
    if len(covered) < nt:
        # HiGHS can return a feasible but incomplete vector at the time cap.
        # Memoizing it would poison every later call (including a longer budget)
        # for the rest of the process.
        logger.warning(
            "[WHEEL-ILP] soluție incompletă (%d/%d ținte, limită %.1fs) — nu memoizez",
            len(covered),
            nt,
            time_limit,
        )
        return cover
    _ILP_COVER_CACHE[key] = cover
    return cover


def wheel_ilp(
    pool, pick, guarantee, max_variants=0, scores=None, time_limit: float = 15.0
):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [], 0.0
    cover = _ilp_cover_positions(v, int(pick), int(guarantee), time_limit)
    if cover is None:
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    try:
        chosen = [[pool[i] for i in blk] for blk in cover]
        # HiGHS poate returna o soluție feasibilă NEoptimă la limita de timp →
        # comparăm cu greedy. Criteriul e (ACOPERIRE, apoi bilete), nu doar numărul
        # de bilete: greedy-ul se oprește la 1000 de iterații
        # (`loto_engine.generate_combinatorial_wheel`), deci pe o cerere degenerată
        # (`guarantee == pick`, unde doar sistemul complet acoperă 100%) întoarce
        # 1001 bilete la ~33% acoperire. Comparate doar pe număr, „mai puține bilete"
        # ar fi câștigat cu o acoperire de trei ori mai mică — iar comentariul de
        # dinainte („ambele 100%") era fals exact în cazul ăsta.
        g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
        ilp_cov = _coverage_pct(chosen, pool, guarantee)
        g_cov = _coverage_pct(g_wheel, pool, guarantee)
        # `>=` păstrează comportamentul vechi la egalitate (ambele 100% → greedy).
        if (g_cov, -len(g_wheel)) >= (ilp_cov, -len(chosen)):
            logger.info(
                "[WHEEL-ILP] greedy (%d bilete, %.2f%%) ≥ ILP (%d bilete, %.2f%%) → păstrez greedy",
                len(g_wheel),
                g_cov,
                len(chosen),
                ilp_cov,
            )
            chosen = [list(t) for t in g_wheel]
        else:
            logger.info(
                "[WHEEL-ILP] cover ILP = %d bilete la %.2f%% (greedy era %d la %.2f%%)",
                len(chosen),
                ilp_cov,
                len(g_wheel),
                g_cov,
            )
        if max_variants > 0 and len(chosen) > max_variants:
            chosen = _order_by_scores(chosen, scores)[:max_variants]
        ordered = _order_by_scores(chosen, scores)
        if max_variants > 0:
            # Trunchierea la buget poate scoate numere slabe complet de pe
            # bilete — completăm ca la `generate_wheel`, ca proprietatea să
            # fie garantată și la apel direct, nu doar prin dispatcher.
            ordered = ensure_pool_numbers_on_tickets(ordered, pool, pick)
        return ordered, _coverage_pct(ordered, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-ILP] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
