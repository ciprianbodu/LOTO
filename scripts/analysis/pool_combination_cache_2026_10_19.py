"""Reuse causal score caches independently of candidate outcomes.

The earlier study's identity describes its candidates and testing protocol;
it is not an input to a stateless registry scorer. Reuse therefore ignores
only that identity and permits a later warmup cutoff, while requiring exact
CSV, canonical input, source-code, scorer/cache version, Python and NumPy
identities. Old caches and frozen study sources are never modified.
"""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import sys
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis import pool_score_cache_2026_10_19 as _base

import numpy as np

from loto_enterprise.benchmark.methods import METHODS

LEGACY_CACHE_ROOT = ROOT / "bench_results" / "pool649_research_20261019"
COMBINATION_CACHE_ROOT = ROOT / "bench_results" / "pool649_combinations_20261019"
WARMUP = 256


def _metadata(data: dict, name: str, identity) -> dict:
    return {
        "version": _base.CACHE_VERSION,
        "identity": identity,
        "method": name,
        "source_sha": data["source_sha"],
        "input_sha": data["input_sha"],
        "code_source_sha": _base._code_source_sha(),
        "start": data["start"],
        "python": list(sys.version_info[:3]),
        "numpy": np.__version__,
    }


def _destination(path) -> Path:
    directory = Path(path).resolve()
    old_root = LEGACY_CACHE_ROOT.resolve()
    if directory == old_root or directory.is_relative_to(old_root):
        raise ValueError("old study cache is read-only")
    new_root = COMBINATION_CACHE_ROOT.resolve()
    if directory != new_root and not directory.is_relative_to(new_root):
        raise ValueError("output must be inside the new combination cache root")
    return directory


def _assert_source(data: dict) -> None:
    if (
        hashlib.sha256(Path(data["path"]).read_bytes()).hexdigest()
        != data["source_sha"]
    ):
        raise RuntimeError("input history changed while loading cached scores")


def _write_provenance(path: Path, report: dict) -> None:
    temporary = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".provenance.json")


def _candidate(path: Path, expected: dict, data: dict):
    """Validate all source metadata and return an exactly aligned score slice."""
    try:
        raw = path.read_bytes()
        with np.load(io.BytesIO(raw), allow_pickle=False) as stored:
            metadata = json.loads(str(stored["metadata"].item()))
        if not isinstance(metadata, dict) or set(metadata) != set(expected):
            return None
        old_start = metadata["start"]
        if (
            isinstance(old_start, bool)
            or not isinstance(old_start, int)
            or not 1 <= old_start <= expected["start"]
            or not isinstance(metadata["identity"], str)
            or not metadata["identity"]
        ):
            return None
        comparable = {
            **metadata,
            "identity": expected["identity"],
            "start": expected["start"],
        }
        if comparable != expected:
            return None
        old_targets = np.flatnonzero(data["prior"] >= old_start)
        cached = _base._read_cache(io.BytesIO(raw), metadata, (len(old_targets), 49))
        if cached is None:
            return None
        old_scores, old_invalid, elapsed, old_errors = cached
        targets = data["targets"]
        positions = np.searchsorted(old_targets, targets)
        if np.any(positions >= len(old_targets)) or not np.array_equal(
            old_targets[positions], targets
        ):
            return None
        scores, invalid = old_scores[positions], old_invalid[positions]
        if not invalid.any():
            errors = {}
        elif np.array_equal(targets, old_targets):
            errors = old_errors
        elif len(old_errors) == 1:
            errors = {next(iter(old_errors)): int(invalid.sum())}
        else:
            # The old format has counts, not a reason per row. Recompute this
            # method rather than inventing reasons for the retained slice.
            return None
        provenance = {
            "mode": "reused_stateless_scores",
            "source_cache": str(path.resolve()),
            "source_cache_sha256": hashlib.sha256(raw).hexdigest(),
            "source_metadata": metadata,
            "target_metadata": expected,
            "old_target_indices": old_targets.tolist(),
            "new_target_indices": targets.tolist(),
            "slice_positions": positions.tolist(),
            "identity_ignored_only_for_raw_scores": True,
            "selection": "all metadata-compatible files, independent of outcomes",
        }
        return (scores, invalid, elapsed, errors), provenance
    except OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile:
        return None


