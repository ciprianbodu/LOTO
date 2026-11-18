"""Selection and identity of complete official LR schemes.

The interactive slider selects an exact code. A pool-only request selects
the cheapest complete official code. Neither path changes official rows.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json

from .lr_schemes import LRScheme, lr_schemes, scheme_guarantees, scheme_odds

_GAMES = {"6/49", "5/40", "joker"}


def _game(game: str) -> str:
    if not isinstance(game, str) or game not in _GAMES:
        raise ValueError(f"Schemele LR nu sunt disponibile pentru jocul {game!r}.")
    return game


def _integer(value, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} trebuie să fie un număr întreg.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} trebuie să fie un număr întreg.") from exc
    if value != number and not (isinstance(value, str) and value.strip() == str(number)):
        raise ValueError(f"{label} trebuie să fie un număr întreg.")
    return number


def lr_pool_sizes(game: str) -> tuple[int, ...]:
    """Only pool sizes for which an official, validated layout is loaded."""
    return tuple(sorted({s.n for s in lr_schemes(_game(game))}))


def resolve_lr_scheme(game: str, code, pool_size=None) -> LRScheme:
    """Resolve an exact code; mismatches never become a generic wheel."""
    game = _game(game)
    code = _integer(code, "Codul LR")
    scheme = next((s for s in lr_schemes(game) if s.code == code), None)
    if scheme is None:
        raise ValueError(f"Cod {code} nu este o schemă LR disponibilă pentru {game}.")
    if pool_size is not None and _integer(pool_size, "Poolul LR") != scheme.n:
        raise ValueError(f"{game} Cod {code} cere exact {scheme.n} numere în pool.")
    return scheme


def select_lr_scheme(game: str, pool_size, *, preferred_code=None,
                     max_variants=0, policy: str = "cost") -> LRScheme:
    """Cheapest official code for an exact pool; explicit codes use resolve.

    ``preferred_code`` only breaks an equal-cost tie. It cannot silently
    promote a more expensive code under the user's minimum-cost policy.
    """
    game = _game(game)
    pool_size = _integer(pool_size, "Poolul LR")
    cap = _integer(max_variants, "Plafonul de variante")
    if cap < 0:
        raise ValueError("Plafonul de variante nu poate fi negativ.")
    if policy != "cost":
        raise ValueError(f"Politică LR necunoscută: {policy!r}.")
    schemes = lr_schemes(game, pool_size)
    if not schemes:
        available = ", ".join(map(str, lr_pool_sizes(game)))
        raise ValueError(f"{game} nu are schemă LR pentru {pool_size} numere. Pooluri LR: {available}.")
    cheapest = min(s.size for s in schemes)
    if cap and cheapest > cap:
        raise ValueError(f"{game}: schema LR cea mai ieftină pentru {pool_size} numere are "
                         f"{cheapest} variante, peste plafonul {cap}.")
    candidates = [s for s in schemes if s.size == cheapest]
    return min(candidates, key=lambda s: (s.code != preferred_code, s.code))


def validate_lr_variants(scheme: LRScheme, max_variants=0) -> int:
    """An explicit LR code is a complete layout, never a truncated budget.

    The LR task contract accepts zero or the exact official row count and
    returns that row count. Validate before legacy integer coercions.
    """
    cap = _integer(max_variants, "Plafonul de variante LR")
    if cap not in (0, scheme.size):
        raise ValueError(f"{scheme.game} {scheme.label} are exact {scheme.size} variante; "
                         f"plafonul LR trebuie să fie 0 sau {scheme.size}, nu {cap}.")
    return scheme.size


@lru_cache(maxsize=64)
def _signature(scheme: LRScheme) -> str:
    payload = ["official-lr-1", scheme.game, scheme.code, scheme.n, scheme.variants]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"lr1-{digest}"


def lr_scheme_signature(game: str, code, pool_size=None) -> str:
    """Identity of the actual loaded geometry and ordered official rows."""
    return _signature(resolve_lr_scheme(game, code, pool_size))


def lr_scheme_metadata(scheme: LRScheme) -> dict:
    """Serializable, exact probabilities and all conditional guarantees."""
    if scheme != resolve_lr_scheme(scheme.game, scheme.code, scheme.n):
        raise ValueError("Desfășurarea nu corespunde codului LR oficial.")
    odds = scheme_odds(scheme)
    result = dict(game=scheme.game, code=scheme.code, pool_size=scheme.n,
                  variant_count=scheme.size, catalog_signature=_signature(scheme),
                  guarantees={h: dict(row) for h, row in scheme_guarantees(scheme).items()},
                  odds=dict(odds))
    if scheme.game == "joker":
        result["odds_with_joker"] = {t: p / 20 for t, p in odds.items()}
    return result


def lr_scheme_transition(previous: LRScheme, current: LRScheme) -> dict:
    """Actual losses at a transition, not an assumption of monotonic gain."""
    if previous.game != current.game:
        raise ValueError("Comparația LR cere același joc.")
    before, after = scheme_odds(previous), scheme_odds(current)
    # In 6/49 three main numbers on a ticket already win a prize.
    first_threshold = 3 if previous.game == "6/49" else 4
    lost = {t: {"before": before[t], "after": after[t]}
            for t in before if t >= first_threshold and after[t] < before[t] - 1e-15}
    old_g, new_g = scheme_guarantees(previous), scheme_guarantees(current)
    lost_sure = []
    for h in old_g.keys() & new_g.keys():
        for t, p in old_g[h].items():
            if p >= 1 - 1e-12 and new_g[h].get(t, 0) < 1 - 1e-12:
                lost_sure.append({"pool_hits": h, "ticket_hits": t,
                                  "new_probability": new_g[h].get(t, 0)})
    result = dict(previous_code=previous.code, code=current.code,
                  lost_odds=lost, lost_sure_guarantees=lost_sure)
    if previous.game == "joker":
        from .lr_schemes import scheme_joker_prize_odds

        old_prizes = scheme_joker_prize_odds(previous)
        new_prizes = scheme_joker_prize_odds(current)
        result["lost_joker_odds"] = {
            key: {"before": value, "after": new_prizes[key]}
            for key, value in old_prizes.items()
            if new_prizes[key] < value - 1e-15
        }
    return result
