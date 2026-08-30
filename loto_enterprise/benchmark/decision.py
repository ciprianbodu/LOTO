"""Decision algorithm: pick the optimal (scorer, sim_depth, use_blacklist) per
(game, urna, pool_size) — for the auto-pilot.

Selection rules (in priority order):
    1. Method must BEAT the random baseline consistently — at least 60% of
       sim_depth windows must show lift > 0 over the random baseline at the
       same (game, pool, window).
    2. Among qualifying methods, pick the one with HIGHEST mean lift across
       all sim_depth windows (averaged with double-weight on larger windows
       since they exercise more historical data).
    3. For the chosen method, pick the optimal sim_depth window — the one
       that maximises avg_hits AND has at least 30 test draws (statistical
       stability). Prefer larger windows on ties.
    4. Use blacklist if the +BL variant beats the no-BL variant at that
       chosen (method, window).

The output is written to best_methods.json under `auto_pilot_per_pool[gk][kN]`
with full traceability (rationale + supporting numbers).

Invarianți (NU strica):
    * Baseline-urile NEDETERMINISTE (`EXCLUDED_FROM_PRODUCTION`, adică `random`)
      sunt doar REFERINȚĂ de comparație — nu pot deveni niciodată scorer de
      producție, nici pe ramura de fallback.
    * Ensemble-ul nu e simplă feliere top-K: membrii cu semnătură de performanță
      cvasi-identică sunt săriți (`ENSEMBLE_MAX_CORR`), altfel eticheta
      "variance-reduction" ar fi falsă.

Cât prinde EFECTIV stratul de decorelare de aici (ca să nu pară mai puternic
decât e): semnătura se măsoară pe RATE din folds.csv, CENTRATE pe coloane, deci
prinde doar redundanța aproape perfectă. Măsurat pe `bench_results/folds.csv`
(20.07.2026): la loto_6_49 doar 7 perechi din 5565 ating r≥0.99, iar pe toată
matricea de decizie rezultă o SINGURĂ eliminare (loto_5_40 k16:
`croston_sba` ~ `croston_classic`, r=1.0). Contra-exemplul care contează:
`649_katz15_gap85` vs `649_katz25_gap75` dau r=0.93 pe RATE — sub prag, deci NU
se elimină aici — deși pe SCORURI au r=0.996. Redundanța de tip "generează
practic același pool" rămâne pe seama stratului următor,
`method_selector._select_decorrelated` (MAX_MEMBER_CORR=0.95, pe vectorii de
scor reali). Stratul de aici e o plasă ieftină pe datele pe care le AVEM în
folds.csv, NU garanția de decorelare.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import numpy as np
import pandas as pd

from loto_enterprise.benchmark.hit_target import clamp_bench_hit_target

logger = logging.getLogger(__name__)


CONSISTENCY_THRESHOLD = 0.60  # method must beat random in ≥60% of windows

# Câte ferestre COMUNE (metodă ∩ random) trebuie să existe ca poarta de
# consistență să însemne ceva. `_windows_method_beats_random` întoarce
# dimensiunea INTERSECȚIEI de percentile; cu o singură fereastră comună,
# „bate random în ≥60% din ferestre" degenerează într-o comparație unică —
# adică 1/1 = 100%, deci poarta lasă să treacă orice.
# Reproductibil: pe folds.csv real (loto_6_49 k12), ștergând rândurile `random`
# de la 3 din 4 percentile, numărul de metode calificate sare de la 3 la 17
# (random doar @100), 12 (@30) sau 4 cu ALT câștigător (@10) — și
# `low_confidence` rămâne False în toate cazurile.
# Nu e doar teoretic: `runner._flush_folds` rescrie folds.csv la fiecare ~100 de
# folds terminate, iar future-urile se termină în altă ordine decât au fost
# trimise, deci un bench întrerupt lasă un folds.csv în care `random` are 1-3
# percentile, iar metodele de dinainte au 4.
MIN_CONSISTENCY_WINDOWS = 3
MIN_TEST_DRAWS_FOR_STABILITY = 30

# Câte metode intră în ensemble-ul de scoring (blend ponderat), în plus față de
# câștigătorul unic `scorer` (păstrat pt compatibilitate/afișare). Loteria e
# aleatoare — diferențele dintre metodele calificate sunt în mare parte zgomot
# statistic (vezi CLAUDE.md); combinarea top-K reduce riscul ca o singură
# metodă "norocoasă" pe date de test să domine complet pool-ul, în loc să
# pretindă că am găsit "cea mai bună" metodă. Cu 1 metodă calificată, ensemble
# degenerează la un singur membru (weight=1.0) — bit-identic cu comportamentul
# vechi (fără normalizare/combinare în loto_engine.py).
ENSEMBLE_MAX_METHODS = 3

# Corelația maximă acceptată între semnăturile de performanță a doi membri de
# ensemble. Peste prag => membrii sunt redundanți (aceeași serie de rate pe
# aceleași folduri) și "variance-reduction" ar fi o etichetă FALSĂ: media a două
# metode cvasi-identice are aceeași varianță ca una singură. Vezi
# `_select_ensemble_members`.
ENSEMBLE_MAX_CORR = 0.99

# Eșantion minim (metode × puncte) sub care dedup-ul de ensemble se DEZACTIVEAZĂ.
# Corelația Pearson degenerează pe puține puncte: pe 2 coloane dă mereu ±1.0
# indiferent de valori (verificat: `_signature_corr([0.1,-0.1], [0.3,-0.3])` →
# +1.0000, iar cu semne opuse → -1.0000), deci am elimina membri fără nicio
# dovadă. Simetric, cu puține METODE "consensul" scăzut la centrare e estimat
# din prea puține observații (cu 2 metode rândurile centrate sunt exact opuse →
# r=-1 mereu), deci nici acolo nu merită încredere.
# ⚠️ ACTUALIZAT 2026-07-27 — garda NU mai e inertă. Înainte de curarea de metode
# (`curated_methods.json`), pe folds.csv cu 107 metode semnătura era (106, 60) pe
# toate cele 3 jocuri, deci garda nu se declanșa niciodată. Cu setul curat (16
# metode benchuite) numărul de metode CALIFICATE per pool scade la 2-4 → sub prag
# → dedup-ul pe semnătura de performanță e SĂRIT sistematic (vezi log-ul
# "[decision] dedup ensemble dezactivat"). Consecință: `ensemble_dropped_redundant`
# rămâne gol. NU e o regresie: curarea garantează deja că niciun membru nu e
# redundant pe axa SCORURI (|Spearman| < 0.95), iar decorelarea de la runtime
# (`method_selector._select_decorrelated`) rămâne activă — dedup-ul de aici nu mai
# are ce să taie. Dacă se revine la toate metodele, garda redevine inertă singură.
ENSEMBLE_MIN_SIGNATURE_POINTS = 5

# Baseline-uri care NU au voie să devină NICIODATĂ scorer de producție.
# `random` (methods.py: `np.random.default_rng()` FĂRĂ sămânță) e nedeterminist:
# două rulări pe aceleași date dau pool-uri diferite => decizia nu e nici
# reproductibilă, nici auditabilă, iar poziția lui în clasament e pur zgomot
# (pe folds.csv din 20.07.2026, cu metrica PROPRIE a deciziei — Wilson-lb pe
# `rate_3plus_k{K}`, pooled pe n_test — `random` urcă până la #6/107 pe
# loto_5_40 k5, #14/107 pe joker_urna1 k15, #40/107 pe loto_6_49 k20; exact
# dovada că diferențele dintre metode sunt zgomot, nu semnal. Cifrele se
# RENUMĂRĂ după fiecare bench, nu se citează din memorie).
# Rolul lui e DOAR de podea de sanitate în bench ("cât înseamnă pură șansă"),
# niciodată de generator.
# `frequency` rămâne permisă ca scorer: e baseline DETERMINIST și SAFE_FALLBACK.
# `recency` a fost blacklistată și ELIMINATĂ din METHODS (2026-08-25).
# `random` e EXCLUS din producție (nedeterminist, fără sămânță).
# (aceleași date → același scor), reproductibile și explicabile pentru
# utilizator; un pool "cele mai frecvente numere" e o alegere onestă, un pool
# "numere date cu zarul" nu.
EXCLUDED_FROM_PRODUCTION = frozenset({"random"})

# Plasă de siguranță DETERMINISTĂ când nu rămâne nicio metodă reală în
# folds.csv (niciodată `random` — vezi EXCLUDED_FROM_PRODUCTION).
SAFE_FALLBACK_SCORER = "frequency"
DEFAULT_SIM_DEPTH_PCT = 40  # identic cu default-ul din method_selector

# Pragul de hituri pe care bench-ul ALEGE metoda câștigătoare (rata extragerilor cu
# ≥ acest număr de numere ghicite). Implicit 3 (rată mai densă → selecție mai stabilă).
# NU schimbă șansele — loteria e aleatoare; doar pe ce metrică se alege câștigătorul.
# Reversibil: schimbă aici, sau setează LOTO_BENCH_TARGET=4.
# Doar 3 sau 4: runner-ul emite exclusiv rate_3plus_* / rate_4plus_*. Orice
# altă valoare făcea `_resolve_rate_col` să cadă TĂCUT pe 4+ (doar WARNING
# în log) — decizia se lua pe altă țintă decât cea cerută.
# `clamp_bench_hit_target` e importat din `hit_target` (stdlib) — re-exportat aici.

BENCH_HIT_TARGET = clamp_bench_hit_target(os.environ.get("LOTO_BENCH_TARGET", "3"))


def _safe_lift(method_hits: float, random_hits: float) -> float:
    return float(method_hits) - float(random_hits)


def _wilson_lower_bound(successes: float, n: float, z: float = 1.0) -> float:
    """Limită inferioară de încredere (Wilson score) pt o proporție.

    Evenimentele T+ (implicit 3+) pe pool-uri mici pot fi rare (puține evenimente în
    sute de extrageri de test) — o comparație pe medie brută poate favoriza o
    metodă cu 1 eveniment din pură șansă în fața uneia cu 0, fără nicio
    semnificație statistică. Wilson reduce increderea în proporții estimate din
    puține evenimente, PUR determinist (fără aleatorism nou) pe datele existente
    din folds.csv. z=1.0 ≈ interval unilateral ~84%, conservator dar nu excesiv.
    """
    n = float(n)
    if n <= 0:
        return 0.0
    phat = max(0.0, min(1.0, float(successes) / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return max(0.0, (center - margin) / denom)


def _windows_method_beats_random(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> tuple[int, int]:
    """Return (n_windows_beat, n_windows_total) for method vs random baseline."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0, 0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0, 0
    n_beat = 0
    for p in common_pcts:
        if method_by_pct[p] > random_by_pct[p]:
            n_beat += 1
    return n_beat, len(common_pcts)


