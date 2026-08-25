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
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "disabled_methods.json"


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
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
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
    """Adaugă (union, nu șterge niciodată) metode în blacklist. Întoarce setul final."""
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
    try:
        _atomic_write_json(_PATH, payload)
        logger.info("[disabled] %d metode legendate (+%d). Fișier: %s", len(cur), len(cur) - before, _PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[disabled] scriere %s eșuată: %s", _PATH, exc)
    return cur
