"""Hardware introspection — CPU/RAM/GPU/VRAM info."""

from __future__ import annotations

import logging
import platform
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_cpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
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


def get_ram_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {}
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


def get_gpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"cuda_available": False, "devices": []}
    try:
        import torch
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            info["cuda_version"] = torch.version.cuda
            info["device_count"] = torch.cuda.device_count()
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                dev: Dict[str, Any] = {
                    "index": i,
                    "name": torch.cuda.get_device_name(i),
                    "total_vram_gb": round(props.total_memory / (1024**3), 2),
                    "compute_capability": f"{props.major}.{props.minor}",
                    "multi_processor_count": props.multi_processor_count,
                }
                try:
                    free_b, total_b = torch.cuda.mem_get_info(i)
                    dev["free_vram_gb"] = round(free_b / (1024**3), 2)
                    dev["used_vram_gb"] = round((total_b - free_b) / (1024**3), 2)
                except Exception:
                    pass
                info["devices"].append(dev)
    except Exception as exc:
        info["error"] = str(exc)
    return info


def reset_gpu_peak() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.empty_cache()
    except Exception:
        pass


def peak_vram_mb() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / (1024**2), 2)
    except Exception:
        pass
    return 0.0


def snapshot() -> Dict[str, Any]:
    return {
        "cpu": get_cpu_info(),
        "ram": get_ram_info(),
        "gpu": get_gpu_info(),
    }


def format_snapshot(snap: Dict[str, Any]) -> str:
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
    gpu = snap.get("gpu", {})
    if gpu.get("cuda_available"):
        out.append(
            f"  Torch    : {gpu.get('torch_version', '?')} | CUDA {gpu.get('cuda_version', '?')}"
        )
        for d in gpu.get("devices", []):
            out.append(
                f"  GPU [{d['index']}] : {d['name']} | "
                f"compute {d.get('compute_capability', '?')} | "
                f"{d.get('multi_processor_count', '?')} SMs | "
                f"VRAM {d.get('used_vram_gb', '?')} / {d['total_vram_gb']} GB used "
                f"({d.get('free_vram_gb', '?')} GB free)"
            )
    else:
        out.append("  GPU      : CUDA not available (CPU-only run)")
    out.append("─" * 70)
    return "\n".join(out)
