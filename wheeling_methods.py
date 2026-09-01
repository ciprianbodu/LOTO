"""Metode alternative de WHEELING (generatoare de bilete), pe lângă greedy-ul
existent din loto_engine.generate_combinatorial_wheel.

Toate au ACEEAȘI semnătură drop-in:
    fn(pool, pick, guarantee, max_variants, scores) -> (list[list[int]], coverage_pct)

și rezolvă același covering design: fiecare submulțime de `guarantee` numere din
pool trebuie să fie conținută în ≥1 bilet de `pick` numere.

  • wheel_ilp        — cover MINIM exact (scipy.optimize.milp). Optim, dar doar
                       pe dimensiuni moderate (guard pe nr. candidați/ținte).
  • wheel_annealing  — pornește din greedy și REDUCE biletele (remove redundante
                       + swap) prin simulated annealing, păstrând acoperirea.
  • wheel_genetic    — buget FIX de bilete; algoritm genetic care MAXIMIZEAZĂ
                       acoperirea (ponderată pe scoruri). Fitness pe CPU (numpy).
  • wheel_lajolla    — designuri precalculate și validate 100% (fișiere
                       covering_designs/); altfel cade pe ILP → greedy.
                       Sunt incumbente deterministe, nu dovezi de optimalitate.
  • wheel_union34    — alias compatibil pentru un cover guarantee=4: un cover
                       complet 4-din-4 acoperă implicit şi orice 3-din-3, fără
                       biletele redundante ale vechii uniuni 3∪4.

Selectabile prin env LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla|union34.
Orice eșec/limită → fallback la greedy (sigur). Default în `loto_engine`:
**lajolla** când `max_variants == 0` (setarea implicită a UI-ului), greedy când
există un cap de bilete. (Textul de dinainte, „Default = greedy (bit-identic)",
descria comportamentul de dinaintea introducerii La Jolla.)
"""
from __future__ import annotations

