"""Teste pentru loto_enterprise/benchmark/reporting.py — verificare globala.

Coloana "vs Random*" calcula lift-ul fata de rata EMPIRICA a randului
`random` din acelasi raport, nu fata de baseline-ul TEORETIC (media
hipergeometrica a hiturilor) — acelasi tipar deja corectat in decision.py,
scan_all_methods_hits.py si analiza_4plus.py."""
from __future__ import annotations

from rich.console import Console

from loto_enterprise.benchmark.reporting import render_per_game


def _report(method_avg_hits: float, base_k: str = "k10") -> dict:
    return {
        "games": {
            "loto_6_49": {
                "label": "Loto 6/49",
                "max_num": 49,
                "draw_n": 6,
                "pool_keys": [base_k],
                "overall_ranking": [{"method": "m1"}],
                "per_method": {
                    "m1": {
                        "family": "test",
                        "per_pool": {base_k: {"avg_hits_real": method_avg_hits}},
                    },
                },
            }
        }
    }


def test_lift_uses_theoretical_expected_hits_not_random_row():
    """E[hits] teoretic la 6/49, pool 10 = 10*6/49 ≈ 1.2245. O metoda cu
    avg_hits_real=1.5 trebuie sa arate lift ≈ +0.2755, indiferent daca in
    raport exista sau nu un rand `random` (n-a mai ramas nicio dependenta
    de el)."""
    report = _report(method_avg_hits=1.5)
    console = Console(record=True, width=200)
    render_per_game(console, report)
    text = console.export_text()
    assert "+0.276" in text or "+0.275" in text


def test_lift_column_header_no_longer_references_random():
    report = _report(method_avg_hits=1.0)
    console = Console(record=True, width=200)
    render_per_game(console, report)
    text = console.export_text()
    assert "vs Random" not in text
    assert "teoretic" in text
