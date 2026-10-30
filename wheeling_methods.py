"""Metode de wheeling — fațadă publică.

Implementarea e în pachetul `covering/` (common, greedy, ilp, search, designs,
dispatch). Importurile vechi `from wheeling_methods import ...` rămân valide.
"""

from __future__ import annotations

from covering.common import (
    _greedy_fallback,
    compute_coverage_pct,
    ensure_pool_numbers_on_tickets,
    filter_preserving_coverage,
    lotto_coverage_pct,
)
from covering.designs import (
    _LAJOLLA_DIRS,
    _LOTTO_COVER_CACHE,
    _LOTTO_MAX_BLOCKS,
    _load_lajolla,
    _load_lotto_design,
    _lotto_cover_positions,
    covering_design_source_signature,
    lotto_design_path,
    wheel_lajolla,
    wheel_lotto,
    wheel_union34,
)
from covering.budget_designs import budget_design_signature, load_budget_design
from covering.dispatch import (
    WHEEL_METHODS,
    budget_buys_complete_design,
    generate_wheel,
    resolve_wheel_method,
)
from covering.ilp import _ILP_COVER_CACHE, _ilp_cover_positions, wheel_ilp
from covering.large_pool import validate_large_pool_request
from covering.multihit import multi_hit_stats, wheel_multihit
from covering.search import wheel_annealing, wheel_genetic
from covering.weighted import align_positions_by_degree, wheel_weighted

__all__ = [
    "WHEEL_METHODS",
    "align_positions_by_degree",
    "budget_buys_complete_design",
    "budget_design_signature",
    "compute_coverage_pct",
    "covering_design_source_signature",
    "load_budget_design",
    "ensure_pool_numbers_on_tickets",
    "filter_preserving_coverage",
    "generate_wheel",
    "resolve_wheel_method",
    "lotto_coverage_pct",
    "lotto_design_path",
    "multi_hit_stats",
    "wheel_annealing",
    "wheel_genetic",
    "wheel_ilp",
    "wheel_lajolla",
    "wheel_lotto",
    "wheel_multihit",
    "wheel_union34",
    "wheel_weighted",
    "validate_large_pool_request",
]
