"""Un bilet fizic complet per joc, din pool-ul rezultatului afisat.

Numarul de variante simple de pe un bilet: 3 la 6/49, 4 la 5/40, 2 la Joker.
Variantele se aleg din pool cu wheel-ul cu buget (acelasi traseu ca
`max_variants` in productie), deci acoperirea e recalculata exact pentru acest
numar de variante, nu mostenita de la wheel-ul complet.
"""

from __future__ import annotations

from covering.dispatch import generate_wheel

TICKET_VARIANTS = {"6/49": 3, "5/40": 4, "joker": 2}
PICK = {"6/49": 6, "5/40": 5, "joker": 5}


def _pool_scores(data: dict) -> dict[int, float] | None:
    audit = data.get("audit") or {}
    raw = audit.get("ranking_scores_top25") or audit.get("timesfm_predictions")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return {int(k): float(v) for k, v in raw.items()}
    except (TypeError, ValueError):
        return None


def build_full_ticket(game: str, data: dict) -> dict:
    """Intoarce {variants, coverage, guarantee, cost_per_variant_count, error}."""
    n_var = TICKET_VARIANTS.get(game)
    pick = PICK.get(game)
    if n_var is None:
        return {"error": f"joc necunoscut: {game}"}
    pool = [int(x) for x in (data.get("hard_core") or [])]
    if len(pool) < pick:
        return {"error": "pool-ul afisat e mai mic decat un bilet"}
    guarantee = max(1, min(int(data.get("guarantee") or 3), pick))
    variants, coverage = generate_wheel(
        "greedy",
        pool=pool,
        pick=pick,
        guarantee=guarantee,
        max_variants=n_var,
        scores=_pool_scores(data),
    )
    variants = [sorted(int(x) for x in v) for v in variants][:n_var]
    joker = None
    if game == "joker":
        jk = data.get("hard_core_joker") or []
        if jk:
            joker = int(jk[0])
            variants = [v + [joker] for v in variants]
    return {
        "variants": variants,
        "coverage": float(coverage),
        "guarantee": guarantee,
        "joker": joker,
        "error": None,
    }
