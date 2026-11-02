"""Durable one-shot completion markers for UI-launched benchmarks."""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
from pathlib import Path

from runtime_paths import RUNTIME_ROOT

RUN_ID_ENV = "LOTO_BENCH_RUN_ID"
UI_PID_ENV = "LOTO_BENCH_UI_PID"


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
        raise ValueError("invalid benchmark run id")
    return RUNTIME_ROOT / "bench_outcomes" / f"{run_id}.json"


def save_context(run_id: str, config: str, pools: dict[str, int]) -> str:
    """Păstrează local datele și dimensiunile de la lansarea acestui bench."""
    path = _path(run_id).with_suffix(".context")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps({"config": config, "pools": pools}, sort_keys=True, separators=(",", ":"))
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    finally:
        tmp.unlink(missing_ok=True)


def load_context(run_id: str, digest: str) -> tuple[str, dict[str, int]] | None:
    """Acceptă numai snapshotul al cărui conținut corespunde markerului PID."""
    try:
        payload = _path(run_id).with_suffix(".context").read_text(encoding="utf-8")
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
            return None
        data = json.loads(payload)
        config, pools = data["config"], data["pools"]
        if not isinstance(config, str):
            return None
        if not isinstance(json.loads(config).get("datasets"), list) or not isinstance(pools, dict):
            return None
        if any(not isinstance(k, str) or type(v) is not int or v < 1 for k, v in pools.items()):
            return None
        return config, pools
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        return None


def clear_context(run_id: str) -> None:
    _path(run_id).with_suffix(".context").unlink(missing_ok=True)


def _launcher_pid(runtime_pid: int) -> int:
    """Resolve the process returned by Popen, including Windows venv launchers."""
    raw = os.environ.get(UI_PID_ENV, "")
    if not raw.isdigit() or int(raw) <= 0:
        return runtime_pid
    ui_pid = int(raw)
    try:
        import psutil

        current = psutil.Process(runtime_pid)
        seen = {runtime_pid}
        for _ in range(16):
            parent_pid = current.ppid()
            if parent_pid == ui_pid:
                return current.pid
            if parent_pid <= 0 or parent_pid in seen:
                break
            parent = psutil.Process(parent_pid)
            if parent.create_time() > current.create_time():
                break  # Recycled parent PID cannot establish ownership.
            seen.add(parent_pid)
            current = parent
    except (OSError, psutil.Error):
        pass
    return runtime_pid


def record_process_exit(run_id: str, exit_code: int) -> None:
    """Publish completion under the launcher's PID, preserving the runtime PID."""
    runtime_pid = os.getpid()
    record_exit(run_id, _launcher_pid(runtime_pid), exit_code, runtime_pid=runtime_pid)


def record_exit(run_id: str, pid: int, exit_code: int, *, runtime_pid: int | None = None) -> None:
    """Publish only after main returns; a killed process leaves no marker."""
    marker = _path(run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as out:
            data = {"run_id": run_id, "pid": int(pid), "exit_code": int(exit_code), "finished_at": time.time()}
            if runtime_pid is not None:
                data["runtime_pid"] = int(runtime_pid)
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)


def is_claimed(run_id: str) -> bool:
    return _path(run_id).with_suffix(".claimed").exists()


def mark_processed(run_id: str) -> None:
    """Retire a failed, cancelled or expired run without touching PID files."""
    claim = _path(run_id).with_suffix(".claimed")
    try:
        claim.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass


def peek_exit(run_id: str, pid: int, max_age: float = 600) -> int | None:
    """Check provenance and freshness without consuming the receipt."""
    marker = _path(run_id)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        code, finished = data.get("exit_code"), data.get("finished_at")
        age = time.time() - finished if type(finished) in (int, float) else -1
        if (data.get("run_id") != run_id or type(data.get("pid")) is not int
                or data["pid"] != pid or type(code) is not int or not 0 <= age <= max_age):
            return None
        if marker.with_suffix(".claimed").exists():
            return None
        return code
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return None


def claim_exit(run_id: str, pid: int) -> int | None:
    """Validate and claim one receipt exactly once, even across UI processes."""
    marker = _path(run_id)
    code = peek_exit(run_id, pid)
    if code is None:
        return None
    try:
        # O_EXCL is a persistent one-shot claim even when two UIs race.
        claim = marker.with_suffix(".claimed")
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        return None
    # On Windows other readers can temporarily hold the receipt open. The
    # exclusive claim is already decisive; cleanup failure must not lose success.
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return code