def _weighted_mean_lift(
    sub_real_method: pd.DataFrame,
    sub_real_random: pd.DataFrame,
    base_col: str,
) -> float:
    """Compute mean lift weighted by sim_depth (larger windows weight more)."""
    if sub_real_method.empty or sub_real_random.empty:
        return 0.0
    method_by_pct = sub_real_method.groupby("percentile")[base_col].mean()
    random_by_pct = sub_real_random.groupby("percentile")[base_col].mean()
    common_pcts = sorted(set(method_by_pct.index) & set(random_by_pct.index))
    if not common_pcts:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for p in common_pcts:
        weight = float(p)  # 100% window weighs 10x more than 10% window
        lift = float(method_by_pct[p]) - float(random_by_pct[p])
        weighted_sum += weight * lift
        weight_total += weight
    return weighted_sum / weight_total if weight_total > 0 else 0.0


def pooled_rate_and_neff(frame: pd.DataFrame, rate_col: str) -> tuple[float, float] | None:
    """(rată pooled, n EFECTIV) pentru o rată T+ măsurată pe ferestre sim_depth.

    Ferestrele sunt sufixe CUIBĂRITE (10% ⊂ 30% ⊂ 60% ⊂ 100% în configurația de
    producție a UI-ului; 10%…100% la default-ul CLI), deci o extragere recentă
    apare în MAI MULTE ferestre. Consecințe, tratate separat:

    • RATA rămâne pooled peste toate ferestrele (Σ rate·n / Σ n). Ponderarea
      implicită pe recență e DELIBERATĂ (aceeași filozofie ca `_weighted_mean_lift`).
    • DOVADA (n-ul dat lui Wilson) NU e Σ n — asta ar număra aceeași extragere de
      mai multe ori (măsurat pe folds de producție: Σn/max ≈ 2.03 la 4 ferestre;
      ~5.5 la default-ul CLI de 10 ferestre) → limite sistematic supraîncrezătoare.
      Nici max(n) nu e corect: rata e o medie PONDERATĂ, deci n-ul corect e cel
      EFECTIV (Kish) al ponderilor. Cu m_j = în câte ferestre intră extragerea j,
      Var(phat) = p(1-p)·Σm² / (Σm)² ⟹ n_eff = (Σm)²/Σm². Pe ferestre-sufix
      Σm = Σ n_i și Σm² = Σ_i Σ_k min(n_i, n_k), deci n_eff se calculează direct
      din dimensiunile ferestrelor, fără să reconstruim multiplicitățile.

    Calibrare verificată prin simulație (40k replici, n=2497, p=0.09, z=1, nominal
    0.8413): acoperirea reală a limitei inferioare = 0.743 cu Σn (varianta veche),
    0.814 cu max(n), 0.844 cu n_eff Kish. `n_eff ≈ 0.805 × n_max` pe ferestrele
    de producție.

    Denominatorul per RÂND: `n_eval` (v13 — extrageri efectiv evaluate, exact
    denominatorul ratelor) cu fallback pe `n_test` FIECARE rând în parte. Alegerea
    pe frame ar arunca tăcut rândurile vechi dintr-un folds.csv mixt (faze de bench
    combinate peste un bump de versiune).

    None = incalculabil (fără coloane / fără rânduri cu n > 0).
    """
    if rate_col not in frame.columns:
        return None
    has_eval, has_test = "n_eval" in frame.columns, "n_test" in frame.columns
    if not (has_eval or has_test):
        return None
    rate = pd.to_numeric(frame[rate_col], errors="coerce")
    n_eval = pd.to_numeric(frame["n_eval"], errors="coerce") if has_eval else None
    n_test = pd.to_numeric(frame["n_test"], errors="coerce") if has_test else None
    # coalesce PE RÂND: n_eval dacă e valid și > 0, altfel n_test
    if n_eval is None:
        n = n_test
    elif n_test is None:
        n = n_eval
    else:
        n = n_eval.where(n_eval.notna() & (n_eval > 0), n_test)
    pairs = pd.DataFrame({"r": rate, "n": n}).dropna()
    pairs = pairs[pairs["n"] > 0]
    if pairs.empty:
        return None
    sizes = pairs["n"].astype(float).to_numpy()
    n_pooled = float(sizes.sum())
    if n_pooled <= 0:
        return None
    phat = float((pairs["r"].astype(float).to_numpy() * sizes).sum()) / n_pooled
    # Σ_i Σ_k min(n_i, n_k) — exact pentru ferestre-sufix (|W_i ∩ W_k| = min).
    sum_m2 = float(np.minimum.outer(sizes, sizes).sum())
    n_eff = (n_pooled ** 2) / sum_m2 if sum_m2 > 0 else n_pooled
    return phat, float(n_eff)


