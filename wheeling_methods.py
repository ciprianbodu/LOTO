"""Metode de wheeling — fațadă publică.

Implementarea e în pachetul `covering/` (common, greedy, ilp, search, designs,
dispatch). Importurile vechi `from wheeling_methods import ...` rămân valide.
"""

from __future__ import annotations

from covering.common import (
    compute_coverage_pct,
    ensure_pool_numbers_on_tickets,
    filter_preserving_coverage,
    lotto_coverage_pct,
)
from covering.designs import (
    covering_design_source_signature,
    lotto_design_path,
    wheel_lajolla,
    wheel_lotto,
    wheel_union34,
)
from covering.dispatch import WHEEL_METHODS, generate_wheel
from covering.ilp import wheel_ilp
from covering.search import wheel_annealing, wheel_genetic

__all__ = [
    "WHEEL_METHODS",
    "compute_coverage_pct",
    "covering_design_source_signature",
    "ensure_pool_numbers_on_tickets",
    "filter_preserving_coverage",
    "generate_wheel",
    "lotto_coverage_pct",
    "lotto_design_path",
    "wheel_annealing",
    "wheel_genetic",
    "wheel_ilp",
    "wheel_lajolla",
    "wheel_lotto",
    "wheel_union34",
]
