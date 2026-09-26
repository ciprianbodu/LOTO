"""Nicio metodă din registry nu are voie să fie un filtru structural deghizat.

Un filtru (paritate, sume, decade, poziție) nu prezice un număr: împarte
universul în câteva clase și dă aceeași valoare tuturor membrilor unei clase.
Interdicția e o cerință a utilizatorului (AGENTS.md §13 P3), iar o metodă nouă
scrisă neatent o poate încălca fără ca nimeni să observe — numele și docstring-ul
spun altceva decât face codul. Testul măsoară comportamentul, nu intenția:

  levels     — un filtru pur dă 2-3 nivele distincte de scor pe tot universul;
  top_mass   — un filtru lasă un platou mare de numere la scor maxim;
  class_R²   — cât din varianța scorului se explică prin apartenența la o clasă
               structurală; ≈ 1 înseamnă că metoda ESTE acea clasă;
  run        — pool-ul top-12 ieșit bloc consecutiv arată un scorer degenerat pe
               axa valorilor (1,2,3…), nu semnal.

Pragurile sunt largi cu bună știință: prind un filtru, nu o metodă care doar
corelează slab cu o clasă (`neighbor_adjacent`, care scorează vecinii ±1/±2 ai
ultimei extrageri, urcă legitim până pe la R² = 0,5 pe decade la 5/40).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.benchmark.methods import METHODS, call_method

HISTORY = 700  # suficient pentru ca fiecare metodă să iasă din ramurile de start
MAX_NUM = 49
COLS = [f"n{i}" for i in range(1, 7)]

MIN_LEVELS = 5  # sub asta, scorul e o scară de clase, nu o ordonare
MAX_TOP_MASS = MAX_NUM // 4  # platou de maxim pe un sfert din univers
MAX_CLASS_R2 = 0.85  # scorul explicat aproape complet de o clasă structurală
MAX_RUN = 8  # bloc consecutiv în pool-ul top-12


@pytest.fixture(scope="module")
def draws() -> np.ndarray:
    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(HISTORY)
    return df[COLS].to_numpy(dtype=np.int64)


def _structural_classes(max_num: int) -> dict[str, np.ndarray]:
    n = np.arange(1, max_num + 1)
    primes = np.array(
        [p for p in range(2, max_num + 1) if all(p % d for d in range(2, int(p**0.5) + 1))]
    )
    return {
        "paritate": n % 2,
        "decada": (n - 1) // 10,
        "prim": np.isin(n, primes).astype(int),
        "mod3": n % 3,
        "mod5": n % 5,
        "jumatate": (n > max_num // 2).astype(int),
    }


def _class_r2(vec: np.ndarray, labels: np.ndarray) -> float:
    """R² al scorului explicat prin media clasei (varianță între clase / totală)."""
    total = float(((vec - vec.mean()) ** 2).sum())
    if total <= 1e-12:
        return 1.0  # scor constant: „explicat" perfect de orice clasă
    resid = sum(
        float(((vec[labels == lab] - vec[labels == lab].mean()) ** 2).sum())
        for lab in np.unique(labels)
    )
    return 1.0 - resid / total


def _longest_run(nums: list[int]) -> int:
    best = run = 1
    for a, b in zip(sorted(nums), sorted(nums)[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


@pytest.mark.parametrize("name", sorted(METHODS))
def test_method_is_not_a_structural_filter(name: str, draws: np.ndarray) -> None:
    scores, _ = call_method(name, draws, MAX_NUM)
    vec = np.array([scores.get(i + 1, 0.0) for i in range(MAX_NUM)], dtype=np.float64)
    rounded = np.round(vec, 9)

    levels = len(np.unique(rounded))
    assert levels >= MIN_LEVELS, (
        f"{name}: doar {levels} nivele distincte de scor — o scară de clase, nu o "
        "ordonare a numerelor; pool-ul ar fi decis de tie-break, nu de metodă"
    )

    top_mass = int((rounded == rounded.max()).sum())
    assert top_mass <= MAX_TOP_MASS, (
        f"{name}: {top_mass} numere la scor maxim — platou de filtru, nu vârf de scorer"
    )

    for cls, labels in _structural_classes(MAX_NUM).items():
        r2 = _class_r2(vec, labels)
        assert r2 < MAX_CLASS_R2, (
            f"{name}: {r2:.3f} din varianța scorului se explică prin clasa "
            f"„{cls}” — metoda e de fapt acel filtru"
        )

    top12 = [int(x) + 1 for x in np.argsort(-vec, kind="stable")[:12]]
    assert _longest_run(top12) < MAX_RUN, (
        f"{name}: pool-ul top-12 conține un bloc consecutiv de {_longest_run(top12)} "
        "numere — scorer degenerat pe axa valorilor"
    )
