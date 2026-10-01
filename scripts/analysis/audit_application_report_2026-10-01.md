# Audit aplicație și hituri — 1 octombrie 2026

## Rezultat

Auditul validează contractele de la CSV până la rezultat: istorice, scoring,
ranking, pool, bilete, acoperire, cache, coadă, worker, UI și walk-forward.
Îmbunătățirea geometrică disponibilă este `hitcover`, pentru un buget fix de
variante. Analiza retrospectivă nu demonstrează un avantaj predictiv al unei
metode noi de scoring.

## Corecții verificate

- Clasamentul UI folosește excluderile aceleiași decizii care produce rangurile:
  folduri incomplet evaluate, rate lipsă și egalități la limita top-K.
  Fracția egalităților se ponderază cu numărul de blocuri, ca în decizie.
- Auditul numerelor nejucate la Joker ia numai cele cinci numere din Urna 1;
  bila atașată din Urna 2 nu mai poate ascunde un număr nejucat. Cache worker v7.
- Scripturile de analiză numără toate cele șase numere extrase la 5/40,
  păstrând biletele de cinci, iar scorarea exclude toate extragerile zilei țintei.
  Ipotezele 5/40 sunt selectate pe ținta 4+.
- Auditorul de output rezolvă sursele externe prin registrul țării/jocului,
  separă draw_n de pick_n și nu confundă numerele jucate cu întregul pool
  la o garanție condiționată. Outputul consolei este UTF-8 pe Windows.
- API-ul de probabilități respinge numerele și geometriile fracționare,
  în loc să le trunchieze. Calculele numără exact reuniunea evenimentelor
  biletelor suprapuse, fără o aproximare de independență între bilete.
- Regresia WF activează executorul paralel real, cere predicții nenule,
  respectarea limitei de consecutive și absența fallback-ului secvențial.
  Testele cozii folosesc aceeași bază temporară inclusiv la verificarea anulării.

## Îmbunătățirea șanselor pe bilete la același buget

`hitcover` pornește de la wheel-ul greedy și caută alternative geometrice.
Acceptă o alternativă numai dacă numărul efectiv de bilete este identic și,
pentru fiecare mărime a intersecției cu pool-ul și fiecare prag de hituri,
numărul intersecțiilor favorabile nu scade. Cere o creștere strictă la 3+ sau 4+.

Această comparație folosește numărători întregi. Fiecare intersecție primește
ponderea nenegativă C(univers − pool, numere extrase − intersecție), deci
proprietatea garantează că șansele uniforme nu scad la niciun prag. Acoperirea
clasică a garanției nu scade nici ea. Scorurile stabilesc numai maparea canonică
a pozițiilor la numere; nu sunt interpretate drept probabilități.

Exemplu exact: pool 11, șapte variante, garanție cerută 4, scoruri fixe
`(număr * 17) % 23`. Procentele sunt necondiționate, pe toate variantele împreună.

| Joc | Acoperire clasică | Cel puțin o variantă cu 3+ | Cel puțin o variantă cu 4+ |
|---|---:|---:|---:|
| 6/49 | 29.39% → 31.82% | 7.841% → 8.962% | 0.583% → 0.637% |
| 5/40 | 10.61% → 10.61% | 8.619% → 10.113% | 0.509% → 0.542% |
| Joker, Urna 1 | 10.61% → 10.61% | 3.444% → 4.044% | 0.112% → 0.115% |

5/40: 3 numere nu aduc premiu. Joker: tabelul privește numai Urna 1.
Nu este o garanție de câștig sau o estimare de profit. Rezultatele istorice ale
experimentului cu bugete 7/10 au 48 de comparații pereche; niciuna nu trece Holm
la 5%. Avantajul descris în tabel provine din enumerarea exactă a geometriei,
nu din semnificația unui backtest.

Metoda este opțională. Pentru activare, în PowerShell, înainte de pornirea UI:

```powershell
$env:LOTO_WHEEL_METHOD = 'hitcover'
.\START_8000.bat
```

