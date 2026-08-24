"""Ranking canonic „top-N după scor" — sursa UNICĂ de adevăr pentru tie-break.

De ce există modulul: până în 2026-07 existau trei implementări divergente ale
selecției „top-N după scor" — bench (`runner._top_k`), producție
(`pool_selection.select_pool_from_scores`) și biletul OMNIUS
(`pick_omnius_ticket`, eliminat între timp odată cu modulul `methods_omnius`) —
fiecare cu ALT tie-break la scoruri egale (număr mare /
frecvență / ordinea de iterare a set-ului). Pe scoreri cu puține nivele
distincte, pool-ul VALIDAT de bench diferea de pool-ul GENERAT în producție
(6/16 numere diferite pe un scorer cu 2 nivele), încălcând regula din CLAUDE.md:
„pool_selection = top-N pur după scor (identic cu bench _top_k)".

Regula CANONICĂ de sortare (în această ordine, toate DESCRESCĂTOR):
  1. scor;
  2. frecvență — OPȚIONALĂ (0.0 pentru toți dacă ``freq`` e None). Toate
     call-site-urile actuale (bench + producție) pasează None: regula trebuie să
     fie aplicabilă IDENTIC în ambele, iar bench-ul nu pasează frecvențe —
     consistența bate frecvența ca tie-break;
  3. numărul însuși — determinist; „număr mare întâi" evită degenerarea
     „1,2,3…K" / „cele mai mici compuse" la egalitate de scor.

Contract: modulul e PUR (doar stdlib) — importabil din benchmark/ ȘI core/
fără risc de import circular — iar funcția e simplă, la nivel de modul
(picklabilă pentru workerii ProcessPoolExecutor din bench/walk-forward).
"""

from __future__ import annotations

from collections.abc import Iterable


def longest_consecutive_run(nums: Iterable[int]) -> int:
    """Lungimea celei mai lungi secvențe de întregi consecutivi din ``nums``.

    Folosit ca gardă de degenerare: un scorer unimodal pe axa 1…N (ex. vechea
    ``sum_affinity`` = gaussiană pe |k − medie/n|) produce un pool care e un
    singur bloc consecutiv. Nu e semnal, e geometria formulei.
    """
    s = sorted({int(x) for x in nums})
    if not s:
        return 0
    best = cur = 1
    for a, b in zip(s, s[1:]):
        if b == a + 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def is_consecutive_block(nums: Iterable[int], *, min_size: int = 6) -> bool:
    """True dacă tot setul e un interval [min, max] fără găuri și are ≥ min_size elemente."""
    s = sorted({int(x) for x in nums})
    if len(s) < min_size:
        return False
    return s[-1] - s[0] + 1 == len(s)


def rank_by_score(
    scores: dict[int, float],
    k: int,
    *,
    freq: dict[int, float] | None = None,
) -> list[int]:
    """Primele ``k`` numere după regula canonică, ÎN ORDINEA RANGULUI.

    NU sortează crescător — apelanții care vor listă numerică fac ``sorted()``
    singuri; cei care vor doar apartenența fac ``set()`` (dar tie-break-ul
    decide APARTENENȚA la egalitate de scor, nu doar ordinea!). Filtrarea
    (blacklist, interval 1..max_num) rămâne responsabilitatea apelantului —
    helperul nu filtrează nimic. Dict gol sau ``k <= 0`` -> [].
    """
    if not scores or k <= 0:
        return []
    f = freq or {}
    return [n for n, _ in sorted(
        scores.items(),
        key=lambda kv: (kv[1], f.get(kv[0], 0.0), kv[0]),
        reverse=True,
    )[: int(k)]]
