"""Reuse verified causal expert scores for the separate adaptive study.

Old research artifacts are read-only. Different study identities may share
stateless scores, but every input, code, runtime and target-alignment identity
must agree. Missing or conflicting source caches fail closed by default.
Only the dedicated temporary cache may be written by this module.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from loto_enterprise.benchmark.methods import METHODS
from scripts.analysis import pool_combination_cache_2026_10_19 as _reuse
from scripts.analysis import pool_score_cache_2026_10_19 as _base

ADAPTIVE_CACHE_ROOT = (
    Path(tempfile.gettempdir()) / "loto-pool-adaptive-20261019" / "cache"
)
SOURCE_CACHE_ROOTS = (_reuse.COMBINATION_CACHE_ROOT, _reuse.LEGACY_CACHE_ROOT)
EXPERTS = ("frequency", "markov_lag2", "ewma_hl30")
WARMUP = 256


def _destination(path) -> Path:
    directory = Path(path if path is not None else ADAPTIVE_CACHE_ROOT).resolve()
    for source in SOURCE_CACHE_ROOTS:
        source = Path(source).resolve()
        if directory == source or directory.is_relative_to(source):
            raise ValueError("old score caches are read-only")
    root = ADAPTIVE_CACHE_ROOT.resolve()
    if directory != root and not directory.is_relative_to(root):
        raise ValueError("output must be inside the dedicated adaptive TEMP cache")
    return directory


def _canonical_sha(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _aligned_target_sha(data: dict) -> str:
    """Sign row identities, strict prefix cutoffs and dates in target order."""
    targets = data["targets"]
    digest = hashlib.sha256(b"adaptive-target-alignment-v1\0")
    digest.update(np.asarray(targets, dtype="<i8").tobytes())
    digest.update(np.asarray(data["prior"][targets], dtype="<i8").tobytes())
    digest.update(json.dumps(data["dates"][targets].tolist()).encode("utf-8"))
    return digest.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".provenance.json")


def _read_current(output: Path, metadata: dict, data: dict):
    cached = _base._read_cache(output, metadata, (len(data["targets"]), 49))
    if cached is None:
        return None
    try:
        report = json.loads(_sidecar(output).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            return None
        payload = {k: v for k, v in report.items() if k != "provenance_sha256"}
        if (
            report.get("provenance_sha256") != _canonical_sha(payload)
            or report.get("target_metadata") != metadata
            or report.get("aligned_target_sha256") != _aligned_target_sha(data)
            or report.get("new_target_indices") != data["targets"].tolist()
            or report.get("materialized_cache_sha256")
            != hashlib.sha256(output.read_bytes()).hexdigest()
            or report.get("mode")
            not in {"reused_stateless_scores", "computed_current_code"}
        ):
            return None
        return cached, report
    except OSError, ValueError, TypeError:
        return None


def _compatible_sources(data: dict, name: str, metadata: dict):
    matches = []
    stem = Path(data["path"]).stem
    seen = set()
    for root in SOURCE_CACHE_ROOTS:
        for path in sorted(Path(root).rglob(f"scores_{stem}_{name}_*.npz")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidate = _reuse._candidate(resolved, metadata, data)
            if candidate is not None:
                matches.append(candidate)
    if matches:
        first = matches[0][0]
        if any(
            not np.array_equal(first[0], other[0][0])
            or not np.array_equal(first[1], other[0][1])
            or first[3] != other[0][3]
            for other in matches[1:]
        ):
            raise RuntimeError(f"conflicting compatible raw-score caches: {name}")
    return matches


def _materialize_one(args: dict):
    name = args["name"]
    directory = _destination(args["cache_dir"])
    data = _base.load_data(args["path"], start=args["start"])
    metadata = _reuse._metadata(data, name, args["identity"])
    output = _base._cache_file(directory, data["path"], name, metadata)
    current = _read_current(output, metadata, data)
    if current is not None and (
        not args["require_reuse"] or current[1]["mode"] == "reused_stateless_scores"
    ):
        _reuse._assert_source(data)
        return (name, *current[0]), current[1]

    matches = _compatible_sources(data, name, metadata)
    if matches:
        cached, report = matches[0]
        report = {
            **report,
            "equivalent_compatible_cache_count": len(matches),
            "compatible_sources": [
                {
                    "path": entry[1]["source_cache"],
                    "sha256": entry[1]["source_cache_sha256"],
                    "metadata": entry[1]["source_metadata"],
                }
                for entry in matches
            ],
        }
        _reuse._assert_source(data)
        _base._write_cache(output, metadata, *cached)
        result = (name, *cached)
    else:
        if args["require_reuse"]:
            raise RuntimeError(f"required compatible raw-score cache missing: {name}")
        result = _base.evaluate_method({**args, "cache_dir": str(directory)})
        report = {
            "mode": "computed_current_code",
            "target_metadata": metadata,
            "old_target_indices": None,
            "new_target_indices": data["targets"].tolist(),
            "slice_positions": None,
            "compatible_sources": [],
        }
    _reuse._assert_source(data)
    report = {
        **report,
        "aligned_target_sha256": _aligned_target_sha(data),
        "materialized_cache_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    report["provenance_sha256"] = _canonical_sha(report)
    _reuse._write_provenance(_sidecar(output), report)
    return result, report


def load_scores(
    path,
    names,
    cache_dir,
    identity,
    workers=4,
    start=WARMUP,
    require_reuse=True,
):
    """Return the unchanged (data, results) API using a separate TEMP cache.

    results[name] is (name, scores[T,49], invalid[T], original_elapsed, errors).
    Target rows are exactly load_data(path, start=start)['targets']; every
    scorer sees its complete strict-day prefix. By default no scorer may run.
    At most four workers are created; an outer child process remains serial.
    data['score_cache_provenance'] signs input alignment and source NPZ bytes.
    """
    directory = _destination(cache_dir)
    names = list(names)
    if len(set(names)) != len(names):
        raise ValueError("scorer names must be unique")
    unknown = set(names) - set(METHODS)
    if unknown:
        raise KeyError(f"unknown registry scorers: {sorted(unknown)}")
    if not isinstance(identity, str) or not identity:
        raise ValueError("a nonempty manifest identity is required")
    if not isinstance(require_reuse, bool):
        raise ValueError("require_reuse must be boolean")
    data = _base.load_data(path, start=start)
    jobs = [
        {
            "path": str(Path(path).resolve()),
            "name": name,
            "cache_dir": str(directory),
            "identity": identity,
            "start": start,
            "require_reuse": require_reuse,
        }
        for name in names
    ]
    count = max(1, min(4, int(workers), os.cpu_count() or 1))
    if multiprocessing.current_process().name != "MainProcess":
        count = 1
    results, reports = {}, {}
    if count == 1 or len(jobs) <= 1:
        for args in jobs:
            result, report = _materialize_one(args)
            results[result[0]], reports[result[0]] = result, report
    else:
        with ProcessPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(_materialize_one, args) for args in jobs]
            for future in as_completed(futures):
                result, report = future.result()
                results[result[0]], reports[result[0]] = result, report
    _reuse._assert_source(data)
    data = {**data, "score_cache_provenance": reports}
    return data, {name: results[name] for name in names}
