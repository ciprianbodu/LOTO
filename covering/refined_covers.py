"""Covere complete rafinate pentru pragurile de peste garanție (2026-10-16).

Fără plafon de bilete, producția joacă coverul complet din `covering_designs/`
(La Jolla). Cu garanția completă, P(cel puțin garanția pe un bilet) e deja
maximul pool-ului, dar pragul următor depinde de așezarea biletelor. Un cover
rafinat offline (`scripts/analysis/gen_refined_covers.py`, recoacere în C)
păstrează garanția și numărul de bilete și ridică pragul g+1 pe geometria unui
joc anume, deci fișierul poartă și numerele extrase și universul:
``covering_designs/refined/R_<v>_<pick>_<g>_<extrase>_<univers>.txt``, câte un
bilet pe linie, poziții 1..v (1 = numărul cel mai bine clasat).

La fiecare apel designul se pune pe pozițiile clasamentului și intră numai
certificat față de wheel-ul construit în proces (`covering.anneal_hits._certify`):
același număr de bilete distincte, numerele jucate rămân jucate, niciun prag de
la 3 la numărul extras nu scade, câștig strict, acoperirea garanției nu scade
(deci rămâne 100%). Altfel rămâne wheel-ul din proces. Fișierele La Jolla nu
se modifică. Numai geometrie, extrageri uniforme: rata pool-ului nu se schimbă.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from covering.anneal_hits import _certify

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parents[1]
_REFINED_DIRS = [
    _MODULE_DIR / "covering_designs" / "refined",
    Path("covering_designs/refined"),
]
_FILE_CACHE: dict[tuple, tuple[tuple[tuple[int, ...], ...], str] | None] = {}


def refined_cover_path(v: int, pick: int, guarantee: int, draw_n: int, max_num: int) -> str:
    return f"R_{int(v)}_{int(pick)}_{int(guarantee)}_{int(draw_n)}_{int(max_num)}.txt"


def _parse(text: str, v: int, pick: int):
    """(blocuri în poziții, amprentă) sau None pentru un fișier invalid."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    blocks = []
    for line in lines:
        nums = [int(x) for x in line.replace(",", " ").split()]
        if len(nums) != pick or len(set(nums)) != pick or any(n < 1 or n > v for n in nums):
            return None
        blocks.append(tuple(sorted(nums)))
    if not blocks or len(set(blocks)) != len(blocks):
        return None
    if {p for b in blocks for p in b} != set(range(1, v + 1)):
        return None
    digest = hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()[:12]
    return tuple(blocks), digest


def _entry(v: int, pick: int, guarantee: int, draw_n: int, max_num: int):
    name = refined_cover_path(v, pick, guarantee, draw_n, max_num)
    for directory in _REFINED_DIRS:
        path = directory / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        key = (str(path), stamp)
        if key not in _FILE_CACHE:
            if len(_FILE_CACHE) > 128:
                _FILE_CACHE.clear()
            try:
                parsed = _parse(path.read_text(encoding="utf-8-sig"), int(v), int(pick))
            except (OSError, ValueError) as exc:
                logger.warning("[WHEEL-refined] ignor %s: %s", path, exc)
                parsed = None
            if parsed is None:
                logger.warning("[WHEEL-refined] fișier invalid %s", path)
            _FILE_CACHE[key] = parsed
        return _FILE_CACHE[key]
    return None


def load_refined_cover(v: int, pick: int, guarantee: int, draw_n: int, max_num: int):
    """Blocurile (poziții 1..v) ale coverului rafinat pentru geometrie, sau None."""
    entry = _entry(int(v), int(pick), int(guarantee), int(draw_n), int(max_num))
    return [list(b) for b in entry[0]] if entry else None


def refined_cover_signature(v: int, pick: int, guarantee: int, draw_n: int, max_num: int) -> str | None:
    """Amprenta coverului rafinat pentru cheia WF; None când nu există."""
    entry = _entry(int(v), int(pick), int(guarantee), int(draw_n), int(max_num))
    return entry[1] if entry else None


def improve_with_refined_cover(ordered_pool, wheel, guarantee: int, draw_n: int, max_num: int):
    """``(wheel, audit)``: coverul rafinat pe pozițiile pool-ului, dacă e certificat."""
    audit = {"applied": False, "reason": "", "gain": {}}
    pool = [int(n) for n in ordered_pool]
    tickets = [sorted(int(n) for n in t) for t in wheel]
    if not tickets:
        audit["reason"] = "no tickets"
        return wheel, audit
    pick = len(tickets[0])
    v = len(pool)
    if len(set(pool)) != v or any(len(t) != pick for t in tickets) or v >= int(max_num):
        audit["reason"] = "outside limits"
        return wheel, audit
    design = load_refined_cover(v, pick, int(guarantee), int(draw_n), int(max_num))
    if design is None:
        audit["reason"] = "no refined cover"
        return wheel, audit
    candidate = [sorted(pool[p - 1] for p in block) for block in design]
    why, gain, _cov = _certify(pool, tickets, candidate, int(guarantee), int(draw_n), int(max_num))
    if why:
        audit["reason"] = why
        return wheel, audit
    audit.update(applied=True, gain=gain)
    return candidate, audit
