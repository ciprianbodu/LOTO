"""cleanup_residual: sterge numai allow-list-ul, pastreaza starea protejata."""

from __future__ import annotations

from pathlib import Path

import cleanup_residual as cr


def _touch(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _tree(root: Path) -> dict[str, Path]:
    obsolete = {
        "disabled": _touch(root / "disabled_methods.json"),
        "backtest": _touch(root / "backtest_results.json"),
        "timesfm": _touch(root / "venv_timesfm" / "lib" / "a.py").parent.parent,
        "lightning": _touch(root / "lightning_logs" / "v0" / "e").parent.parent,
        "overnight": _touch(root / "x.backup_overnight"),
        "autopilot": _touch(root / "y.pre_autopilot"),
        "bm_pre": _touch(root / "best_methods.json.pre_20260101"),
        "pycache": _touch(root / "pkg" / "__pycache__" / "m.pyc").parent,
        "old_module": _touch(root / "loto_enterprise/benchmark/methods_ml.py"),
        "old_test": _touch(root / "test_parity_balance.py"),
    }
    protected = {
        "best": _touch(root / "best_methods.json"),
        "curated": _touch(root / "curated_methods.json"),
        "pool": _touch(root / "pool_history.json"),
        "db": _touch(root / "loto_jobs.db"),
        "wal": _touch(root / "loto_jobs.db-wal"),
        "mail": _touch(root / "mail_config.json"),
        "istoric": _touch(root / "_ISTORIC" / "loto649.csv"),
        "decision": _touch(root / "decisions" / "DE" / "best_methods.json"),
        "bat": _touch(root / "personal.bat"),
        "pid": _touch(root / ".bench_pid"),
        "log": _touch(root / "loto.log"),
        "venv_pyc": _touch(root / ".venv" / "lib" / "__pycache__" / "a.pyc"),
        "claude_pyc": _touch(root / ".claude" / "w" / "__pycache__" / "a.pyc"),
    }
    _touch(root / ".venv" / "pyvenv.cfg")
    return {**{f"o_{k}": v for k, v in obsolete.items()},
            **{f"p_{k}": v for k, v in protected.items()}}


def _run(root: Path, **kw):
    kw.setdefault("tracked", set())
    return cr.run(root, root, purge_bench=False, **kw)


def test_obsolete_deleted_protected_kept(tmp_path):
    paths = _tree(tmp_path)
    res = _run(tmp_path)
    for name, p in paths.items():
        assert p.exists() == name.startswith("p_"), name
    assert res["errors"] == []
    assert res["bytes"] > 0


def test_dry_run_deletes_nothing(tmp_path):
    paths = _tree(tmp_path)
    res = _run(tmp_path, dry_run=True)
    assert all(p.exists() for p in paths.values())
    assert len(res["deleted"]) >= 10


def test_tracked_files_kept(tmp_path):
    paths = _tree(tmp_path)
    tracked = {"disabled_methods.json", "loto_enterprise/benchmark/methods_ml.py",
               "venv_timesfm/lib/a.py"}
    _run(tmp_path, tracked=tracked)
    assert paths["o_disabled"].exists()
    assert paths["o_old_module"].exists()
    assert paths["o_timesfm"].exists()
    assert not paths["o_backtest"].exists()


def test_without_git_skips_possibly_tracked_files(tmp_path):
    paths = _tree(tmp_path)
    _run(tmp_path, tracked=None)
    assert paths["o_disabled"].exists()
    assert paths["o_old_module"].exists()
    assert not paths["o_pycache"].exists()  # ignorat de git oricum


def test_root_logs_removed_only_when_runtime_moved(tmp_path):
    root, runtime = tmp_path / "repo", tmp_path / "rt"
    log = _touch(root / "loto.log")
    runtime.mkdir()
    cr.run(root, root, tracked=set(), purge_bench=False)
    assert log.exists()
    cr.run(root, runtime, tracked=set(), purge_bench=False)
    assert not log.exists()


def test_main_never_fails(monkeypatch):
    monkeypatch.setattr(cr, "run", lambda *a, **k: 1 / 0)
    assert cr.main(["--dry-run"]) == 0


def test_bat_calls_cleanup_after_wf_purge():
    bat = (Path(__file__).resolve().parent / "ACTUALIZARI.bat").read_text()
    i_wf = bat.index("[2c/4]")
    i_clean = bat.index("cleanup_residual.py")
    assert i_wf < i_clean < bat.index("[3/4]")
