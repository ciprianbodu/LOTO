"""Designurile precalculate din `covering_designs/` — validitate și determinism.

De ce există fișierele: `_ilp_cover_positions` rulează cu `time_limit` și NU
demonstrează niciodată optimalitatea în bugetul dat — arde tot ceasul și întoarce
cel mai bun incumbent găsit. Deci rezultatul depinde de cât de încărcată e mașina
în acele secunde. Măsurat pe main, ACELAȘI cod și aceleași date, două rulări
consecutive: 5/40 pool 12 / g3 → 33 de bilete, apoi 30; joker pool 12 / g3 → 32,
apoi 30. Un cover e o constantă matematică (nu depinde de pool, de scoruri sau de
dată), deci se calculează o dată și se citește de pe disc.

Optimizare (`scripts/analysis/optimize_covering_designs.py`, ruin-and-recreate
local search pornind de la designurile lui #93): 5454 → 5137 bilete (−5.8%),
18/55 la limita matematică Schönheim (provabil optime). Golul rămas față de
Schönheim = 14.3% — limita nu e mereu atinsă de un covering design real (nu
orice v/k/t are un sistem Steiner exact), deci nu e ținta finală, doar un
plafon inferior de verificare.
"""
import itertools
from math import ceil, comb
from pathlib import Path

import pytest

from wheeling_methods import _load_lajolla, generate_wheel

DESIGNS = sorted(Path("covering_designs").glob("C_*_*_*.txt"))

# Plafon SUPERIOR (nu regenerat automat): dimensiunile atinse de optimizare la
# 2026-08-30. Un design NU are voie să crească peste ce am obținut deja — dacă
# `gen_covering_designs.py` sau optimizatorul rulează din nou și găsește ceva
# mai mic, actualizează valoarea (lanț monoton: niciodată mai multe bilete).
MAX_KNOWN_SIZE = {
    "C_6_5_3.txt": 4, "C_6_5_4.txt": 5, "C_6_6_3.txt": 1, "C_6_6_4.txt": 1,
    "C_6_6_5.txt": 1, "C_7_5_3.txt": 5, "C_7_5_4.txt": 9, "C_7_6_3.txt": 4,
    "C_7_6_4.txt": 5, "C_7_6_5.txt": 6, "C_8_5_3.txt": 8, "C_8_5_4.txt": 20,
    "C_8_6_3.txt": 4, "C_8_6_4.txt": 7, "C_8_6_5.txt": 12, "C_9_5_3.txt": 12,
    "C_9_5_4.txt": 30, "C_9_6_3.txt": 7, "C_9_6_4.txt": 12, "C_9_6_5.txt": 30,
    "C_10_5_3.txt": 17, "C_10_5_4.txt": 51, "C_10_6_3.txt": 10, "C_10_6_4.txt": 20,
    "C_10_6_5.txt": 50, "C_11_5_3.txt": 20, "C_11_5_4.txt": 66, "C_11_6_3.txt": 11,
    "C_11_6_4.txt": 34, "C_11_6_5.txt": 100, "C_12_5_3.txt": 30, "C_12_5_4.txt": 113,
    "C_12_6_3.txt": 15, "C_12_6_4.txt": 41, "C_12_6_5.txt": 132, "C_13_5_3.txt": 37,
    "C_13_5_4.txt": 163, "C_13_6_3.txt": 21, "C_13_6_4.txt": 67, "C_13_6_5.txt": 245,
    "C_14_5_3.txt": 45, "C_14_5_4.txt": 238, "C_14_6_3.txt": 25, "C_14_6_4.txt": 96,
    "C_14_6_5.txt": 417, "C_15_5_3.txt": 59, "C_15_5_4.txt": 312, "C_15_6_3.txt": 33,
    "C_15_6_4.txt": 142, "C_15_6_5.txt": 652, "C_16_5_3.txt": 74, "C_16_5_4.txt": 441,
    "C_16_6_3.txt": 44, "C_16_6_4.txt": 194, "C_16_6_5.txt": 939,
}


def _schonheim(v: int, k: int, t: int) -> int:
    """Limita inferioară Schönheim pentru C(v,k,t) — riguroasă matematic
    (verificată aici pe sisteme Steiner cunoscute: C(13,3,2)=26, C(14,4,3)=91,
    egalitate exactă). Niciun covering design real nu poate avea mai puține
    bilete decât atât."""
    def rec(v: int, k: int, t: int) -> int:
        if t == 1:
            return ceil(v / k)
        return ceil(v / k * rec(v - 1, k - 1, t - 1))
    return rec(v, k, t)


def _parse(name: str) -> tuple[int, int, int]:
    v, pick, g = name.replace("C_", "").replace(".txt", "").split("_")
    return int(v), int(pick), int(g)


