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
    except (TypeError, ValueError):
        n = default
    if n not in (3, 4):
        logger.warning(
            "[decision] LOTO_BENCH_TARGET=%r invalid (doar 3 sau 4) — folosesc %d",
            value, default,
        )
        return default
    return n
