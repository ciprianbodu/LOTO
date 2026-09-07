"""HwSampler trebuie sa masoare consumul PROPRIULUI proces, nu al masinii
intregi — Re-Bench ruleaza zeci de folds ca procese concurente pe un
ProcessPoolExecutor, deci o citire system-wide ar contamina fiecare rand din
folds.csv cu ce fac TOATE metodele in acelasi moment (verificare globala
2026-09-07)."""
from __future__ import annotations

import time
from types import SimpleNamespace

from loto_enterprise.benchmark.hw_sampler import HwSampler


class _FakeProcess:
    def __init__(self, cpu_pct: float, rss_bytes: int):
        self._cpu_pct = cpu_pct
        self._rss_bytes = rss_bytes

    def cpu_percent(self, interval=None):
        return self._cpu_pct

    def memory_info(self):
        return SimpleNamespace(rss=self._rss_bytes)


def test_sampler_uses_per_process_readings_not_system_wide():
    sampler = HwSampler(interval=0.01)
    assert sampler._have_psutil, "test presupune psutil disponibil in venv-ul de test"

    # Proces: 42% CPU, 1 GiB RSS. Sistem: valori "otravite" — daca sampler-ul
    # le-ar folosi din greseala, testul le-ar prinde in loc de cele per-proces.
    sampler._proc = _FakeProcess(cpu_pct=42.0, rss_bytes=1 * 1024**3)

    class _PoisonPsutil:
        @staticmethod
        def cpu_percent(interval=None):
            return 999.0

        @staticmethod
        def virtual_memory():
            return SimpleNamespace(total=999 * 1024**3, available=0)

    sampler._psutil = _PoisonPsutil()

    sampler.start()
    time.sleep(0.05)
    snap = sampler.stop().snapshot()

    assert snap.samples > 0
    assert snap.cpu_pct_peak == 42.0
    assert snap.cpu_pct_avg == 42.0
    assert abs(snap.ram_gb_peak - 1.0) < 0.01
    # Nimic din valorile "otravite" (999) nu a scapat in snapshot.
    assert snap.cpu_pct_peak != 999.0
    assert snap.ram_gb_peak < 900