import itertools
import hashlib
import logging
import math
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilitare comune
# ---------------------------------------------------------------------------
def _comb(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _greedy_fallback(pool, pick, guarantee, max_variants, scores):
    """Apel lazy la greedy-ul canonic (evită import circular)."""
    from loto_engine import generate_combinatorial_wheel
    return generate_combinatorial_wheel(pool, pick, guarantee, max_variants, scores)


def _sorted_pool(pool, scores) -> list[int]:
    if scores:
        return sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    return sorted(list(pool))


def _coverage_pct(wheel: list[list[int]], pool: list[int], guarantee: int) -> float:
    targets = set(itertools.combinations(sorted(pool), guarantee))
    if not targets:
        return 100.0
    covered = set()
    for t in wheel:
        for sub in itertools.combinations(sorted(t), guarantee):
            covered.add(sub)
    return round(len(covered & targets) / len(targets) * 100.0, 2)


def compute_coverage_pct(wheel: list[list[int]], pool: list[int], guarantee: int) -> float:
    """API publică pt recalcularea acoperirii garanției pe un set de bilete DAT.

    Folosit din loto_engine.py pentru a revalida `coverage_pct` DUPĂ filtre
    post-wheeling (ex. anomaly filter) care pot elimina bilete fără să
    actualizeze procentul de acoperire raportat inițial de wheeling."""
    return _coverage_pct(wheel, pool, guarantee)


def ensure_pool_numbers_on_tickets(
    wheel: list[list[int]], pool: list[int], pick: int,
) -> list[list[int]]:
    """După un cap de bilete, fiecare număr din pool pe ≥1 bilet dacă încape.

    Trunchierea lexicografică pe pool-ul sortat după scor lăsa numerele slabe
    pe dinafară — pool-ul VALIDAT nu mai era pe bilete. Punem un număr lipsă
    în locul unui număr care apare deja pe alt bilet (duplicat). Scanăm de la
    COADĂ, ca T1 (cel mai bine punctat) să rămână intact — nu doar la cap=1.
    Nu înlocuim un număr unic: un cap de 1 bilet n-are duplicate, deci cele
    `pick` numere tari rămân. Dacă toate numerele de pe bilete sunt unice,
    capacitatea e epuizată (`len(wheel)*pick < len(pool)`) și ne oprim.
    """
    if not wheel or not pool:
        return [list(t) for t in wheel] if wheel else wheel
    pick = int(pick)
    if pick < 1:
        return [list(t) for t in wheel]
    out = [sorted(int(x) for x in t) for t in wheel]
    pool_list = [int(n) for n in pool]

    def _counts() -> Counter:
        c: Counter = Counter()
        for t in out:
            for n in t:
                c[int(n)] += 1
        return c

    missing = [n for n in pool_list if _counts()[n] == 0]
    for miss in missing:
        counts = _counts()
        placed = False
        # Coada întâi: T1 e cel mai bine punctat; îl atingem doar dacă
        # biletele slabe n-au niciun duplicat de evacuat.
        for ti in range(len(out) - 1, -1, -1):
            t = out[ti]
            for j, n in enumerate(t):
                if counts[int(n)] >= 2:
                    new_t = list(t)
                    new_t[j] = miss
                    out[ti] = sorted(int(x) for x in new_t)
                    placed = True
                    break
            if placed:
                break
    return out


def filter_preserving_coverage(
    wheel: list[list[int]],
    pool: list[int],
    guarantee: int,
    removal_priority: list[int],
) -> tuple[list[list[int]], int]:
    """Elimină bilete din `wheel`, în ordinea din `removal_priority` (indici în
    `wheel`, de la cel mai indezirabil la cel mai puțin dorit — ex. cele mai
    "anomale" statistic), PĂSTRÂND garanția combinatorică — un bilet e eliminat
    DOAR dacă toate țintele lui (subseturi de `guarantee` numere din pool) mai
    sunt acoperite de cel puțin un alt bilet rămas.

    Folosit ca să reconciliem filtrul anti-anomalie (bazat pe scoruri) cu
    garanția de wheeling (bazată pe covering design) — anterior, filtrul putea
    elimina bilete care erau UNICUL acoperitor al unei ținte, spărgând garanția
    promisă utilizatorului chiar și în modul "nelimitat" (max_variants=0).

    Returnează (wheel_filtrat, n_bilete_eliminate).
    """
    wheel = [list(t) for t in wheel]
    targets_per_ticket = [
        set(itertools.combinations(sorted(t), guarantee)) for t in wheel
    ]
    coverage_count: dict[tuple, int] = {}
    for targets in targets_per_ticket:
        for t in targets:
            coverage_count[t] = coverage_count.get(t, 0) + 1

    keep = [True] * len(wheel)
    removed = 0
    for idx in removal_priority:
        if idx < 0 or idx >= len(wheel) or not keep[idx]:
            continue
        targets = targets_per_ticket[idx]
        # Sigur de eliminat DOAR dacă fiecare țintă a lui mai are ≥1 acoperitor
        # rămas (coverage_count>1 acum, înainte de a-l scădea pe al lui). Ținte
        # deja neacoperite (count=0, ex. dintr-un max_variants anterior) NU
        # forțează eliminarea — sunt tratate conservator, biletul e păstrat.
        if all(coverage_count.get(t, 0) > 1 for t in targets):
            keep[idx] = False
            for t in targets:
                coverage_count[t] -= 1
            removed += 1

    result = [w for w, k in zip(wheel, keep) if k]
    return result, removed


def _order_by_scores(wheel: list[list[int]], scores) -> list[list[int]]:
    if not scores:
        return [sorted(t) for t in wheel]
    return sorted([sorted(t) for t in wheel],
                  key=lambda t: sum(scores.get(n, 0) for n in t), reverse=True)


# ===========================================================================
# 1) ILP — cover minim EXACT (scipy.optimize.milp)
# ===========================================================================
_ILP_MAX_BLOCKS = 12000   # guard: peste asta, ILP devine prea greu → fallback
_ILP_MAX_TARGETS = 6000

# Cache de PROCES pentru coverul ILP, keyed pe (v, pick, guarantee).
# De ce e legitim: obiectivul ILP-ului e `c=ones(nb)` — minimizează NUMĂRUL de
# bilete — deci NU depinde de scoruri, iar matricea de constrângeri se
# construiește pe POZIȚII (0..v-1), fiind identică pentru orice pool de aceeași
# dimensiune. Coverul e deci invariant la reetichetare, exact ca un design
# La Jolla (verificat: același cover pozițional pe `1..10`, pe `21..30`, pe un
# set împrăștiat și pe același set CU scoruri).
# Ce NU se cache-uiește: comparația cu greedy de mai jos — `generate_combinatorial_wheel`
# ordonează țintele după SUMA SCORURILOR, deci depinde de scoruri și se reface la
# fiecare apel (costă < 0.5 s măsurat, față de ~15 s cât ia solver-ul).
# Motivul optimizării: fără el, fiecare pas de walk-forward (~1940/rulare) plătea
# `time_limit` întreg (15 s) ca să re-deducă exact același cover.
_ILP_COVER_CACHE: dict[tuple[int, int, int], list[tuple[int, ...]] | None] = {}


def _ilp_cover_positions(v: int, pick: int, guarantee: int,
                         time_limit: float) -> list[tuple[int, ...]] | None:
    """Coverul ILP pentru C(v, pick, guarantee) ca POZIȚII 0..v-1 (memoizat).

    None = ILP indisponibil pentru configurația asta (prea mare / fără soluție /
    scipy lipsă) → apelantul cade pe greedy, ca înainte.
    """
    key = (int(v), int(pick), int(guarantee))
    if key in _ILP_COVER_CACHE:
        return _ILP_COVER_CACHE[key]
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _ILP_MAX_BLOCKS or nt > _ILP_MAX_TARGETS:
        logger.info("[WHEEL-ILP] prea mare (blocuri=%d ținte=%d) → greedy", nb, nt)
        _ILP_COVER_CACHE[key] = None
        return None
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
        idxs = range(v)
        blocks = list(itertools.combinations(idxs, pick))
        targets = list(itertools.combinations(idxs, guarantee))
        tidx = {t: i for i, t in enumerate(targets)}
        A = lil_matrix((nt, nb), dtype=np.float64)
        for j, b in enumerate(blocks):
            for sub in itertools.combinations(b, guarantee):
                A[tidx[sub], j] = 1.0
        res = milp(
            c=np.ones(nb),
            constraints=LinearConstraint(A.tocsr(), lb=1, ub=np.inf),
            integrality=np.ones(nb),
            bounds=Bounds(0, 1),
            options={"time_limit": time_limit},
        )
        if res.x is None:
            # TIMEOUT / infeasible raportat de solver. NU memoiza: `time_limit` e o
            # limită de CEAS, nu o proprietate a geometriei — o mașină încărcată o
            # ratează o dată și o prinde data viitoare. Memoizat, un singur timeout
            # dezactiva ILP-ul pentru TOT restul procesului (adică tot walk-forward-ul,
            # ~1940 de pași) și trecea tăcut pe greedy, cu bilete mai multe.
            logger.warning("[WHEEL-ILP] fără soluție în %.1fs → greedy "
                           "(NU memoizez: e limită de timp, nu geometrie)", time_limit)
            return None
    except Exception as exc:  # noqa: BLE001
        # Idem: scipy lipsă, MemoryError, orice excepție = eșec de MEDIU, nu de
        # geometrie. Singurul „nu se poate niciodată" memoizabil e pragul de
        # dimensiune de mai sus (`_ILP_MAX_BLOCKS` / `_ILP_MAX_TARGETS`).
        logger.warning("[WHEEL-ILP] eșec (%s) → greedy (NU memoizez)", exc)
        return None
    cover = [blocks[j] for j in range(nb) if res.x[j] > 0.5]
    _ILP_COVER_CACHE[key] = cover
    return cover


def wheel_ilp(pool, pick, guarantee, max_variants=0, scores=None,
              time_limit: float = 15.0):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    cover = _ilp_cover_positions(v, int(pick), int(guarantee), time_limit)
    if cover is None:
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    try:
        chosen = [[pool[i] for i in blk] for blk in cover]
        # HiGHS poate returna o soluție feasibilă NEoptimă la limita de timp →
        # comparăm cu greedy. Criteriul e (ACOPERIRE, apoi bilete), nu doar numărul
        # de bilete: greedy-ul se oprește la 1000 de iterații
        # (`loto_engine.generate_combinatorial_wheel`), deci pe o cerere degenerată
        # (`guarantee == pick`, unde doar sistemul complet acoperă 100%) întoarce
        # 1001 bilete la ~33% acoperire. Comparate doar pe număr, „mai puține bilete"
        # ar fi câștigat cu o acoperire de trei ori mai mică — iar comentariul de
        # dinainte („ambele 100%") era fals exact în cazul ăsta.
        g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
        ilp_cov = _coverage_pct(chosen, pool, guarantee)
        g_cov = _coverage_pct(g_wheel, pool, guarantee)
        # `>=` păstrează comportamentul vechi la egalitate (ambele 100% → greedy).
        if (g_cov, -len(g_wheel)) >= (ilp_cov, -len(chosen)):
            logger.info("[WHEEL-ILP] greedy (%d bilete, %.2f%%) ≥ ILP (%d bilete, %.2f%%) → păstrez greedy",
                        len(g_wheel), g_cov, len(chosen), ilp_cov)
            chosen = [list(t) for t in g_wheel]
        else:
            logger.info("[WHEEL-ILP] cover ILP = %d bilete la %.2f%% (greedy era %d la %.2f%%)",
                        len(chosen), ilp_cov, len(g_wheel), g_cov)
        if max_variants > 0 and len(chosen) > max_variants:
            chosen = _order_by_scores(chosen, scores)[:max_variants]
        return _order_by_scores(chosen, scores), _coverage_pct(chosen, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-ILP] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 2) Simulated annealing — reduce wheel-ul greedy păstrând acoperirea
