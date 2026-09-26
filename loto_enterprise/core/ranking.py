"""Ranking canonic „top-N după scor" — sursa UNICĂ de adevăr pentru tie-break.

De ce există modulul: până în 2026-07 existau implementări divergente ale
selecției „top-N după scor" — bench (`runner._top_k`), producție
(`pool_selection.select_pool_from_scores`) și un al treilea apelant scos din
cod — fiecare cu ALT tie-break la scoruri egale (număr mare /
frecvență / ordinea de iterare a set-ului). Pe scoreri cu puține nivele
distincte, pool-ul VALIDAT de bench diferea de pool-ul GENERAT în producție
(6/16 numere diferite pe un scorer cu 2 nivele), încălcând regula din AGENTS.md:
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

import math
from collections.abc import Iterable


def longest_consecutive_run(nums: Iterable[int]) -> int:
    """Lungimea celei mai lungi secvențe de întregi consecutivi din ``nums``.

    Folosit ca gardă de degenerare: un scorer unimodal pe axa 1…N (ex. vechea
    ``sum_affinity`` = gaussiană pe |k − medie/n|) produce un pool care e un
    singur bloc consecutiv. Nu e semnal, e geometria formulei. Tot el măsoară
    limita de consecutive a utilizatorului (`limit_consecutive_run`).
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
    helperul nu filtrează nimic ÎN AFARA scorurilor nefinite: intrările cu
    NaN/±inf sunt ELIMINATE înainte de sortare (comparația de tuple cu NaN
    scurt-circuitează pe primul element și rezultatul devenea dependent de
    ordinea de inserare în dict — nedeterminist). Pe scoruri toate-finite
    (contractul oricărui scorer valid) output-ul e bit-identic cu înainte.
    Dict gol, toate-nefinite sau ``k <= 0`` -> [].
    """
    if not scores or k <= 0:
        return []
    f = freq or {}
    finite = [(n, s) for n, s in scores.items() if math.isfinite(s)]
    return [
        n
        for n, _ in sorted(
            finite,
            key=lambda kv: (kv[1], f.get(kv[0], 0.0), kv[0]),
            reverse=True,
        )[: int(k)]
    ]


def _max_addable(chosen: set[int], optional: set[int], max_run: int) -> int:
    """Câte numere din ``optional`` încap lângă ``chosen`` fără o secvență de
    peste ``max_run`` consecutive; -1 dacă ``chosen`` depășește deja limita.

    Programare dinamică pe axa numerelor: starea este lungimea secvenței care se
    termină la x (0..max_run); numerele din ``chosen`` sunt obligatorii.
    """
    universe = chosen | optional
    if not universe:
        return 0
    dp = [0] + [-1] * max_run
    for x in range(min(universe), max(universe) + 2):
        forced, allowed = x in chosen, x in optional
        new = [-1] * (max_run + 1)
        for run, best in enumerate(dp):
            if best < 0:
                continue
            if not forced:
                new[0] = max(new[0], best)
            if (forced or allowed) and run < max_run:
                new[run + 1] = max(new[run + 1], best + (0 if forced else 1))
        dp = new
        if max(dp) < 0:
            return -1
    return max(dp)


def limit_consecutive_run(
    ranked: list[int], k: int, max_run: int
) -> tuple[list[int], int, list[int]]:
    """Primele ``k`` numere din ``ranked`` fără mai mult de ``max_run`` consecutive.

    ``ranked`` este ieșirea lui ``rank_by_score``; funcția nu sortează nimic, deci
    tie-break-ul canonic rămâne cel al clasamentului. Un număr intră în pool dacă
    pool-ul se mai poate completa până la ``k`` din numerele clasate după el;
    altfel este sărit și locul lui îl ia următorul care încape. Rezultatul este cel
    mai bun set după rang care respectă limita: dacă primele ``k`` o respectă deja,
    ele sunt pool-ul. Verificarea de completare contează pe o bază restrânsă:
    acolo parcurgerea simplă (ia orice număr care nu formează o secvență) poate
    rămâne fără numere, deși un pool valid există.

    Dacă niciun set de ``k`` nu respectă limita (interval restrâns mai îngust decât
    pool-ul permite), limita crește cu câte 1 până devine posibilă; valoarea
    folosită se întoarce ca ``applied``. ``max_run <= 0`` înseamnă fără limită.

    Întoarce ``(pool în ordinea rangului, limita aplicată, numere sărite în
    ordinea rangului)``.
    """
    ranked = [int(n) for n in ranked]
    k = max(0, min(int(k), len(ranked)))
    if int(max_run) <= 0 or k == 0:
        return ranked[:k], 0, []
    applied = int(max_run)
    while _max_addable(set(), set(ranked), applied) < k:
        applied += 1
    pool: list[int] = []
    skipped: list[int] = []
    for i, n in enumerate(ranked):
        if len(pool) == k:
            break
        trial = set(pool) | {n}
        room = _max_addable(trial, set(ranked[i + 1 :]), applied)
        if room >= 0 and len(trial) + room >= k:
            pool.append(n)
        else:
            skipped.append(n)
    return pool, applied, skipped
