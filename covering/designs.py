"""Precomputed covering designs (La Jolla, union, lotto t-if-p)."""
from __future__ import annotations

import hashlib
import itertools
import logging
import math
from pathlib import Path

from covering.common import (
    _comb,
    _coverage_pct,
    _greedy_fallback,
    _order_by_scores,
    _sorted_pool,
    filter_preserving_coverage,
    lotto_coverage_pct,
)
from covering.ilp import _ilp_cover_positions, wheel_ilp

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parents[1]
_LAJOLLA_DIRS = [
    _MODULE_DIR / "covering_designs",
    _MODULE_DIR / "_ISTORIC" / "covering_designs",
    Path("covering_designs"),
    Path("_ISTORIC/covering_designs"),
]


def covering_design_source_signature(
    v: int, pick: int, guarantee: int, condition: int | None = None
) -> str:
    """Amprentă a fișierelor de design candidate pentru cheia cache-ului WF.

    Un design se poate îmbunătăți fără să se schimbe numele metodei sau
    geometria. Hash-ul conținutului împiedică walk-forward-ul să reutilizeze
    costuri și hit-uri calculate pe lista veche de bilete.
    """
    digest = hashlib.sha256(f"C({v},{pick},{guarantee})".encode("ascii"))
    found = False
    seen: set[str] = set()
    name = (
        lotto_design_path(v, pick, guarantee, condition)
        if condition is not None and int(condition) > int(guarantee)
        else f"C_{v}_{pick}_{guarantee}.txt"
    )
    for directory in _LAJOLLA_DIRS:
        path = directory / name
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
                    if (
                        len(nums) != pick
                        or len(set(nums)) != pick
                        or any(n < 1 or n > v for n in nums)
                    ):
                        raise ValueError(f"bloc invalid: {nums}")
                    blocks.append(nums)
                if not blocks:
                    raise ValueError("fișier gol")
                coverage = _coverage_pct(blocks, list(range(1, v + 1)), guarantee)
                if coverage < 100.0:
                    raise ValueError(f"acoperire incompletă: {coverage:.2f}%")
                logger.info(
                    "[WHEEL-LaJolla] folosesc design valid %s (%d blocuri)",
                    f,
                    len(blocks),
                )
                return blocks
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WHEEL-LaJolla] ignor design invalid %s: %s", f, exc)
    return None


def wheel_lajolla(pool, pick, guarantee, max_variants=0, scores=None):
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pick:
        return [], 0.0
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
                    v,
                    pick,
                    guarantee,
                    cov_full,
                    len(wheel),
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
                        v,
                        pick,
                        guarantee,
                        len(_gw),
                        len(wheel),
                    )
                    wheel = [sorted(int(x) for x in t) for t in _gw]
                if max_variants > 0 and len(wheel) > max_variants:
                    wheel = _order_by_scores(wheel, scores)[:max_variants]
                ordered = _order_by_scores(wheel, scores)
                if max_variants > 0:
                    ordered = ensure_pool_numbers_on_tickets(ordered, pool, pick)
                return ordered, _coverage_pct(ordered, pool, guarantee)
    # fără fișier → încearcă ILP exact (mic), altfel greedy
    logger.info(
        "[WHEEL-LaJolla] fără design local pt C(%d,%d,%d) → ILP/greedy",
        v,
        pick,
        guarantee,
    )
    return wheel_ilp(pool, pick, guarantee, max_variants, scores)


# ===========================================================================
# 5) COMPATIBILITATE UNION34 — un cover 4-din-4 implică deja 3-din-3
# ===========================================================================
def wheel_union34(
    pool, pick, guarantee=4, max_variants=0, scores=None, time_limit: float = 15.0
):
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
        pool,
        pick,
        target_guarantee,
        max_variants=max_variants,
        scores=scores,
    )
    logger.info(
        "[WHEEL-U34] cover g%d = %d bilete (pool=%d, pick=%d; 3+/4+ acoperite când g=4)",
        target_guarantee,
        len(wheel),
        len(pool),
        pick,
    )
    # Contractul comun al modulelor de wheeling: procentul raportat corespunde
    # garanției CERUTE de apelant. La un cap de bilete, C(v,pick,4) poate avea
    # altă acoperire decât C(v,pick,3), chiar dacă fără cap ambele sunt 100%.
    return wheel, compute_coverage_pct(wheel, pool, guarantee)


# ===========================================================================
# 6) LOTTO DESIGN „t dacă p" — greedy pozițional + ILP exact pe geometrii mici
# ===========================================================================
_LOTTO_MAX_BLOCKS = 12000  # C(v, pick) peste care nici greedy-ul exhaustiv nu merită
_LOTTO_ILP_MAX_BLOCKS = 4000  # ILP doar pe geometrii mici (timp de solver)
_LOTTO_ILP_MAX_TARGETS = 4000
_LOTTO_COVER_CACHE: dict[tuple[int, int, int, int], list[tuple[int, ...]]] = {}