# ===========================================================================
def wheel_annealing(pool, pick, guarantee, max_variants=0, scores=None,
                    iters: int = 4000, seed: int = 42):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    base, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)  # plecăm din greedy complet
    # ⚠️ Cheile ȚINTELOR se construiesc pe pool-ul sortat NUMERIC, fiindcă
    # `ticket_targets` caută cu `tuple(sorted(...))`. `_sorted_pool` reordonează
    # după SCOR, deci pe apelul de producție (care pasează mereu scoruri) cheile
    # ieșeau în ordinea scorurilor și fiecare căutare dădea KeyError — funcția era
    # moartă în producție, iar `test_wheeling.py` n-o prindea fiindcă testează
    # FĂRĂ scoruri, caz în care cele două ordini coincid.
    _pool_sorted = sorted(pool)
    targets = list(itertools.combinations(_pool_sorted, guarantee))
    if not targets:
        return base, 100.0
    tidx = {t: i for i, t in enumerate(targets)}
    nt = len(targets)

    def ticket_targets(tk) -> set:
        return {tidx[s] for s in itertools.combinations(sorted(tk), guarantee)}

    cur = [tuple(sorted(t)) for t in base]
    cur_tt = [ticket_targets(t) for t in cur]
    cover_count = np.zeros(nt, dtype=np.int32)
    for tt in cur_tt:
        for i in tt:
            cover_count[i] += 1

    def full() -> bool:
        return bool((cover_count > 0).all())

    rng = np.random.default_rng(seed)
    all_blocks = None  # lazy pt swap

    # 1) Eliminare bilete redundante (target-ele lor sunt acoperite de altele).
    changed = True
    while changed:
        changed = False
        order = sorted(range(len(cur)), key=lambda i: len(cur_tt[i]))  # întâi cele mici
        for i in order:
            if all(cover_count[t] > 1 for t in cur_tt[i]):
                for t in cur_tt[i]:
                    cover_count[t] -= 1
                cur.pop(i); cur_tt.pop(i)
                changed = True
                break

    # 2) Annealing: încearcă să înlocuiască un bilet cu altul aleator dacă scade
    #    redundanța (păstrând acoperirea completă) — relaxează spre minim local mai bun.
    if _comb(v, pick) <= 200000:
        all_blocks = list(itertools.combinations(_pool_sorted, pick))
    if all_blocks:
        T = 1.0
        for it in range(iters):
            T = max(0.01, 1.0 - it / iters)
            if len(cur) <= guarantee:
                break
            i = int(rng.integers(len(cur)))
            cand = all_blocks[int(rng.integers(len(all_blocks)))]
            cand_tt = ticket_targets(cand)
            old_tt = cur_tt[i]
            # simulează scoaterea lui i + adăugarea cand
            ok = True
            for t in old_tt:
                if cover_count[t] - 1 < 1 and t not in cand_tt:
                    ok = False
                    break
            if not ok:
                continue
            # acceptăm dacă cand acoperă ≥ target-e (redundanță mai utilă) sau prob. termică
            gain = len(cand_tt) - len(old_tt)
            if gain >= 0 or rng.random() < math.exp(gain / T):
                for t in old_tt:
                    cover_count[t] -= 1
                for t in cand_tt:
                    cover_count[t] += 1
                cur[i] = tuple(sorted(cand)); cur_tt[i] = cand_tt
                # re-eliminare redundante după swap
                for j in range(len(cur) - 1, -1, -1):
                    if all(cover_count[t] > 1 for t in cur_tt[j]):
                        for t in cur_tt[j]:
                            cover_count[t] -= 1
                        cur.pop(j); cur_tt.pop(j)

    wheel = [list(t) for t in cur]
    if max_variants > 0 and len(wheel) > max_variants:
        wheel = _order_by_scores(wheel, scores)[:max_variants]
    logger.info("[WHEEL-SA] %d bilete (din %d greedy)", len(wheel), len(base))
    return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)


