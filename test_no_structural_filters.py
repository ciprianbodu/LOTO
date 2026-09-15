"""Gardă de contract: nicio metodă din registry nu are voie să fie un FILTRU STRUCTURAL.

NU este un test de performanță. Nu măsoară rate de hit, nu compară metode între
ele și nu spune că vreuna prezice ceva. Verifică un singur lucru, cerut de
CLAUDE.md §4.2/§4.3 și de §12 P1 („testul care rulează fiecare metodă activă pe
toate geometriile"): fiecare intrare din ``METHODS`` este un SCORER PER NUMĂR —
``fn(draws_2d, max_num) -> {număr: scor}`` — nu un filtru de combinație
(paritate, decade, sume, poziție, secvențe) deghizat în scorer. Filtrele
constrâng combinația, nu prezic un număr; ele au fost șterse din registry la
14.09.2026, iar acest fișier există ca să nu se întoarcă pe nesimțite.

Cum se recunoaște un filtru, fără să știm ce e „înăuntru" la metodă: prin
POOL-ul pe care îl produce. Un filtru de paritate lasă în pool o singură clasă
de rest; un filtru de decadă îngrămădește tot pool-ul în una-două decade; un
filtru de sumă sau de poziție (gaussiană pe axa valorilor) produce un interval
contiguu. Pool-ul se compară cu o distribuție nulă de pool-uri aleatoare din
același univers — DETERMINISTĂ (seed fix, număr fix de replici), ca testul să nu
fie flaky.

Discriminatorul care contează este PERSISTENȚA, nu valoarea de vârf. Măsurat pe
istoricul real (52 de metode × 3 geometrii × 5 prefixe de istoric):

* concentrarea pe decade a unui scorer legitim e MOȘTENITĂ din date — vine din
  ultima extragere sau din istoric — deci apare și dispare odată cu ele.
  ``neighbor_adjacent`` e cazul de frontieră: pe 6/49 depășește pragul pe 1 din
  cele 5 prefixe (vârf 3.54 la prag 3.23), pe 5/40 pe niciunul (vârf 3.24 la
  prag 3.62), deși pe alte tăieturi de istoric urcă mult mai sus. Valoarea de
  vârf singură nu îl deosebește de un filtru; numărul de prefixe, da;
* un filtru IMPUNE structura, deci depășește pe TOATE prefixele — inclusiv cele
  dependente de date, gen „decada fierbinte" (verificat în
  ``test_controalele_pozitive_sunt_prinse_de_porti``: 5 din 5, pe fiecare joc).

De aceea porțile de paritate și de decadă se declanșează numai când abaterea
apare pe TOATE prefixele. Poarta de bloc consecutiv rămâne strictă (orice
apariție e eșec): probabilitatea unui interval contiguu de 16 numere sub null e
~34/C(49,16) ≈ 7e-12, iar pe 52 de metode × 3 jocuri × 16 prefixe nu s-a produs
niciodată. Aceeași gardă există deja în ``pool_selection.py``, dar acolo doar
scrie un avertisment în audit; aici e contract.

Ce NU prind porțile (limită cunoscută, scrisă aici ca să nu fie citită drept
acoperire completă a listei din CLAUDE.md §4.3):

* un filtru care ar IMPUNE echilibru perfect — 8 pare / 8 impare, sau
  uniformitate pe decade — stă exact pe media nulă, adică acolo unde stau și
  pool-urile aleatoare; ar fi prins doar dacă ar produce în plus bloc
  consecutiv;
* filtrele de SPAȚIERE și de clasă de rest (pas fix între numere: „doar
  n % 3 == 1", „doar n % 7 ∈ {0,1,2}"), deși secvențele sunt și ele interzise
  de §4.3. Verificat prin injectare în registry: un pool cu pas 3 trece neprins
  pe toate cele trei geometrii — are paritate mixtă, decade uniforme și niciun
  bloc consecutiv. O poartă pe regularitatea golurilor nu se poate calibra azi:
  pe 5/40 filtrul cu pas 3 dă deviație a golurilor 0.80, iar cea mai mică
  valoare a unei metode reale e 0.85 (``imapa_agg``) — pragul ar tăia metoda
  reală odată cu filtrul. Clasa rămâne descoperită până când există o
  statistică ce o separă;
* geometria Joker Urna 2 (pool de un singur număr) nu trece prin porțile de
  formă — vezi ``test_urna2_doar_metodele_relationale_pot_avea_scoruri_plate``.

Nu scrie niciun fișier, nu atinge cache-uri și nu depinde de
``best_methods.json`` (care poate lipsi). Dacă un CSV din ``_ISTORIC/`` lipsește,
testul se marchează skip, nu eșuează.
"""

