# Analiză de tipare — „se poate reduce baza de numere?"

**Schemele reduse ale Loteriei Române, 17.10.2026:** [desfășurări oficiale verificate și comparate cu aplicația](lr_schemes_2026-10-17.md).
Cele 33 de scheme cu variante întregi (6/49, 5/40, Joker) sunt importate din
broșurile loto.ro numai dacă indexul de câștiguri recalculat coincide cu cel
tipărit. Cod 59 (6/49, 16 numere, 112 variante) are aceleași șanse ca cel mai
bun wheel al aplicației cu 112 variante: 4+ 7,43%, 5+ 0,207%. Reproducere:
`import_lr_schemes.py`, `analyze_lr_schemes.py`.

**Audit și mai multe hituri pe bilete, 16.10.2026:** [pârghii certificate și frontiera pool-ului 16](audit_hits_2026-10-16.md).
Configurația 6/49 pool 16, garanția 4, fără plafon e la frontieră (3+ și 4+ la
maximul pool-ului, nicio rearanjare certificată a coverului de 152 pentru 5+).
Livrate: covere complete rafinate la pool 9..14 ([detalii](refined_covers_2026-10-16.md)),
66 de designuri lotto „g dacă c” rafinate (mediana +4,65% la pragul garanției),
ramura lotto cu plafon pe wheel-ul clasic certificat, garanția 5-6 peste 64 de
bilete pe designurile garanției 4, garanția egală cu biletul pe biletele unei
garanții mai mici (5/40, 100 de bilete: 3+ 32,99% → 45,56%).
Reproducere: `gen_refined_covers.py`, `gen_refined_lotto.py` (gcc).

**Designuri cu buget pentru 65+ bilete, 12.10.2026:** [recoacere lungă în C, livrată ca fișiere](budget_designs_2026-10-12.md).
6/49, garanția 4, pool 13-16, toate cele 194 de bugete sub designul complet:
P(4+ pe un bilet) crește cu mediana +7,25% la pool 16 (maxim +15,56%), niciun
prag nu scade; certificate la fiecare apel față de căutarea din proces. Biletul
de 5 măsurat și nelivrat (cel mult +2,6%).
Reproducere: `python scripts/analysis/gen_budget_designs.py` (gcc, ~1 h pe 3 procese).

**„Bilet complet” cu garanția completă, 12.10.2026:** [rearanjarea tuturor variantelor](full_cover_anneal_2026-10-12.md).
Când baza garanției e completă înainte de a umple biletele, pragul-țintă nu se
mai mișcă; recoacerea pe toate variantele, cu acoperirea 100% ținută, ridică
pragul următor: câștig în 39 din 67 de configurații cu pârghie, maxim +6,92%,
nicio scădere la vreun prag, 0 la 5/40.
Reproducere: `python scripts/analysis/bench_full_cover_anneal.py`.

**Mai multe hituri pe bilete la același cost, 11.10.2026:** [recoacerea pe șansa exactă](anneal_hits_2026-10-11.md).
155 de configurații: bilete schimbate în 98, câștig la țintă în 83, maxim 2,76%,
nicio scădere la vreun prag. Rata pool-ului rămâne a hazardului.
Reproducere: `python scripts/analysis/bench_anneal_hits.py`.

**Test pe extrageri viitoare, 11.10.2026:** [`ens_win100+winall+ewma0.01`, Joker Urna 1, pool 8, 3+](forward_test_joker/PREREGISTRATION.md).
Candidatul rămas deschis din ecranul de pe 10.10, înghețat și testat secvențial
(SPRT) numai pe extragerile de după înregistrare. Nu atinge producția.
Rulare: `python scripts/analysis/forward_test_joker/forward_test_joker.py`.

**Teste noi, 10.10.2026:** [1.002 metode noi față de liderul clasamentului](method_screen_2026-10-10.md).
19 familii, 16 celule ale clasamentului, nul al căutării pe 200 de istorii,
McNemar față de lider și binomial față de hazard pe confirmare, Holm, replicare
externă. Nicio metodă nu intră în aplicație.
Reproducere: `python scripts/analysis/method_screen_2026-10-10.py`.

**Teste noi, 09.10.2026:** [aleatorism, semnale între jocuri, „urmărește liderul”, era curentă](hit_screen_2026-10-09.md).
131 de teste de aleatorism pe 14 serii și 492 de combinații metodă × pool, cu nul
al căutării, confirmare și replicare externă. Nicio metodă promovată; Joker
Urna 1 are o concentrare retrospectivă peste hazard, instabilă în timp.
Reproducere: `python scripts/analysis/hit_screen_2026-10-09.py`.

**Teste noi, 21.09.2026:** [contracte și ferestre distincte pentru toate cele 52 de metode](raport_test_metode_2026-09-21.md).
375 teste noi; diagnostic pe 180 extrageri/joc, pool 6/8/10/12/16, inclusiv Joker
Urna 2. Niciun avantaj confirmat după corecția comparațiilor multiple; fără
modificarea deciziei de producție. Reproducere: `python scripts/analysis/audit_method_windows.py`.

**Audit nou, 05.09.2026:** [tipare de calendar, penalizări și designuri](raport_patternuri_cover_penalizari_2026-09-05.md).
Experiment cronologic 50/20/30 la pool 11; nicio regulă nouă confirmată după
corecția comparațiilor multiple. 151 designuri verificate; 7 blocuri redundante
eliminate din 4 fișiere, cu aceeași garanție. WF validează acum geometria jucată.

```bash
python scripts/analysis/audit_patterns_and_designs.py
```

Comanda nu schimbă registry-ul sau deciziile. JSON-ul este scris în
`bench_results/pattern_design_audit.json`. Numai opțiunea explicită
`--prune-redundant` modifică designurile, după reverificarea garanției.

Scripturi de MĂSURĂTOARE, nu de producție; se rulează manual, din rădăcina
repo-ului, când vrei să reverifici concluzia pe date noi (`_ISTORIC/` crește
la fiecare `update_csv.py`). Cele trei de mai jos (`pattern_*.py`) nu sunt
importate de nicăieri. `audit_output.py` și `audit_patterns_and_designs.py`
(de mai sus) fac excepție: sunt importate direct de testele lor de regresie
(`test_output_consistency.py`, `test_wf_generation_settings.py`).

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
mare, nu doar mai mici. Vezi și „Curare de metode" din `AGENTS.md`: pârghiile
reale rămân dimensiunea pool-ului (K) și acoperirea pool→bilete (wheeling).