def pooled_wilson_distinct(frame: pd.DataFrame, rate_col: str) -> float | None:
    """Limita inferioară Wilson pe rata pooled, cu n EFECTIV (Kish).

    Sursă UNICĂ de adevăr pentru „cât de sigură e rata T+ a acestei metode":
    folosită de decizie (`_rate_target_confidence`) ȘI de clasamentul UI
    (`app_nicegui._wilson_pooled_rate`) — nu re-implementa agregarea în alt loc.
    Detaliile agregării: vezi `pooled_rate_and_neff`.
    """
    got = pooled_rate_and_neff(frame, rate_col)
    if got is None:
        return None
    phat, n_eff = got
    return _wilson_lower_bound(phat * n_eff, n_eff)


def _build_ensemble_weights(entries: list[tuple[str, float]]) -> list[dict]:
    """Transformă [(method, confidence), ...] (deja ordonate, top-K) în
    [{"method": m, "weight": w}, ...] cu ponderi proporționale cu confidence
    (limita Wilson), normalizate la sumă 1. Floor mic (1e-6) ca metodele cu
    confidence=0 (evenimente insuficiente) să nu fie complet excluse — cad la
    ponderi egale între ele, nu la zero."""
    if not entries:
        return []
    floored = [(m, max(float(c), 1e-6)) for m, c in entries]
    total = sum(c for _, c in floored)
    if total <= 0:
        w = 1.0 / len(floored)
        return [{"method": m, "weight": round(w, 4)} for m, _ in floored]
    return [{"method": m, "weight": round(c / total, 4)} for m, c in floored]


