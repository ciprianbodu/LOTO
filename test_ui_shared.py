"""Teste de regresie pentru fixurile din ui_shared.py (verificare globală 2026-09-07):
`file_lock` (staleness pe vârsta fișierului, nu pe waiter; ownership token la exit),
`is_worker_running()` (potrivire exactă pe argument, nu substring pe linia de
comandă), `read_tail_lines(0)`, `pack_queue_result` (fallback pe orice eșec de
compresie, nu doar import lipsă) și `clear_logs()`.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

import ui_shared as us


# --------------------------------------------------------------------------- #
# file_lock
# --------------------------------------------------------------------------- #
def test_file_lock_excludes_concurrent_holder_and_survives_waiting(tmp_path):
    """Exclusivitate de bază: cât lock1 ține lock-ul (proaspăt, timeout mare), un
    al doilea waiter nu-l poate lua — indiferent CÂT așteaptă el însuși. Testat cu
    thread-uri, nu cu praguri scurte de timp (fragil pe sandbox-uri încărcate):
    confirmă direct fixul „staleness pe vârsta fișierului, nu pe cronometrul
    waiterului" fără să depindă de granularitatea reală a ceasului."""
    target = tmp_path / "state.json"
    lock1 = us.file_lock(target, timeout=5.0)
    lock1.__enter__()
    lock2 = us.file_lock(target, timeout=5.0)
    acquired = threading.Event()

    def _try_acquire():
        lock2.__enter__()
        acquired.set()

    t = threading.Thread(target=_try_acquire, daemon=True)
    t.start()
    t.join(timeout=0.5)
    assert not acquired.is_set()  # lock1 încă ține lock-ul PROASPĂT
    assert lock2._fd is None

    lock1.__exit__(None, None, None)
    t.join(timeout=5.0)
    assert acquired.is_set()
    assert lock2._fd is not None
    lock2.__exit__(None, None, None)


def test_file_lock_breaks_genuinely_stale_lock(tmp_path):
    target = tmp_path / "state.json"
    lockpath = str(target) + ".lock"
    with open(lockpath, "wb") as f:
        f.write(b"dead-owner-token")
    old = time.time() - 3600
    os.utime(
        lockpath, (old, old)
    )  # lock "stale" — deținătorul a crăpat cu o oră în urmă
    waiter = us.file_lock(target, timeout=1.0)
    waiter.__enter__()
    try:
        assert waiter._fd is not None
        with open(lockpath, "rb") as f:
            assert f.read().decode("ascii") == waiter._token
    finally:
        waiter.__exit__(None, None, None)


def test_file_lock_exit_does_not_delete_a_different_owners_lock(tmp_path):
    """Dacă între `__enter__` și `__exit__` un alt proces a spart și recreat
    lock-ul (staleness detectată aproape simultan de ambii), conținutul fișierului
    nu mai e tokenul nostru — `__exit__` nu are voie să-l șteargă."""
    target = tmp_path / "state.json"
    lockpath = str(target) + ".lock"
    lock = us.file_lock(target, timeout=5.0)
    lock.__enter__()
    assert lock._fd is not None
    # Simulăm un al doilea proces care a spart și recreat lock-ul cu alt token.
    os.close(lock._fd)
    os.unlink(lockpath)
    with open(lockpath, "wb") as f:
        f.write(b"other-process-token")
    lock._fd = os.open(lockpath, os.O_WRONLY)  # doar ca exit-ul să nu sară peste bloc
    lock.__exit__(None, None, None)
    assert os.path.exists(lockpath)
    with open(lockpath, "rb") as f:
        assert f.read() == b"other-process-token"  # lock-ul viu al celuilalt, NEȘTERS


# --------------------------------------------------------------------------- #
# is_worker_running
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, cmdline):
        self.info = {"cmdline": cmdline}


def test_is_worker_running_true_on_exact_worker_path(monkeypatch):
    monkeypatch.setattr(
        us.psutil,
        "process_iter",
        lambda attrs=None: iter(
            [
                _FakeProc(
                    [str(us.WORKER_PATH.parent / "python.exe"), str(us.WORKER_PATH)]
                )
            ]
        ),
    )
    assert us.is_worker_running() is True


def test_is_worker_running_false_on_mere_string_prefix_match(monkeypatch):
    """Regresie: un worker.py dintr-un director FRATE al cărui nume începe la fel
    (ex. PROJECT_ROOT_OLD) conținea PROJECT_ROOT ca substring pur textual — vechea
    implementare (`root in cmd`) îl confunda cu workerul CORECT al acestui proiect."""
    sibling_worker = str(us.PROJECT_ROOT) + "_OLD" + os.sep + "worker.py"
    monkeypatch.setattr(
        us.psutil,
        "process_iter",
        lambda attrs=None: iter([_FakeProc(["python", sibling_worker])]),
    )
    assert us.is_worker_running() is False


def test_is_worker_running_false_with_no_matching_process(monkeypatch):
    monkeypatch.setattr(
        us.psutil,
        "process_iter",
        lambda attrs=None: iter([_FakeProc(["python", "some_other_script.py"])]),
    )
    assert us.is_worker_running() is False


# --------------------------------------------------------------------------- #
# read_tail_lines
# --------------------------------------------------------------------------- #
def test_read_tail_lines_zero_returns_empty(tmp_path):
    p = tmp_path / "log.txt"
    p.write_text("linia 1\nlinia 2\nlinia 3\n", encoding="utf-8")
    assert us.read_tail_lines(str(p), 0) == []


def test_read_tail_lines_positive_returns_tail(tmp_path):
    p = tmp_path / "log.txt"
    p.write_text("linia 1\nlinia 2\nlinia 3\n", encoding="utf-8")
    assert us.read_tail_lines(str(p), 2) == ["linia 2\n", "linia 3\n"]


# --------------------------------------------------------------------------- #
# pack_queue_result — fallback pe ORICE eșec de compresie, nu doar import lipsă
# --------------------------------------------------------------------------- #
def test_pack_queue_result_falls_back_when_compress_itself_raises(monkeypatch):
    import compression.zstd as real_zstd

    def _boom(*a, **kw):
        raise RuntimeError("eroare simulată de compresie zstd")

    monkeypatch.setattr(real_zstd, "compress", _boom)
    encoded = us.pack_queue_result({"a": 1})
    decoded = us.decode_queue_result(encoded)
    assert decoded == {"a": 1}
    assert '"encoding": "pickle+b64"' in encoded


# --------------------------------------------------------------------------- #
# clear_logs
# --------------------------------------------------------------------------- #
def test_clear_logs_replaces_not_appends(tmp_path, monkeypatch):
    log_path = tmp_path / "loto.log"
    log_path.write_text("linie veche 1\nlinie veche 2\n", encoding="utf-8")
    monkeypatch.setattr(us, "LOG_FILE", str(log_path))
    us.clear_logs()
    content = log_path.read_text(encoding="utf-8")
    assert "linie veche" not in content
    assert "Log curățat manual" in content


def test_clear_logs_creates_file_when_missing(tmp_path, monkeypatch):
    log_path = tmp_path / "does_not_exist_yet.log"
    monkeypatch.setattr(us, "LOG_FILE", str(log_path))
    us.clear_logs()
    assert log_path.exists()
    assert "Log curățat manual" in log_path.read_text(encoding="utf-8")
