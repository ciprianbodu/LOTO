"""Auditul global 2026-10-22 (b): motorul, workerul și detecția workerului.

Fiecare test pica pe codul de dinainte (55ea7f6), în afară de controalele
pozitive `test_a_python_worker_is_still_detected` și
`test_a_python_worker_started_with_interpreter_options_is_still_detected`
(workerul Python real rămâne găsit) și de ramura True a testului cu procese reale:
- C7: o celulă Joker (urna 2) necitibilă în primele/ultimele 3 extrageri oprea
  tot pipeline-ul în blocul de context, după scoring și wheel;
- C9: o eroare SQLite trecătoare la progresul de 2%/3%/99% (și la cache hit) sau
  la o verificare a anulării marca jobul FAILED; la 99% și după rulare
  rezultatul calculat se pierdea fără copie;
- C10: un proces care nu e Python (editor, shell) cu calea worker.py în argv
  trecea drept worker, iar workerul mort nu mai era repornit; la fel un Python
  pornit cu -m/-c (IDLE: `pythonw.exe -m idlelib worker.py`).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import psutil
import pytest

import ui_shared as us
import worker
from loto_engine import LotoEngine
from ui_shared import decode_queue_result

# --------------------------------------------------------------------------- #
# C7: Urna 2 necitibilă în contextul primelor/ultimelor 3 extrageri
# --------------------------------------------------------------------------- #

_JOKER_ROWS = 60


def _joker_csv(tmp_path: Path, bad_row: int, bad_value: str) -> Path:
    dates = pd.date_range("2024-01-04", periods=_JOKER_ROWS, freq="3D")
    rows = []
    for i, day in enumerate(dates.strftime("%d-%m-%Y")):
        base = (i * 7) % 45
        nums = [1 + (base + 9 * j) % 45 for j in range(5)]  # distincte: 9*4 < 45
        rows.append(
            {"date": day, **{f"n{j + 1}": n for j, n in enumerate(nums)}, "joker": 1 + i % 20}
        )
    df = pd.DataFrame(rows)
    df["joker"] = df["joker"].astype(object)
    df.loc[bad_row, "joker"] = bad_value
    path = tmp_path / "joker.csv"
    df.to_csv(path, index=False)
    return path


@pytest.mark.parametrize("bad_value", ["?", "1o", "inf"])
@pytest.mark.parametrize("bad_row", [0, _JOKER_ROWS - 1])
def test_an_unreadable_joker_cell_in_the_context_rows_does_not_crash_the_pipeline(
    tmp_path, monkeypatch, bad_row, bad_value
):
    path = _joker_csv(tmp_path, bad_row, bad_value)
    engine = LotoEngine("joker")
    assert engine.load_data(str(path))
    # Scorer determinist și rapid: defectul e în blocul de context de la final.
    monkeypatch.setattr(
        engine,
        "_get_timesfm_scores",
        lambda is_joker_drum=False, **kw: {
            n: float(46 - n) for n in range(1, 21 if is_joker_drum else 46)
        },
    )
    lines, _p10, _p90, _g, context, _audit = engine.run_institutional_pipeline(
        pool_size=8, guarantee=3, track_pool_variation=False
    )
    assert lines
    shown = context["first_3"] if bad_row == 0 else context["last_3"]
    bad = shown[0] if bad_row == 0 else shown[-1]
    neighbour = shown[1] if bad_row == 0 else shown[-2]
    assert bad["joker"] is None
    assert len(bad["numbers"]) == 5 and bad["date"] != "N/A"  # rândul rămâne afișat
    expected = 1 + (1 if bad_row == 0 else _JOKER_ROWS - 2) % 20
    assert neighbour["joker"] == expected  # celelalte rânduri neschimbate


# --------------------------------------------------------------------------- #
# C9: o eroare SQLite trecătoare în worker nu mai pierde jobul / rezultatul
# --------------------------------------------------------------------------- #


class _FakeEngine:
    """Motor minimal: workerul real (`_run_pipeline_job_inner`) rulează în jur."""

    def __init__(self, game_type, game_key=None, country=None):
        self.hard_core = [1, 2, 3, 4, 5, 6, 7]
        self.data = None

    def load_data(self, path):
        self.data = pd.read_csv(path)
        return True

    def run_institutional_pipeline(self, progress_cb=None, **_kw):
        if progress_cb is not None:
            progress_cb("scoring", 50)
        return [[1, 2, 3, 4, 5, 6]], 1.0, 2.0, [1.0, 2.0], {"first_3": [], "last_3": []}, {}


_LABELS = ("6/49", "5/40")


def _job(job_id: int, n_tasks: int = 1, use_cache: bool = False) -> dict:
    df = pd.DataFrame({"date": ["01-01-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    tasks = [{"game_label": label, "pool_size": 12, "guarantee": 3} for label in _LABELS]
    cfg = {
        "input_hash": "h" if use_cache else "",
        "use_cache": use_cache,
        "datasets": [
            {"fname": "x.csv", "df_json": df.to_json(orient="split"), "tasks": tasks[:n_tasks]}
        ],
    }
    return {"id": job_id, "task_type": "pipeline", "config_json": json.dumps(cfg)}


@pytest.fixture
def stubs(monkeypatch):
    """Coada înlocuită de înregistrări; `fail_at_pct` / `fail_cancel_call` aleg
    scrierea de progres, respectiv verificarea anulării, care cade o dată."""
    state = {
        "fail_at_pct": None,
        "fail_cancel_call": None,
        "cancel_calls": 0,
        "completed": [],
        "complete_returns": True,
        "failed": [],
    }

    def progress(job_id, pct, msg, **_kw):
        if pct == state["fail_at_pct"]:
            state["fail_at_pct"] = None
            raise sqlite3.OperationalError("database is locked")
        return False

    def cancelled(job_id, **_kw):
        state["cancel_calls"] += 1
        if state["cancel_calls"] == state["fail_cancel_call"]:
            raise sqlite3.OperationalError("disk I/O error")
        return False

    def complete(job_id, result_json, worker_token=None):
        state["completed"].append(result_json)
        return state["complete_returns"]

    monkeypatch.setattr(worker, "update_job_progress", progress)
    monkeypatch.setattr(worker, "is_job_cancelled", cancelled)
    monkeypatch.setattr(worker, "complete_job", complete)
    monkeypatch.setattr(
        worker, "fail_job", lambda job_id, msg, **_kw: state["failed"].append(msg)
    )
    monkeypatch.setattr(worker, "_set_bench_hit_target", lambda task: None)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    monkeypatch.setattr("loto_engine.LotoEngine", _FakeEngine)
    return state


def _decoded_games(result_json: str) -> set[str]:
    bundle, n = decode_queue_result(result_json)
    assert n == 1
    ((fname, outputs),) = bundle
    assert fname == "x.csv"
    assert all(out["variants"] == [[1, 2, 3, 4, 5, 6]] for out in outputs.values())
    return set(outputs)


@pytest.mark.parametrize("pct", [2, 3, 99])
def test_a_db_error_on_a_worker_progress_write_does_not_fail_the_job(
    stubs, caplog, pct
):
    stubs["fail_at_pct"] = pct
    with caplog.at_level(logging.WARNING):
        worker._process_one_job(_job(11))
    assert stubs["fail_at_pct"] is None  # eroarea chiar s-a produs
    assert len(stubs["completed"]) == 1 and not stubs["failed"]
    assert _decoded_games(stubs["completed"][0]) == {"6/49"}
    assert "progresul n-a putut fi scris" in caplog.text


def test_a_db_error_on_the_cache_hit_progress_still_returns_the_cached_result(
    stubs, monkeypatch
):
    monkeypatch.setattr(worker, "_pipeline_cache_key", lambda *a, **k: "cheie")
    monkeypatch.setattr(worker, "get_pipeline_cache", lambda key: "PAYLOAD-DIN-CACHE")
    stubs["fail_at_pct"] = 100
    worker._process_one_job(_job(12, use_cache=True))
    assert stubs["fail_at_pct"] is None
    assert stubs["completed"] == ["PAYLOAD-DIN-CACHE"]


@pytest.mark.parametrize(
    "n_tasks,fail_call",
    [
        (1, 1),  # înainte de rulare
        (1, 2),  # înaintea task-ului
        (1, 3),  # după rulare, cu rezultatul calculat
        (2, 3),  # înaintea celui de-al doilea joc: primul e deja calculat
    ],
)
def test_a_db_error_on_a_cancel_check_does_not_lose_the_result(
    stubs, caplog, n_tasks, fail_call
):
    stubs["fail_cancel_call"] = fail_call
    with caplog.at_level(logging.WARNING):
        worker._process_one_job(_job(13, n_tasks=n_tasks))
    assert stubs["cancel_calls"] >= fail_call
    assert len(stubs["completed"]) == 1 and not stubs["failed"]
    assert _decoded_games(stubs["completed"][0]) == set(_LABELS[:n_tasks])
    assert "verificarea anulării a eșuat" in caplog.text


def test_a_missed_cancel_after_the_run_goes_to_disk_not_over_the_cancelled_job(
    stubs, tmp_path, monkeypatch
):
    # Verificarea de după rulare cade, iar jobul fusese de fapt anulat:
    # `complete_job` nu scrie peste el (0 rânduri), rezultatul ajunge pe disc.
    monkeypatch.setattr(worker.tempfile, "gettempdir", lambda: str(tmp_path))
    stubs["fail_cancel_call"] = 3
    stubs["complete_returns"] = False
    worker._process_one_job(_job(14))
    dumps = list(tmp_path.glob("loto_orphan_result_14_*.txt"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == stubs["completed"][0]
    assert _decoded_games(stubs["completed"][0]) == {"6/49"}


# --------------------------------------------------------------------------- #
# C10: numai un interpretor Python poate fi workerul
# --------------------------------------------------------------------------- #


class _Proc:
    def __init__(self, name, cmdline):
        self.info = {"name": name, "cmdline": cmdline}

    def cwd(self):
        return str(us.PROJECT_ROOT)


def _only(monkeypatch, *procs):
    monkeypatch.setattr(us.psutil, "process_iter", lambda attrs=None: iter(procs))


@pytest.mark.parametrize(
    "name", ["notepad.exe", "Code.exe", "cmd.exe", "bash", "vim"]
)
def test_a_non_python_process_holding_the_worker_path_is_not_the_worker(
    monkeypatch, name
):
    _only(monkeypatch, _Proc(name, [name, str(us.WORKER_PATH)]))
    assert us.is_worker_running() is False
    _only(monkeypatch, _Proc(name, [name, "worker.py"]))  # relativ, cwd = proiectul
    assert us.is_worker_running() is False


@pytest.mark.parametrize(
    "name", ["python.exe", "pythonw.exe", "Python", "python3", "python3.14", None, ""]
)
def test_a_python_worker_is_still_detected(monkeypatch, name):
    # Fără nume (AccessDenied, procese simulate vechi): regula de dinainte.
    _only(monkeypatch, _Proc(name, ["python", str(us.WORKER_PATH)]))
    assert us.is_worker_running() is True


_IDLE_PYW = r"C:\Users\Cipix\AppData\Local\Programs\Python\Python314\pythonw.exe"
_VENV_PY = r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe"


@pytest.mark.parametrize(
    "opts",
    [
        ["-m", "idlelib"],  # „Edit with IDLE 3.14” de pe stație
        ["-m", "idlelib", "-e"],
        ["-midlelib"],
        ["-Bm", "idlelib"],
        ["-u", "-m", "idlelib"],
        ["-c", "import idlelib.idle"],
        ["-Ic", "pass"],
    ],
)
def test_a_python_running_a_module_or_a_command_on_worker_py_is_not_the_worker(
    monkeypatch, opts
):
    # IDLE e un proces Python: după -m/-c calea workerului e argv-ul
    # modulului sau al comenzii, nu scriptul rulat.
    _only(monkeypatch, _Proc("pythonw.exe", [_IDLE_PYW, *opts, str(us.WORKER_PATH)]))
    assert us.is_worker_running() is False
    _only(monkeypatch, _Proc("pythonw.exe", [_IDLE_PYW, *opts, "worker.py"]))  # relativ
    assert us.is_worker_running() is False


@pytest.mark.parametrize(
    "opts",
    [
        ["-u"],
        ["-X", "utf8"],
        ["-Xutf8"],
        ["-Ximporttime"],  # „m” în valoarea lui -X nu e -m
        ["-W", "ignore"],
        ["-Wdefault::ImportWarning"],
        ["-Bu", "-X", "utf8"],
        ["-OO"],
        ["--check-hash-based-pycs", "never"],
    ],
)
@pytest.mark.parametrize("exe", [_VENV_PY, "python"])
def test_a_python_worker_started_with_interpreter_options_is_still_detected(
    monkeypatch, exe, opts
):
    # START_8000.bat / ensure_worker_running: [python(.exe), worker.py]; pe
    # Windows lansatorul venv și copilul lui au același argv (.venv\Scripts).
    _only(monkeypatch, _Proc("python.exe", [exe, *opts, str(us.WORKER_PATH)]))
    assert us.is_worker_running() is True
    _only(monkeypatch, _Proc("python.exe", [exe, *opts, "worker.py"]))  # relativ
    assert us.is_worker_running() is True


_READY_CMD = (
    "import pathlib, sys, time; "
    "pathlib.Path(sys.argv[1]).with_suffix('.ready').touch(); "
    "time.sleep(60)"
)


@pytest.mark.parametrize(
    "opts,expected",
    [
        (["-c", _READY_CMD], False),  # ca `pythonw -m idlelib worker.py`, fără GUI
        (["-X", "utf8", "-u"], True),
    ],
)
def test_real_python_processes_holding_the_worker_path(
    monkeypatch, tmp_path, opts, expected
):
    # sys.executable e pe stație lansatorul venv: el și copilul lui (același
    # argv) sunt procese python.exe reale, ambele vizibile la verificare.
    target = tmp_path / "worker.py"
    target.write_text(
        "import pathlib, time\n"
        "pathlib.Path(__file__).with_suffix('.ready').touch()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    ready = target.with_suffix(".ready")
    monkeypatch.setattr(us, "WORKER_PATH", target)
    proc = subprocess.Popen(
        [sys.executable, *opts, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()  # interpretorul real rulează deja
        assert us.is_worker_running() is expected
    finally:
        try:
            for child in psutil.Process(proc.pid).children(recursive=True):
                child.kill()
        except psutil.Error:
            pass
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe ca proces ne-Python real")
def test_a_real_shell_holding_the_worker_path_does_not_block_the_respawn(
    monkeypatch, tmp_path
):
    target = tmp_path / "worker.py"
    target.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(us, "WORKER_PATH", target)
    holder = subprocess.Popen(
        ["cmd.exe", "/c", "ping -n 30 127.0.0.1 >nul & rem", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    spawned = []
    monkeypatch.setattr(us, "_WORKER_SPAWN_TS", 0.0)
    # După pornirea holderului: `us.subprocess` e modulul global.
    monkeypatch.setattr(us.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    try:
        # Procesul e vizibil cu calea în argv înainte de verificare.
        deadline = time.monotonic() + 10
        seen = False
        while not seen and time.monotonic() < deadline:
            try:
                seen = str(target) in psutil.Process(holder.pid).cmdline()
            except psutil.Error:
                pass
            if not seen:
                time.sleep(0.1)
        assert seen
        assert us.is_worker_running() is False
        us.ensure_worker_running()
        assert len(spawned) == 1  # workerul mort e repornit
    finally:
        try:
            for child in psutil.Process(holder.pid).children(recursive=True):
                child.kill()
        except psutil.Error:
            pass
        holder.kill()
        holder.wait(timeout=10)