def _perf_signature_frame(
    sub_real: pd.DataFrame,
    methods: list[str],
    target: int,
) -> pd.DataFrame | None:
    """Semnătura de performanță a fiecărei metode, din folds.csv.

    decision.py NU are vectorii de scor bruți (lucrează pe folds.csv), deci
    redundanța se măsoară pe ce EXISTĂ: ratele T+ pe toate pool-urile × toate
    ferestrele sim_depth (pivot `method` × `(rate_col, percentile)`). Două
    metode care produc practic același pool au și aceeași serie de rate.

    Coloanele sunt CENTRATE pe metode (se scade media pe coloană) — altfel
    "forma" comună tuturor metodelor (k20 are inevitabil rată mai mare decât
    k6) ar împinge orice pereche spre corelație 1 și am elimina membri
    legitimi. După centrare rămâne doar cum DEVIAZĂ o metodă de la consens.

    Semnătura e POOL-AGNOSTICĂ, DELIBERAT: `rate_cols` ia TOATE coloanele
    `rate_{target}plus_k*` ale jocului, nu doar pool-ul pentru care se decide,
    deci frame-ul e IDENTIC pentru toți k ai aceluiași joc (verificat pe
    folds.csv: shape (106, 60) = 15 pool-uri × 4 ferestre percentile, iar
    `sig(k6).equals(sig(k20))` → True). Motivul e statistic, nu neatenție:
    restrânsă la coloana pool-ului CURENT, semnătura ar avea 4 puncte (cele 4
    ferestre) — sub `ENSEMBLE_MIN_SIGNATURE_POINTS`, adică fix zona în care
    Pearson degenerează și am elimina membri pe zgomot. Consecința ACCEPTATĂ:
    două metode care coincid la k7..k20 dar diferă la k6 sunt tratate ca
    redundante și la k6 (redundanța se măsoară PE TOT JOCUL, nu per pool).
    Recalcularea de 15 ori per joc e intenționat nememoizată: costă ~3 ms/apel
    (măsurat), adică sub 1.5% din cei ~3.3 s ai unei `build_auto_pilot_matrix`.

    Returnează None dacă nu se poate construi o semnătură utilizabilă
    (fără coloane de rată, sub 2 puncte pe metodă) → dedup dezactivat.
    """
    rate_cols = [
        c for c in sub_real.columns
        if c.startswith(f"rate_{target}plus_k") and sub_real[c].notna().any()
    ]
    if not rate_cols:  # folds.csv vechi (doar 4+) — vezi _resolve_rate_col
        rate_cols = [
            c for c in sub_real.columns
            if c.startswith("rate_4plus_k") and sub_real[c].notna().any()
        ]
    if not rate_cols:
        return None
    frame = sub_real[sub_real["method"].isin(methods)]
    if frame.empty:
        return None
    try:
        piv = frame.pivot_table(
            index="method", columns="percentile", values=rate_cols, aggfunc="mean",
        )
    except Exception as exc:  # date malformate — dedup e best-effort, nu blocant
        logger.warning("[decision] semnătura de performanță indisponibilă: %s", exc)
        return None
    piv = piv.dropna(axis=1, how="any")  # doar coloane complete → comparabile 1:1
    if piv.shape[0] < 2 or piv.shape[1] < 2:
        return None
    return piv - piv.mean(axis=0)


def _signature_corr(v: np.ndarray, w: np.ndarray) -> float | None:
    """Corelație Pearson între două semnături; None dacă nu e definită.

    Scurtătura `np.allclose` acoperă DOAR identitatea (serii EGALE → 1.0), NU
    varianța nulă în general. Două serii CONSTANTE dar diferite rămân
    necalculabile: `_signature_corr([1,1,1], [2,2,2])` → None, nu 1.0 (verificat);
    la fel `_signature_corr([1,1,1], [1,2,3])` → None. Motivul e că `corrcoef`
    dă NaN când o serie are varianță zero (ex. o metodă exact pe consens după
    centrare), iar None înseamnă aici "necalculabil", nu "necorelat":
    `_select_ensemble_members` NU elimină pe baza lui — la îndoială păstrăm
    membrul, ca să nu tăiem din lipsă de dovezi.
    Corelația e SEMNATĂ; apelantul decide ce face cu semnul. Atenție: pe SCORURI
    (`method_selector._select_decorrelated`) anti-corelația NU e complementaritate —
    după min-max normalizare blend-ul devine monoton în membrul mai greu, deci
    membrul anti-corelat nu aduce informație. Aici măsurăm însă RATE pe folduri,
    unde semnul are altă semnificație: două metode care câștigă pe folduri diferite
    chiar se completează. Cele două straturi au deci praguri și semantici diferite
    în mod DELIBERAT — vezi ENSEMBLE_MAX_CORR (rate, semnat) vs MAX_MEMBER_CORR
    (scoruri, modul).
    """
    if v.shape != w.shape or v.size < 2:
        return None
    if np.allclose(v, w, rtol=1e-9, atol=1e-12):
        return 1.0
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.corrcoef(v, w)[0, 1]
    return None if not np.isfinite(c) else float(c)


def _select_ensemble_members(
    ordered: list[tuple[str, float]],
    sig: pd.DataFrame | None,
    max_members: int = ENSEMBLE_MAX_METHODS,
    max_corr: float = ENSEMBLE_MAX_CORR,
) -> tuple[list[tuple[str, float]], list[dict]]:
    """Alege membrii ensemble-ului cu DECORELARE, nu prin simplă feliere top-K.

    `ordered` = [(metodă, confidence Wilson), ...] deja sortate descrescător.
    Se parcurge lista în ordine și se păstrează o metodă doar dacă semnătura ei
    de performanță nu e cvasi-identică (corelație ≥ `max_corr`) cu a unui membru
    deja păstrat — adică se ține mereu cea cu Wilson mai bun, iar duplicatul se
    sare. Fără asta, top-K poate conține variante ale aceleiași metode
    (ex. blend-uri pe aceleași două componente), iar eticheta
    "variance-reduction" ar fi falsă.

    Pragul e SEMNAT (`c >= max_corr`), nu în modul: pe RATE, doi membri
    anti-corelați câștigă pe folduri diferite, deci se completează — îi
    păstrăm. Stratul următor (`method_selector._select_decorrelated`) lucrează
    pe SCORURI și elimină în MODUL (|r| ≥ MAX_MEMBER_CORR), inclusiv
    anti-corelații. Diferența e deliberată (vezi `_signature_corr`), dar are o
    consecință de reținut: ensemble-ul + ponderile Wilson scrise în
    best_methods.json sunt NOMINALE — la generare pot fi reduse tacit de
    method_selector, deci nu sunt neapărat cele folosite efectiv.

    Returnează (membri_păstrați, eliminați_ca_redundanți). Al doilea element
    reia CHEILE din method_selector.describe_ensemble ({method, vs, r, reason}),
    ca UI-ul să poată explica uniform de ce lipsește o metodă din blend — DAR
    valoarea `reason` e "perf_signature", care nu apare în enum-ul documentat
    acolo ({"empty", "flat", "correlated", "anticorrelated"}): acolo axa e
    SCORURILE (engine), aici sunt RATELE pe folduri (decizie). Un consumator de
    UI trebuie deci să trateze `reason` ca enum DESCHIS, nu închis.
    """
    kept: list[tuple[str, float]] = []
    dropped: list[dict] = []
    # Gardă de eșantion: pe semnături prea scurte (puține ferestre/pool-uri) sau
    # cu prea puține metode, corelația degenerează spre ±1 și am elimina membri
    # pe zgomot — vezi ENSEMBLE_MIN_SIGNATURE_POINTS. Dedup dezactivat, NU
    # aproximat. Inert pe date reale (semnătura are 60 de coloane × 106 metode).
    if sig is not None and min(sig.shape) < ENSEMBLE_MIN_SIGNATURE_POINTS:
        logger.info(
            "[decision] dedup ensemble dezactivat: semnătură prea mică %s "
            "(prag %d pe ambele axe) — corelația ar fi nefiabilă",
            tuple(sig.shape), ENSEMBLE_MIN_SIGNATURE_POINTS,
        )
        sig = None
    for m, conf in ordered:
        if len(kept) >= max_members:
            break
        redundant_with: str | None = None
        redundant_r: float | None = None
        if sig is not None and m in sig.index:
            v = sig.loc[m].to_numpy(dtype=float)
            for kept_m, _kept_conf in kept:
                if kept_m not in sig.index:
                    continue
                c = _signature_corr(v, sig.loc[kept_m].to_numpy(dtype=float))
                if c is not None and c >= max_corr:
                    redundant_with, redundant_r = kept_m, c
                    break
        if redundant_with is None:
            kept.append((m, conf))
        else:
            dropped.append({
                "method": m,
                "vs": redundant_with,
                "r": round(float(redundant_r), 4),
                "reason": "perf_signature",
            })
    return kept, dropped


