"""Ținta de hituri ÎN POOL a bench-ului — 3, 4, 5 sau 6.

Extrasă din `decision.py` ca să fie importabilă fără pandas (teste în
container, worker, UI).
"""

from __future__ import annotations

import logging

from loto_enterprise.core.lotteries import (
    foreign_bench_draw_pick,
    foreign_min_hit_target,
)

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
    if n not in (3, 4, 5, 6):
        logger.warning(
            "[decision] LOTO_BENCH_TARGET=%r invalid (doar 3, 4, 5 sau 6) — folosesc %d",
            value,
            default,
        )
        return default
    return n


# Ținta minimă PER JOC. Loto 5/40: se extrag 6 numere, biletul are 5, iar 3
# numere nu aduc premiu — cel mai mic premiu cere 4 din cele 6 extrase.
# Ținta globală 3 nu coboară 5/40 sub 4. Țintele 5 și 6 măsoară hiturile
# din pool, nu de pe bilet: toate cele șase numere extrase contează.
# Urna 2 (top-1) rămâne tratată separat de decizie.
GAME_MIN_HIT_TARGET: dict[str, int] = {"loto_5_40": 4}
# Jocurile din alte țări: din registru (azi toate cu 3 numere premiate, deci
# nicio intrare; un joc cu prag mai mare apare aici fără cod nou).
GAME_MIN_HIT_TARGET.update(foreign_min_hit_target())


def game_hit_target(game_key: str, global_target) -> int:
    """Ținta efectivă în pool, limitată de numărul de bile EXTRASE.

    Joker Urna 1 are cel mult cinci hituri; Urna 2 rămâne top-1. La 5/40
    cele șase extrase permit ținta 6 în pool, deși varianta jucată are cinci.
    """
    t = clamp_bench_hit_target(global_target)
    key = str(game_key)
    draw_n = GAME_DRAW_PICK.get(key, (6, 6))[0]
    return min(draw_n, max(t, int(GAME_MIN_HIT_TARGET.get(key, t))))


# Geometria jocurilor de bench: (numere EXTRASE, numere pe BILET). Hiturile se
# numără pe extragere; pool-ul de bază (coloana fără `_kN`) are mărimea biletului.
# Loto 5/40 extrage 6 numere, dar biletul are 5.
GAME_DRAW_PICK: dict[str, tuple[int, int]] = {
    "loto_6_49": (6, 6),
    "loto_5_40": (6, 5),
    "joker_urna1": (5, 5),
    "joker_urna2": (1, 1),
}
# Cheile de bench ale jocurilor din alte țări, generate din registru (sursa
# unică a geometriei). Tabelul românesc de mai sus rămâne literal; testul de
# consistență al registrului verifică că cele două spun același lucru.
GAME_DRAW_PICK.update(foreign_bench_draw_pick())
