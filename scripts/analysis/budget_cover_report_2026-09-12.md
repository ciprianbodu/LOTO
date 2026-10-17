# Covering design cu buget fix — 12 septembrie 2026

**Concluzie:** maxcover îmbunătățește acoperirea, dar avantajul ratei istorice de hits nu este confirmat după corecția statistică. Metoda rămâne experimentală și necesită activare explicită.

## Protocol

Scorer fix: `score_frequency`, recalculat pe trecut; pool ales prin `rank_by_score`. Ultimele 30% din extragerile valide, ordonate cronologic, sunt evaluate în trei ferestre consecutive. Se exclud din scorare toate extragerile de la data evaluată, inclusiv extragerile suplimentare din aceeași zi. Segmentul este rezervat acestui experiment, dar istoricul a mai fost analizat de proiect: nu este o confirmare prospectivă.

Pool 11; bugete 7/10; ținte de construcție 3/4. Greedy, La Jolla, bilete aleatoare fără înlocuire din același pool și maxcover primesc același pool și exact același număr de bilete. Controlul aleator folosește seed 20260912, nu o medie peste multe seed-uri. Algoritmul și cele două treceri locale au fost fixate înainte de evaluare; nu s-au ales hiperparametri după rezultate.

Rata 3+/4+ înseamnă extrageri cu cel puțin un bilet care atinge pragul. Nu este rata de hits a pool-ului și nu reprezintă profitabilitate. Joker măsoară numai Urna 1; 5/40 folosește n1..n5 conform aplicației, nu toate categoriile oficiale de premii.

## Date

| Joc | Extrageri evaluate | Prima dată | Ultima dată |
|---|---:|---|---|
| 6/49 | 775 | 2019-02-14 | 2026-09-10 |
| 5/40 | 520 | 2020-10-25 | 2026-09-10 |
| Joker urn 1 | 656 | 2020-03-19 | 2026-09-10 |

Amprentele SHA-256 ale CSV-urilor și rândurile respinse sunt în JSON.

## Toate comparațiile principale

Fiecare celulă arată greedy → maxcover. Acoperirea este media procentelor de grupuri de mărimea țintei prezente integral pe cel puțin un bilet, după repararea numerelor lipsă.

| Joc | Țintă | Bilete | Acoperire % | Rata 3+ % | Rata 4+ % |
|---|---:|---:|---:|---:|---:|
| 6/49 | 3 | 7 | 69.02 → 72.73 | 9.68 → 10.19 | 0.77 → 0.52 |
| 6/49 | 3 | 10 | 88.65 → 91.52 | 12.13 → 12.77 | 0.77 → 0.77 |
| 6/49 | 4 | 7 | 29.36 → 31.82 | 9.68 → 10.19 | 0.65 → 0.77 |
| 6/49 | 4 | 10 | 40.77 → 45.45 | 11.35 → 13.03 | 0.90 → 1.03 |
| 5/40 | 3 | 7 | 39.41 → 41.82 | 5.19 → 5.77 | 0.00 → 0.19 |
| 5/40 | 3 | 10 | 54.58 → 57.58 | 7.50 → 7.31 | 0.19 → 0.00 |
| 5/40 | 4 | 7 | 10.60 → 10.61 | 4.23 → 4.23 | 0.19 → 0.19 |
| 5/40 | 4 | 10 | 15.04 → 15.15 | 5.38 → 5.77 | 0.19 → 0.19 |
| Joker urn 1 | 3 | 7 | 39.31 → 41.82 | 4.88 → 5.18 | 0.15 → 0.30 |
| Joker urn 1 | 3 | 10 | 53.30 → 57.58 | 6.71 → 6.86 | 0.15 → 0.61 |
| Joker urn 1 | 4 | 7 | 10.59 → 10.61 | 3.66 → 3.51 | 0.30 → 0.30 |
| Joker urn 1 | 4 | 10 | 14.98 → 15.15 | 5.03 → 4.88 | 0.30 → 0.46 |

Test binomial pereche pe cazurile discordante, cu corecție Holm pentru toate cele 24 de comparații (12 configurații × 2 praguri): **niciun p ajustat sub 0,05; minim 0.0564**. JSON-ul include intervale Wilson marginale, rezultatele celor trei ferestre, hiturile de pool, biletele câștigătoare la 100 de bilete și timpii mediani. Intervalele Wilson nu sunt simultane.