def decide_optimal_config_for_pool(
    folds_df: pd.DataFrame,
    game_key: str,
    pool_size: int,
    draw_n: int,
) -> dict:
    """Run the decision algorithm for one (game, pool_size) pair.

    Returns a dict with the chosen scorer, sim_depth, use_blacklist + rationale.
    """
    base_col = f"k{pool_size}"
    base_col_bl = f"k{pool_size}_bl"
    rate_target_col = f"rate_{BENCH_HIT_TARGET}plus_k{pool_size}"

    if base_col not in folds_df.columns:
        return {"error": f"column {base_col} missing in folds.csv"}

    sub = folds_df[(folds_df["game"] == game_key) & (folds_df["failed"] != True)]  # noqa: E712
    if sub.empty:
        return {"error": f"no folds for game {game_key}"}

    real_random = sub[(sub["method"] == "random") & (sub["is_random"] == False)]  # noqa: E712
    # `random` rămâne REFERINȚĂ (real_random, de mai sus), dar e scos din
    # candidați — ca și restul baseline-urilor nedeterministe (vezi
    # EXCLUDED_FROM_PRODUCTION): nu au voie să ajungă scorer de producție.
    # Plus: sare metodele eliminate din METHODS / tombstone (folds vechi).
    try:
        from loto_enterprise.benchmark.methods import METHODS as _METHODS_NOW
        from loto_enterprise.benchmark.disabled import load_disabled as _load_dis
        _alive = set(_METHODS_NOW) - _load_dis()
    except Exception:  # noqa: BLE001
        _alive = None
    methods = [
        m for m in sub["method"].unique()
        if m not in EXCLUDED_FROM_PRODUCTION
        and (_alive is None or m in _alive)
    ]
    try:
        from loto_enterprise.benchmark.curated import load_per_game as _load_pg
        _pg = [m for m in (_load_pg().get(game_key) or []) if m in methods]
        if _pg:
            methods = _pg
            logger.info(
                "[decision] %s k%d: candidați restrânși la per_game (%d metode)",
                game_key, pool_size, len(methods),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[decision] per_game neaplicat: %s", exc)

    # Coloane candidate pt rata T+, în ordine de preferință; sare peste coloane
    # all-NaN (cache vechi fără 3+) și cade pe 4+ → niciodată NaN în decizie.
    _rate_target_candidate_cols = (
        rate_target_col, f"rate_{BENCH_HIT_TARGET}plus",
        f"rate_4plus_k{pool_size}", "rate_4plus",
    )
    # Coloanele care CORESPUND țintei curente; orice rezoluție în afara lor =
    # fallback de metrică (ex. ținta 3+ dar folds.csv vechi are doar 4+) —
    # trebuie SEMNALIZAT (rate_col_mismatch + rationale), nu tăcut.
    # ATENȚIE la ce e „mismatch": `rate_{T}plus` FĂRĂ sufix `_kN` NU e rata
    # pool-ului curent — `runner._evaluate_fold` o scrie ca rata la pool-ul de
    # BAZĂ (K = draw_n). E deci un fallback de POOL, nu doar de țintă, și trebuie
    # semnalizat la fel ca trecerea pe 4+.
    _target_cols = {rate_target_col}
    _mismatch_cols_used: set[str] = set()

    def _resolve_rate_col(frame: pd.DataFrame) -> str | None:
        for c in _rate_target_candidate_cols:
            if c in frame.columns and frame[c].notna().any():
                if c not in _target_cols and c not in _mismatch_cols_used:
                    _mismatch_cols_used.add(c)  # log O DATĂ per (game, pool, coloană)
                    logger.warning(
                        "[decision] %s k%d: %r lipsește/e all-NaN în folds.csv — "
                        "decizia cade pe %r. Ținta e %d+ @ k%d. Re-rulează bench-ul.",
                        game_key, pool_size, rate_target_col, c, BENCH_HIT_TARGET, pool_size,
                    )
                return c
        return None

    # Coloana se rezolvă O SINGURĂ DATĂ, pe TOT frame-ul (game, pool) — nu per
    # metodă. Rezolvată per metodă, un folds.csv asamblat din două faze de bench
    # (unele metode fără coloanele per-pool) făcea ca rânduri din ACELAȘI
    # `qualifying.sort` să fie punctate pe coloane DIFERITE și incomparabile:
    # metodele „vechi" cădeau pe rata pool-ului de bază, structural mai mică
    # (măsurat pe date reale: 0.023 vs 0.154 la k12), deci erau îngropate tăcut.
    # Pe un folds.csv omogen (cazul normal) rezoluția e aceeași pentru toată
    # lumea, deci decizia rămâne neschimbată.
    _thin_gate_warned: list[tuple[str, int]] = []  # rezumat, o SINGURĂ linie per (joc, pool)
    _all_real = sub[sub["is_random"] == False]  # noqa: E712
    _frame_rate_col = _resolve_rate_col(_all_real) if not _all_real.empty else None

    def _rate_col_for(frame: pd.DataFrame) -> str | None:
        """Coloana comună dacă metoda are date în ea; altfel None (metoda e sărită)."""
        if _frame_rate_col is None:
            return None
        if _frame_rate_col in frame.columns and frame[_frame_rate_col].notna().any():
            return _frame_rate_col
        return None

    def _rate_target_mean(frame: pd.DataFrame) -> float:
        # Rata POOLED — ACELAȘI estimator punctual pe care se calculează Wilson
        # (`pooled_rate_and_neff`). Înainte era media NEPONDERATĂ pe ferestre, deci
        # rationale-ul putea tipări o limită INFERIOARĂ mai mare decât „rata" de
        # lângă ea (măsurat: 86 de celule joc×pool×metodă). Cei doi estimatori
        # trebuie să fie ai aceleiași mărimi. Fallback pe media brută doar dacă
        # lipsesc coloanele de n (folds foarte vechi).
        c = _rate_col_for(frame)
        if c is None:
            return 0.0
        got = pooled_rate_and_neff(frame, c)
        if got is not None:
            return float(got[0])
        v = float(frame[c].mean())
        return v if v == v else 0.0  # nu e NaN (NaN != NaN)

    def _rate_target_confidence(frame: pd.DataFrame) -> float:
        # Limită inferioară Wilson pe proporția agregată — mai robustă la zgomot
        # decât media brută pe evenimente rare (4+ hituri). Agregarea (rata pooled
        # + n EFECTIV Kish, nu suma ferestrelor cuibărite) e canonică în
        # `pooled_rate_and_neff` — vezi docstring-ul ei; înainte n-ul era suma
        # (măsurat pe folds de producție: ≈2.03× n real) → limite supraîncrezătoare.
        c = _rate_col_for(frame)
        if c is None:
            return 0.0
        v = pooled_wilson_distinct(frame, c)
        return float(v) if v is not None else 0.0

    qualifying: list[tuple[str, float, int, int, float, float]] = []
    for m in methods:
        real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
        if real_m.empty:
            continue
        n_beat, n_total = _windows_method_beats_random(real_m, real_random, base_col)
        if 0 < n_total < MIN_CONSISTENCY_WINDOWS:
            _thin_gate_warned.append((m, n_total))
        if n_total < MIN_CONSISTENCY_WINDOWS:
            continue
        consistency = n_beat / n_total
        if consistency < CONSISTENCY_THRESHOLD:
            continue
        w_lift = _weighted_mean_lift(real_m, real_random, base_col)
        # REGULA T+: rata medie de extrageri cu >=BENCH_HIT_TARGET numere ghicite
        # (r4 = rată brută, doar pt afișare) + r4_conf (limită Wilson, pt sortare).
        r4 = _rate_target_mean(real_m)
        r4_conf = _rate_target_confidence(real_m)
        qualifying.append((m, w_lift, n_beat, n_total, r4, r4_conf))

    # Decizia e "low confidence" când nicio metodă nu a trecut pragul de
    # consistență față de random — alegem tot o metodă REALĂ (nu baseline-ul
    # nedeterminist), dar marcăm explicit că diferențele sunt zgomot.
    if _thin_gate_warned:
        _wmin = min(n for _, n in _thin_gate_warned)
        _wmax = max(n for _, n in _thin_gate_warned)
        logger.warning(
            "[decision] %s k%d: %d metode au doar %s fereastră(e) comună(e) cu `random` "
            "(prag %d) — poarta de consistență nu e concludentă, le sar. folds.csv pare "
            "PARȚIAL (bench întrerupt?) — re-rulează bench-ul. Ex.: %s",
            game_key, pool_size, len(_thin_gate_warned),
            f"{_wmin}" if _wmin == _wmax else f"{_wmin}-{_wmax}",
            MIN_CONSISTENCY_WINDOWS,
            ", ".join(m for m, _ in _thin_gate_warned[:5]),
        )

    low_confidence = False
    dropped_redundant: list[dict] = []

    if not qualifying:
        # Fallback: nicio metodă nu a bătut random ≥60% din ferestre.
        # Prioritizăm tot regula T+ (robust, via Wilson), apoi avg_hits (la egalitate).
        # `random` NU e candidat aici (EXCLUDED_FROM_PRODUCTION): faptul că
        # nimeni nu-l bate consistent NU îl face scorer legitim — ar însemna să
        # generăm pool-uri nedeterministe, nereproductibile.
        low_confidence = True
        ranked = []
        for m in methods:
            real_m = sub[(sub["method"] == m) & (sub["is_random"] == False)]  # noqa: E712
            if real_m.empty:
                continue
            r4 = _rate_target_mean(real_m)
            r4_conf = _rate_target_confidence(real_m)
            ranked.append((m, r4_conf, r4, float(real_m[base_col].mean())))
        ranked.sort(key=lambda kv: (kv[1], kv[3]), reverse=True)
        if not ranked:
            # Nicio metodă reală utilizabilă în folds.csv → cădem pe baseline-ul
            # DETERMINIST, niciodată pe `random`. Nu mai putem calcula sim_depth
            # (nu există rânduri pt scorer) → valori implicite + low_confidence.
            logger.warning(
                "[decision] %s k%d: nicio metodă reală în folds.csv — fallback determinist %r",
                game_key, pool_size, SAFE_FALLBACK_SCORER,
            )
            return {
                "scorer": SAFE_FALLBACK_SCORER,
                "ensemble": _build_ensemble_weights([(SAFE_FALLBACK_SCORER, 1.0)]),
                "sim_depth_pct": DEFAULT_SIM_DEPTH_PCT,
                # True, NU False: înainte celula degenerată întorcea {"error": ...}
                # (fără cheia "scorer"), deci `recommend_optimal_config` cădea pe
                # ramura fallback → `should_use_blacklist`, a cărei politică
                # documentată e "default True (safe — producția rulează
                # blacklist-ul oricum)". Cheia asta e citită PRIMA de
                # `should_use_blacklist` (auto_pilot_per_pool[kN].use_blacklist),
                # deci un False aici ar fi schimbat TACIT politica exact pe cazul
                # cel mai degradat (nicio metodă reală în folds.csv), unde n-avem
                # nicio măsurătoare care să justifice renunțarea la blacklist.
                "use_blacklist": True,
                # 0.0, NU None: `method_selector.recommend_optimal_config` și UI-ul
                # citesc `avg_hits` și îl formatează cu :.3f — None ar arunca
                # TypeError exact pe calea de fallback, adică fix când totul merge
                # deja prost. `low_confidence` marchează că cifra nu e măsurată.
                "avg_hits": 0.0,
                "avg_hits_with_blacklist": 0.0,
                "rationale": (
                    f"FALLBACK: nicio metodă reală în folds.csv pentru k{pool_size}; "
                    f"selecție conservatoare pe baseline-ul DETERMINIST "
                    f"{SAFE_FALLBACK_SCORER} (baseline-urile nedeterministe "
                    f"{sorted(EXCLUDED_FROM_PRODUCTION)} nu pot fi scorer de producție)"
                ),
                "rate_col_used": None,
                "rate_col_mismatch": bool(_mismatch_cols_used),
                "qualifying_methods": 0,
                "consistency_threshold": CONSISTENCY_THRESHOLD,
                "low_confidence": True,
                "ensemble_dropped_redundant": [],
            }
        scorer = ranked[0][0]
        rationale = (
            f"FALLBACK: no method consistently beat random "
            f"(≥{int(CONSISTENCY_THRESHOLD*100)}% of windows); "
            f"picked highest {BENCH_HIT_TARGET}+ rate (raw={ranked[0][2]:.3f}, "
            f"Wilson_lb={ranked[0][1]:.3f}) + avg_hits "
            f"[nicio metodă nu bate random; selecție conservatoare, "
            f"diferențele sunt zgomot]"
        )
        # Fallback: NICIO metodă a bătut random — conservator, ensemble cu un
        # singur membru (nu combinăm metode neconfirmate statistic).
        ensemble = _build_ensemble_weights([(scorer, 1.0)])
    else:
        # REGULA T+: sortăm întâi după limita Wilson a ratei BENCH_HIT_TARGET+
        # (robustă la evenimente rare/zgomot — nu doar rata brută), apoi după
        # lift mediu, apoi consistență. Metoda care prinde T+ cel mai des ȘI cu
        # dovezi suficiente câștigă, nu cea cu 1 eveniment norocos.
        qualifying.sort(key=lambda r: (r[5], r[1], r[2] / max(r[3], 1)), reverse=True)
        scorer = qualifying[0][0]
        rationale = (
            f"{scorer}: rată {BENCH_HIT_TARGET}+ @ k{pool_size} = {qualifying[0][4]:.3f} "
            f"(Wilson_lb={qualifying[0][5]:.3f}), beat random in "
            f"{qualifying[0][2]}/{qualifying[0][3]} windows (lift {qualifying[0][1]:+.4f}); "
            f"din {len(qualifying)} metode calificate [prioritate: {BENCH_HIT_TARGET}+ numere ghicite, robust]"
        )
        # Ensemble: top ENSEMBLE_MAX_METHODS calificate, ponderate cu limita
        # Wilson a ratei T+ (r[5]) — variance-reduction, NU o pretenție de
        # "predicție mai bună" (loteria e aleatoare; diferențele dintre
        # metodele calificate sunt majoritar zgomot statistic). Cu o singură
        # metodă calificată, degenerează la ensemble cu 1 membru (weight=1.0).
        # NU e simplă feliere top-K: metodele cvasi-identice (aceeași semnătură
        # de performanță pe folduri) sunt sărite — vezi _select_ensemble_members.
        ordered = [(m, r4_conf) for m, _, _, _, _, r4_conf in qualifying]
        sig = _perf_signature_frame(
            sub[sub["is_random"] == False],  # noqa: E712
            [m for m, _ in ordered],
            BENCH_HIT_TARGET,
        )
        members, dropped_redundant = _select_ensemble_members(
            ordered, sig, ENSEMBLE_MAX_METHODS, ENSEMBLE_MAX_CORR,
        )
        if dropped_redundant:
            logger.info(
                "[decision] %s k%d: %d membri ensemble eliminați ca redundanți (r≥%.2f): %s",
                game_key, pool_size, len(dropped_redundant), ENSEMBLE_MAX_CORR,
                ", ".join(f"{d['method']}~{d['vs']}" for d in dropped_redundant),
            )
        ensemble = _build_ensemble_weights(members)

    # Pick the best sim_depth for the chosen scorer based on target hit rate (percentage of drawings)
    real_chosen = sub[(sub["method"] == scorer) & (sub["is_random"] == False)]  # noqa: E712
    rate_col = _rate_col_for(real_chosen) or _resolve_rate_col(real_chosen)
    if rate_col is None:
        rate_col = base_col  # fallback to avg_hits — tot un fallback de metrică
        _mismatch_cols_used.add(base_col)


    # Poarta de stabilitate se aplică pe EXTRAGERILE EVALUATE (n_eval, cu fallback
    # pe rând la n_test): `target_rate` și `avg_hits` sunt ambele denominate în
    # n_eval de la v13, deci a filtra pe n_test ar valida o fereastră pe extrageri
    # care poate n-au fost niciodată evaluate (scorer care întoarce {} pe blocuri).
    _rc = real_chosen.copy()
    _nt = pd.to_numeric(_rc["n_test"], errors="coerce") if "n_test" in _rc.columns else None
    if "n_eval" in _rc.columns:
        _ne = pd.to_numeric(_rc["n_eval"], errors="coerce")
        _rc["_n_stab"] = _ne.where(_ne.notna() & (_ne > 0), _nt) if _nt is not None else _ne
    else:
        _rc["_n_stab"] = _nt
    by_pct = _rc.groupby("percentile").agg(
        target_rate=(rate_col, "mean"),
        avg_hits=(base_col, "mean"),
        n_test=("_n_stab", "mean"),
        runtime=("runtime_sec", "mean"),
    )
    # Filter to windows with enough EVALUATED draws
    stable = by_pct[by_pct["n_test"] >= MIN_TEST_DRAWS_FOR_STABILITY]
    if stable.empty:
        stable = by_pct
    # Sort by target_rate desc (optimizing percentage of hits), tiebreak on avg_hits desc
    stable = stable.sort_values(
        by=["target_rate", "avg_hits"], ascending=[False, False]
    )
    best_pct = int(stable.index[0])
    best_avg = float(stable.iloc[0]["avg_hits"])

    # Decide use_blacklist at the chosen (method, sim_depth)
    use_bl = False
    bl_avg = None
    if base_col_bl in real_chosen.columns:
        at_pct = real_chosen[real_chosen["percentile"] == best_pct]
        if not at_pct.empty:
            no_bl = float(at_pct[base_col].mean())
            bl_avg = float(at_pct[base_col_bl].mean())
            if bl_avg > no_bl:
                use_bl = True

    # Semnalizare fallback de metrică: dacă ORICE rezoluție a folosit o coloană
    # din afara țintei (3+ absent → 4+), o marcăm în rezultat + rationale — ca
    # UI-ul și logurile să nu pretindă că decizia s-a luat pe metrica "T+".
    rate_col_mismatch = bool(_mismatch_cols_used)
    if rate_col_mismatch:
        rationale += (
            f" [ATENȚIE: metrica {BENCH_HIT_TARGET}+ indisponibilă în folds.csv — "
            f"decizie luată pe {', '.join(sorted(_mismatch_cols_used))}; re-rulează bench-ul]"
        )

    return {
        "scorer": scorer,
        "ensemble": ensemble,
        "sim_depth_pct": best_pct,
        "use_blacklist": use_bl,
        "avg_hits": best_avg,
        "avg_hits_with_blacklist": bl_avg,
        "rationale": rationale,
        "rate_col_used": rate_col,
        "rate_col_mismatch": rate_col_mismatch,
        "qualifying_methods": len(qualifying),
        "consistency_threshold": CONSISTENCY_THRESHOLD,
        # ATENȚIE: următoarele două chei sunt TELEMETRIE ADITIVĂ în
        # best_methods.json (debug / inspecție manuală), NU un contract de UI.
        # Verificat prin grep pe tot repo-ul: niciun consumator în afara acestui
        # fișier — `method_selector.recommend_optimal_config` întoarce o listă
        # ALBĂ de chei și nu le propagă, deci UI-ul nu le vede. Sunt păstrate
        # fiindcă nu strică nimic (JSON aditiv) și fiindcă un consumator viitor
        # le poate citi; până atunci NU te baza pe ele pentru afișare.
        #
        # True = nicio metodă nu bate random consistent → alegerea e conservatoare,
        # diferențele dintre metode sunt zgomot. Pe folds.csv curent semnalul NU
        # e validat pe nicio celulă reală (0 din 46 de celule ies low_confidence).
        "low_confidence": low_confidence,
        # Membri săriți de la ensemble fiindcă erau cvasi-identici cu unul deja
        # păstrat (decorelare pe RATE; decorelarea pe SCORURI e în method_selector).
        "ensemble_dropped_redundant": dropped_redundant,
    }


def build_auto_pilot_matrix(
    folds_csv_path: str,
    games_meta: dict[str, dict],
) -> dict[str, dict[str, dict]]:
    """Build the full {game_key: {pool_key: config}} matrix from folds.csv.

    Args:
        folds_csv_path: path to bench_results/folds.csv
        games_meta: {game_key: {"draw_n": int, "pool_range": [int]}}

    Returns:
        Nested dict suitable for embedding in best_methods.json.
    """
    df = pd.read_csv(folds_csv_path)
    if "failed" not in df.columns:
        df["failed"] = False
    matrix: dict[str, dict[str, dict]] = {}
    for gk, meta in games_meta.items():
        matrix[gk] = {}
        for k in meta["pool_range"]:
            cfg = decide_optimal_config_for_pool(
                df, gk, pool_size=k, draw_n=meta["draw_n"]
            )
            matrix[gk][f"k{k}"] = cfg
    return matrix


def update_best_methods_with_auto_pilot(
    best_methods_path: str = "best_methods.json",
    folds_csv_path: str = "bench_results/folds.csv",
) -> dict:
    """Read folds.csv, run decision algo, write `auto_pilot_per_pool` into best_methods.json."""
    bm_path = Path(best_methods_path)
    if not bm_path.exists():
        raise FileNotFoundError(f"{best_methods_path} not found")
    if not Path(folds_csv_path).exists():
        raise FileNotFoundError(f"{folds_csv_path} not found")

    cfg = json.loads(bm_path.read_text(encoding="utf-8"))
    games = cfg.get("games", {})

    games_meta = {}
    for gk, gd in games.items():
        draw_n = int(gd.get("draw_n", 6))
        if gk == "joker_urna2":
            pool_range = [draw_n]
        else:
            # Aligned with runner.py pool_extra=14 → K=draw_n..draw_n+14
            # (2026-05-26: extins de la +6 → +14 ca să acopere pool size sweep
            # K=15/18/20 cerut de user pentru testare 4+ hits stable).
            pool_range = list(range(draw_n, draw_n + 15))  # draw_n .. draw_n+14
        games_meta[gk] = {"draw_n": draw_n, "pool_range": pool_range}

    matrix = build_auto_pilot_matrix(folds_csv_path, games_meta)

    for gk in games:
        games[gk]["auto_pilot_per_pool"] = matrix.get(gk, {})

    from ui_shared import atomic_write_json
    atomic_write_json(bm_path, cfg)  # atomic: tmp+fsync+os.replace
    return matrix