# ===========================================================================
# 3) Algoritm genetic — buget FIX, MAXIMIZEAZĂ acoperirea (fitness pe CPU/numpy)
# ===========================================================================
_GA_MAX_BLOCKS = 60000


def wheel_genetic(pool, pick, guarantee, max_variants=0, scores=None,
                  pop: int = 200, gens: int = 80, seed: int = 42):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _GA_MAX_BLOCKS:
        logger.info("[WHEEL-GA] univers prea mare (blocuri=%d) → greedy", nb)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    # Greedy-ul ne dă atât bugetul implicit, cât și o sămânță bună (elitism).
    g_wheel, _ = _greedy_fallback(pool, pick, guarantee, 0, scores)
    budget = min(max_variants, nb) if max_variants > 0 else min(len(g_wheel), nb)
    budget = max(1, budget)
    try:
        import numpy as np
        rng = np.random.default_rng(seed)
        blocks = list(itertools.combinations(pool, pick))
        # ⚠️ Cheile pe tuple SORTAT NUMERIC: elitismul de mai jos caută cu
        # `bidx[tuple(sorted(t))]`, iar `pool` e în ordinea SCORULUI. Cu scoruri
        # (cazul de producție) nicio cheie nu se potrivea → sămânța greedy se
        # pierdea tăcut, GA pornea aleator și întorcea sub 100% acoperire acolo
        # unde greedy dă 100% cu același număr de bilete. Valorile rămân indici
        # în `blocks`, deci M/`P` nu se schimbă.
        bidx = {tuple(sorted(b)): i for i, b in enumerate(blocks)}
        targets = list(itertools.combinations(pool, guarantee))
        tidx = {t: i for i, t in enumerate(targets)}
        # M[b, t] = 1 dacă blocul b acoperă ținta t (t ⊆ b)
        M = np.zeros((nb, nt), dtype=np.float32)
        for j, b in enumerate(blocks):
            for sub in itertools.combinations(b, guarantee):
                M[j, tidx[sub]] = 1.0
        # ponderi ținte după scoruri (ținte din numere bune cântăresc mai mult)
        if scores:
            tw = np.array([sum(scores.get(n, 0.0) for n in t) for t in targets],
                          dtype=np.float32)
            tw = tw / (tw.mean() + 1e-9)
        else:
            tw = np.ones(nt, dtype=np.float32)

        def fitness(P_idx):  # P_idx: (P, budget) int
            masks = M[P_idx]                # (P, budget, nt)
            cov = masks.max(axis=1)         # (P, nt) acoperit?
            return (cov * tw).sum(axis=1)   # (P,)

        # populație inițială: indici aleatori de blocuri
        P = rng.integers(0, nb, size=(pop, budget))
        # ELITISM: sămânță = soluția greedy (mapată pe indici), trunchiată/umplută la buget.
        g_idx = [bidx[tuple(sorted(t))] for t in g_wheel if tuple(sorted(t)) in bidx]
        if g_idx:
            if len(g_idx) >= budget:
                seed_row = np.array(g_idx[:budget], dtype=np.int64)
            else:
                pad = rng.integers(0, nb, size=(budget - len(g_idx),))
                seed_row = np.concatenate([np.array(g_idx, dtype=np.int64), pad])
            P[0] = seed_row  # GA pornește de la ≥ acoperirea greedy → poate doar îmbunătăți
        best_idx, best_fit = None, -1.0
        for _g in range(gens):
            fit = fitness(P)
            mx = int(np.argmax(fit))
            if float(fit[mx]) > best_fit:
                best_fit = float(fit[mx]); best_idx = P[mx].copy()
            # selecție prin turnir
            a = rng.integers(0, pop, size=pop)
            b = rng.integers(0, pop, size=pop)
            winners = np.where(fit[a] >= fit[b], a, b)
            parents = P[winners]
            # crossover uniform între perechi consecutive
            perm = rng.permutation(pop)
            p2 = parents[perm]
            mask = rng.random(parents.shape) < 0.5
            child = np.where(mask, parents, p2)
            # mutație: înlocuiește ~8% din gene cu blocuri aleatoare
            mut = rng.random(child.shape) < 0.08
            rnd = rng.integers(0, nb, size=child.shape)
            child = np.where(mut, rnd, child)
            child[0] = best_idx  # elitism
            P = child

        chosen = sorted({int(i) for i in best_idx.tolist()})
        wheel = [list(blocks[j]) for j in chosen]
        logger.info("[WHEEL-GA] buget=%d → %d bilete unice, fitness=%.1f", budget, len(wheel), best_fit)
        return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WHEEL-GA] eșec (%s) → greedy", exc)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 4) La Jolla — designuri precalculate validate (fișiere) → ILP → greedy
