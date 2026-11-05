"""Benchmarkul GPU vs CPU din 2026-11-01 trebuie să ruleze și fără GPU (doar
CPU, gpu_available=false) și să producă histograma exactă a enumerării."""

import importlib.util
from math import comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "gpu_vs_cpu_20261101", ROOT / "scripts" / "analysis" / "gpu_vs_cpu_2026-11-01.py"
)
gb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gb)


def test_popcount_matches_python():
    x = np.array([0, 1, 255, 1 << 20, (1 << 32) - 1], dtype=np.uint32)
    assert list(gb._popcount(x)) == [bin(int(v)).count("1") for v in x]


def test_hit_counts_v16_matches_reference():
    rng = np.random.default_rng(2)
    masks = [int(sum(1 << int(v) for v in rng.choice(16, 6, replace=False))) for _ in range(20)]
    h = gb._hit_counts(np, 16, masks)
    # referință brute-force pe toate submulțimile
    ref = np.zeros((17, 17), dtype=np.int64)
    for m in range(1, 1 << 16):
        best = max(((m & t).bit_count() for t in masks), default=0)
        ref[m.bit_count(), best] += 1
    assert int(h.sum()) == (1 << 16)  # include și submulțimea vidă (mask 0)
    assert int(h[6, 6]) == int(ref[6, 6])


def test_enumerate_histogram_is_exact():
    # verificare rapidă pe o geometrie mică: C(10,3) extrageri vs 5 bilete
    from itertools import combinations

    rng = np.random.default_rng(5)
    tickets = [tuple(sorted(rng.choice(10, 3, replace=False))) for _ in range(5)]
    tmasks = np.array([sum(1 << int(t) for t in tk) for tk in tickets], dtype=np.int64)
    counts = np.zeros(4, dtype=np.int64)
    for c in combinations(range(10), 3):
        dm = sum(1 << i for i in c)
        mx = max((dm & tm).bit_count() for tm in tmasks)
        counts[mx] += 1
    assert counts.sum() == comb(10, 3)
    assert counts[3] == 5  # cele 5 bilete însele
