"""Pool extins pentru 4+ (2026-10-22): 17-18 numere pe același număr de variante.

Opțiune a utilizatorului, implicit oprită. Cu 16 numere și coverul complet
C(16, bilet, 4) (6/49: 152 de variante; 5/40 și Joker: 404) șansa 4+ pe o
variantă e deja rata pool-ului, plafonul oricărei așezări pe 16 numere. La
același număr de variante, un pool de 17-18 numere cu un cover PARȚIAL de 4
prinde mai des 4+ (6/49, 152: 7,960% -> 8,499% / 9,320%), fără ca vreun prag
3..extrase să scadă: variantele se suprapun mai puțin. Prețul: „4 sigur dacă
ies 4 din pool” devine parțial (la 18 numere, 71,6% din grupele de 4 ale
pool-ului sunt pe o variantă). Numărul așteptat de variante câștigătoare nu se
schimbă: câștigurile se împart pe mai multe extrageri. Nicio afirmație
predictivă.

Designurile sunt calculate offline (`scripts/analysis/extended_pool/`) și stau
în ``covering_designs/extended/X_<v>_<bilet>_<extrase>_<univers>_<variante>.txt``
(poziții 1..v, 1 = numărul cel mai bine clasat). La FIECARE apel designul se
pune pe clasamentul pool-ului de v numere și se certifică față de ce s-ar juca
fără opțiune, coverul complet pe primele 16 numere:

- același număr de variante distincte (cât coverul complet);
- cele 16 numere ale coverului rămân jucate;
- P(cel puțin t pe o variantă) nu scade la niciun prag de la 3 la
  min(bilet, extrase) și crește strict la cel puțin unul (exact, enumerare);
- numărul absolut de grupe de 4 numere aflate pe o variantă nu scade.

Altfel motorul joacă pool-ul de 16, iar auditul spune de ce.
"""

from __future__ import annotations

import hashlib
import logging
from itertools import combinations
from pathlib import Path

from covering.common import _sorted_pool
from covering.probability import MAX_EXACT_POOL, wheel_hit_probabilities

logger = logging.getLogger(__name__)

EXTENDED_SIZES = (17, 18)
BASE_POOL = 16
GUARANTEE = 4
MIN_THRESHOLD = 3
_MODULE_DIR = Path(__file__).resolve().parents[1]
DESIGN_DIR = _MODULE_DIR / "covering_designs" / "extended"
_CACHE: dict[tuple, tuple[tuple[tuple[int, ...], ...], str] | None] = {}


def normalize_size(value) -> int:
    """0 (oprit) sau o mărime livrată (17, 18); orice altceva -> 0."""
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return size if size in EXTENDED_SIZES else 0


def extended_budget(pick: int) -> int | None:
    """Numărul de variante: cât coverul complet C(16, bilet, 4) al aplicației."""
    from covering.designs import complete_design_size

    return complete_design_size(BASE_POOL, int(pick), GUARANTEE)


def design_name(v: int, pick: int, draw_n: int, max_num: int, budget: int) -> str:
    return f"X_{int(v)}_{int(pick)}_{int(draw_n)}_{int(max_num)}_{int(budget)}.txt"


def _parse(text: str, v: int, pick: int, budget: int) -> tuple[tuple[tuple[int, ...], ...], str]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.isascii():
            raise ValueError("linie ne-ASCII")
        nums = tuple(sorted(int(x) for x in line.split()))
        if len(nums) != pick or len(set(nums)) != pick or nums[0] < 1 or nums[-1] > v:
            raise ValueError(f"varianta invalida: {line}")
        rows.append(nums)
    if len(rows) != budget or len(set(rows)) != budget:
        raise ValueError(f"{len(rows)} variante distincte, nu {budget}")
    if {p for r in rows for p in r} != set(range(1, v + 1)):
        raise ValueError("nu joaca toate pozitiile")
    digest = hashlib.sha256("\n".join(" ".join(map(str, r)) for r in rows).encode("ascii"))
    return tuple(rows), digest.hexdigest()[:12]


def load_design(v: int, pick: int, draw_n: int, max_num: int):
    """(blocuri pe poziții, amprentă) sau None; memorat pe data și mărimea fișierului."""
    budget = extended_budget(pick)
    if budget is None or normalize_size(v) == 0:
        return None
    path = DESIGN_DIR / design_name(v, pick, draw_n, max_num, budget)
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_mtime_ns, info.st_size)
    if key not in _CACHE:
        if len(_CACHE) > 32:
            _CACHE.clear()
        try:
            _CACHE[key] = _parse(path.read_text(encoding="utf-8-sig"), int(v), int(pick), int(budget))
        except (OSError, ValueError) as exc:
            logger.warning("[WHEEL-extins] ignor %s: %s", path.name, exc)
            _CACHE[key] = None
    return _CACHE[key]


def design_signature(v: int, pick: int, draw_n: int, max_num: int) -> str | None:
    """Amprenta designului pentru cheia WF și a worker-ului; None fără design."""
    entry = load_design(v, pick, draw_n, max_num)
    return entry[1] if entry else None