from __future__ import annotations

import csv
import inspect
import math
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np
import pytest

from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.core.ranking import is_consecutive_block, rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance


ISTORIC_DIR = Path(__file__).resolve().parent / "_ISTORIC"

# Pool-ul maxim admis de UI (CLAUDE.md §6: 6..16). Cel mai mare pool e și cel
# mai informativ pentru forma pool-ului: la 6 numere orice statistică de formă e
# zgomot pur.
POOL_SIZE = 16

# Distribuția nulă: seed fix + număr fix de replici => testul dă exact același
# verdict la fiecare rulare. 4000 de replici sunt de ajuns pentru medie și
# deviație standard stabile pe a doua zecimală (statisticile sunt hipergeometrice,
# cu suport mic) și costă ~10 ms per joc — tot scanul rămâne sub 2 s per geometrie.
NULL_SEED = 20260915
NULL_REPLICAS = 4000

# Prefixe de istoric pe care se repetă scanul. Fracțiuni fixe => deterministe.
# Rolul lor: un filtru impune structura pe ORICE istoric, un scorer legitim o
# moștenește doar din unele.
PREFIX_FRACTIONS = (0.60, 0.70, 0.80, 0.90, 1.00)

# Paritate: 4σ față de media nulă. Măsurat pe registry-ul curent, cea mai mare
# abatere reală e |z| = 3.16 (knn_feature_pooled, 6/49); un filtru care păstrează
# o singură clasă de paritate ajunge la |z| = 4.8..5.1 pe cele trei geometrii.
# Pragul 4.0 cade curat între cele două regimuri.
PARITY_SIGMA = 4.0

# Decade: prag DELIBERAT permisiv. Percentila 99 a distribuției nule ar aprinde
# testul pe neighbor_adjacent, hot_consistency, gbm_stumps_pooled, holt_forecast
# și online_logit_sgd — metode care NU sunt filtre, doar moștenesc concentrarea
# din ultima extragere. Pragul e media nulă + 4.5σ, ridicat la nevoie până la
# MAXIMUL observat în distribuția nulă: nicio valoare pe care hazardul pur o
# produce singur nu poate fi acuzată de structură (contează pe Joker, unde
# maximul nul urcă la 4.68σ). Valorile efective: 6/49 3.23, 5/40 3.62,
# Joker 3.54. Separarea reală nu vine de aici, ci din regula de persistență
# explicată în docstring-ul modulului.
DECADE_SIGMA = 4.5

# Bloc consecutiv: pragul din `ranking.is_consecutive_block`, identic cu cel
# folosit de garda de audit din `pool_selection.py`.
CONSECUTIVE_MIN_SIZE = 6

# Familii bazate pe RELAȚII între numere (perechi, tranziții, graf de
# co-apariție, similaritate de extrageri). Pe Joker Urna 2 — o singură bilă per
# extragere — nu există perechi, deci matricea de co-apariție e structural goală
# și scorurile ies plate. Nu e defect de metodă, e geometrie.
RELATIONAL_FAMILIES = frozenset({"cooccurrence", "graph", "transition", "similarity"})


class Geometry(NamedTuple):
    """Un joc real: fișierul de istoric, coloanele de bile și universul."""

    key: str
    csv_name: str
    columns: tuple[str, ...]
    max_num: int


# Cele trei geometrii cu pool (Urna 2 are pool de un singur număr — vezi
# `test_urna2_doar_metodele_relationale_pot_avea_scoruri_plate`).
GAMES: tuple[Geometry, ...] = (
    Geometry("loto_6_49", "loto_6_49.csv", ("n1", "n2", "n3", "n4", "n5", "n6"), 49),
    Geometry("loto_5_40", "loto_5_40.csv", ("n1", "n2", "n3", "n4", "n5"), 40),
    Geometry("joker_urna1", "joker.csv", ("n1", "n2", "n3", "n4", "n5"), 45),
)

URNA2 = Geometry("joker_urna2", "joker.csv", ("joker",), 20)


# ---------------------------------------------------------------------------
# Încărcarea istoricului (csv, fără pandas — testul trebuie să fie ieftin)
# ---------------------------------------------------------------------------

_HISTORY_CACHE: dict[str, np.ndarray] = {}