# ===========================================================================
# Ancorate la MODUL, nu la directorul de lucru. Căile relative rămân pe listă (ca
# override), dar nu mai sunt singura sursă: cu ele singure, orice apel cu alt CWD
# pierdea tăcut designul și cădea pe ILP/greedy — măsurat pe 6/49 pool 12
# garanție 4: 55 de bilete în loc de 41 (+34%) pentru exact aceeași garanție.
_MODULE_DIR = Path(__file__).resolve().parent
_LAJOLLA_DIRS = [
    _MODULE_DIR / "covering_designs",
    _MODULE_DIR / "_ISTORIC" / "covering_designs",
    Path("covering_designs"),
    Path("_ISTORIC/covering_designs"),
]


def covering_design_source_signature(v: int, pick: int, guarantee: int) -> str:
    """Amprentă a fișierelor de design candidate pentru cheia cache-ului WF.

    Un design se poate îmbunătăți fără să se schimbe numele metodei sau
    geometria. Hash-ul conținutului împiedică walk-forward-ul să reutilizeze
    costuri și hit-uri calculate pe lista veche de bilete.
    """
    digest = hashlib.sha256(f"C({v},{pick},{guarantee})".encode("ascii"))
    found = False
    seen: set[str] = set()
    for directory in _LAJOLLA_DIRS:
        path = directory / f"C_{v}_{pick}_{guarantee}.txt"
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not path.is_file():
                continue
            found = True
            digest.update(key.encode("utf-8", errors="surrogatepass"))
            digest.update(path.read_bytes())
        except OSError as exc:
            digest.update(f"{key}:{type(exc).__name__}".encode("utf-8"))
    return digest.hexdigest()[:12] if found else "missing"


