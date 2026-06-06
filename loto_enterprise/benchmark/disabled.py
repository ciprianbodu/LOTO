"""Blacklist PERMANENT de metode dezactivate (legendate, nu se mai folosesc).

Sursa: disabled_methods.json (rădăcina proiectului). Merge-only: o metodă
adăugată aici NU se mai rulează niciodată în benchmark și nu intră în decizie.
Metodele NOI (neînregistrate aici) nu sunt afectate.

Populat de prune_methods.py pe baza ultimelor rezultate din benchmark.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Set

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "disabled_methods.json"


def load_disabled() -> Set[str]:
    try:
        if _PATH.exists():
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            return set(data.get("disabled", []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[disabled] citire %s eșuată: %s", _PATH, exc)
    return set()


def add_disabled(names: Iterable[str], reason: str = "") -> Set[str]:
    """Adaugă (union, nu șterge niciodată) metode în blacklist. Întoarce setul final."""
    cur = load_disabled()
    before = len(cur)
    cur |= {str(n) for n in names}
    payload = {
        "disabled": sorted(cur),
        "_meta": {
            "note": "Metode legendate — NU se mai rulează în bench și NU se reactivează. "
                    "Merge-only. Metodele noi nu sunt afectate.",
            "last_reason": reason,
            "count": len(cur),
        },
    }
    try:
        _PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[disabled] %d metode legendate (+%d). Fișier: %s", len(cur), len(cur) - before, _PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[disabled] scriere %s eșuată: %s", _PATH, exc)
    return cur
