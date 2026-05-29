"""Helpere neutre față de framework-ul de UI, partajate între front-end-ul
Streamlit (app.py) și cel NiceGUI (app_nicegui.py).

Conțin DOAR logică pură (decode rezultat job, citire/curățare loguri, lansare
worker) — fără import de streamlit/nicegui — ca ambele UI-uri să le poată folosi
identic. Backend-ul real (job_queue, worker, engine) rămâne neatins.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pickle
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

_node = platform.node().replace(" ", "_") if platform.node() else ""
LOG_FILE = f"loto-{_node}.log" if _node else "loto.log"

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
