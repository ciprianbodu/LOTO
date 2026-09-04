"""Artefactele runtime grele/frecvente nu trebuie să rămână în OneDrive."""

from __future__ import annotations

import os
from pathlib import Path


def test_runtime_and_wf_overrides(monkeypatch, tmp_path):
    import runtime_paths as paths

    runtime = tmp_path / "runtime"
    wf_cache = tmp_path / "custom-wf"
    monkeypatch.setenv("LOTO_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("LOTO_WF_CACHE_DIR", str(wf_cache))

    assert paths._resolve_runtime_root() == runtime.resolve()
    assert paths._resolve_wf_cache_dir() == wf_cache
    assert runtime.is_dir()


def test_active_log_paths_share_runtime_root():
    import runtime_paths as paths

    assert paths.ENGINE_LOG_FILE.parent == paths.RUNTIME_ROOT
    assert paths.BENCH_LOG_FILE.parent == paths.RUNTIME_ROOT
    assert paths.STARTUP_LOG_FILE.parent == paths.RUNTIME_ROOT
    configured = os.environ.get("LOTO_WF_CACHE_DIR", "").strip()
    expected_wf = Path(configured).expanduser() if configured else paths.RUNTIME_ROOT / ".wf_cache"
    assert paths.WF_CACHE_DIR == expected_wf


def test_legacy_wf_migration_is_scoped_and_collision_safe(monkeypatch, tmp_path):
    from loto_enterprise.core import walk_forward_adapter as wf

    legacy = tmp_path / "repo" / "bench_results"
    target = tmp_path / "build" / ".wf_cache"
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    (legacy / "walk_forward_v22_a.pkl").write_bytes(b"current")
    (legacy / "walk_forward_v21_b.pkl").write_bytes(b"stale")
    (legacy / "walk_forward_v22_collision.pkl").write_bytes(b"legacy")
    (target / "walk_forward_v22_collision.pkl").write_bytes(b"target")
    (legacy / "folds.csv").write_text("must stay", encoding="utf-8")
    monkeypatch.setattr(wf, "LEGACY_CACHE_DIR", legacy)
    monkeypatch.setattr(wf, "CACHE_DIR", target)

    info = wf.migrate_legacy_wf_cache()
    assert (info["found"], info["moved"], info["skipped"], info["errors"]) == (3, 2, 1, [])
    assert (target / "walk_forward_v22_a.pkl").read_bytes() == b"current"
    assert (target / "walk_forward_v21_b.pkl").read_bytes() == b"stale"
    assert (target / "walk_forward_v22_collision.pkl").read_bytes() == b"target"
    assert (legacy / "walk_forward_v22_collision.pkl").read_bytes() == b"legacy"
    assert (legacy / "folds.csv").read_text(encoding="utf-8") == "must stay"

    again = wf.migrate_legacy_wf_cache()
    assert (again["found"], again["moved"], again["skipped"]) == (1, 0, 1)
