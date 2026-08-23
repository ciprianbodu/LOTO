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
  • wheel_lajolla    — designuri optime cunoscute (fișiere covering_designs/);
                       altfel cade pe ILP exact (mic) → greedy. „Best-known".
  • wheel_union34    — UNIUNEA coverelor ILP pt guarantee=3 ȘI guarantee=4:
                       garantează SIMULTAN 3-din-3 și 4-din-4 la o fracție din
                       costul sistemului complet.

Selectabile prin env LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla|union34.
Orice eșec/limită → fallback la greedy (sigur). Default = greedy (bit-identic).
"""
from __future__ import annotations

import itertools
import logging
import math
import os
import time
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
        return sorted(list(pool), key=lambda x: _num_score(x, scores), reverse=True)
    return sorted(list(pool))


def _num_score(n, scores) -> float:
    """Scor finit al unui număr; NaN/inf/lipsă → −inf (ultimele la sort desc)."""
    v = scores.get(n, 0) if scores else 0
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return float("-inf")
    if math.isfinite(fv):
        return fv
    return float("-inf")


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


def _ticket_score(ticket, scores) -> float:
    """Sumă de scoruri FINITE pe bilet. NaN/inf nu mai otrăvesc ordinea."""
    s = 0.0
    for n in ticket:
        v = _num_score(n, scores)
        if math.isfinite(v):
            s += v
    return s


def cap_wheel_max_coverage(
    wheel: list[list[int]],
    pool: list[int],
    guarantee: int,
    max_variants: int,
    scores=None,
    guarantees: tuple[int, ...] | None = None,
) -> list[list[int]]:
    """Taie wheel-ul la ``max_variants`` păstrând CÂT MAI MULTE ținte acoperite.

    Tăierea după scor (``wheel[:N]``) poate scoate unicul acoperitor al unei
    ținte și păstra bilete redundante. Aici: greedy set-cover pe biletele
    existente, tie-break pe scor. Dacă acoperirea ajunge 100% înainte de cap,
    umplem restul bugetului cu bilete rămase (mai multe șanse, aceeași
    garanție). ``guarantees`` (ex. union34: 3 și 4) unește țintele; implicit
    doar ``guarantee``.
    """
    if max_variants <= 0 or len(wheel) <= max_variants:
        return [sorted(t) for t in wheel]
    gs = tuple(g for g in (guarantees or (guarantee,)) if isinstance(g, int) and g > 0)
    if not gs:
        gs = (guarantee,)
    pool_t = tuple(sorted(pool))
    remaining: set[tuple[int, ...]] = set()
    for g in gs:
        remaining |= set(itertools.combinations(pool_t, g))
    tickets = [tuple(sorted(t)) for t in wheel]
    targets_of = []
    for t in tickets:
        tset: set[tuple[int, ...]] = set()
        for g in gs:
            tset |= set(itertools.combinations(t, g))
        targets_of.append(tset)
    chosen: list[list[int]] = []
    used: set[int] = set()
    while remaining and len(chosen) < max_variants:
        best_i = -1
        best_cov = -1
        best_sc = float("-inf")
        for i, tts in enumerate(targets_of):
            if i in used:
                continue
            cov = len(tts & remaining)
            sc = _ticket_score(tickets[i], scores)
            if cov > best_cov or (cov == best_cov and sc > best_sc):
                best_cov = cov
                best_sc = sc
                best_i = i
        if best_i < 0 or best_cov <= 0:
            break
        used.add(best_i)
        chosen.append(list(tickets[best_i]))
        remaining -= targets_of[best_i]
    if len(chosen) < max_variants:
        rest = [tickets[i] for i in range(len(tickets)) if i not in used]
        rest.sort(key=lambda t: _ticket_score(t, scores), reverse=True)
        for t in rest:
            if len(chosen) >= max_variants:
                break
            chosen.append(list(t))
    return chosen


def _order_by_scores(wheel: list[list[int]], scores) -> list[list[int]]:
    if not scores:
        return [sorted(t) for t in wheel]
    return sorted([sorted(t) for t in wheel],
                  key=lambda t: _ticket_score(t, scores), reverse=True)


# ===========================================================================
# 1) ILP — cover minim EXACT (scipy.optimize.milp)
# ===========================================================================
_ILP_MAX_BLOCKS = 12000   # guard: peste asta, ILP devine prea greu → fallback
_ILP_MAX_TARGETS = 6000


def wheel_ilp(pool, pick, guarantee, max_variants=0, scores=None,
              time_limit: float = 15.0):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    nb, nt = _comb(v, pick), _comb(v, guarantee)
    if nb > _ILP_MAX_BLOCKS or nt > _ILP_MAX_TARGETS:
        logger.info("[WHEEL-ILP] prea mare (blocuri=%d ținte=%d) → greedy", nb, nt)
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
        blocks = list(itertools.combinations(pool, pick))
        targets = list(itertools.combinations(pool, guarantee))
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
            logger.warning("[WHEEL-ILP] fără soluție → greedy")
            return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
        chosen = [list(blocks[j]) for j in range(nb) if res.x[j] > 0.5]
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
            chosen = cap_wheel_max_coverage(chosen, pool, guarantee, max_variants, scores)
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
    targets = list(itertools.combinations(pool, guarantee))
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
        all_blocks = list(itertools.combinations(pool, pick))
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
        wheel = cap_wheel_max_coverage(wheel, pool, guarantee, max_variants, scores)
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
        bidx = {b: i for i, b in enumerate(blocks)}
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
# 4) La Jolla — designuri optime cunoscute (fișiere) → ILP → greedy
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


def _load_lajolla(v: int, pick: int, guarantee: int) -> list[list[int]] | None:
    """Citește un design C(v, pick, guarantee) dintr-un fișier local dacă există.
    Format La Jolla: fiecare linie = un bloc de `pick` numere (1-based, 1..v)."""
    for d in _LAJOLLA_DIRS:
        f = d / f"C_{v}_{pick}_{guarantee}.txt"
        if f.exists():
            try:
                blocks = []
                for line in f.read_text().splitlines():
                    nums = [int(x) for x in line.replace(",", " ").split() if x.strip()]
                    if len(nums) == pick:
                        blocks.append(nums)
                if blocks:
                    logger.info("[WHEEL-LaJolla] folosesc design cunoscut %s (%d blocuri)", f, len(blocks))
                    return blocks
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WHEEL-LaJolla] citire %s eșuată: %s", f, exc)
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
            if len(mapped) == pick:
                wheel.append(sorted(mapped))
        if wheel:
            if max_variants > 0 and len(wheel) > max_variants:
                wheel = cap_wheel_max_coverage(wheel, pool, guarantee, max_variants, scores)
            return _order_by_scores(wheel, scores), _coverage_pct(wheel, pool, guarantee)
    # fără fișier → încearcă ILP exact (mic), altfel greedy
    logger.info("[WHEEL-LaJolla] fără design local pt C(%d,%d,%d) → ILP/greedy", v, pick, guarantee)
    return wheel_ilp(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 5) UNIUNE 3∪4 — garanție SIMULTANĂ 3-din-3 ȘI 4-din-4 (uniune de covere ILP)
# ===========================================================================
def wheel_union34(pool, pick, guarantee=4, max_variants=0, scores=None,
                  time_limit: float = 15.0):
    """Cover UNIUNE 3∪4: garantează SIMULTAN că ORICE 3-subset ȘI ORICE 4-subset
    din pool sunt conținute în cel puțin un bilet (3-din-3 și 4-din-4).

    Motivație: ținta bench e 3+, dar premiile 4+ contează — uniunea convertește
    AMBELE tipuri de evenimente pool→bilet la o fracție din costul sistemului
    complet. Empiric (venv): pool 10, bilet 6 → uniune = 30 bilete (vs 210
    complet); pool 10, bilet 5 → 63 bilete (vs 252 complet).

    Construiește coverul ILP pentru guarantee=3 și separat pentru guarantee=4
    (refolosește wheel_ilp, cu guard-urile și fallback-urile lui), face UNIUNEA
    cu dedup pe tuple sortate și ordonează după scoruri. `coverage_pct` raportat
    = pe guarantee-ul CERUT de apelant (compute_coverage_pct). Parametrul
    `guarantee` NU schimbă componentele uniunii (mereu 3 și 4) — la guarantee>4
    doar se loghează WARNING (metoda e gândită pentru ținte 3/4).
    """
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [list(pool)], 100.0
    if guarantee > 4:
        logger.warning("[WHEEL-U34] guarantee=%d > 4 — metoda e gândită pentru ținte 3/4; "
                       "componentele rămân 3∪4, acoperirea e raportată pe %d",
                       guarantee, guarantee)
    seen: set[tuple[int, ...]] = set()
    union: list[list[int]] = []
    for g in (3, 4):
        if g > pick:
            continue  # bilet prea mic ca să conțină un g-subset → componentă imposibilă
        comp, _ = wheel_ilp(pool, pick, g, 0, scores, time_limit=time_limit)
        for t in comp:
            key = tuple(sorted(t))
            if key not in seen:
                seen.add(key)
                union.append(list(key))
    union = _order_by_scores(union, scores)
    if max_variants > 0 and len(union) > max_variants:
        logger.warning("[WHEEL-U34] max_variants=%d < uniune=%d — tai păstrând acoperirea "
                       "maximă; garanția 3∪4 poate pică", max_variants, len(union))
        union = cap_wheel_max_coverage(
            union, pool, guarantee, max_variants, scores, guarantees=(3, 4),
        )
    logger.info("[WHEEL-U34] uniune 3∪4 = %d bilete (pool=%d, pick=%d)", len(union), v, pick)
    return union, compute_coverage_pct(union, pool, guarantee)


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
        return _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    return fn(pool, pick, guarantee, max_variants, scores)
