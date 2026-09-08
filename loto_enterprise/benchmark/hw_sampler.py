"""Background hardware sampler — runs in a thread and records peaks.

Tracks per-phase peaks of:
    * CPU %    — psutil.Process(pid).cpu_percent — PROPRIU procesului, nu al
                 mașinii întregi
    * RAM      — psutil.Process(pid).memory_info().rss — RSS-ul propriu

Re-Bench paralelizează folds pe un ProcessPoolExecutor (zeci de procese
concurente, ~75% din nuclee) — un sampler care ar citi CPU%/RAM la nivel de
MAȘINĂ (nu de proces) ar contamina fiecare rând din folds.csv cu ce fac TOATE
metodele concurente în același moment, nu costul metodei măsurate. Cu
sampler-ul per-proces, fiecare fold raportează exclusiv propriul consum.

(GPU/VRAM sampling a fost eliminat odată cu tot suportul GPU. Câmpurile
gpu_pct_* / vram_mb_peak rămân în snapshot, mereu 0, pt compatibilitate cu
schema folds.csv.)

Usage:
    sampler = HwSampler(interval=0.1).start()
    ... run model ...
    snap = sampler.stop().snapshot()
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HwSnapshot:
    cpu_pct_peak: float = 0.0
    cpu_pct_avg: float = 0.0
    ram_gb_peak: float = 0.0
    gpu_pct_peak: float = 0.0  # mereu 0 (GPU eliminat)
    gpu_pct_avg: float = 0.0  # mereu 0 (GPU eliminat)
    vram_mb_peak: float = 0.0  # mereu 0 (GPU eliminat)
    samples: int = 0
    duration_sec: float = 0.0


class HwSampler:
    """Thread-based hardware sampler (CPU/RAM). Cheap (~0.1s tick)."""

    def __init__(self, interval: float = 0.1, gpu_index: int = 0):
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cpu_peak = 0.0
        self._cpu_total = 0.0
        self._ram_peak = 0.0
        self._samples = 0
        self._t0 = 0.0
        self._t1 = 0.0
        self._proc = None
        self._have_psutil = False
        try:
            import psutil

            self._psutil = psutil
            self._proc = psutil.Process(os.getpid())
            self._have_psutil = True
            # Prime cpu_percent so the first sample isn't 0
            self._proc.cpu_percent(interval=None)
        except Exception as exc:
            logger.debug("psutil unavailable: %s", exc)

    def start(self) -> "HwSampler":
        self._stop.clear()
        self._cpu_peak = 0.0
        self._cpu_total = 0.0
        self._ram_peak = 0.0
        self._samples = 0
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(
            target=self._run, name="hw-sampler", daemon=True
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._have_psutil and self._proc is not None:
                    # PER-PROCES (nu system-wide): psutil.Process.cpu_percent()
                    # poate depăși 100% dacă procesul folosește mai multe nuclee
                    # (100% = un nucleu întreg) — corect pentru un fold single-
                    # thread BLAS (§8: "BLAS single-thread per proces"), unde
                    # valoarea reflectă exact cât din ACEL nucleu a consumat.
                    cpu = self._proc.cpu_percent(interval=None)
                    if cpu > self._cpu_peak:
                        self._cpu_peak = cpu
                    self._cpu_total += cpu
                    ram_gb = self._proc.memory_info().rss / (1024**3)
                    if ram_gb > self._ram_peak:
                        self._ram_peak = ram_gb
                self._samples += 1
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self) -> "HwSampler":
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._t1 = time.perf_counter()
        return self

    def snapshot(self) -> HwSnapshot:
        n = max(self._samples, 1)
        return HwSnapshot(
            cpu_pct_peak=round(self._cpu_peak, 1),
            cpu_pct_avg=round(self._cpu_total / n, 1),
            ram_gb_peak=round(self._ram_peak, 2),
            samples=self._samples,
            duration_sec=round(self._t1 - self._t0, 3),
        )

    def close(self) -> None:
        return None
