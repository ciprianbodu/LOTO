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

from loto_engine import generate_combinatorial_wheel
from wheeling_methods import (
    WHEEL_METHODS,
    compute_coverage_pct,
    ensure_pool_numbers_on_tickets,
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
    nu trebuie să arunce excepție, dar nici să pretindă acoperire 100% pe
    bilete nevalidabile (UI-ul arăta verde „✅ 100%")."""
    wheel, coverage = generate_wheel("lajolla", [4, 8, 15], 6, 4, 0, None)
    assert wheel
    assert coverage == pytest.approx(0.0)
    assert all(len(t) < 6 for t in wheel)
    greedy, gcov = generate_combinatorial_wheel([4, 8, 15], pick=6, guarantee=4)
    assert greedy and gcov == pytest.approx(0.0)


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


def test_complete_system_guarantee_equals_pick_is_full_cover():
    """guarantee==pick nu mai trece prin greedy-ul cu cap 1000 iterații."""
    from math import comb

    pool = list(range(1, 9))  # 8 numere, pick=5 → C(8,5)=56
    wheel, cov = generate_combinatorial_wheel(pool, pick=5, guarantee=5, max_variants=0)
    assert cov == 100.0
    assert len(wheel) == comb(8, 5)
    assert _covers_all(wheel, pool, 5)
    for t in wheel:
        assert t == sorted(t), "bilet nesortat (afișare Joker v[:5])"
    # cu cap de bilete: acoperire parțială, dar fără timeout-ul de 1000
    wheel_cap, cov_cap = generate_combinatorial_wheel(
        pool, pick=5, guarantee=5, max_variants=10,
    )
    assert len(wheel_cap) == 10
    assert cov_cap < 100.0
    for t in wheel_cap:
        assert t == sorted(t)
    # 10 bilete × 5 ≥ 8 numere în pool → fiecare număr din pool e pe ≥1 bilet
    assert {n for t in wheel_cap for n in t} == set(pool)


def test_capped_wheel_keeps_weak_pool_numbers() -> None:
    """max_variants > 0 trunchia lexicografic — numerele slabe din pool nu apăreau
    pe niciun bilet. Acum un lipsă înlocuiește un duplicat (cât încape pick*cap)."""
    pool = list(range(1, 13))  # 12 numere
    scores = {n: float(n) for n in pool}  # 12 e cel mai tare, 1 cel mai slab
    tickets, _ = generate_combinatorial_wheel(
        pool, pick=6, guarantee=4, max_variants=2, scores=scores,
    )
    assert len(tickets) == 2
    union_nums = {n for t in tickets for n in t}
    assert union_nums == set(pool), f"lipsesc din bilete: {set(pool) - union_nums}"


def test_single_ticket_cap_does_not_drop_unique_numbers() -> None:
    """Un cap de 1 bilet nu poate acoperi 12 numere. Nu înlocuim tot biletul
    cu numerele slabe — rămân cele 6 tari (unici pe wheel)."""
    pool = list(range(1, 13))
    scores = {n: float(n) for n in pool}
    tickets, _ = generate_combinatorial_wheel(
        pool, pick=6, guarantee=4, max_variants=1, scores=scores,
    )
    assert len(tickets) == 1
    assert set(tickets[0]) == {7, 8, 9, 10, 11, 12}


def test_ensure_pool_numbers_swaps_duplicates_only() -> None:
    wheel = [[1, 2, 3, 4], [1, 2, 5, 6]]  # 1,2 duplicate; lipsesc 7,8 din pool 1-8
    pool = list(range(1, 9))
    out = ensure_pool_numbers_on_tickets(wheel, pool, pick=4)
    union = {n for t in out for n in t}
    assert {7, 8}.issubset(union)
    assert len(out) == 2
    # numerele care erau unice (3,4,5,6) nu au voie să dispară
    assert {3, 4, 5, 6}.issubset(union)
    # scanare de la coadă: T1 (cel mai bine punctat) rămâne intact
    assert out[0] == [1, 2, 3, 4]


def test_capped_wheel_keeps_first_ticket_strongest() -> None:
    """Măsurat: pool 16 / pick 6 / g4 / cap 3. Scanarea de la capăt rescria
    T1=[11..16] → [2,3,4,11,12,13]. De la coadă T1 rămâne cele 6 tari,
    acoperirea e aceeași, 0 numere lipsă."""
    from unittest.mock import patch

    pool = list(range(1, 17))
    scores = {n: float(n) for n in pool}
    kwargs = dict(pick=6, guarantee=4, max_variants=3, scores=scores)
    with patch(
        "wheeling_methods.ensure_pool_numbers_on_tickets",
        side_effect=lambda w, p, k: [list(t) for t in w],
    ):
        raw, raw_cov = generate_combinatorial_wheel(pool, **kwargs)
    packed, packed_cov = generate_combinatorial_wheel(pool, **kwargs)

    assert len(packed) == len(raw) == 3
    assert raw[0] == packed[0] == [11, 12, 13, 14, 15, 16]
    raw_union = {n for t in raw for n in t}
    packed_union = {n for t in packed for n in t}
    assert set(pool) - raw_union, "fără packing, trunchierea lexicografică pierde numere"
    assert packed_union == set(pool)
    assert packed_cov == raw_cov
    assert 0.0 < packed_cov < 100.0


def test_complete_system_tickets_are_sorted_ascending():
    """Numerele DIN bilet ies crescător, ca pe ramura greedy.

    Cu `scores`, pool-ul e sortat DESCRESCĂTOR după scor înainte de
    `itertools.combinations`, deci fără sortare explicită biletele ieșeau în
    ordinea scorului — singurele din tot modulul. Ordinea BILETELOR trebuie însă
    să rămână cea dată de scor, deci testul verifică ambele proprietăți deodată.
    Pool-ul e ales exact `pick + 1`, ca trunchierea să nu lase niciun număr pe
    dinafară (altfel `ensure_pool_numbers_on_tickets` ar rescrie primul bilet).
    """
    from loto_engine import generate_combinatorial_wheel

    pool = [7, 3, 40, 12, 25, 9]
    ranking = [40, 25, 12, 9, 7, 3]  # 40 = cel mai tare
    scores = {n: 1.0 / (i + 1) for i, n in enumerate(ranking)}

    wheel, cov = generate_combinatorial_wheel(pool, pick=5, guarantee=5,
                                              max_variants=0, scores=scores)
    assert cov == 100.0
    assert all(t == sorted(t) for t in wheel), "numerele din bilet nu sunt crescătoare"
    assert {tuple(sorted(t)) for t in wheel} == set(combinations(sorted(pool), 5))
    # primul bilet = cele mai bine punctate `pick` numere (ordinea biletelor = scor)
    assert wheel[0] == sorted(ranking[:5])

    capped, cov_capped = generate_combinatorial_wheel(pool, pick=5, guarantee=5,
                                                      max_variants=3, scores=scores)
    assert len(capped) == 3 and cov_capped < 100.0
    assert all(t == sorted(t) for t in capped)
    assert capped[0] == sorted(ranking[:5])


def test_union34_covers_3_and_4_from_designs(monkeypatch):
    """LOTO_WHEEL_METHOD=union34 nu are voie să reintroducă ILP pe ceas când
    există designuri C(v,k,3) și C(v,k,4) pe disc."""
    import wheeling_methods as wm

    ilp_calls: list = []
    real_ilp = wm.wheel_ilp

    def spy(*a, **k):
        ilp_calls.append(1)
        return real_ilp(*a, **k)

    monkeypatch.setattr(wm, "wheel_ilp", spy)
    pool = list(range(1, 11))
    wheel, _ = generate_wheel("union34", pool, pick=6, guarantee=4, max_variants=0)
    assert _covers_all(wheel, pool, 3)
    assert _covers_all(wheel, pool, 4)
    assert ilp_calls == [], "union34 a căzut pe ILP deși designurile există"
    # C_10_6_3=10 + C_10_6_4=20, uniune ≤ 30
    assert len(wheel) <= 30


def test_lajolla_guarantee_equals_pick_skips_ilp(monkeypatch):
    """g == pick = sistem complet. ILP pe C(v,pick) ținte e risipă + nedeterminist."""
    import math
    import wheeling_methods as wm

    called: list = []
    monkeypatch.setattr(wm, "wheel_ilp", lambda *a, **k: called.append(1) or ([], 0.0))
    wheel, cov = wm.wheel_lajolla(list(range(1, 11)), 5, 5, 0, None)
    assert called == []
    assert cov == 100.0
    assert len(wheel) == math.comb(10, 5)


def test_incomplete_design_does_not_claim_missing(monkeypatch, caplog):
    """Un design trunchiat loghează INCOMPLET, nu „fără design local"."""
    import logging
    import wheeling_methods as wm

    monkeypatch.setattr(wm, "_load_lajolla", lambda v, pick, g: [[1, 2, 3, 4, 5, 6]])
    ilp_called = {}

    def fake_ilp(pool, pick, guarantee, max_variants=0, scores=None, time_limit=15.0):
        ilp_called["yes"] = True
        from loto_engine import generate_combinatorial_wheel
        return generate_combinatorial_wheel(pool, pick, guarantee, max_variants, scores)

    monkeypatch.setattr(wm, "wheel_ilp", fake_ilp)
    with caplog.at_level(logging.INFO):
        wm.wheel_lajolla(list(range(1, 11)), 6, 3, 0, None)
    assert ilp_called.get("yes")
    assert "INCOMPLET" in caplog.text
    assert "fără design local" not in caplog.text

