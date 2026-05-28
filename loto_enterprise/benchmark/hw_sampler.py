"""Background hardware sampler — runs in a thread and records peaks.

Tracks per-phase peaks of:
    * CPU %    — psutil.cpu_percent
    * RAM      — psutil.Process(self).memory_info().rss
    * GPU %    — pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
    * VRAM     — torch.cuda.max_memory_allocated() (torch's own counter, in MB)

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
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HwSnapshot:
    cpu_pct_peak: float = 0.0
    cpu_pct_avg: float = 0.0
    ram_gb_peak: float = 0.0
    gpu_pct_peak: float = 0.0
    gpu_pct_avg: float = 0.0
    vram_mb_peak: float = 0.0
    samples: int = 0
    duration_sec: float = 0.0


class HwSampler:
    """Thread-based hardware sampler. Cheap (~0.1s tick)."""

    def __init__(self, interval: float = 0.1, gpu_index: int = 0):
        self.interval = interval
        self.gpu_index = gpu_index
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cpu_peak = 0.0
        self._cpu_total = 0.0
        self._ram_peak = 0.0
        self._gpu_peak = 0.0
        self._gpu_total = 0.0
        self._samples = 0
        self._t0 = 0.0
        self._t1 = 0.0
        self._proc = None
        self._nvml_handle = None
        self._have_psutil = False
        self._have_nvml = False
        try:
            import psutil
            self._psutil = psutil
            self._proc = psutil.Process(os.getpid())
            self._have_psutil = True
            # Prime cpu_percent so the first sample isn't 0
            self._proc.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None)
        except Exception as exc:
            logger.debug("psutil unavailable: %s", exc)
        try:
            import pynvml
            self._pynvml = pynvml
            pynvml.nvmlInit()
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
            self._have_nvml = True
        except Exception as exc:
            logger.debug("pynvml unavailable: %s", exc)

    def start(self) -> "HwSampler":
        self._stop.clear()
        self._cpu_peak = 0.0
        self._cpu_total = 0.0
        self._ram_peak = 0.0
        self._gpu_peak = 0.0
        self._gpu_total = 0.0
        self._samples = 0
        self._t0 = time.perf_counter()
        # Reset torch's own peak counter
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, name="hw-sampler", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._have_psutil:
                    cpu = self._psutil.cpu_percent(interval=None)  # system-wide %
                    if cpu > self._cpu_peak:
                        self._cpu_peak = cpu
                    self._cpu_total += cpu
                    # RSS al PROCESULUI curent (cât consumă efectiv bench-ul),
                    # conform docstring-ului — nu RAM-ul system-wide.
                    if self._proc is not None:
                        ram_gb = self._proc.memory_info().rss / (1024**3)
                        if ram_gb > self._ram_peak:
                            self._ram_peak = ram_gb
                if self._have_nvml and self._nvml_handle is not None:
                    util = self._pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
                    if util.gpu > self._gpu_peak:
                        self._gpu_peak = float(util.gpu)
                    self._gpu_total += float(util.gpu)
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
        # VRAM peak from torch (more accurate for the process)
        vram_mb = 0.0
        try:
            import torch
            if torch.cuda.is_available():
                vram_mb = torch.cuda.max_memory_allocated() / (1024**2)
        except Exception:
            pass
        n = max(self._samples, 1)
        return HwSnapshot(
            cpu_pct_peak=round(self._cpu_peak, 1),
            cpu_pct_avg=round(self._cpu_total / n, 1),
            ram_gb_peak=round(self._ram_peak, 2),
            gpu_pct_peak=round(self._gpu_peak, 1),
            gpu_pct_avg=round(self._gpu_total / n, 1),
            vram_mb_peak=round(vram_mb, 1),
            samples=self._samples,
            duration_sec=round(self._t1 - self._t0, 3),
        )

    def close(self) -> None:
        try:
            if self._have_nvml:
                self._pynvml.nvmlShutdown()
        except Exception:
            pass