def _load_lajolla(v: int, pick: int, guarantee: int) -> list[list[int]] | None:
    """Citește și validează un design C(v, pick, guarantee) local.

    Format La Jolla: fiecare linie = un bloc de ``pick`` poziții 1-based din
    ``1..v``. Un fișier invalid sau incomplet nu este un design disponibil:
    continuăm căutarea, apoi callerul poate cădea pe ILP/greedy.
    """
    for d in _LAJOLLA_DIRS:
        f = d / f"C_{v}_{pick}_{guarantee}.txt"
        if f.exists():
            try:
                blocks: list[list[int]] = []
                # `utf-8-sig` acceptă BOM, dar păstrează erorile de decodare
                # vizibile; `errors="replace"` ar putea transforma corupția
                # OneDrive într-un design parțial acceptat tăcut.
                for line in f.read_text(encoding="utf-8-sig").splitlines():
                    if not line.strip():
                        continue
                    nums = [int(x) for x in line.replace(",", " ").split() if x.strip()]
                    if (len(nums) != pick or len(set(nums)) != pick
                            or any(n < 1 or n > v for n in nums)):
                        raise ValueError(f"bloc invalid: {nums}")
                    blocks.append(nums)
                if not blocks:
                    raise ValueError("fișier gol")
                coverage = _coverage_pct(blocks, list(range(1, v + 1)), guarantee)
                if coverage < 100.0:
                    raise ValueError(f"acoperire incompletă: {coverage:.2f}%")
                logger.info("[WHEEL-LaJolla] folosesc design valid %s (%d blocuri)", f, len(blocks))
                return blocks
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WHEEL-LaJolla] ignor design invalid %s: %s", f, exc)
    return None


