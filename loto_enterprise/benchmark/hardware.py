"""Hardware introspection — CPU/RAM info (GPU eliminat)."""

from __future__ import annotations

import logging
import platform
from typing import Any

logger = logging.getLogger(__name__)


def get_cpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python": platform.python_version(),
    }
    try:
        import psutil
        info["physical_cores"] = psutil.cpu_count(logical=False)
        info["logical_cores"] = psutil.cpu_count(logical=True)
        freq = psutil.cpu_freq()
        if freq:
            info["max_freq_mhz"] = round(freq.max, 1)
            info["cur_freq_mhz"] = round(freq.current, 1)
        info["cpu_percent_now"] = psutil.cpu_percent(interval=0.2)
    except Exception as exc:
        info["psutil_error"] = str(exc)
    return info


def get_ram_info() -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["total_gb"] = round(vm.total / (1024**3), 2)
        info["available_gb"] = round(vm.available / (1024**3), 2)
        info["used_gb"] = round(vm.used / (1024**3), 2)
        info["percent"] = vm.percent
    except Exception as exc:
        info["error"] = str(exc)
    return info


def snapshot() -> dict[str, Any]:
    return {
        "cpu": get_cpu_info(),
        "ram": get_ram_info(),
    }


def format_snapshot(snap: dict[str, Any]) -> str:
    out = []
    out.append("─" * 70)
    out.append("HARDWARE INFO")
    out.append("─" * 70)
    cpu = snap.get("cpu", {})
    out.append(f"  Platform : {cpu.get('platform', 'n/a')}")
    out.append(f"  CPU      : {cpu.get('processor', 'n/a')}")
    out.append(
        f"  Cores    : {cpu.get('physical_cores', '?')} physical / "
        f"{cpu.get('logical_cores', '?')} logical "
        f"@ {cpu.get('max_freq_mhz', '?')} MHz max"
    )
    out.append(f"  CPU load : {cpu.get('cpu_percent_now', '?')}%")
    ram = snap.get("ram", {})
    out.append(
        f"  RAM      : {ram.get('used_gb', '?')} / {ram.get('total_gb', '?')} GB "
        f"used ({ram.get('percent', '?')}%); {ram.get('available_gb', '?')} GB free"
    )
    out.append("─" * 70)
    return "\n".join(out)
