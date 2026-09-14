"""Greedy covering wheel — extracted from loto_engine (cycle break).

loto_engine.generate_combinatorial_wheel re-exports this function.
"""

from __future__ import annotations

import itertools
import logging
import math
import time

def generate_combinatorial_wheel(
    pool, pick=6, guarantee=4, max_variants=0, scores=None
):
    """
    Sistem de Wheeling (Set Cover Optimizat Memorie & Viteză)
    Optimizat pentru hit-uri de 4 și 5 numere prin prioritizarea scorurilor NQI/Frecvență.
    """
    if int(guarantee) > int(pick):
        raise ValueError(
            f"guarantee={guarantee} > pick={pick}: garanția nu poate depăși numerele extrase"
        )
    start_time = time.time()
    pool_len = len(pool)
    logging.info(
        f"[WHEEL] Inițializare sistem Wheeling pentru pool de {pool_len} numere. Pick={pick}, Guarantee={guarantee}."
    )

    if pool_len < pick:
        return [], 0.0

    if scores:
        pool = sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    else:
        pool = sorted(list(pool))

    if int(guarantee) == int(pick):
        n_full = math.comb(pool_len, pick)
        combinations = itertools.combinations(pool, pick)
        if max_variants > 0 and n_full > max_variants:
            logging.warning(
                "[WHEEL] guarantee==pick cu max_variants=%d < C(%d,%d)=%d — "
                "acoperirea NU poate fi 100%% (sistem incomplet).",
                max_variants,
                pool_len,
                pick,
                n_full,
            )
            combinations = itertools.islice(combinations, max_variants)
        wheel = [sorted(c) for c in combinations]
        if max_variants > 0:
            from wheeling_methods import ensure_pool_numbers_on_tickets

            wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        coverage_pct = (
            100.0
            if len(wheel) >= n_full
            else round(100.0 * len(wheel) / max(n_full, 1), 2)
        )
        logging.info(
            "[WHEEL] Sistem complet C(%d,%d): %d bilete, acoperire %.2f%% în %.2fs.",
            pool_len,
            pick,
            len(wheel),
            coverage_pct,
            time.time() - start_time,
        )
        return wheel, coverage_pct

    all_targets_list = [
        tuple(sorted(t)) for t in itertools.combinations(pool, guarantee)
    ]

    if scores:
        all_targets_list.sort(
            key=lambda t: sum(scores.get(n, 0) for n in t), reverse=True
        )

    total_targets = len(all_targets_list)
    target_index = {t: i for i, t in enumerate(all_targets_list)}
    covered_mask = 0
    covered_count = 0
    wheel = []

    iteration = 0
    max_search_per_iter = 10000 if pool_len <= 15 else 50000

    _tm_cache: dict = {}
    target_cursor = 0
    max_ticket_coverage = math.comb(pick, guarantee)

    def _ticket_mask(ticket):
        cached = _tm_cache.get(ticket)
        if cached is not None:
            return cached
        m = 0
        for sub in itertools.combinations(ticket, guarantee):
            idx = target_index.get(sub)
            if idx is not None:
                m |= 1 << idx
        _tm_cache[ticket] = m
        return m

    while covered_count < total_targets:
        if max_variants > 0 and len(wheel) >= max_variants:
            logging.info(
                f"[WHEEL] S-a atins limita maxima cerută de variante: {max_variants}."
            )
            break

        iteration += 1
        best_ticket = None
        best_coverage = -1
        best_targets_covered = 0

        while target_cursor < total_targets and (
            covered_mask >> target_cursor
        ) & 1:
            target_cursor += 1
        target_to_cover = (
            all_targets_list[target_cursor] if target_cursor < total_targets else None
        )

        if not target_to_cover:
            break
        base_ticket = set(target_to_cover)
        remaining_pool = [n for n in pool if n not in base_ticket]

        search_count = 0
        if len(remaining_pool) >= (pick - guarantee):
            for extra_nums in itertools.combinations(remaining_pool, pick - guarantee):
                ticket = tuple(sorted(list(base_ticket) + list(extra_nums)))
                tmask = _ticket_mask(ticket)
                gain = (tmask & ~covered_mask).bit_count()

                if gain > best_coverage:
                    best_coverage = gain
                    best_ticket = ticket
                    best_targets_covered = tmask
                    if best_coverage == max_ticket_coverage:
                        break

                search_count += 1
                if search_count > max_search_per_iter:
                    break

        if best_ticket:
            wheel.append(list(best_ticket))
            covered_mask |= best_targets_covered
            covered_count = covered_mask.bit_count()
            if iteration % 20 == 0 or covered_count == total_targets:
                logging.info(
                    f"[WHEEL] Progres {iteration}: Acoperite {covered_count}/{total_targets} ținte. Bilete: {len(wheel)}"
                )
        else:
            logging.warning(
                "[WHEEL] Nu am găsit acoperire suplimentară, oprire timpurie."
            )
            break

        if iteration > 1000:
            logging.warning(f"[WHEEL] TIMEOUT: 1000 iterații.")
            break

    if max_variants > 0:
        from wheeling_methods import ensure_pool_numbers_on_tickets

        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        covered_mask = 0
        g = int(guarantee)
        for t in wheel:
            ticket = tuple(sorted(int(x) for x in t))
            for sub in itertools.combinations(ticket, g):
                idx = target_index.get(sub)
                if idx is not None:
                    covered_mask |= 1 << idx
        coverage_pct = (
            100.0
            if total_targets == 0
            else round(100.0 * covered_mask.bit_count() / total_targets, 2)
        )
    else:
        coverage_pct = (
            100.0
            if total_targets == 0
            else round((covered_count / total_targets) * 100, 2)
        )
    logging.info(
        f"[WHEEL] Generare completă în {time.time() - start_time:.2f}s. Total variante: {len(wheel)}. Acoperire: {coverage_pct}%"
    )
    return wheel, coverage_pct
