"""Designurile precalculate din `covering_designs/` — validitate și determinism.

De ce există fișierele: `_ilp_cover_positions` rulează cu `time_limit` și NU
demonstrează niciodată optimalitatea în bugetul dat — arde tot ceasul și întoarce
cel mai bun incumbent găsit. Deci rezultatul depinde de cât de încărcată e mașina
în acele secunde. Măsurat pe main, ACELAȘI cod și aceleași date, două rulări
consecutive: 5/40 pool 12 / g3 → 33 de bilete, apoi 30; joker pool 12 / g3 → 32,
apoi 30. Un cover e o constantă matematică (nu depinde de pool, de scoruri sau de
dată), deci se calculează o dată și se citește de pe disc.
"""
import itertools
from math import comb
from pathlib import Path

import pytest

from wheeling_methods import _load_lajolla, generate_wheel

DESIGNS = sorted(Path("covering_designs").glob("C_*_*_*.txt"))


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