def _lotto_cover_positions(
    v: int, pick: int, guarantee: int, condition: int, time_limit: float = 10.0
) -> list[tuple[int, ...]]:
    """Cover „guarantee dacă condition" pe POZIȚII 0..v-1, determinist, memoizat.

    Obiectivul e numărul de bilete, deci coverul nu depinde de scoruri și e
    invariant la reetichetare (ca la ILP-ul clasic). Greedy: la fiecare pas
    biletul care acoperă cele mai multe ținte noi, cu tie-break lexicografic;
    apoi eliminarea biletelor devenite redundante. Pe geometrii mici încearcă
    și ILP-ul exact și păstrează varianta cu mai puține bilete.
    """
    key = (int(v), int(pick), int(guarantee), int(condition))
    if key in _LOTTO_COVER_CACHE:
        return _LOTTO_COVER_CACHE[key]
    g, c = int(guarantee), int(condition)
    idxs = range(int(v))
    # Marimea se verifica pe `_comb` (math.comb), NU pe `len(list(itertools.combinations(...)))`:
    # `itertools.combinations` materializeaza intreaga lista chiar daca doar ii citesti lungimea,
    # deci garda trebuie sa vina INAINTE de orice apel la `list(itertools.combinations(...))` —
    # altfel un `condition` mare (apelant direct, nu prin engine, care plafoneaza condiția la
    # draw_n) poate porni minute intregi de materializare tacuta inainte ca garda sa apuce sa esueze.
    n_blocks = _comb(v, pick)
    if n_blocks > _LOTTO_MAX_BLOCKS:
        raise ValueError(f"lotto design prea mare: C({v},{pick})={n_blocks} blocuri")
    nt = _comb(v, c)
    if nt > _LOTTO_MAX_BLOCKS:
        raise ValueError(f"lotto design prea mare: C({v},{c})={nt} tinte")
    blocks = list(itertools.combinations(idxs, int(pick)))
    targets = list(itertools.combinations(idxs, c))
    # Bitmask-uri: ținta t acoperită de blocul b dacă |b ∩ t| >= g.
    block_masks: list[int] = []
    target_sets = [frozenset(t) for t in targets]
    for b in blocks:
        bs = set(b)
        m = 0
        for ti, ts in enumerate(target_sets):
            if len(bs & ts) >= g:
                m |= 1 << ti
        block_masks.append(m)
    full = (1 << nt) - 1

    # Greedy determinist.
    covered = 0
    chosen: list[int] = []
    while covered != full:
        best_j, best_gain = -1, 0
        for j, m in enumerate(block_masks):
            gain = bin(m & ~covered).count("1")
            if gain > best_gain:
                best_j, best_gain = j, gain
        if best_j < 0:
            break
        chosen.append(best_j)
        covered |= block_masks[best_j]
    # Eliminare redundanțe: un bilet iese dacă țintele lui rămân acoperite.
    changed = True
    while changed:
        changed = False
        for pos in range(len(chosen) - 1, -1, -1):
            others = 0
            for q, j in enumerate(chosen):
                if q != pos:
                    others |= block_masks[j]
            if others == full:
                chosen.pop(pos)
                changed = True
                break
    best = [blocks[j] for j in chosen]

    # ILP exact pe geometrii mici: acelasi obiectiv, solutie posibil mai mica.
    if len(blocks) <= _LOTTO_ILP_MAX_BLOCKS and nt <= _LOTTO_ILP_MAX_TARGETS:
        try:
            from scipy.optimize import milp, LinearConstraint, Bounds
            from scipy.sparse import lil_matrix

            A = lil_matrix((nt, len(blocks)), dtype=np.float64)
            for j, m in enumerate(block_masks):
                mm = m
                ti = 0
                while mm:
                    if mm & 1:
                        A[ti, j] = 1.0
                    mm >>= 1
                    ti += 1
            res = milp(
                c=np.ones(len(blocks)),
                constraints=LinearConstraint(A.tocsr(), lb=1, ub=np.inf),
                integrality=np.ones(len(blocks)),
                bounds=Bounds(0, 1),
                options={"time_limit": time_limit},
            )
            if res.x is not None:
                ilp = [blocks[j] for j in range(len(blocks)) if res.x[j] > 0.5]
                ilp_cov = 0
                for j in range(len(blocks)):
                    if res.x[j] > 0.5:
                        ilp_cov |= block_masks[j]
                if ilp_cov == full and len(ilp) < len(best):
                    logger.info(
                        "[WHEEL-LOTTO] ILP %d < greedy %d bilete pentru L(%d,%d,%d,%d)",
                        len(ilp),
                        len(best),
                        v,
                        pick,
                        c,
                        g,
                    )
                    best = ilp
        except Exception as exc:  # noqa: BLE001
            logger.info("[WHEEL-LOTTO] ILP indisponibil (%s) — păstrez greedy", exc)
    _LOTTO_COVER_CACHE[key] = best
    return best


