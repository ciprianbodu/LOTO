"""Blacklist PERMANENT de metode dezactivate (legendate, nu se mai folosesc).

Sursa: disabled_methods.json (rădăcina proiectului). Merge-only: o metodă
adăugată aici NU se mai rulează niciodată în benchmark și nu intră în decizie.
Metodele NOI (neînregistrate aici) nu sunt afectate.

Populat de prune_methods.py pe baza ultimelor rezultate din benchmark.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "disabled_methods.json"


@contextmanager
def _file_lock(path: Path, timeout: float = 10.0):
    """Lock advisory minimal (O_EXCL + timeout pe vârsta fișierului), fără
    dependența ui_shared/psutil (deliberat evitată în tot acest modul — vezi
    `_atomic_write_json`). Mai simplu decât `ui_shared.file_lock` (fără token
    de staleness), suficient pentru singurul apelant curent (`prune_methods.py`,
    rulare rară, nu concurentă). Previne un "lost update" între doi scriitori
    care fac read-modify-write pe `disabled_methods.json` — merge-only, deci o
    scriere pierdută aici ar însemna o metodă tombstoned de un proces, ștearsă
    tăcut de scrierea următoare a altui proces."""
    lockpath = str(path) + ".lock"
    start = time.time()
    acquired = False
    while True:
        try:
            fd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except OSError:
            pass
        try:
            age = time.time() - os.stat(lockpath).st_mtime
        except OSError:
            age = 0.0
        if age > timeout or (time.time() - start) > timeout:
            break  # anti-deadlock: continuăm fără lock, scrierea e oricum atomică
        time.sleep(0.05)
    try:
        yield
    finally:
        if acquired:
            try:
                os.unlink(lockpath)
            except OSError:
                pass


def load_disabled() -> set[str]:
    try:
        if _PATH.exists():
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            return set(data.get("disabled", []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[disabled] citire %s eșuată: %s", _PATH, exc)
    return set()


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Scriere atomică fără dependență de ui_shared (psutil/NiceGUI)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_disabled(names: Iterable[str], reason: str = "") -> set[str]:
    """Adaugă (union, nu șterge niciodată) metode în blacklist.

    Întoarce setul final scris cu succes pe disc. Ridică excepția mai departe
    la eșec de scriere — apelanții (`prune_methods.py`) nu au voie să anunțe
    succes când `disabled_methods.json` n-a fost de fapt atins."""
    with _file_lock(_PATH):
        cur = load_disabled()
        before = len(cur)
        cur |= {str(n) for n in names}
        payload = {
            "disabled": sorted(cur),
            "_meta": {
                "note": "Tombstone: metode ELIMINATE din METHODS. NU le reintroduce. Merge-only.",
                "last_reason": reason,
                "count": len(cur),
            },
        }
        _atomic_write_json(_PATH, payload)
    logger.info(
        "[disabled] %d metode legendate (+%d). Fișier: %s",
        len(cur),
        len(cur) - before,
        _PATH,
    )
    return cur