def _coverage(blocks: list[list[int]], v: int, g: int) -> float:
    """Acoperire exactă: câte g-submulțimi din 1..v sunt într-un bloc."""
    need = set(itertools.combinations(range(1, v + 1), g))
    for b in blocks:
        for s in itertools.combinations(sorted(b), g):
            need.discard(s)
    return 100.0 * (1 - len(need) / max(1, comb(v, g)))


def test_designs_exist():
    assert DESIGNS, "niciun design în covering_designs/"


def test_every_ui_geometry_has_a_design():
    """Calea implicită (max_variants=0 → lajolla) nu are voie să cadă pe ILP
    pentru nicio geometrie din UI (pool 6-16, pick 5/6, g 3..pick-1).

    #93 a sărit C(6,6,*) (`v in range(pick+1, 17)`). 6/49 pool 6 rămânea pe
    ILP — exact sursa de nedeterminism pe care o închidea setul.
    """
    have = {p.name for p in DESIGNS}
    missing = []
    for v in range(6, 17):
        for pick in (5, 6):
            if v < pick:
                continue
            for g in range(3, pick):
                name = f"C_{v}_{pick}_{g}.txt"
                if name not in have:
                    missing.append(name)
    assert missing == [], f"designuri lipsă (cad pe ILP): {missing}"


def test_pool_equals_pick_is_one_ticket():
    """C(6,6,g) = un singur bilet (tot pool-ul)."""
    for g in (3, 4, 5):
        w, cov = generate_wheel("lajolla", list(range(1, 7)), 6, g, 0, None)
        assert cov == 100.0
        assert len(w) == 1
        assert sorted(w[0]) == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("path", DESIGNS, ids=lambda p: p.name)
def test_design_is_wellformed_and_complete(path: Path):
    v, pick, g = _parse(path.name)
    blocks = [[int(x) for x in ln.split()]
              for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert blocks, f"{path.name} gol"
    for b in blocks:
        assert len(b) == pick, f"{path.name}: bloc de {len(b)} numere, nu {pick}"
        assert len(set(b)) == pick, f"{path.name}: indici duplicați în {b}"
        assert all(1 <= x <= v for x in b), f"{path.name}: index în afara 1..{v}: {b}"
    # ASTA e proprietatea care contează: garanția e chiar 100%, nu aproape
    assert _coverage(blocks, v, g) == 100.0, f"{path.name}: acoperire incompletă"


@pytest.mark.parametrize("path", DESIGNS, ids=lambda p: p.name)
def test_design_is_at_least_the_mathematical_minimum(path: Path):
    """Niciun cover NU poate avea mai puține bilete decât limita Schönheim —
    dacă testul ăsta pică, e un bug de generare (acoperire falsă), nu un
    record combinatoric."""
    v, pick, g = _parse(path.name)
    n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    lb = 1 if v == pick else _schonheim(v, pick, g)
    assert n >= lb, f"{path.name}: {n} bilete < limita Schönheim {lb} — impposibil"


@pytest.mark.parametrize("path", DESIGNS, ids=lambda p: p.name)
def test_design_does_not_regress_past_known_best(path: Path):
    """Lanț monoton (regula de aur wheeling): niciodată mai multe bilete decât
    ce am obținut deja prin optimizare. Actualizează `MAX_KNOWN_SIZE` DOAR când
    un rulaj nou găsește ceva mai mic."""
    n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    cap = MAX_KNOWN_SIZE.get(path.name)
    assert cap is not None, f"{path.name}: fișier nou, fără plafon în MAX_KNOWN_SIZE"
    assert n <= cap, f"{path.name}: {n} bilete > plafonul cunoscut {cap} — regresie"


@pytest.mark.parametrize("path", DESIGNS, ids=lambda p: p.name)
def test_loader_accepts_the_design(path: Path):
    """`_load_lajolla` validează la citire — un design pe care el îl respinge e inutil."""
    v, pick, g = _parse(path.name)
    assert _load_lajolla(v, pick, g) is not None


@pytest.mark.parametrize("v,pick,g", [(12, 5, 3), (12, 6, 3), (12, 6, 4), (10, 5, 4)])
def test_wheel_is_deterministic_across_runs(v, pick, g):
    """Regresia pe care o blochează: același apel, de două ori, același rezultat.

    Fără design pe disc, ILP-ul re-rezolvă pe ceas și poate întoarce altceva.
    """
    pool = list(range(1, v + 1))
    scores = {n: float(v - n) for n in pool}  # scoruri ca în producție
    a, cov_a = generate_wheel("lajolla", pool=pool, pick=pick, guarantee=g,
                              max_variants=0, scores=scores)
    b, cov_b = generate_wheel("lajolla", pool=pool, pick=pick, guarantee=g,
                              max_variants=0, scores=scores)
    assert a == b, f"C({v},{pick},{g}): două apeluri, două rezultate"
    assert cov_a == cov_b == 100.0
