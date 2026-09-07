"""Ținta de hituri a bench-ului — doar 3 sau 4.

Extrasă din `decision.py` ca să fie importabilă fără pandas (teste în
container, worker, UI).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def clamp_bench_hit_target(value, *, default: int = 3) -> int:
    try:
        n = int(value)
    except TypeError, ValueError:
        # Eșecul de parsare cădea tăcut pe `default` — spre deosebire de o valoare
        # doar în afara intervalului (3/4), care logează mai jos. O valoare
        # neparsabilă (ex. "4.0" scrisă programatic din str(float(x))) trecea
        # neobservată, deși schimbă tinta deciziei (3+ vs 4+) la fel de mult.
        logger.warning(
            "[decision] LOTO_BENCH_TARGET=%r neparsabil ca intreg — folosesc %d",
            value,
            default,
        )
        return default
    if n not in (3, 4):
        logger.warning(
            "[decision] LOTO_BENCH_TARGET=%r invalid (doar 3 sau 4) — folosesc %d",
            value,
            default,
        )
        return default
    return n
