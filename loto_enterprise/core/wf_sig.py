"""Semnături pentru cache-ul walk-forward — pur stdlib (fără pandas).

Extrase din `walk_forward_adapter` ca să fie testabile în mediul container
fără sklearn/pandas, și ca serializarea ensemble-ului să aibă o singură
implementare.
"""
from __future__ import annotations


def ensemble_sig(ensemble) -> str:
    """Semnătură stabilă a ensemble-ului (listă {method,weight} sau dict).

    Greutatea intră în cheie prin `float(...).hex()`, NU `round(..., 4)`:
    aceeași convenție ca `_penalty_sig` (walk_forward_adapter.py) — 0.30001
    și 0.30004 nu sunt aceeași pondere si nu au voie sa cada pe ACELASI
    rezultat WF cache-uit. Rotunjirea la 4 zecimale e inofensiva azi (
    ENSEMBLE_MAX_METHODS=1 in productie, decision.py), dar devine un bug de
    cache STALE viu de indata ce plafonul creste la blend-uri multi-membru
    validate direct (CLAUDE.md §5 pct. 8)."""
    if not ensemble:
        return ""
    if isinstance(ensemble, dict):
        return ",".join(f"{k}:{float(v).hex()}" for k, v in sorted(ensemble.items()))
    if isinstance(ensemble, list):
        parts = []
        for item in ensemble:
            if isinstance(item, dict):
                parts.append(f"{item.get('method')}:{float(item.get('weight', 0) or 0).hex()}")
            else:
                parts.append(str(item))
        return ",".join(sorted(parts))
    return str(ensemble)


def lookback_pct(lookback_percent) -> int:
    """0 / None / falsy → 100 (tot istoricul). Altfel rotunjit la întreg."""
    if not lookback_percent:
        return 100
    try:
        return int(round(float(lookback_percent)))
    except (TypeError, ValueError):
        return 100
