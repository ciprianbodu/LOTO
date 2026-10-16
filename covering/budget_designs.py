"""Designuri cu buget precalculate (2026-10-12): bilete pentru 65+ variante.

Peste 64 de bilete, căutarea exactă din proces (`covering.budget_climb`, 12.000
de mutări, acceptare strict nedescrescătoare) rămâne într-un optim local:
măsurat pe 6/49, pool 16, garanție 4, 100 de bilete, P(4+ pe un bilet) e
6,03% față de 6,73% pentru o recoacere lungă (3 milioane de mutări, ~50 s în
C, `scripts/analysis/budget_anneal.c`). În Python aceeași căutare ar costa
~15 minute pe geometrie, deci rezultatul e calculat offline și livrat ca
fișier, cum sunt și coverele complete din `covering_designs/`.

Format, un fișier pe geometrie, ``covering_designs/budget/H_<v>_<pick>_<g>.txt``::

    # comentarii
    B 65
    1 2 3 4 5 6        <- 65 de linii, poziții 1..v (1 = numărul cel mai bine clasat)
    ...
    B 66
    ...

Un design intră în producție numai certificat la fiecare apel față de wheel-ul
construit în proces, cu regula etapei de recoacere (`covering.anneal_hits`):
același număr de bilete distincte, numerele jucate rămân jucate, P(cel puțin
t pe un bilet) nu scade la niciun prag de la 3 la ``draw_n``, crește strict la
cel puțin unul, acoperirea garanției nu scade. Altfel wheel-ul din proces
rămâne. Numai geometrie: pozițiile urmează ordinea canonică a scorurilor;
nicio afirmație predictivă (rata pool-ului nu se schimbă).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from covering.anneal_hits import _certify

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parents[1]
_BUDGET_DIRS = [
    _MODULE_DIR / "covering_designs" / "budget",
    Path("covering_designs/budget"),
]
MIN_THRESHOLD = 3
_FILE_CACHE: dict[tuple, dict[int, tuple[tuple[tuple[int, ...], ...], str]]] = {}


def budget_design_path(v: int, pick: int, guarantee: int) -> str:
    return f"H_{int(v)}_{int(pick)}_{int(guarantee)}.txt"


def _parse(text: str, v: int, pick: int) -> dict[int, tuple[tuple[tuple[int, ...], ...], str]]:
    """{buget: (blocuri în poziții, amprenta secțiunii)}; o secțiune invalidă e sărită."""
    sections: dict[int, list[str]] = {}
    current: list[str] | None = None
    budget = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("B "):
            try:
                budget = int(line.split()[1])
            except (IndexError, ValueError):
                budget, current = None, None
                continue
            current = sections.setdefault(budget, [])
            continue
        if current is not None:
            current.append(line)
    out = {}
    for budget, lines in sections.items():
        blocks = []
        ok = True
        for line in lines:
            try:
                nums = [int(x) for x in line.replace(",", " ").split() if x.strip()]
            except ValueError:
                ok = False  # un caracter nenumeric strică numai secțiunea lui
                break
            if len(nums) != pick or len(set(nums)) != pick or any(n < 1 or n > v for n in nums):
                ok = False
                break
            blocks.append(tuple(sorted(nums)))
        if not ok or len(blocks) != budget or len(set(blocks)) != budget:
            logger.warning("[WHEEL-budget] secțiune invalidă B %s în H_%s_%s", budget, v, pick)
            continue
        if any(not any(p in b for b in blocks) for p in range(1, v + 1)):
            logger.warning("[WHEEL-budget] B %s nu joacă toate pozițiile", budget)
            continue
        digest = hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()[:12]
        out[budget] = (tuple(blocks), digest)
    return out


def _designs(v: int, pick: int, guarantee: int):
    """Secțiunile fișierului geometriei, memorate pe data modificării."""
    name = budget_design_path(v, pick, guarantee)
    for directory in _BUDGET_DIRS:
        path = directory / name
        try:
            info = path.stat()
        except OSError:
            continue
        # Data și mărimea: un fișier înlocuit în aceeași secundă, cu altă
        # lungime, nu mai e servit din memorie.
        key = (str(path), info.st_mtime_ns, info.st_size)
        if key not in _FILE_CACHE:
            if len(_FILE_CACHE) > 64:
                _FILE_CACHE.clear()
            try:
                _FILE_CACHE[key] = _parse(path.read_text(encoding="utf-8-sig"), int(v), int(pick))
            except (OSError, ValueError) as exc:
                logger.warning("[WHEEL-budget] ignor %s: %s", path, exc)
                _FILE_CACHE[key] = {}
        return _FILE_CACHE[key]
    return {}


def _stored(v: int, pick: int, guarantee: int, budget: int):
    """Secțiunea pentru buget: întâi la garanția cerută, apoi la cea mai mare
    garanție mai mică, până la 4 (2026-10-16). Un design de garanție 4 fără
    două bilete cu 5 numere comune trece și certificarea pe garanția 5 sau 6
    (aceleași bilete distincte, niciun prag în scădere); certificarea se face
    oricum la apel, pe garanția cerută."""
    for source in [int(guarantee), *range(int(guarantee) - 1, 3, -1)]:
        entry = _designs(int(v), int(pick), source).get(int(budget))
        if entry:
            return entry
    return None


def load_budget_design(v: int, pick: int, guarantee: int, budget: int):
    """Blocurile (poziții 1..v) pentru bugetul cerut, sau None."""
    entry = _stored(v, pick, guarantee, budget)
    return [list(b) for b in entry[0]] if entry else None


def budget_design_signature(v: int, pick: int, guarantee: int, budget: int) -> str | None:
    """Amprenta secțiunii livrate pentru cheia WF; None când nu există design."""
    entry = _stored(v, pick, guarantee, budget)
    return entry[1] if entry else None


def improve_with_budget_design(ordered_pool, wheel, guarantee: int, draw_n: int, max_num: int):
    """``(wheel, audit)``: designul precalculat pe pozițiile pool-ului, dacă e certificat."""
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
    design = load_budget_design(v, pick, int(guarantee), len(tickets))
    if design is None:
        audit["reason"] = "no stored design"
        return wheel, audit
    candidate = [sorted(pool[p - 1] for p in block) for block in design]
    why, gain, _cov = _certify(pool, tickets, candidate, int(guarantee), int(draw_n), int(max_num))
    if why:
        audit["reason"] = why
        return wheel, audit
    audit.update(applied=True, gain=gain)
    return candidate, audit