def wheel_lajolla(pool, pick, guarantee, max_variants=0, scores=None):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    design = _load_lajolla(v, pick, guarantee)
    if design is not None:
        # mapăm indicii 1..v ai design-ului pe pool-ul sortat după scor (numerele bune
        # primesc pozițiile cu apariții multiple → mai des în bilete)
        wheel = []
        for blk in design:
            mapped = [pool[i - 1] for i in blk if 1 <= i <= v]
            # set(): o linie cu indici DUPLICAȚI ar produce bilet cu numere repetate
            if len(mapped) == pick and len(set(mapped)) == pick:
                wheel.append(sorted(mapped))
        if wheel:
            # VALIDARE înainte de folosire: un fișier trunchiat/corupt (OneDrive poate
            # sincroniza parțial) trecea tăcut cu acoperire <100% deși UI-ul promite
            # garanție plină. Design incomplet → fallback ILP/greedy, ca la fișier lipsă.
            cov_full = _coverage_pct(wheel, pool, guarantee)
            if cov_full < 100.0:
                logger.warning(
                    "[WHEEL-LaJolla] design C(%d,%d,%d) INCOMPLET (%.1f%% acoperire, %d blocuri) "
                    "— fișier corupt/trunchiat? Fallback ILP/greedy.",
                    v, pick, guarantee, cov_full, len(wheel),
                )
            else:
                # Designul e valid (100%). Îl comparăm totuși cu greedy și luăm
                # MINIMUL de bilete. Motivul: `_greedy_fallback` folosește
                # VALORILE scorurilor, nu doar ordinea, deci pe pool-uri reale
                # nimerește uneori un cover mai mic decât designul neutru de pe
                # disc (măsurat: C(16,5,4) → 458 pe joker, 467 pe 5/40, 467 în
                # design). Ambele ramuri sunt DETERMINISTE — singura sursă de
                # nedeterminism era ILP-ul pe `time_limit`, care nu mai e
                # consultat aici. Deci: același rezultat la fiecare rulare, și
                # niciodată mai multe bilete decât înainte.
                _gw, _gc = _greedy_fallback(pool, pick, guarantee, 0, scores)
                if _gc >= 100.0 and len(_gw) < len(wheel):
                    logger.info(
                        "[WHEEL-LaJolla] greedy bate designul C(%d,%d,%d): %d < %d bilete",
                        v, pick, guarantee, len(_gw), len(wheel),
                    )
                    wheel = [sorted(int(x) for x in t) for t in _gw]
                if max_variants > 0 and len(wheel) > max_variants:
                    wheel = _order_by_scores(wheel, scores)[:max_variants]
                return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)
    # fără fișier → încearcă ILP exact (mic), altfel greedy
    logger.info("[WHEEL-LaJolla] fără design local pt C(%d,%d,%d) → ILP/greedy", v, pick, guarantee)
    return wheel_ilp(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 5) COMPATIBILITATE UNION34 — un cover 4-din-4 implică deja 3-din-3
