"""Causal, geometry-signed score caches for the separate 5/40/Joker study.

Only explicit per-method NPZ files inside the dedicated TEMP cache are written.
No pools, hits, application state or production decisions are evaluated here.
Each distinct date sees its complete strictly earlier prefix, including all
six extracted balls for 5/40 and only the five first-urn balls for Joker.
"""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from numbers import Integral
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _key in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_key] = "1"

import numpy as np
import pandas as pd

from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON

CACHE_ROOT = Path(tempfile.gettempdir()) / "loto-pool-extension-20261019" / "cache"
CACHE_VERSION = 1
WARMUP = 256
CORE_EXPERTS = ("frequency", "markov_lag2", "ewma_hl30")
_DATA_CACHE: dict[tuple, dict] = {}


def _positive_integer(value, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be a positive integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _geometry(max_num, draw_n) -> tuple[int, int]:
    max_num = _positive_integer(max_num, "max_num")
    draw_n = _positive_integer(draw_n, "draw_n")
    if max_num < 2 or draw_n > max_num:
        raise ValueError("geometry requires 1 <= draw_n <= max_num and max_num >= 2")
    return max_num, draw_n


def _complete_scores(raw, max_num):
    if not isinstance(raw, Mapping):
        return None, "not_a_mapping"
    if (
        len(raw) != max_num
        or set(raw) != set(range(1, max_num + 1))
        or any(
            isinstance(n, (bool, np.bool_)) or not isinstance(n, Integral) for n in raw
        )
    ):
        return None, "incomplete_scores"
    try:
        values = np.asarray(
            [float(raw[n]) for n in range(1, max_num + 1)], dtype=np.float64
        )
    except TypeError, ValueError, OverflowError:
        return None, "non_numeric_scores"
    if not np.isfinite(values).all():
        return None, "nonfinite_scores"
    if np.ptp(values) <= SCORE_VARIANCE_EPSILON:
        return None, "flat_scores"
    return values, None


def _alignment_sha(dates, prior, targets) -> str:
    digest = hashlib.sha256(b"pool-extension-target-alignment-v1\0")
    digest.update(np.asarray(targets, dtype="<i8").tobytes())
    digest.update(np.asarray(prior[targets], dtype="<i8").tobytes())
    digest.update(json.dumps(dates[targets].tolist()).encode("ascii"))
    return digest.hexdigest()


def load_data(path, *, max_num, draw_n, start=WARMUP) -> dict:
    """Return stable chronological, read-only arrays with strict-day targets.

    frequency_past[T,max_num] contains actual canonical frequency output,
    including its weighting and normalization; it is never a tiebreak.
    input_sha covers the full geometry/history independently of start;
    aligned_target_sha256 additionally signs the exact retained row order.
    """
    max_num, draw_n = _geometry(max_num, draw_n)
    start = _positive_integer(start, "start")
    path = Path(path).resolve()
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    key = (str(path), source_sha, max_num, draw_n, start)
    if key in _DATA_CACHE:
        return _DATA_CACHE[key]
    frame = chronological_history(pd.read_csv(io.BytesIO(raw)))
    if "date" not in frame:
        raise ValueError("strict-day research requires a date column")
    draws, valid = valid_draw_matrix(
        frame,
        [f"n{i}" for i in range(1, draw_n + 1)],
        draw_n=draw_n,
        max_num=max_num,
    )
    if not valid.all():
        raise ValueError(f"invalid draw rows: {np.flatnonzero(~valid)[:10].tolist()}")
    dates = history_dates(frame).dt.strftime("%Y-%m-%d").to_numpy(dtype="U10")
    prior = np.asarray(training_cutoffs(frame), dtype=np.int64)
    groups = np.zeros(len(draws), dtype=np.int64)
    if len(draws) > 1:
        groups[1:] = np.cumsum(dates[1:] != dates[:-1])
    targets = np.flatnonzero(prior >= start).astype(np.int64)
    frequency = np.empty((len(targets), max_num), dtype=np.float64)
    last_cutoff, last_values = None, None
    for row, target in enumerate(targets):
        cutoff = int(prior[target])
        if cutoff != last_cutoff:
            scores, _ = call_method(
                "frequency",
                draws[:cutoff],
                max_num,
                history_cutoffs=prior[:cutoff],
            )
            last_values, reason = _complete_scores(scores, max_num)
            if reason:
                raise RuntimeError(f"canonical frequency fallback unusable: {reason}")
            last_cutoff = cutoff
        frequency[row] = last_values
    digest = hashlib.sha256(f"geometry:{max_num}:{draw_n}\0".encode("ascii"))
    digest.update(np.asarray(draws, dtype="<i8").tobytes())
    digest.update("\n".join(dates.tolist()).encode("ascii"))
    digest.update(np.asarray(prior, dtype="<i8").tobytes())
    data = {
        "path": str(path),
        "dates": dates,
        "draws": draws,
        "groups": groups,
        "prior": prior,
        "targets": targets,
        "frequency_past": frequency,
        "source_sha": source_sha,
        "input_sha": digest.hexdigest(),
        "aligned_target_sha256": _alignment_sha(dates, prior, targets),
        "start": start,
        "max_num": max_num,
        "draw_n": draw_n,
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
    paths = [Path(__file__).resolve()]
    paths.extend(sorted((ROOT / "loto_enterprise").rglob("*.py")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _metadata(data, name, identity) -> dict:
    return {
        "scope": "pool_extension_20261019",
        "version": CACHE_VERSION,
        "identity": identity,
        "method": name,
        "geometry": {"max_num": data["max_num"], "draw_n": data["draw_n"]},
        "source_sha": data["source_sha"],
        "input_sha": data["input_sha"],
        "code_source_sha": _code_source_sha(),
        "start": data["start"],
        "aligned_target_sha256": data["aligned_target_sha256"],
        "python": list(sys.version_info[:3]),
        "numpy": np.__version__,
    }


def _destination(path) -> Path:
    directory = Path(CACHE_ROOT if path is None else path).resolve()
    root = CACHE_ROOT.resolve()
    if directory != root and not directory.is_relative_to(root):
        raise ValueError("output must be inside the dedicated extension TEMP cache")
    return directory


def _cache_file(directory, data, name, metadata) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "_-")
    if not safe or safe != name:
        raise ValueError("invalid method name for score cache")
    token = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return Path(directory) / f"scores_{Path(data['path']).stem}_{name}_{token[:24]}.npz"


def _read_cache(path, metadata, shape):
    try:
        with np.load(path, allow_pickle=False) as stored:
            if json.loads(str(stored["metadata"].item())) != metadata:
                return None
            scores, invalid = stored["scores"], stored["invalid"]
            elapsed = float(stored["elapsed"].item())
            errors = json.loads(str(stored["errors"].item()))
        if (
            scores.shape != shape
            or scores.dtype != np.float64
            or invalid.shape != (shape[0],)
            or invalid.dtype != np.bool_
            or not np.isfinite(scores).all()
            or not (np.ptp(scores, axis=1) > SCORE_VARIANCE_EPSILON).all()
            or not np.isfinite(elapsed)
            or elapsed < 0
            or not isinstance(errors, dict)
            or any(
                not isinstance(k, str)
                or isinstance(v, bool)
                or not isinstance(v, int)
                or v < 0
                for k, v in errors.items()
            )
            or sum(errors.values()) != int(invalid.sum())
        ):
            return None
        return scores, invalid, elapsed, errors
    except OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile:
        return None


def _write_cache(path, metadata, scores, invalid, elapsed, errors):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
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


def _assert_source(data):
    current = hashlib.sha256(Path(data["path"]).read_bytes()).hexdigest()
    if current != data["source_sha"]:
        raise RuntimeError("input history changed while computing/loading scores")


def evaluate_method(args: dict):
    """Picklable one-method worker with the unchanged five-element score tuple."""
    started = time.perf_counter()
    directory = _destination(args["cache_dir"])
    name = args["name"]
    if name not in METHODS:
        raise KeyError(f"unknown registry scorer: {name}")
    identity = args["identity"]
    if not isinstance(identity, str) or not identity:
        raise ValueError("a nonempty manifest identity is required")
    data = load_data(
        args["path"],
        max_num=args["max_num"],
        draw_n=args["draw_n"],
        start=args.get("start", WARMUP),
    )
    metadata = _metadata(data, name, identity)
    path = _cache_file(directory, data, name, metadata)
    shape = (len(data["targets"]), data["max_num"])
    cached = _read_cache(path, metadata, shape)
    if cached is not None:
        _assert_source(data)
        return name, *cached
    if name == "frequency":
        scores = data["frequency_past"].copy()
        invalid, errors = np.zeros(shape[0], dtype=bool), {}
    else:
        scores = np.empty(shape, dtype=np.float64)
        invalid = np.zeros(shape[0], dtype=bool)
        errors = Counter()
        last_cutoff, last_values, last_reason = None, None, None
        for row, target in enumerate(data["targets"]):
            cutoff = int(data["prior"][target])
            if cutoff != last_cutoff:
                try:
                    raw, _ = call_method(
                        name,
                        data["draws"][:cutoff],
                        data["max_num"],
                        history_cutoffs=data["prior"][:cutoff],
                    )
                    last_values, last_reason = _complete_scores(raw, data["max_num"])
                except Exception as exc:
                    last_values = None
                    last_reason = f"exception:{type(exc).__name__}:{str(exc)[:180]}"
                last_cutoff = cutoff
            if last_reason:
                invalid[row] = True
                errors[last_reason] += 1
                scores[row] = data["frequency_past"][row]
            else:
                scores[row] = last_values
        errors = dict(errors)
    elapsed = time.perf_counter() - started
    _assert_source(data)
    _write_cache(path, metadata, scores, invalid, elapsed, errors)
    return name, scores, invalid, elapsed, errors


def load_scores(
    path,
    names,
    cache_dir,
    identity,
    *,
    max_num,
    draw_n,
    workers=4,
    start=WARMUP,
):
    """Return (data, {name: (name, scores[T,N], invalid[T], elapsed, errors)}).

    Reuse requires exact signed metadata, including geometry, source/code,
    runtime, manifest identity and target alignment. Older hardcoded 6/49
    caches cannot satisfy this separate schema and are never touched.
    At most four workers are used, with no pool nested inside an outer child.
    """
    directory = _destination(cache_dir)
    if not isinstance(identity, str) or not identity:
        raise ValueError("a nonempty manifest identity is required")
    names = list(names)
    if len(names) != len(set(names)):
        raise ValueError("scorer names must be unique")
    unknown = set(names) - set(METHODS)
    if unknown:
        raise KeyError(f"unknown registry scorers: {sorted(unknown)}")
    data = load_data(path, max_num=max_num, draw_n=draw_n, start=start)
    jobs = [
        {
            "path": data["path"],
            "name": name,
            "cache_dir": str(directory),
            "identity": identity,
            "max_num": data["max_num"],
            "draw_n": data["draw_n"],
            "start": data["start"],
        }
        for name in names
    ]
    count = max(1, min(4, int(workers), os.cpu_count() or 1))
    if multiprocessing.current_process().name != "MainProcess":
        count = 1
    results = {}
    if count == 1 or len(jobs) <= 1:
        for args in jobs:
            result = evaluate_method(args)
            results[result[0]] = result
    else:
        with ProcessPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(evaluate_method, args) for args in jobs]
            for future in as_completed(futures):
                result = future.result()
                results[result[0]] = result
    _assert_source(data)
    return data, {name: results[name] for name in names}