def _load_history(geom: Geometry) -> np.ndarray:
    """Istoricul real ca matrice (n_extrageri × n_bile), fără rândurile invalide.

    Rândurile cu valori ne-întregi, în afara universului sau cu duplicate sunt
    sărite: contractul de extragere validă din `draw_validation.py`, aplicat aici
    minimal ca să nu importăm tot lanțul de validare într-un test de structură.
    """
    cached = _HISTORY_CACHE.get(geom.key)
    if cached is not None:
        return cached

    path = ISTORIC_DIR / geom.csv_name
    if not path.is_file():
        pytest.skip(f"istoric lipsă: {path}")

    rows: list[list[int]] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for record in csv.DictReader(fh):
            try:
                values = [int(record[col]) for col in geom.columns]
            except (KeyError, TypeError, ValueError):
                continue
            if len(set(values)) != len(values):
                continue
            if not all(1 <= v <= geom.max_num for v in values):
                continue
            rows.append(values)

    if len(rows) < 100:
        pytest.skip(f"istoric prea scurt pentru {geom.key}: {len(rows)} extrageri")

    draws = np.asarray(rows, dtype=np.int64)
    _HISTORY_CACHE[geom.key] = draws
    return draws


# ---------------------------------------------------------------------------
# Statistici de formă a pool-ului + distribuția nulă
# ---------------------------------------------------------------------------


def _odd_count(pool: list[int]) -> int:
    return sum(1 for n in pool if n % 2 == 1)


