"""Helpere neutre față de framework-ul de UI (NiceGUI: app_nicegui.py).

Conțin DOAR logică pură (decode rezultat job, citire/curățare loguri, lansare
worker) — fără import de nicegui — ca UI-ul să le poată folosi identic.
Backend-ul real (job_queue, worker, engine) rămâne neatins.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pickle
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

LOG_FILE = "loto.log"

PROJECT_ROOT = Path(__file__).resolve().parent
WORKER_PATH = PROJECT_ROOT / "worker.py"


# --------------------------------------------------------------------------- #
# Loguri
# --------------------------------------------------------------------------- #
def read_logs_filtered(n_lines: int = 50) -> str:
    if not os.path.exists(LOG_FILE):
        return "Nu există log-uri încă."
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent = lines[-n_lines:]
        filtered: list[str] = []
        pattern = re.compile(r"Iteratia \d+: Acoperite \d+/\d+")
        similar = 0
        for line in recent:
            if pattern.search(line):
                similar += 1
                if similar <= 3:
                    filtered.append(line)
                elif similar == 4:
                    filtered.append("... [mesaje de progres ascunse pentru claritate] ...\n")
            else:
                similar = 0
                filtered.append(line)
        return "".join(filtered).strip()
    except Exception as e:  # noqa: BLE001
        return f"Eroare citire log: {e}"


def clear_logs() -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- Log curățat manual ---\n")
    except OSError as exc:
        logger.warning("clear_logs: nu am putut rescrie %s: %s", LOG_FILE, exc)


# --------------------------------------------------------------------------- #
# Decode rezultat job (pickle+b64, scris de worker._pack_result_payload)
# --------------------------------------------------------------------------- #
def decode_queue_result(result_json: str) -> object:
    """La cancel-race worker-ul poate scrie payload gol ('{}'); întoarcem None
    și apelantul tratează non-tuple ca rezultat invalid."""
    try:
        data = json.loads(result_json)
    except Exception:  # noqa: BLE001
        return None
    payload = str((data or {}).get("payload", "")) if isinstance(data, dict) else ""
    if not payload:
        return None
    try:
        return pickle.loads(base64.b64decode(payload))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Scriere atomică (anti-corupere: crash/sleep/sync OneDrive la mijlocul scrierii)
# --------------------------------------------------------------------------- #
def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Scrie în <file>.tmp (flush+fsync) apoi os.replace — atomic pe Win+POSIX.
    Cititorii văd fie versiunea veche completă, fie cea nouă completă."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def atomic_write_json(path, obj, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))


class file_lock:
    """Lock advisory cross-proces (Win+POSIX) prin lock-file O_EXCL, cu timeout.
    Previne lost-updates când worker-ul și UI-ul fac read-modify-write pe același
    fișier (ex. adaptive_state.json). La timeout continuă fără lock (anti-deadlock
    pe lock-uri stale), pentru că scrierea în sine e oricum atomică."""

    def __init__(self, target, timeout: float = 10.0):
        self.lockpath = str(target) + ".lock"
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self._fd = os.open(self.lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - start > self.timeout:
                    logger.debug("[file_lock] timeout pe %s — continui fără lock", self.lockpath)
                    return self
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        try:
            os.unlink(self.lockpath)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def is_worker_running() -> bool:
    root = str(PROJECT_ROOT)
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            cmd = " ".join(str(p) for p in cmdline)
            if "worker.py" in cmd and root in cmd:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


def ensure_worker_running() -> None:
    if is_worker_running():
        return
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [sys.executable, str(WORKER_PATH)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
        )
        logger.info("[ui_shared] worker.py lansat.")
    except Exception as exc:  # noqa: BLE001
        logger.error("[ui_shared] nu pot lansa worker.py: %s", exc)
