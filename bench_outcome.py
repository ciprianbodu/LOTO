"""Durable one-shot completion markers for UI-launched benchmarks."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from runtime_paths import RUNTIME_ROOT

RUN_ID_ENV = "LOTO_BENCH_RUN_ID"


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
        raise ValueError("invalid benchmark run id")
    return RUNTIME_ROOT / "bench_outcomes" / f"{run_id}.json"


def record_exit(run_id: str, pid: int, exit_code: int) -> None:
    """Publish only after main returns; a killed process leaves no marker."""
    marker = _path(run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as out:
            json.dump({"run_id": run_id, "pid": int(pid), "exit_code": int(exit_code), "finished_at": time.time()}, out)
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
