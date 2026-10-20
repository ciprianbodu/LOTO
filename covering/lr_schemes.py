"""Schemele reduse predefinite ale Loteriei Române (2026-10-17).

La agenție o schemă redusă se joacă după cod: se marchează n numere, iar
terminalul le desfășoară în variantele schemei. Desfășurările vin din broșurile
loto.ro, importate offline prin `scripts/analysis/import_lr_schemes.py`, care
scrie o schemă numai dacă indexul de câștiguri recalculat din variante
coincide exact cu cel tipărit. Fișierele sunt
``covering_designs/lr/LR_<joc>_cod<NN>.txt``: o variantă pe linie, poziții
1..n.

Poziția 1 este cel mai mic număr jucat (broșura 6/49, exemplul 3: numerele
„teoretice” 1..13 corespund numerelor jucate în ordine crescătoare). Schema se
pune deci pe pool-ul sortat crescător, nu pe clasamentul metodei. Pe extrageri
uniforme șansele nu depind de această așezare.

Numai geometrie: o schemă nu schimbă rata pool-ului (hipergeometrică), ci felul
în care numerele prinse în pool ajung pe aceeași variantă.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from math import comb
from pathlib import Path

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parents[1]
LR_DIR = _MODULE_DIR / "covering_designs" / "lr"
# Eticheta jocului în aplicație -> cheia din numele fișierului; bilet, extrase, univers.
_GAMES = {
    "6/49": ("649", 6, 6, 49),
    "5/40": ("540", 5, 6, 40),
    "joker": ("joker", 5, 5, 45),
}
_FILE = re.compile(r"^LR_(649|540|joker)_cod(\d+)\.txt$")


@dataclass(frozen=True)
class LRScheme:
    """O schemă oficială: `variants` pe pozițiile 1..n, în ordinea broșurii."""

    game: str
    code: int
    n: int
    variants: tuple[tuple[int, ...], ...]

    @property
    def label(self) -> str:
        return f"Cod {self.code}"

    @property
    def size(self) -> int:
        return len(self.variants)


def _parse(path: Path, game: str, code: int) -> LRScheme:
    pick, universe = _GAMES[game][1], _GAMES[game][3]
    header: dict[str, int] = {}
    rows: list[tuple[int, ...]] = []
    # utf-8-sig: un fișier salvat cu BOM de un editor Windows rămâne citibil.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*(numere|variante):\s*(\d+)\s*$", line)
            if m:
                header[m.group(1)] = int(m.group(2))
            continue
        rows.append(tuple(int(x) for x in line.split()))
    n = header.get("numere")
    if n is None or header.get("variante") != len(rows):
        raise ValueError("antet fără numere sau cu alt număr de variante")
    if not rows or not pick < n <= universe:
        raise ValueError(
            f"schemă fără variante sau cu {n} numere în afara {pick + 1}..{universe}"
        )
    if any(
        len(r) != pick or len(set(r)) != pick or not all(1 <= p <= n for p in r)
        for r in rows
    ):
        raise ValueError(f"variantă care nu are {pick} poziții distincte din 1..{n}")
    if len(set(rows)) != len(rows):
        raise ValueError("variante repetate")
    if {p for r in rows for p in r} != set(range(1, n + 1)):
        raise ValueError("poziție nejucată")
    return LRScheme(game, code, n, tuple(tuple(sorted(r)) for r in rows))


@lru_cache(maxsize=4)
def _load(directory: str) -> tuple[LRScheme, ...]:
    out = []
    base = Path(directory)
    if not base.is_dir():
        return ()
    labels = {key: label for label, (key, *_rest) in _GAMES.items()}
    seen: set[tuple[str, int]] = set()
    for path in sorted(base.iterdir()):
        m = _FILE.match(path.name)
        if not m:
            continue
        key = (labels[m.group(1)], int(m.group(2)))
        if key in seen:
            logger.warning("Schema LR %s ignorată: codul apare de două ori", path.name)
            continue
        try:
            out.append(_parse(path, *key))
            seen.add(key)
        except (OSError, ValueError) as exc:
            # Un fișier stricat e sărit singur; celelalte scheme rămân.
            logger.warning("Schema LR %s ignorată: %s", path.name, exc)
    return tuple(sorted(out, key=lambda s: (s.game, s.n, s.size, s.code)))


@lru_cache(maxsize=4)
def _manifest(directory: str) -> tuple[tuple[str, int, int], ...]:
    """(joc, cod, numere) din `index_oficial.json`, scris de import."""
    labels = {key: label for label, (key, *_rest) in _GAMES.items()}
    try:
        entries = json.loads(
            (Path(directory) / "index_oficial.json").read_text("utf-8")
        )
        return tuple((labels[e["game"]], int(e["code"]), int(e["n"])) for e in entries)
    except OSError, ValueError, KeyError, TypeError:
        return ()


def unloaded_schemes(game: str, n: int) -> list[str]:
    """Codurile importate pentru (joc, numere) al căror fișier nu s-a putut citi.

    Panoul spune atunci că schema nu s-a încărcat, nu că Loteria Română n-o are."""
    loaded = {s.code for s in lr_schemes(game, n)}
    return [
        f"Cod {code}"
        for g, code, size in _manifest(str(LR_DIR))
        if g == game and size == int(n) and code not in loaded
    ]


def lr_schemes(game: str | None = None, n: int | None = None) -> list[LRScheme]:
    """Schemele valide, filtrate pe joc („6/49”, „5/40”, „joker”) și pe numere."""
    return [
        s
        for s in _load(str(LR_DIR))
        if (game is None or s.game == game) and (n is None or s.n == int(n))
    ]


def scheme_tickets(scheme: LRScheme, pool) -> list[list[int]]:
    """Variantele schemei pe numerele jucate, cum le desfășoară terminalul."""
    nums = sorted({int(x) for x in pool})
    if len(nums) != scheme.n:
        raise ValueError(f"{scheme.label} cere {scheme.n} numere, nu {len(nums)}")
    return [[nums[p - 1] for p in v] for v in scheme.variants]


@lru_cache(maxsize=64)
def scheme_odds(scheme: LRScheme) -> dict[int, float]:
    """P(cel puțin t pe o variantă) la o extragere uniformă, t = 3..min(bilet, extrase).

    Profilul exact e limitat la 16 numere, cât are cel mai mare pool al
    aplicației; schemele 5/40 de 17-19 numere ridică ValueError.
    """
    from covering.probability import wheel_hit_probabilities

    _, pick, draw_n, max_num = _GAMES[scheme.game]
    odds = wheel_hit_probabilities(
        list(range(1, scheme.n + 1)),
        [list(v) for v in scheme.variants],
        draw_n,
        max_num,
    )
    return {t: float(odds["ticket"][t]) for t in range(3, min(pick, draw_n) + 1)}


@lru_cache(maxsize=64)
def scheme_guarantees(scheme: LRScheme) -> dict[int, dict[int, float]]:
    """{k ieșite din cele n: {t: fracția cazurilor cu cel puțin t pe o variantă}}.

    k merge de la 3 la min(n, extrase); t de la 3 la min(k, bilet). 1.0 = sigur.
    Ca la `scheme_odds`, profilul exact e limitat la 16 numere.
    """
    from covering.probability import wheel_hit_profile

    _, pick, draw_n, _ = _GAMES[scheme.game]
    counts = wheel_hit_profile(
        list(range(1, scheme.n + 1)), [list(v) for v in scheme.variants]
    )
    out: dict[int, dict[int, float]] = {}
    for k in range(3, min(scheme.n, draw_n) + 1):
        total = comb(scheme.n, k)
        out[k] = {t: counts[k][t] / total for t in range(3, min(k, pick) + 1)}
    return out


@lru_cache(maxsize=64)
def _position_masks(scheme: LRScheme) -> tuple[int, ...]:
    return tuple(sum(1 << (p - 1) for p in v) for v in scheme.variants)


def best_hits(scheme: LRScheme, pool, drawn) -> int:
    """Cele mai multe numere extrase pe o singură variantă a schemei puse pe `pool`."""
    nums = sorted({int(x) for x in pool})
    if len(nums) != scheme.n:
        raise ValueError(f"{scheme.label} cere {scheme.n} numere, nu {len(nums)}")
    hit = {int(x) for x in drawn}
    mask = sum(1 << i for i, x in enumerate(nums) if x in hit)
    return max((m & mask).bit_count() for m in _position_masks(scheme))


# --------------------------------------------------------------------------- #
# Optimalitatea pe praguri
# --------------------------------------------------------------------------- #
def _upper_bound(
    n: int, size: int, pick: int, t: int, draw_n: int, max_num: int
) -> float:
    """Cea mai mare P(cel puțin t pe o variantă) posibilă cu `size` variante pe n numere.

    Numără, pentru fiecare k numere ieșite din pool, câte k-submulțimi pot
    conține o t-submulțime de pe o variantă: cel mult C(n, k) și cel mult
    D·C(n-t, k-t), unde D = min(C(n, t), size·C(pick, t)) e numărul maxim de
    t-submulțimi acoperite. La k = bilet și t = bilet - 1, fiecare variantă își
    conține toate cele `pick` t-submulțimi, deci e numărată de `pick` ori:
    marginea scade cu (pick - 1)·size. La t = bilet, D = size exact.
    """
    total = comb(max_num, draw_n)
    d_max = size if t == pick else min(comb(n, t), size * comb(pick, t))
    prob = 0.0
    for k in range(t, min(n, draw_n) + 1):
        rest = draw_n - k
        if rest > max_num - n:
            continue
        cap = min(comb(n, k), d_max * comb(n - t, k - t))
        if k == pick and t == pick - 1:
            cap = min(cap, (n - t) * d_max - (pick - 1) * size)
        prob += cap * comb(max_num - n, rest) / total
    return prob


@lru_cache(maxsize=64)
def optimal_thresholds(scheme: LRScheme) -> tuple[int, ...]:
    """Pragurile la care nicio așezare cu tot atâtea variante pe aceleași numere
    nu prinde mai des: șansa exactă a schemei atinge marginea superioară
    (`_upper_bound`). Cod 59 (6/49, 16 numere, 112 variante) e o împachetare
    perfectă: o atinge la 3+, 4+, 5+ și 6."""
    _, pick, draw_n, max_num = _GAMES[scheme.game]
    odds = scheme_odds(scheme)
    return tuple(
        t
        for t, p in odds.items()
        if p
        >= _upper_bound(scheme.n, scheme.size, pick, t, draw_n, max_num) * (1 - 1e-12)
    )


# --------------------------------------------------------------------------- #
# Schema ca variante ale aplicației, la același număr de bilete
# --------------------------------------------------------------------------- #
def lr_candidates(v: int, pick: int, size: int) -> list[LRScheme]:
    """Schemele cu `v` numere, variante de `pick` și exact `size` variante.

    Geometria jocului nu contează aici: o schemă e o așezare pe poziții, iar
    certificarea (`improve_with_lr_scheme`) o judecă pe extragerile jocului."""
    return [
        s
        for s in lr_schemes()
        if s.n == int(v) and s.size == int(size) and _GAMES[s.game][1] == int(pick)
    ]


def lr_scheme_signature(v: int, pick: int, size: int) -> str | None:
    """Amprenta schemelor pe care le poate adopta motorul la acest plafon (cheia WF)."""
    found = lr_candidates(v, pick, size)
    if not found:
        return None
    import hashlib

    text = "|".join(
        f"{s.game}:{s.code}:" + ";".join(" ".join(map(str, r)) for r in s.variants)
        for s in found
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:12]


def improve_with_lr_scheme(
    ordered_pool, wheel, guarantee: int, draw_n: int, max_num: int
):
    """``(wheel, audit)``: o schemă LR de aceeași mărime, pe pozițiile pool-ului.

    Intră numai certificată față de wheel-ul dat (`anneal_hits._certify`):
    același număr de bilete distincte, numerele jucate rămân, niciun prag de la
    3 la numărul extras nu scade, câștig strict, acoperirea garanției nu scade.
    Pe extrageri uniforme, șansele nu depind de numerele puse pe poziții."""
    from covering.anneal_hits import _certify

    audit = {"applied": False, "reason": "", "gain": {}, "code": None}
    pool = [int(n) for n in ordered_pool]
    tickets = [sorted(int(n) for n in t) for t in wheel]
    if not tickets or len(set(pool)) != len(pool):
        audit["reason"] = "outside limits"
        return wheel, audit
    pick = len(tickets[0])
    found = lr_candidates(len(pool), pick, len(tickets))
    if not found or any(len(t) != pick for t in tickets):
        audit["reason"] = "no scheme of this size"
        return wheel, audit
    best = tickets
    for scheme in found:
        candidate = [sorted(pool[p - 1] for p in v) for v in scheme.variants]
        why, gain, _cov = _certify(
            pool, best, candidate, int(guarantee), int(draw_n), int(max_num)
        )
        if not why:
            best = candidate
            audit.update(applied=True, gain=gain, code=scheme.code, reason="")
        elif not audit["applied"]:
            audit["reason"] = why
    return (best if audit["applied"] else wheel), audit


@lru_cache(maxsize=4)
def _app_same_size(directory: str) -> dict:
    """{(joc, cod): [(garanție, variante, {t: P})]} din `app_same_size.json`.

    Fișierul e scris offline de `scripts/analysis/analyze_lr_schemes.py`:
    wheel-urile motorului cu bugetul egal cu fiecare schemă, pe fiecare garanție.
    Lipsă sau stricat: {} (panoul nu mai compară la același cost)."""
    labels = {key: label for label, (key, *_rest) in _GAMES.items()}
    try:
        entries = json.loads(
            (Path(directory) / "app_same_size.json").read_text("utf-8")
        )
        return {
            (e["game"] if e["game"] in _GAMES else labels[e["game"]], int(e["code"])): [
                (
                    int(d["g"]),
                    int(d["variants"]),
                    {int(t): float(p) for t, p in d["P"].items()},
                )
                for d in e["designs"]
            ]
            for e in entries
        }
    except OSError, ValueError, KeyError, TypeError:
        return {}


def app_same_size(scheme: LRScheme) -> list[tuple[int, int, dict[int, float]]]:
    """Wheel-urile aplicației cu bugetul schemei: (garanție, variante, șanse)."""
    return _app_same_size(str(LR_DIR)).get((scheme.game, scheme.code), [])