def covered_groups(tickets, size: int = GUARANTEE) -> int:
    """Câte grupe distincte de `size` numere stau pe cel puțin o variantă."""
    return len({c for t in tickets for c in combinations(sorted(int(n) for n in t), size)})


def _plain(tickets, pick: int) -> list[list[int]]:
    return [sorted(int(n) for n in list(t)[:pick]) for t in tickets]


def _ordered(numbers, order) -> list[int]:
    """`numbers` în ordinea clasamentului `order` (restul la coadă, crescător).

    Șansa exactă își memorează tabelul pe măștile pozițiilor: cu ordinea
    clasamentului, aceleași bilete pe poziții dau aceleași măști la fiecare pas
    WF, deci calculul se refolosește."""
    wanted = {int(n) for n in numbers}
    head = [int(n) for n in order if int(n) in wanted]
    return head + sorted(wanted - set(head))


def certify(incumbent, candidate, pick: int, draw_n: int, max_num: int, order=()):
    """(motiv, câștig pe prag); motivul e gol la certificare reușită.

    `order` = clasamentul pool-ului extins (opțional, numai pentru memorie)."""
    inc, cand = _plain(incumbent, pick), _plain(candidate, pick)
    budget = extended_budget(pick)
    n_inc = len({tuple(t) for t in inc})
    if len({tuple(t) for t in cand}) != len(cand):
        return "designul are variante repetate", {}
    if n_inc != len(cand):
        return (
            f"varianta de bază pe 16 numere are {n_inc} variante, nu {len(cand)} "
            f"(coverul complet are {budget}; altă metodă de wheeling sau alt cover)"
        ), {}
    played_inc = {n for t in inc for n in t}
    played_cand = {n for t in cand for n in t}
    if not played_inc <= played_cand:
        return "cele 16 numere de bază nu sunt toate în pool-ul extins", {}
    if len(played_cand) > MAX_EXACT_POOL:
        return "pool peste limita șansei exacte", {}
    before = wheel_hit_probabilities(_ordered(played_inc, order), inc, draw_n, max_num)["ticket"]
    after = wheel_hit_probabilities(_ordered(played_cand, order), cand, draw_n, max_num)["ticket"]
    top = min(int(pick), int(draw_n))
    thresholds = range(MIN_THRESHOLD, top + 1)
    if any(after[t] < before[t] - 1e-15 for t in thresholds):
        return "șansa ar scădea la un prag", {}
    if not any(after[t] > before[t] + 1e-15 for t in thresholds):
        return "niciun câștig strict", {}
    if covered_groups(cand) < covered_groups(inc):
        return "mai puține grupe de 4 pe variante decât pe 16 numere", {}
    return "", {t: after[t] - before[t] for t in thresholds}


def apply(pool, scores, incumbent, size: int, pick: int, draw_n: int, max_num: int):
    """(variante sau None, audit). `pool` = cele v numere alese, `incumbent` =
    variantele coverului complet pe primele 16 (fără numărul Joker)."""
    v = normalize_size(size)
    audit = {
        "requested": int(size or 0),
        "applied": False,
        "reason": "",
        "pool_size": len(pool),
        "base_pool": BASE_POOL,
    }
    inc = _plain(incumbent, pick)
    audit["base_pool"] = sorted({n for t in inc for n in t})
    if not v:
        audit["reason"] = "mărime nelivrată"
        return None, audit
    if len(pool) != v:
        audit["reason"] = (
            f"pool-ul are {len(pool)} numere, nu {v} (bază restrânsă prea îngustă)"
        )
        return None, audit
    budget = extended_budget(pick)
    entry = load_design(v, pick, draw_n, max_num)
    if budget is None or entry is None:
        audit["reason"] = "nu există design pentru acest joc"
        return None, audit
    blocks, digest = entry
    audit.update(design=design_name(v, pick, draw_n, max_num, budget), signature=digest, variants=budget)
    order = _sorted_pool(pool, scores)
    candidate = [sorted(order[p - 1] for p in b) for b in blocks]
    why, gain = certify(incumbent, candidate, pick, draw_n, max_num, order)
    p_before = (
        wheel_hit_probabilities(_ordered(audit["base_pool"], order), inc, draw_n, max_num)["ticket"]
        if inc
        else {}
    )
    p_after = wheel_hit_probabilities(order, candidate, draw_n, max_num)["ticket"]
    top = min(int(pick), int(draw_n))
    audit["chances"] = {
        str(t): {"base": float(p_before.get(t, 0.0)), "extended": float(p_after[t])}
        for t in range(MIN_THRESHOLD, top + 1)
    }
    audit["groups4"] = {
        "base": covered_groups(inc),
        "extended": covered_groups(candidate),
        "pool_total": len(list(combinations(range(v), GUARANTEE))),
    }
    # Garanția 3 rămâne completă pe toate designurile livrate: 3 numere ieșite
    # din pool stau mereu pe o variantă. Se măsoară, nu se presupune.
    audit["guarantee3_complete"] = covered_groups(candidate, 3) == len(
        list(combinations(range(v), 3))
    )
    if why:
        audit["reason"] = why
        return None, audit
    audit["applied"] = True
    audit["gain"] = {str(t): float(g) for t, g in gain.items()}
    return candidate, audit