def lotto_design_path(v: int, pick: int, guarantee: int, condition: int) -> str:
    """Numele fișierului local pentru lotto design L(v, pick, condition, guarantee)."""
    return f"L_{int(v)}_{int(pick)}_{int(condition)}_{int(guarantee)}.txt"


def _load_lotto_design(
    v: int, pick: int, guarantee: int, condition: int
) -> list[tuple[int, ...]] | None:
    """Citește și validează un lotto design local (același format ca La Jolla:
    un bloc de `pick` poziții 1-based pe linie). Întoarce POZIȚII 0-based sau
    None dacă fișierul lipsește ori nu acoperă 100%."""
    name = lotto_design_path(v, pick, guarantee, condition)
    for d in _LAJOLLA_DIRS:
        f = d / name
        if not f.exists():
            continue
        try:
            blocks: list[tuple[int, ...]] = []
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                nums = [int(x) for x in line.replace(",", " ").split() if x.strip()]
                if (
                    len(nums) != pick
                    or len(set(nums)) != pick
                    or any(n < 1 or n > v for n in nums)
                ):
                    raise ValueError(f"bloc invalid: {nums}")
                blocks.append(tuple(n - 1 for n in nums))
            if not blocks:
                raise ValueError("fișier gol")
            cov = lotto_coverage_pct(
                [list(b) for b in blocks], list(range(v)), guarantee, condition
            )
            if cov < 100.0:
                raise ValueError(f"acoperire incompletă: {cov:.2f}%")
            logger.info(
                "[WHEEL-LOTTO] folosesc design valid %s (%d blocuri)", f, len(blocks)
            )
            return blocks
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WHEEL-LOTTO] ignor design invalid %s: %s", f, exc)
    return None


def wheel_lotto(pool, pick, guarantee, condition, max_variants=0, scores=None):
    """Lotto design „guarantee dacă condition" pe pool-ul dat.

    Returnează (bilete, acoperire_pct) cu ACEEAȘI semantică de acoperire ca
    `lotto_coverage_pct`. Pozițiile designului se mapează pe pool-ul sortat după
    scor, ca numerele tari să apară mai des pe bilete. Cu `max_variants > 0`
    biletele se trunchiază după scor, numerele din pool se readuc pe bilete
    (`ensure_pool_numbers_on_tickets`) și acoperirea se recalculează.
    """
    g, c, pk = int(guarantee), int(condition), int(pick)
    if c < g:
        raise ValueError(f"condition={c} < guarantee={g}")
    if g > pk:
        raise ValueError(f"guarantee={g} > pick={pk}")
    pool = _sorted_pool(pool, scores)
    v = len(pool)
    if v < pk:
        return [], 0.0
    if c > v:
        # Nu există nicio submulțime de `condition` numere ÎN pool — o clampare
        # tăcută ar construi alt design decât cel raportat de apelant (care a
        # scris deja `wheel_condition_used = condition`, nu valoarea redusă)
        # în audit ÎNAINTE de acest apel. Neatins de UI/engine (acolo condiția
        # e plafonată la draw_n ≤ pool_size), dar orice alt apelant trebuie
        # avertizat, nu servit tăcut cu o garanție diferită de cea cerută.
        raise ValueError(f"condition={c} > pool size={v}")
    if c == g:
        return generate_wheel("lajolla", pool, pk, g, max_variants, scores)
    # Design local precalculat (validat 100%) → altfel greedy + ILP la cerere.
    cover = _load_lotto_design(v, pk, g, c)
    if cover is None:
        cover = _lotto_cover_positions(v, pk, g, c)
    wheel = [sorted(pool[i] for i in blk) for blk in cover]
    wheel = _order_by_scores(wheel, scores)
    if max_variants > 0 and len(wheel) > max_variants:
        wheel = wheel[:max_variants]
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pk)
    cov = lotto_coverage_pct(wheel, pool, g, c)
    logger.info(
        "[WHEEL-LOTTO] L(%d,%d,%d,%d): %d bilete, acoperire %.2f%%",
        v,
        pk,
        c,
        g,
        len(wheel),
        cov,
    )
    return wheel, cov