def _materialize_one(args: dict):
    name = str(args["name"])
    if name not in METHODS:
        raise KeyError(f"unknown registry scorer: {name}")
    directory = _destination(args["cache_dir"])
    start = args.get("start", WARMUP)
    data = _base.load_data(args["path"], start=start)
    metadata = _metadata(data, name, args["identity"])
    output = _base._cache_file(directory, data["path"], name, metadata)
    shape = (len(data["targets"]), 49)
    cached = _base._read_cache(output, metadata, shape)
    if cached is not None:
        _assert_source(data)
        try:
            provenance = json.loads(_sidecar(output).read_text(encoding="utf-8"))
        except OSError, ValueError:
            provenance = {"mode": "current_identity_cache", "target_metadata": metadata}
            _write_provenance(_sidecar(output), provenance)
        return (name, *cached), provenance

    compatible = []
    stem = Path(data["path"]).stem
    # Candidate ordering depends only on file names and metadata, never hits,
    # candidate objectives, accuracy or the earlier study's selected method.
    for source in sorted(LEGACY_CACHE_ROOT.rglob(f"scores_{stem}_{name}_*.npz")):
        result = _candidate(source, metadata, data)
        if result is not None:
            compatible.append(result)
    conflicting = False
    if compatible:
        first = compatible[0][0]
        conflicting = any(
            not np.array_equal(first[0], value[0][0])
            or not np.array_equal(first[1], value[0][1])
            or first[3] != value[0][3]
            for value in compatible[1:]
        )
        if not conflicting:
            cached, provenance = compatible[0]
            provenance["equivalent_compatible_cache_count"] = len(compatible)
            provenance["compatible_source_sha256"] = [
                entry[1]["source_cache_sha256"] for entry in compatible
            ]
            _assert_source(data)
            _base._write_cache(output, metadata, *cached)
            _write_provenance(_sidecar(output), provenance)
            return (name, *cached), provenance

    # Missing/incompatible/ambiguous source caches take the normal canonical
    # path. The supplied output directory is always the new study's directory.
    result = _base.evaluate_method({**args, "cache_dir": str(directory)})
    _assert_source(data)
    provenance = {
        "mode": "computed_current_code",
        "target_metadata": metadata,
        "legacy_metadata_conflict": conflicting,
        "old_target_indices": None,
        "new_target_indices": data["targets"].tolist(),
        "slice_positions": None,
    }
    _write_provenance(_sidecar(output), provenance)
    return result, provenance


def evaluate_or_reuse(args: dict):
    """Picklable single-method worker; returns the previous five-element tuple."""
    return _materialize_one(args)[0]


def load_scores(path, names, cache_dir, identity, workers=4, start=WARMUP):
    """Return (data, {name: (name, scores, invalid, elapsed, errors)}).

    data is the old load_data API plus score_cache_provenance. start changes
    only eligible targets: every scorer still receives its complete strict
    historical prefix. At most four processes are used; a call from an outer
    child process stays serial, so process pools never nest.
    """
    directory = _destination(cache_dir)
    names = list(names)
    if len(set(names)) != len(names):
        raise ValueError("scorer names must be unique")
    unknown = set(names) - set(METHODS)
    if unknown:
        raise KeyError(f"unknown registry scorers: {sorted(unknown)}")
    data = _base.load_data(path, start=start)
    jobs = [
        {
            "path": str(Path(path).resolve()),
            "name": name,
            "cache_dir": str(directory),
            "identity": identity,
            "start": start,
        }
        for name in names
    ]
    count = max(1, min(4, int(workers), os.cpu_count() or 1))
    if multiprocessing.current_process().name != "MainProcess":
        count = 1
    results, provenance = {}, {}
    if count == 1 or len(jobs) <= 1:
        for args in jobs:
            result, report = _materialize_one(args)
            results[result[0]], provenance[result[0]] = result, report
    else:
        with ProcessPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(_materialize_one, args) for args in jobs]
            for future in as_completed(futures):
                result, report = future.result()
                results[result[0]], provenance[result[0]] = result, report
    _assert_source(data)
    data = {**data, "score_cache_provenance": provenance}
    return data, {name: results[name] for name in names}
