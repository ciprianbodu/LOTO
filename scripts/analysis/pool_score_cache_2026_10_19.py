"""Causal 6/49 score matrices for research; never writes application state.

Scores are computed once per distinct target day from the complete strict
prefix. This module does not select pools, count hits or select a winner.
Only per-method NPZ files under the caller's explicit cache directory are
written. The usual destination is RESEARCH_CACHE_ROOT.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Mapping
from functools import lru_cache
from numbers import Integral
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Set these before numpy/scipy are imported by either this module or METHODS.
for _variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_variable] = "1"

import numpy as np
import pandas as pd

from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.score_validation import has_usable_score_variance

WARMUP = 256
RESEARCH_CACHE_ROOT = ROOT / "bench_results" / "pool649_research_20261019"
CACHE_VERSION = 1
_NUMBERS = tuple(range(1, 50))
_DATA_CACHE: dict[tuple[str, str, int], dict] = {}


def load_data(path, *, start: int = WARMUP) -> dict:
    """Validated, read-only arrays for all targets with >= start earlier rows.

    dates: ISO date strings per row; groups: contiguous day ids; prior: the
    exclusive strict-day training prefix for each row; targets: eligible row
    indices. frequency_past holds the real canonical frequency scores for
    those strict prefixes, including its exponential weighting and scaling.
    source_sha hashes exact input bytes; input_sha hashes the canonical data.
    """
    path = Path(path).resolve()
    if isinstance(start, bool) or int(start) != start or start < 1:
        raise ValueError("start must be a positive integer")
    start = int(start)
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    key = (str(path), source_sha, start)
    if key in _DATA_CACHE:
        return _DATA_CACHE[key]
    frame = chronological_history(pd.read_csv(io.BytesIO(raw)))
    if "date" not in frame:
        raise ValueError("research requires a date column for strict-day cutoffs")
    draws, valid = valid_draw_matrix(
        frame, [f"n{i}" for i in range(1, 7)], draw_n=6, max_num=49
    )
    if not valid.all():
        bad = np.flatnonzero(~valid).tolist()
        raise ValueError(f"invalid 6/49 draw rows: {bad[:10]}")
    dates = history_dates(frame).dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
    prior = np.asarray(training_cutoffs(frame), dtype=np.int64)
    groups = np.zeros(len(draws), dtype=np.int64)
    if len(draws) > 1:
        groups[1:] = np.cumsum(dates[1:] != dates[:-1])
    targets = np.flatnonzero(prior >= start).astype(np.int64)
    frequency_past = np.empty((len(targets), 49), dtype=np.float64)
    last_cutoff, last_values = None, None
    for row, target in enumerate(targets):
        cutoff = int(prior[target])
        if cutoff != last_cutoff:
            raw_frequency, _ = call_method(
                "frequency", draws[:cutoff], 49, history_cutoffs=prior[:cutoff]
            )
            last_values, reason = _complete_scores(raw_frequency)
            if reason:
                raise RuntimeError(f"frequency fallback unusable: {reason}")
            last_cutoff = cutoff
        frequency_past[row] = last_values
    digest = hashlib.sha256()
    digest.update(draws.astype("<i8", copy=False).tobytes())
    digest.update("\n".join(dates.tolist()).encode("ascii"))
    digest.update(prior.astype("<i8", copy=False).tobytes())
    data = {
        "path": str(path),
        "dates": dates,
        "draws": draws,
        "groups": groups,
        "prior": prior,
        "targets": targets,
        "frequency_past": frequency_past,
        "source_sha": source_sha,
        "input_sha": digest.hexdigest(),
        "start": start,
    }
    for value in data.values():
        if isinstance(value, np.ndarray):
            value.flags.writeable = False
    if len(_DATA_CACHE) >= 8:
        _DATA_CACHE.pop(next(iter(_DATA_CACHE)))
    _DATA_CACHE[key] = data
    return data


@lru_cache(maxsize=1)
def _code_source_sha() -> str:
    """Code identity supplements the orchestrator's frozen manifest identity."""
    paths = [Path(__file__).resolve()]
    paths.extend(sorted((ROOT / "loto_enterprise").rglob("*.py")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _complete_scores(raw) -> tuple[np.ndarray | None, str | None]:
    if not isinstance(raw, Mapping):
        return None, "not_a_mapping"
    if (
        len(raw) != 49
        or set(raw) != set(_NUMBERS)
        or any(
            isinstance(n, (bool, np.bool_)) or not isinstance(n, Integral) for n in raw
        )
    ):
        return None, "incomplete_scores"
    try:
        values = np.asarray([float(raw[n]) for n in _NUMBERS], dtype=np.float64)
    except TypeError, ValueError, OverflowError:
        return None, "non_numeric_scores"
    if not np.isfinite(values).all():
        return None, "nonfinite_scores"
    if not has_usable_score_variance(raw):
        return None, "flat_scores"
    return values, None


def _cache_file(cache_dir, path, name: str, metadata: dict) -> Path:
    # A caller can use a temporary cache for tests; application paths are never
    # inferred from runtime settings. File names cannot collide with CSV/JSON.
    directory = Path(cache_dir).resolve()
    token = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()[
        :24
    ]
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-")
    if not safe_name or safe_name != name:
        raise ValueError("invalid method name for score cache")
    stem = Path(path).stem
    return directory / f"scores_{stem}_{safe_name}_{token}.npz"


def _read_cache(path: Path, metadata: dict, shape: tuple[int, int]):
    try:
        with np.load(path, allow_pickle=False) as stored:
            if json.loads(str(stored["metadata"].item())) != metadata:
                return None
            scores = stored["scores"]
            invalid = stored["invalid"]
            elapsed = float(stored["elapsed"].item())
            errors = json.loads(str(stored["errors"].item()))
        if (
            scores.shape != shape
            or scores.dtype != np.float64
            or invalid.shape != (shape[0],)
            or invalid.dtype != np.bool_
            or not np.isfinite(scores).all()
            or elapsed < 0
            or not np.isfinite(elapsed)
            or not isinstance(errors, dict)
            or any(not isinstance(v, int) or v < 0 for v in errors.values())
            or sum(errors.values()) != int(invalid.sum())
        ):
            return None
        return scores, invalid, elapsed, errors
    except OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile:
        return None


def _write_cache(path: Path, metadata: dict, scores, invalid, elapsed, errors):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(
                stream,
                metadata=np.asarray(json.dumps(metadata, sort_keys=True)),
                scores=scores,
                invalid=invalid,
                elapsed=np.asarray(elapsed),
                errors=np.asarray(json.dumps(errors, sort_keys=True)),
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def evaluate_method(args: dict) -> tuple[str, np.ndarray, np.ndarray, float, dict]:
    """Return (name, scores[T,49], invalid[T], elapsed_seconds, error_counts).

    Required args: path, name, cache_dir, identity; optional start (default256).
    Rows follow load_data(path,start=...)['targets']; score column j is ball j+1.
    Invalid outputs are visible in invalid and errors, then replaced by the
    real canonical frequency scorer. elapsed records generation time even on
    a cache hit. This function starts no process pool and counts no hits.
    """
    started = time.perf_counter()
    name = str(args["name"])
    data = load_data(args["path"], start=args.get("start", WARMUP))
    metadata = {
        "version": CACHE_VERSION,
        "identity": args["identity"],
        "method": name,
        "source_sha": data["source_sha"],
        "input_sha": data["input_sha"],
        "code_source_sha": _code_source_sha(),
        "start": data["start"],
        "python": list(sys.version_info[:3]),
        "numpy": np.__version__,
    }
    cache_path = _cache_file(args["cache_dir"], data["path"], name, metadata)
    shape = (len(data["targets"]), 49)
    cached = _read_cache(cache_path, metadata, shape)
    if cached is not None:
        return name, *cached
    scores = np.empty(shape, dtype=np.float64)
    invalid = np.zeros(shape[0], dtype=bool)
    errors = Counter()
    prior = data["prior"]
    previous_cutoff = None
    previous_values = None
    previous_invalid = False
    previous_reason = None
    for row, target in enumerate(data["targets"]):
        cutoff = int(prior[target])
        if cutoff == previous_cutoff:
            scores[row] = previous_values
            invalid[row] = previous_invalid
            if previous_reason:
                errors[previous_reason] += 1
            continue
        prefix = data["draws"][:cutoff]
        context = {"history_cutoffs": prior[:cutoff]}
        try:
            raw, _ = call_method(name, prefix, 49, **context)
            values, reason = _complete_scores(raw)
        except Exception as exc:
            values = None
            reason = f"exception:{type(exc).__name__}:{str(exc)[:180]}"
        if reason:
            invalid[row] = True
            errors[reason] += 1
            values = data["frequency_past"][row]
        scores[row] = values
        previous_cutoff, previous_values = cutoff, values
        previous_invalid, previous_reason = bool(invalid[row]), reason
    elapsed = time.perf_counter() - started
    # Refuse to attach a cache to data that changed during the calculation.
    if (
        hashlib.sha256(Path(data["path"]).read_bytes()).hexdigest()
        != data["source_sha"]
    ):
        raise RuntimeError("input history changed during score computation")
    errors = dict(errors)
    _write_cache(cache_path, metadata, scores, invalid, elapsed, errors)
    return name, scores, invalid, elapsed, errors