def _decade_spread(pool: list[int], max_num: int) -> float:
    """Deviația standard a histogramei pe decade (1-10, 11-20, ...).

    Mare = pool îngrămădit în puține decade. Un pool uniform pe decade dă valori
    mici; distribuția nulă arată cât de mare poate fi din pură întâmplare.
    """
    n_bins = (max_num + 9) // 10
    counts = np.bincount((np.asarray(pool, dtype=np.int64) - 1) // 10, minlength=n_bins)
    return float(counts.std())


class NullStats(NamedTuple):
    parity_mean: float
    parity_sd: float
    decade_threshold: float
    decade_mean: float
    decade_sd: float


_NULL_CACHE: dict[int, NullStats] = {}


def _null_stats(max_num: int) -> NullStats:
    """Distribuția nulă a statisticilor de formă, pe pool-uri aleatoare de 16 numere.

    Determinist: `default_rng(NULL_SEED)` + `NULL_REPLICAS` fixe. Pool-urile se
    extrag fără repetiție din 1..max_num prin argsort pe o matrice de uniforme —
    echivalent cu o permutare per replică, dar vectorizat.
    """
    cached = _NULL_CACHE.get(max_num)
    if cached is not None:
        return cached

    rng = np.random.default_rng(NULL_SEED)
    pools = np.argsort(rng.random((NULL_REPLICAS, max_num)), axis=1)[:, :POOL_SIZE] + 1

    odd = (pools % 2 == 1).sum(axis=1).astype(np.float64)

    n_bins = (max_num + 9) // 10
    decades = (pools - 1) // 10
    counts = np.empty((NULL_REPLICAS, n_bins), dtype=np.float64)
    for b in range(n_bins):
        counts[:, b] = (decades == b).sum(axis=1)
    spread = counts.std(axis=1)

    stats = NullStats(
        parity_mean=float(odd.mean()),
        parity_sd=float(odd.std(ddof=1)),
        # Pragul nu coboară niciodată sub ce produce hazardul singur.
        decade_threshold=max(
            float(spread.max()),
            float(spread.mean() + DECADE_SIGMA * spread.std(ddof=1)),
        ),
        decade_mean=float(spread.mean()),
        decade_sd=float(spread.std(ddof=1)),
    )
    _NULL_CACHE[max_num] = stats
    return stats


# ---------------------------------------------------------------------------
# Scanul comun (folosit identic de metodele reale și de controalele pozitive)
# ---------------------------------------------------------------------------

ScoreFn = Callable[[np.ndarray, int], dict[int, float]]


class Breaches(NamedTuple):
    """Pe câte prefixe de istoric a depășit fiecare poartă, plus valoarea de vârf."""

    prefixes: int
    parity: int
    decade: int
    consecutive: int
    worst_parity_z: float
    worst_decade: float
    worst_pool: list[int]


def _scan(score_fn: ScoreFn, draws: np.ndarray, geom: Geometry) -> Breaches:
    """Rulează scorerul pe prefixele fixe și numără depășirile fiecărei porți."""
    null = _null_stats(geom.max_num)
    parity_hits = decade_hits = block_hits = 0
    worst_parity_z = 0.0
    worst_decade = 0.0
    worst_pool: list[int] = []

    cuts = [max(50, int(len(draws) * frac)) for frac in PREFIX_FRACTIONS]
    for cut in cuts:
        scores = score_fn(draws[:cut], geom.max_num)
        pool = sorted(rank_by_score(scores, POOL_SIZE))

        parity_z = abs(_odd_count(pool) - null.parity_mean) / null.parity_sd
        if parity_z > PARITY_SIGMA:
            parity_hits += 1
        if parity_z > worst_parity_z:
            worst_parity_z = parity_z
            worst_pool = pool

        spread = _decade_spread(pool, geom.max_num)
        if spread > null.decade_threshold:
            decade_hits += 1
        if spread > worst_decade:
            worst_decade = spread
            if not worst_pool:
                worst_pool = pool

        if is_consecutive_block(pool, min_size=CONSECUTIVE_MIN_SIZE):
            block_hits += 1
            worst_pool = pool

    return Breaches(
        prefixes=len(cuts),
        parity=parity_hits,
        decade=decade_hits,
        consecutive=block_hits,
        worst_parity_z=worst_parity_z,
        worst_decade=worst_decade,
        worst_pool=worst_pool or [],
    )


def _registry_score_fn(name: str) -> ScoreFn:
    def _call(draws: np.ndarray, max_num: int) -> dict[int, float]:
        return call_method(name, draws, max_num)[0]

    return _call


# ---------------------------------------------------------------------------
# 1. Porțile pe metodele reale din registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geom", GAMES, ids=[g.key for g in GAMES])
def test_metodele_din_registry_nu_sunt_filtre_structurale(geom: Geometry) -> None:
    """Nicio metodă nu impune paritate, bloc consecutiv sau concentrare pe decade.

    Porțile și pragurile lor sunt explicate în docstring-ul modulului. Pe scurt:
    paritatea și decadele se acuză doar când abaterea apare pe TOATE prefixele
    (un filtru o impune mereu, un scorer o moștenește din date doar uneori),
    blocul consecutiv se acuză la prima apariție (sub null e practic imposibil).
    """
    draws = _load_history(geom)
    null = _null_stats(geom.max_num)

    parity_offenders: list[str] = []
    decade_offenders: list[str] = []
    block_offenders: list[str] = []

    for name in sorted(METHODS):
        result = _scan(_registry_score_fn(name), draws, geom)

        if result.consecutive:
            block_offenders.append(
                f"{name}: pool bloc consecutiv pe {result.consecutive}/"
                f"{result.prefixes} prefixe (ex. {result.worst_pool})"
            )
        if result.parity == result.prefixes:
            parity_offenders.append(
                f"{name}: {_odd_count(result.worst_pool)} impare din {POOL_SIZE}, "
                f"|z| = {result.worst_parity_z:.2f} > {PARITY_SIGMA} pe toate cele "
                f"{result.prefixes} prefixe (medie nulă {null.parity_mean:.2f} ± "
                f"{null.parity_sd:.2f})"
            )
        if result.decade == result.prefixes:
            decade_offenders.append(
                f"{name}: spread decade {result.worst_decade:.3f} > prag "
                f"{null.decade_threshold:.3f} pe toate cele {result.prefixes} "
                f"prefixe (medie nulă {null.decade_mean:.3f} ± {null.decade_sd:.3f}, "
                f"pool {result.worst_pool})"
            )

    # Cele trei porți se verifică separat, dar pytest raportează doar primul
    # assert căzut: coada spune câte metode a prins fiecare dintre celelalte
    # porți, ca o reparație să nu se facă „pe bucăți", descoperind pe rând.
    def _rest(*others: tuple[str, list[str]]) -> str:
        found = [f"{label}={len(items)}" for label, items in others if items]
        return f" [și alte porți: {', '.join(found)}]" if found else ""

    assert not parity_offenders, (
        f"[{geom.key}] metodă-filtru de PARITATE în registry (CLAUDE.md §4.3): "
        + "; ".join(parity_offenders)
        + _rest(("bloc", block_offenders), ("decade", decade_offenders))
    )
    assert not block_offenders, (
        f"[{geom.key}] pool bloc consecutiv — scorer degenerat pe axa valorilor "
        f"(filtru de sumă/poziție), nu semnal: "
        + "; ".join(block_offenders)
        + _rest(("decade", decade_offenders))
    )
    assert not decade_offenders, (
        f"[{geom.key}] metodă-filtru de DECADE în registry (CLAUDE.md §4.3): "
        + "; ".join(decade_offenders)
        + " — dacă metoda chiar nu e filtru, praguri și calibrare în "
        "docstring-ul modulului test_no_structural_filters.py"
    )


# ---------------------------------------------------------------------------
# 2. Controale pozitive — porțile chiar au dinți
# ---------------------------------------------------------------------------


def _filter_parity(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru de paritate: păstrează o singură clasă de rest."""
    return {n: (1.0 if n % 2 else 0.0) for n in range(1, max_num + 1)}


def _filter_hot_parity(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru de paritate DEPENDENT DE DATE: clasa cea mai frecventă din istoric."""
    flat = np.asarray(draws).ravel()
    per_class = [float((flat % 2 == 0).sum()), float((flat % 2 == 1).sum())]
    return {n: per_class[n % 2] + 1e-6 * n for n in range(1, max_num + 1)}


def _filter_decades(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru de decade: doar decadele 1-10 și 21-30 (neadiacente, deci fără bloc)."""
    return {
        n: ((n % 7) / 7.0 if ((n - 1) // 10) in (0, 2) else 0.0)
        for n in range(1, max_num + 1)
    }


def _filter_hot_decade(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru „decada fierbinte", DEPENDENT DE DATE — genul vechiului 649_decade_hot."""
    n_bins = (max_num + 9) // 10
    hist = np.bincount((np.asarray(draws).ravel() - 1) // 10, minlength=n_bins)
    return {n: float(hist[(n - 1) // 10]) + 1e-6 * n for n in range(1, max_num + 1)}


def _filter_sum(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru de sumă: preferă numerele care apropie suma biletului de medie."""
    center = (max_num + 1) / 2.0
    return {n: float(math.exp(-abs(n - center) / 6.0)) for n in range(1, max_num + 1)}


def _filter_position(draws: np.ndarray, max_num: int) -> dict[int, float]:
    """Filtru pozițional: scor monoton pe axa valorilor (numere mici întâi)."""
    return {n: float(max_num - n) / max_num for n in range(1, max_num + 1)}


# (nume, scorer-filtru, poarta care TREBUIE să îl prindă)
POSITIVE_CONTROLS = (
    ("filtru_paritate", _filter_parity, "parity"),
    ("filtru_paritate_fierbinte", _filter_hot_parity, "parity"),
    ("filtru_decade", _filter_decades, "decade"),
    ("filtru_decada_fierbinte", _filter_hot_decade, "decade"),
    ("filtru_suma", _filter_sum, "consecutive"),
    ("filtru_pozitie", _filter_position, "consecutive"),
)


@pytest.mark.parametrize("geom", GAMES, ids=[g.key for g in GAMES])
@pytest.mark.parametrize(
    "label,filter_fn,gate", POSITIVE_CONTROLS, ids=[c[0] for c in POSITIVE_CONTROLS]
)
def test_controalele_pozitive_sunt_prinse_de_porti(
    geom: Geometry, label: str, filter_fn: ScoreFn, gate: str
) -> None:
    """Fără controale pozitive, un prag prea permisiv ar trece drept „registry curat".

    Șase filtre structurale sintetice (două dintre ele dependente de date, deci
    nu banal de prins) trec prin ACELAȘI scan ca metodele reale și trebuie
    reținute pe TOATE prefixele. Dacă vreodată cineva ridică pragurile ca să
    „liniștească" testul de mai sus, testul ăsta cade primul.
    """
    draws = _load_history(geom)
    result = _scan(filter_fn, draws, geom)
    hits = getattr(result, gate)
    assert hits == result.prefixes, (
        f"[{geom.key}] controlul pozitiv {label} NU e prins de poarta {gate}: "
        f"{hits}/{result.prefixes} prefixe (|z| paritate {result.worst_parity_z:.2f}, "
        f"spread decade {result.worst_decade:.3f}, pool {result.worst_pool}) — "
        "porțile s-au relaxat prea mult și nu mai apără nimic"
    )


# ---------------------------------------------------------------------------
# 3. Contractul static al scorerului
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geom", GAMES + (URNA2,), ids=[g.key for g in GAMES + (URNA2,)])
def test_contract_scorer_semnatura_si_iesire(geom: Geometry) -> None:
    """`fn(draws_2d, max_num) -> {1..max_num: float finit}`, pe fiecare geometrie.

    Ieftin (istoric scurt) și complementar porților statistice: prinde o „metodă"
    care ar returna altceva decât scoruri per număr — o listă de combinații, un
    dict indexat pe altceva, sau un scor lipsă pentru numerele pe care filtrul
    le-a eliminat deja. Un scorer nu are voie să elimine numere: eliminarea e
    treaba top-N-ului canonic, nu a metodei.
    """
    draws = _load_history(geom)[-40:]
    expected_keys = set(range(1, geom.max_num + 1))

    for name, (fn, _family, _train, _notes) in sorted(METHODS.items()):
        params = [
            p
            for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert len(params) >= 2, (
            f"{name}: semnătura {tuple(p.name for p in params)} nu respectă "
            "contractul fn(draws_2d, max_num) din CLAUDE.md §4.2"
        )

        scores, _elapsed = call_method(name, draws, geom.max_num)

        assert isinstance(scores, dict), (
            f"[{geom.key}] {name} a returnat {type(scores).__name__}, nu un dict de "
            "scoruri per număr — un scorer nu returnează combinații"
        )
        assert set(scores) == expected_keys, (
            f"[{geom.key}] {name} a returnat {len(scores)} chei, nu toate numerele "
            f"1..{geom.max_num} (lipsă: {sorted(expected_keys - set(scores))[:5]}, "
            f"în plus: {sorted(set(scores) - expected_keys)[:5]}) — o metodă care "
            "omite numere e un filtru, nu un scorer"
        )
        assert all(isinstance(k, (int, np.integer)) for k in scores), (
            f"[{geom.key}] {name} are chei care nu sunt întregi"
        )
        bad = [n for n, v in scores.items() if not math.isfinite(float(v))]
        assert not bad, f"[{geom.key}] {name} are scoruri ne-finite pentru {bad[:5]}"

        pool = rank_by_score(scores, POOL_SIZE)
        assert len(pool) == POOL_SIZE, (
            f"[{geom.key}] {name}: top-{POOL_SIZE} canonic a dat {len(pool)} numere"
        )


# ---------------------------------------------------------------------------
# 4. Scoruri utilizabile pe geometriile reale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geom", GAMES, ids=[g.key for g in GAMES])
def test_scoruri_utilizabile_pe_geometriile_cu_pool(geom: Geometry) -> None:
    """Pe cele trei jocuri cu pool, nicio metodă nu produce scoruri inutilizabile.

    `has_usable_score_variance` e aceeași poartă pe care o folosesc bench-ul și
    producția (CLAUDE.md §4.2): scoruri goale, plate, ne-numerice sau ne-finite
    nu definesc un ranking. O metodă plată aici ar ajunge în bench ca `failed`
    și în producție pe fallback, tăcut.
    """
    draws = _load_history(geom)
    flat = [
        name
        for name in sorted(METHODS)
        if not has_usable_score_variance(call_method(name, draws, geom.max_num)[0])
    ]
    assert not flat, (
        f"[{geom.key}] metode cu scoruri inutilizabile (goale/plate/ne-finite) pe "
        f"istoricul complet: {flat}"
    )


def test_urna2_doar_metodele_relationale_pot_avea_scoruri_plate() -> None:
    """Joker Urna 2 (1 bilă din 20) e EXCLUSĂ explicit din testul de mai sus — de ce.

    Cu o singură bilă pe extragere nu există perechi, deci matricea de
    co-apariție e structural goală și metodele construite pe relații între numere
    (co-apariție, graf, tranziții, similaritate) ies plate. Nu e defect de metodă,
    e geometria jocului — bench-ul le marchează `failed` acolo, ceea ce e corect.
    Excluderea nu e însă o relaxare tăcută: orice metodă care NU e relațională
    trebuie să producă scoruri utilizabile și pe 1/20. Dacă o metodă de recență
    sau de învățare începe să iasă plată aici, testul cade.
    """
    draws = _load_history(URNA2)
    unexpected = []
    for name in sorted(METHODS):
        family = METHODS[name][1]
        if family in RELATIONAL_FAMILIES:
            continue
        if not has_usable_score_variance(call_method(name, draws, URNA2.max_num)[0]):
            unexpected.append(f"{name} (familia {family})")
    assert not unexpected, (
        "[joker_urna2] metode NErelaționale cu scoruri plate pe geometria 1/20: "
        + ", ".join(unexpected)
        + " — pe Urna 2 sunt tolerate plate doar familiile "
        f"{sorted(RELATIONAL_FAMILIES)}, care au nevoie de perechi"
    )