Acoperirea totală poate crește deși se înlocuiesc unele grupuri anterior acoperite: o anumită extragere poate pierde un hit. Scăderile din tabel nu sunt eliminate din raport.

## Referințe la ținta 3 și 10 bilete

| Joc | Metodă | Acoperire % | Rata 3+ % | Rata 4+ % | Mediană ms/pas |
|---|---|---:|---:|---:|---:|
| 6/49 | greedy | 88.65 | 12.13 | 0.77 | 1.486 |
| 6/49 | lajolla | 93.94 | 13.03 | 0.77 | 2.091 |
| 6/49 | random_tickets | 73.10 | 11.10 | 0.90 | 0.069 |
| 6/49 | maxcover | 91.52 | 12.77 | 0.77 | 1.518 |
| 5/40 | greedy | 54.58 | 7.50 | 0.19 | 0.488 |
| 5/40 | lajolla | 52.31 | 6.54 | 0.38 | 1.336 |
| 5/40 | random_tickets | 46.78 | 7.50 | 0.00 | 0.055 |
| 5/40 | maxcover | 57.58 | 7.31 | 0.00 | 0.537 |
| Joker urn 1 | greedy | 53.30 | 6.71 | 0.15 | 0.489 |
| Joker urn 1 | lajolla | 52.79 | 6.25 | 0.46 | 1.331 |
| Joker urn 1 | random_tickets | 46.73 | 5.64 | 0.15 | 0.055 |
| Joker urn 1 | maxcover | 57.58 | 6.86 | 0.61 | 0.537 |

## Geometrie și interpretare

JSON-ul include 16 geometrii (pool 11/16, bilet 5/6, țintă 3/4, buget 7/10), fiecare cu trei metode. Probabilitatea uniformă se calculează exact, enumerând intersecțiile cu pool-ul și completările combinatoriale din afara sa. Universul este 45 pentru biletul de 5 și 49 pentru cel de 6; partea geometrică cu bilet de 5 nu reprezintă 5/40.

Reducerea suprapunerii poate crește probabilitatea de cel puțin un bilet cu hituri, fără a crește numărul așteptat de bilete câștigătoare la buget egal într-o extragere uniformă. Scorurile nu sunt probabilități. Această analiză nu justifică o schimbare a metodelor de selecție a numerelor.

## Implementare și verificare

`budget_cover.py` construiește un cover după câștigul marginal pe toate biletele candidate și aplică două treceri de înlocuiri locale cu îmbunătățire strictă. Geometria este memoizată și deterministă. Candidatul este păstrat numai dacă acoperă un număr EXACT mai mare de ținte după reparare, cu cel mult același număr de bilete. Euristică, fără certificat de optimalitate.

Domeniu: pool ≤16, bilet ≤6, buget 1..64, țintă mai mică decât biletul. Alte cazuri revin la greedy; condiția lotto peste garanție păstrează traseul `wheel_lotto`. Default-urile nu se schimbă. WF distinge numele metodei; workerul separă cache-ul numai când maxcover este activ.

- 49 de teste noi trecute, inclusiv verificare exhaustivă independentă, lipsa anticipării, extrageri în aceeași zi, cache separat și pipeline pentru cele trei jocuri.
- 329 de teste specifice metodelor și designurilor trecute.
- Suita completă: 842 de teste trecute și patru erori de procese deja reproduse pe codul de bază. Testele de integrare adăugate ulterior au fost verificate separat în cele 49 de teste noi. Nu declarăm suita complet verde.

## Reproducere

```bash
python scripts/analysis/bench_budget_cover.py --output rezultat_cover.json
python -m pytest -q test_budget_cover.py
```

Activare experimentală pe Windows, după actualizare, în aceeași fereastră Command Prompt:

```bat
set "LOTO_WHEEL_METHOD=maxcover"
START_8000.bat
```

Configurează 7 sau 10 variante pentru reproducerea experimentului. La buget zero se revine la greedy; metoda vizează bugete limitate. Pentru revenirea la alegerea implicită: `set "LOTO_WHEEL_METHOD="`, apoi repornește aplicația.

## Surse și date complete

- [La Jolla Combinatorics Repository — Dan Gordon](https://dmgordon.org/).
- [SciPy — test binomial și intervale](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html).
- [Rezultate complete JSON](budget_cover_results_2026-09-12.json).
