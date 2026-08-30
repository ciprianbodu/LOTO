# Analiză de tipare — „se poate reduce baza de numere?"

Scripturi de MĂSURĂTOARE, nu de producție. Nu sunt importate de nicăieri;
se rulează manual, din rădăcina repo-ului, când vrei să reverifici concluzia
pe date noi (`_ISTORIC/` crește la fiecare `update_csv.py`).

```bash
python scripts/analysis/pattern_constraints.py      # pasul 1
python scripts/analysis/pattern_predictability.py   # pasul 2
python scripts/analysis/pattern_base_reduction.py   # pasul 3
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

## De ce nu există o metodă care să facă asta

Nu s-a scris niciun scorer pe tiparele astea fiindcă pasul 2 dă zero semnal —
un scorer construit pe ele ar fi zgomot cu nume. Mai rău: o constrângere chiar
aplicată taie universul, deci în cazurile în care extragerea o încalcă (70.75%
din timp pentru „toate ≤ 40" pe 6/49) pool-ul are **zero** șanse la premiul
mare, nu doar mai mici. Vezi și „Curare de metode" din `CLAUDE.md`: pârghiile
reale rămân dimensiunea pool-ului (K) și acoperirea pool→bilete (wheeling).