# ===========================================================================
def wheel_union34(pool, pick, guarantee=4, max_variants=0, scores=None,
                  time_limit: float = 15.0):
    """Alias istoric pentru acoperire simultană 3+/4+.

    Orice 3-submulțime a unui pool cu cel puțin patru numere poate fi extinsă la
    o 4-submulțime. Dacă fiecare 4-submulțime este pe un bilet, extensia și deci
    3-submulțimea inițială sunt deja pe un bilet. Vechea uniune dintre două
    covere complete adăuga, așadar, bilete fără să adauge vreo garanție.

    Pentru cereri 3 sau 4 folosim un singur C(v, pick, 4), preferând designurile
    precalculate. Pentru o garanție mai mare delegăm exact cererea, nu pretindem
    că un cover 4-din-4 garantează 5+.
    """
    del time_limit  # păstrat în semnătură pentru apelanți existenți.
    target_guarantee = 4 if int(guarantee) <= 4 else int(guarantee)
    wheel, _coverage_for_target = wheel_lajolla(
        pool, pick, target_guarantee, max_variants=max_variants, scores=scores,
    )
    logger.info(
        "[WHEEL-U34] cover g%d = %d bilete (pool=%d, pick=%d; 3+/4+ acoperite când g=4)",
        target_guarantee, len(wheel), len(pool), pick,
    )
    # Contractul comun al modulelor de wheeling: procentul raportat corespunde
    # garanției CERUTE de apelant. La un cap de bilete, C(v,pick,4) poate avea
    # altă acoperire decât C(v,pick,3), chiar dacă fără cap ambele sunt 100%.
    return wheel, compute_coverage_pct(wheel, pool, guarantee)


# ===========================================================================
# Dispatcher
# ===========================================================================
WHEEL_METHODS = {
    "ilp": wheel_ilp,
    "annealing": wheel_annealing,
    "genetic": wheel_genetic,
    "lajolla": wheel_lajolla,
    "union34": wheel_union34,
}


def generate_wheel(method: str, pool, pick, guarantee, max_variants=0, scores=None):
    """Selectează algoritmul de wheeling. 'greedy' (sau necunoscut) → canonic."""
    fn = WHEEL_METHODS.get((method or "greedy").strip().lower())
    if fn is None:
        wheel, cov = _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    else:
        wheel, cov = fn(pool, pick, guarantee, max_variants, scores)
    if int(max_variants or 0) > 0:
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