Alege în UI un buget pozitiv de variante, de exemplu 7. Limitele metodei sunt:
pool ≤16, bilet ≤6, garanție mai mică decât biletul, buget 1..64 și acoperire
inițială incompletă. În afara lor păstrează greedy; bugetul zero nu aplică această
optimizare. Condiția lotto mai mare decât garanția folosește designul condiționat.
Pentru revenire la alegerea implicită, elimină variabila înainte de repornire:

```powershell
Remove-Item Env:LOTO_WHEEL_METHOD
```

Implicitul existent rămâne La Jolla fără buget și greedy cu buget. Semnăturile
WF și worker disting `hitcover` de aceste alegeri. Optimizarea se aplică
variantelor motorului; generatorul separat de formulare «Bilet complet»
folosește în continuare propria construcție greedy.

## Dovezi de execuție

- Python 3.14.7 pe Windows.
- 13 istorice din registru: toate rândurile și urnele validate.
- 52 metode; lista activă are 52 incluzând martorul random, per joc 51.
- 832 verificări de scoring pe cele patru geometrii și patru lungimi de istoric:
  aceeași validare a scorului și aceeași selecție top-N bench/producție.
- 151 designuri locale: acoperire exhaustivă 100%, zero blocuri redundante.
- 26 pipeline-uri: toate jocurile, buget zero/7, limita de consecutive 2.
- 24 comparații de probabilitate la bugete 7/10, pool-uri 11/16 și ținte 3/4;
  niciun prag de hituri nu pierde șanse.
- Worker separat, coadă SQLite izolată, 13 rezultate COMPLETED și decodate.
- UI real pe port liber: HTTP 200, coadă și setări temporare separate.
- Hash-urile istoricului, deciziei, benchmarkului și stării de producție sunt
  identice înainte/după auditul de execuție și diagnosticul statistic.
- 92 clasamente UI randate verificate; ultimul rezultat stocat este BG Toto 2,
  pool 16, 191 variante, acoperire 100%. Pentru această configurație nu există
  cache WF curent; auditorul îl raportează lipsă, fără a inventa o validare.
- Suita completă: 1.733 trecute, 17 omise, zero eșecuri (310,89 s).
  Cele 272 avertismente sunt deprecieri NumPy în trei generatoare de date din teste.
  Compilarea fișierelor modificate și `git diff --check` trec.

## Evaluarea statistică a scorerilor

Trei ferestre consecutive distincte, câte 60 de extrageri, prefix cronologic
extensibil, fără celelalte extrageri din ziua țintei. Pool-uri principale
6/8/10/12/16, Urna 2 top-1, praguri 3+/4+ și 1 pentru Urna 2. Toate cele 52 de
metode sunt evaluate; scorurile plate sunt raportate și excluse din inferență
atunci când evaluarea nu este completă.

37.440 evaluări cerute, zero erori de scorer. Familie planificată: 1.612
comparații, dintre care 1.605 au eșantion complet. 57 valori p brute sunt sub
0,05, dar zero după corecția Holm; cel mai mic p ajustat este 0,523217.

Acesta este un diagnostic retrospectiv. Istoricul a fost disponibil la
construirea metodelor, deci nu este un holdout extern neatins și nu validează
selecția metodei pe viitoarele extrageri. Nu justifică o schimbare automată de
scorer sau un blend de metode. Un Re-Bench complet de producție nu a fost rulat
și nu au fost rescrise folds.csv ori best_methods.json.

## Reproducere

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts/analysis/audit_application.py --output scratch/audit_application.json
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts/analysis/audit_method_windows.py --output scratch/method_windows
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts/analysis/bench_budget_cover.py --output scratch/budget_experiment.json
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts/analysis/audit_patterns_and_designs.py --output scratch/patterns_designs.json
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts/analysis/audit_output.py --report scratch/rendered_report.txt
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m pytest -q
```

Auditul certifică validarea și coerența datelor locale, nu verificarea
independentă a fiecărei extrageri vechi la sursa oficială. Ultima dată din
istoricele românești folosite este 27 septembrie 2026. Șansele și costul unei
generări viitoare depind de configurația și variantele ei efective.
