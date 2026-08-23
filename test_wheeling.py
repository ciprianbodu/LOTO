"""Teste pentru wheeling — zona asta nu avea niciun test, iar garanția de acoperire
e singura proprietate pe care app-ul o PROMITE explicit utilizatorului (UI afișează
„✅ Acoperire garanție: 100%"). Dacă se sparge, utilizatorul plătește bilete care nu
mai garantează nimic, fără niciun semnal.

Invariantul central verificat peste tot: orice submulțime de `guarantee` numere din
pool trebuie să apară integral pe cel puțin un bilet.
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

from wheeling_methods import (
    WHEEL_METHODS,
    _order_by_scores,
    cap_wheel_max_coverage,
    compute_coverage_pct,
    filter_preserving_coverage,
    generate_wheel,
    wheel_lajolla,
)

DESIGN_DIR = Path(__file__).parent / "covering_designs"


def _covers_all(wheel: list[list[int]], pool: list[int], guarantee: int) -> bool:
    """Verificare independentă de codul aplicației (nu folosim compute_coverage_pct,
    ca testul să nu valideze bug-ul cu el însuși)."""
    covered = set()
    for ticket in wheel:
        covered.update(combinations(sorted(ticket), guarantee))
    return covered.issuperset(combinations(sorted(pool), guarantee))


# --------------------------------------------------------------------------- #
# Design-urile de acoperire instalate local (La Jolla)
# --------------------------------------------------------------------------- #
def _installed_designs() -> list[Path]:
    return sorted(DESIGN_DIR.glob("C_*.txt")) if DESIGN_DIR.is_dir() else []


@pytest.mark.parametrize("design_file", _installed_designs(), ids=lambda p: p.stem)
def test_design_file_covers_completely(design_file: Path):
    """Un design instalat care NU acoperă complet e mai rău decât lipsa lui: e folosit
    preferențial față de ILP/greedy și ar raporta o garanție pe care n-o are."""
    v, pick, guarantee = (int(x) for x in design_file.stem.split("_")[1:])
    blocks = [[int(x) for x in ln.split()] for ln in
              design_file.read_text().splitlines() if ln.strip()]
    pool = list(range(1, v + 1))

    assert blocks, f"{design_file.name} e gol"
    assert all(len(b) == pick for b in blocks), "bloc cu dimensiune greșită"
    assert all(1 <= x <= v for b in blocks for x in b), "număr în afara intervalului 1..v"
    assert len({tuple(sorted(b)) for b in blocks}) == len(blocks), "blocuri duplicate"
    assert _covers_all(blocks, pool, guarantee), "design-ul NU acoperă toate țintele"


@pytest.mark.parametrize("design_file", _installed_designs(), ids=lambda p: p.stem)
def test_design_not_worse_than_greedy(design_file: Path):
    """Rostul design-urilor e să coste mai puțin. Dacă un design ajunge să aibă mai
    multe bilete decât greedy, instalarea lui e o regresie de preț, nu o optimizare."""
    v, pick, guarantee = (int(x) for x in design_file.stem.split("_")[1:])
    pool = list(range(1, v + 1))
    lajolla, _ = wheel_lajolla(pool, pick, guarantee, 0, None)
    greedy, _ = generate_wheel("greedy", pool, pick, guarantee, 0, None)
    assert len(lajolla) <= len(greedy), (
        f"design {design_file.name}: {len(lajolla)} bilete > greedy {len(greedy)}"
    )


def test_lajolla_uses_installed_design_for_current_config():
    """Configurația reală din UI (pool 12, garanție 4). Blochează regresia în care
    design-ul e ignorat tăcut și se cade pe ILP/greedy — aceeași garanție, dar
    mai scump, fără niciun mesaj de eroare."""
    pool = list(range(1, 13))
    for pick, expected in ((6, 41), (5, 113)):
        if not (DESIGN_DIR / f"C_12_{pick}_4.txt").exists():
            pytest.skip(f"design C(12,{pick},4) neinstalat")
        wheel, _ = wheel_lajolla(pool, pick, 4, 0, None)
        assert len(wheel) == expected, f"C(12,{pick},4): {len(wheel)} bilete, aștept {expected}"
        assert _covers_all(wheel, pool, 4)


# --------------------------------------------------------------------------- #
# Contractul comun al tuturor algoritmilor de wheeling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method", ["greedy", "lajolla", "ilp", "necunoscut_cade_pe_greedy"])
@pytest.mark.parametrize("pick,guarantee", [(6, 4), (5, 4), (6, 3)])
def test_full_guarantee_when_no_ticket_cap(method: str, pick: int, guarantee: int):
    """Fără plafon de bilete (max_variants=0) garanția trebuie să fie REALĂ, la orice
    algoritm — inclusiv pe numele necunoscute, care trebuie să cadă pe greedy, nu să crape."""
    pool = [3, 7, 11, 12, 19, 23, 28, 31, 35, 40]
    wheel, coverage = generate_wheel(method, pool, pick, guarantee, 0, None)

    assert wheel, "wheel gol"
    assert all(len(t) == pick for t in wheel), "bilet cu dimensiune greșită"
    assert all(x in pool for t in wheel for x in t), "număr din afara pool-ului"
    assert len({tuple(sorted(t)) for t in wheel}) == len(wheel), "bilete duplicate"
    assert _covers_all(wheel, pool, guarantee), f"{method}: garanția {guarantee} nu e acoperită"
    assert coverage == pytest.approx(100.0), f"{method}: raportează {coverage}%, dar acoperă tot"


@pytest.mark.parametrize("method", sorted(WHEEL_METHODS) + ["greedy"])
def test_ticket_cap_is_respected(method: str):
    """Cu plafon de bilete garanția se poate rupe (e acceptat), dar plafonul NU are voie
    să fie depășit — altfel utilizatorul plătește mai mult decât a cerut explicit."""
    pool = list(range(1, 13))
    cap = 10
    wheel, coverage = generate_wheel(method, pool, 6, 4, cap, None)
    assert len(wheel) <= cap, f"{method}: {len(wheel)} bilete > plafon {cap}"
    assert coverage <= 100.0


def test_coverage_pct_matches_independent_count():
    """compute_coverage_pct e folosit ca sursă a procentului afișat în UI —
    dacă minte, utilizatorul crede că are garanție când n-are."""
    pool = list(range(1, 9))
    full, _ = generate_wheel("greedy", pool, 4, 3, 0, None)
    assert compute_coverage_pct(full, pool, 3) == pytest.approx(100.0)

    partial = full[: max(1, len(full) // 3)]
    pct = compute_coverage_pct(partial, pool, 3)
    targets = list(combinations(sorted(pool), 3))
    covered = set()
    for t in partial:
        covered.update(combinations(sorted(t), 3))
    assert pct == pytest.approx(len(covered) / len(targets) * 100, abs=0.01)
    assert pct < 100.0, "un subset strict n-ar trebui să acopere tot"


def test_pool_smaller_than_ticket_does_not_crash():
    """Pool sub dimensiunea biletului e o stare degenerată reală (pool trunchiat);
    nu trebuie să arunce excepție în mijlocul pipeline-ului."""
    wheel, coverage = generate_wheel("lajolla", [4, 8, 15], 6, 4, 0, None)
    assert wheel and coverage == pytest.approx(100.0)


def test_filter_preserving_coverage_keeps_guarantee():
    """Helperul există ca plasă de siguranță dacă se reactivează vreun filtru
    post-wheel. Dacă el însuși sparge garanția, e o capcană, nu o plasă."""
    pool = list(range(1, 11))
    wheel, _ = generate_wheel("greedy", pool, 5, 3, 0, None)
    filtered, removed = filter_preserving_coverage(
        wheel, pool, 3, removal_priority=list(range(len(wheel)))
    )
    assert removed >= 0
    assert len(filtered) == len(wheel) - removed
    assert _covers_all(filtered, pool, 3), "filtrarea a spart garanția"


def test_order_by_scores_ignores_nan():
    """Un NaN pe un număr nu mai face ordinea biletelor dependentă de inserare."""
    wheel = [[1, 2, 3], [4, 5, 6]]
    scores = {1: 1.0, 2: 1.0, 3: 1.0, 4: float("nan"), 5: 0.1, 6: 0.1}
    ordered = _order_by_scores(wheel, scores)
    assert ordered[0] == [1, 2, 3]
    assert ordered[1] == [4, 5, 6]


def test_cap_wheel_max_coverage_keeps_more_hits_than_score_slice():
    """Sub buget, tăierea după scor poate arunca acoperirea; greedy pe bilete o păstrează."""
    pool = [1, 2, 3, 4, 5, 6]
    # 3 bilete: primele 2 (scor mare) acoperă doar {1,2,3,4};
    # al 3-lea (scor mic) e necesar pentru 5 și 6.
    tickets = [(1, 2, 3), (2, 3, 4), (4, 5, 6)]
    scores = {1: 10.0, 2: 10.0, 3: 10.0, 4: 1.0, 5: 0.0, 6: 0.0}

    by_score = tickets[:2]
    capped = cap_wheel_max_coverage(tickets, pool, guarantee=2, max_variants=2, scores=scores)
    assert len(capped) <= 2
    assert compute_coverage_pct(capped, pool, 2) > compute_coverage_pct(by_score, pool, 2)


def test_cap_wheel_stops_when_full_coverage_then_fills_budget():
    pool = [1, 2, 3, 4]
    tickets = [(1, 2, 3), (1, 2, 4), (1, 3, 4), (2, 3, 4)]
    capped = cap_wheel_max_coverage(tickets, pool, guarantee=2, max_variants=10)
    assert compute_coverage_pct(capped, pool, 2) == pytest.approx(100.0)
    assert len(capped) == len(tickets)


def test_cap_wheel_union_guarantees_keeps_unique_3_cover():
    """union34: un bilet slab pe scor poate fi necesar pe 3-din-3."""
    pool = [1, 2, 3, 4, 5, 6]
    tickets = [(1, 2, 3, 4), (1, 2, 3, 5), (2, 4, 5, 6)]
    scores = {1: 10.0, 2: 10.0, 3: 10.0, 4: 1.0, 5: 1.0, 6: 0.0}
    only4 = cap_wheel_max_coverage(tickets, pool, 4, 2, scores)
    both = cap_wheel_max_coverage(tickets, pool, 4, 2, scores, guarantees=(3, 4))
    assert (2, 4, 5, 6) in {tuple(t) for t in both}
    assert (2, 4, 5, 6) not in {tuple(t) for t in only4}
    assert compute_coverage_pct(both, pool, 3) > compute_coverage_pct(only4, pool, 3)


def test_guarantee_equals_pick_is_complete_system():
    """Sistem complet (guarantee==pick): C(v, pick) bilete, acoperire 100%.
    Greedy-ul vechi se oprea la 1000 de iterații și raporta ~33%."""
    pytest.importorskip("pandas")
    from math import comb

    from loto_engine import generate_combinatorial_wheel

    pool = list(range(1, 9))
    wheel, coverage = generate_combinatorial_wheel(pool, pick=6, guarantee=6, max_variants=0)
    assert len(wheel) == comb(8, 6)
    assert coverage == pytest.approx(100.0)
    assert _covers_all(wheel, pool, 6)

    capped, cap_cov = generate_combinatorial_wheel(pool, pick=6, guarantee=6, max_variants=5)
    assert len(capped) == 5
    assert cap_cov < 100.0


def test_initial_hard_core_fills_zero_frequency_numbers():
    """Istoric scurt: fără umplere, pool-ul rămânea cu 1 număr și wheel-ul pierdea bilete."""
    pytest.importorskip("pandas")
    import numpy as np

    from loto_engine import LotoEngine

    eng = LotoEngine("6/49")
    freq = np.zeros(49, dtype=np.float64)
    freq[0] = 5.0
    pool = eng._get_initial_hard_core(freq, pool_size=5, blacklist=set())
    assert len(pool) == 5
    assert 1 in pool
    assert len(set(pool)) == 5
