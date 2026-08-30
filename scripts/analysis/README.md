# Analiză de tipare — „se poate reduce baza de numere?"

Scripturi de MĂSURĂTOARE, nu de producție. Nu sunt importate de nicăieri;
se rulează manual, din rădăcina repo-ului, când vrei să reverifici concluzia
pe date noi (`_ISTORIC/` crește la fiecare `update_csv.py`).

```bash
python scripts/analysis/pattern_constraints.py              # pasul 1
python scripts/analysis/pattern_predictability.py           # pasul 2
python scripts/analysis/pattern_base_reduction.py           # pasul 3
python scripts/analysis/joker_complex_base_reduction.py     # pasul 4 (doar Joker)
python scripts/analysis/joker_complex_base_reduction.py --smoke
```

## Întrebarea

„Au fost printre ultimele extrageri numai numere sub 40? Numai pare? Se poate
folosi asta ca să tai baza de numere?"

Se descompune în trei întrebări DIFERITE, în ordinea asta. A treia e cea care
decide, iar primele două sunt cele pe care e ușor să le confunzi între ele.

## Pasul 1 — `pattern_constraints.py`: cât de des se întâmplă

27 de constrângeri („toate ≤ 25/30/35/40/45", „toate > 5/10/15", „toate pare",
„toate impare") × 3 jocuri, pe tot istoricul, comparate cu probabilitatea
hipergeometrică EXACTĂ a aceleiași constrângeri.

Rezultat (2026-08-30, n = 2578 / 1729 / 2181): cel mai mare |z| din toate cele
27 = **2.70**. La 27 de teste independente, maximul așteptat din pur hazard e
~2.5. Frecvențele observate se lipesc de teorie.

⚠️ Un |z| mare aici NU ar fi însă o metodă — vezi pasul 2.

## Pasul 2 — `pattern_predictability.py`: sunt PREZICIBILE

Asta e întrebarea care contează: știind extragerile de până acum, pot ghici
dacă URMĂTOAREA respectă constrângerea? Autocorelație lag 1/2/3 pe 6 trăsături
continue (sumă, maxim, minim, amplitudine, câte pare, câte sub mijloc) × 3
jocuri, fiecare cu test de permutare (2000 de replici).

Rezultat: cel mai „semnificativ" din 18 = joker/maxim, r = −0.036, p = 0.108.
**Zero** sub p < 0.05, unde hazardul pur ar fi dat ~1 fals pozitiv. Extragerile
n-au memorie pe niciuna dintre axele astea.

## Pasul 3 — `pattern_base_reduction.py`: și dacă ai reduce baza oricum

400 de pool-uri × tot istoricul 6/49, pool de K = 10 ales din universul întreg
vs. din baze reduse:

```
univers complet (1-49)   rata 3+ = 9.03%
bază redusă <= 40        rata 3+ = 9.19%
bază redusă <= 35        rata 3+ = 9.23%
bază redusă doar pare    rata 3+ = 9.19%
bază redusă > 10         rata 3+ = 8.80%
```

Toate în jurul lui 9.03%, adică exact hipergeometricul. **P(≥3 din pool de K)
depinde doar de K și de joc, nu de CARE numere sunt în pool** — numerele tale
sunt K din 49 indiferent din ce submulțime le-ai ales. Reducerea bazei nu e o
pârghie; e o etichetă pusă pe aceeași probabilitate.

## Pasul 4 — `joker_complex_base_reduction.py`: Joker, măști adaptive

Pașii 1–3 sunt constrângeri STATICE (bază fixă ≤40, doar pare, …). Pasul 4
caută reduceri care **depind de ultimele W extrageri**, doar pe Joker:

- urna 1 (5/45) și urna 2 (1/20)
- 195 măști atomice (cap/floor/anvelopă/paritate/decade/cifre/mod/vecini/
  hot-union/drop-last/overdue/recent, W=1..8) + AND între top-12
- descoperire pe primele 70% (lift de acoperire vs |M|/N, test de permutare,
  Bonferroni)
- confirmare walk-forward pe ultimele 30%: frequency **în mască** vs
  frequency, pool 11, `rank_by_score`

Rezultat (2026-08-30, n=2181, disc=1526, confirm=655):

| | Bonferroni | confirmare vs frequency |
|---|---|---|
| Urna 1 | 0 / 261 (α=0.00019) | top-ul e clonă (`|ρ|=1.00`) sau **pierde** la 3+ |
| Urna 2 | 0 / 80 (α=0.00063) | `u2_hot_W5` +7 hit@1 vs frequency, dar 4.27% **< 5% random**; hit@3 = 15.3% = 3/20; p_disc=0.38. E recency pe ultimele 5, nu un tipar. |

Niciun scorer. Capcana `u2_hot_W5`: frequency pe urna 2 e sub random, deci
„bate frequency" ≠ „are semnal".

## De ce nu există o metodă care să facă asta

Nu s-a scris niciun scorer pe tiparele astea fiindcă pasul 2 dă zero semnal,
iar pasul 4 (Joker, adaptive) tot zero după corecție de teste multiple —
un scorer construit pe ele ar fi zgomot cu nume. Mai rău: o constrângere chiar
aplicată taie universul, deci în cazurile în care extragerea o încalcă (70.75%
din timp pentru „toate ≤ 40" pe 6/49) pool-ul are **zero** șanse la premiul
mare, nu doar mai mici. Vezi și „Curare de metode" din `CLAUDE.md`: pârghiile
reale rămân dimensiunea pool-ului (K) și acoperirea pool→bilete (wheeling).
