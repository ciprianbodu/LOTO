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


# Ținta minimă PER JOC. Loto 5/40: se extrag 6 numere, biletul are 5, iar 3
# numere nu aduc premiu — cel mai mic premiu cere 4 din cele 6 extrase.
# Ținta globală 3 nu coboară 5/40 sub 4; ținta globală 4 îl lasă tot pe 4.
# Urna 2 (top-1) rămâne tratată separat de decizie.
GAME_MIN_HIT_TARGET: dict[str, int] = {"loto_5_40": 4}


def game_hit_target(game_key: str, global_target) -> int:
    """Ținta efectivă a deciziei pentru `game_key` (3 sau 4)."""
    t = clamp_bench_hit_target(global_target)
    return max(t, int(GAME_MIN_HIT_TARGET.get(str(game_key), t)))


# Geometria jocurilor de bench: (numere EXTRASE, numere pe BILET). Hiturile se
# numără pe extragere; pool-ul de bază (coloana fără `_kN`) are mărimea biletului.
# Loto 5/40 extrage 6 numere, dar biletul are 5.
GAME_DRAW_PICK: dict[str, tuple[int, int]] = {
    "loto_6_49": (6, 6),
    "loto_5_40": (6, 5),
    "joker_urna1": (5, 5),
    "joker_urna2": (1, 1),
}
