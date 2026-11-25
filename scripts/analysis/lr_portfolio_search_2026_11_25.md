# Portofolii LR în același buget, 25 noiembrie 2026

În 42 de comparații pe același pool și la cost cel mult egal, au crescut șansele obiectivului în 4 cazuri. În toate cazurile au fost păstrate sau îmbunătățite șansele de 4+, 5+ și 6+ unde pragul există în joc. Timpul median cu o combinație de geometrii noi și deja calculate a fost 0.0010 s, iar maximul 0.4319 s. Pentru algoritmul anterior timpii au fost 0.0002 s și 0.1675 s. Măsurătoarea este o singură rulare Windows și include calculele portofoliului, fără notarea rezultatelor și fără extragerea poolului.

## Metodă

Algoritmul anterior a furnizat portofoliul inițial pentru aceeași configurație. SHA-256 al fișierului de referință folosit la măsurare este ddfe1b121e10f6973e2aaa7524f69a0686951a8ca165ff0ccc12d76781a0b762. Varianta curentă cu `time_limit_s=0` reproduce amplasările inițiale și adaugă verificarea lor independentă. Costul de căutare a fost plafonat la prețul concret al portofoliului inițial. Biletele costă numărul de variante ori 8 lei la 6/49, 5 lei la 5/40 și 7 lei la Joker, plus 0,50 lei pe bilet fizic. Pe bilet încap, respectiv, trei, patru și două zone. [Tarife Loteria Română](https://www.loto.ro/?page_id=1068)

Evaluatorul benchmarkului desface fiecare zonă pe toate pozițiile schemei oficiale și numără evenimentele direct pe fiecare intersecție posibilă cu poolul. Costul include orice variantă repetată. Probabilitatea de cel puțin un succes numără o singură dată evenimentul, iar două pariuri Joker cu aceleași numere principale și bile Joker diferite rămân pariuri distincte. La 6/49 numărătorul de 6/6 este numărul variantelor principale distincte și numitorul este 13.983.816. Jocul 5/40 extrage șase bile, dar o variantă are cinci. La Joker, 4+ și 5+ privesc urna principală, iar «orice premiu» include a doua urnă.

În 23 cazuri, probabilitatea aleasă atinge limita globală demonstrată de combinatorică. În 6 cazuri, modelul MILP a demonstrat maximul în setul de amplasări pe care l-a enumerat. Certificatul local se aplică doar acelor amplasări și cheltuielii plafonate. Limita globală din tabel se aplică bugetului declarat, deci poate rămâne mai mare decât orice portofoliu permis la plafonul concret de cost. Secțiunile de căutare se opresc la timp sau la anulare și păstrează cea mai bună soluție deja verificată.

Poolurile fixe au 16, 19, respectiv 15 numere. La Joker, bilele eligibile sunt 3 și 13. La probele taxei am restrâns poolul la dimensiunea codului minim plus o bilă. Fișierul Python de lângă raport reproduce toate comparațiile.

## Bugete întregi și praguri

Celulele cu număr arată evenimente favorabile înainte → după. Coloana «Δ pp» exprimă schimbarea absolută în puncte procentuale și «Δ relativ» schimbarea față de probabilitatea inițială. Coloana «Rest» arată diferența între numărătorul ales și limita globală.

| Joc | Obiectiv | Buget lei | Cost lei înainte → după | 4+ | 5+ | 6+ | Obiectiv | Δ pp | Δ relativ | Timp s înainte → după | Limită globală | Rest |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 6/49 | 4+ | 100 | 96.50 | 103,404 | 2,964 | 12 | 103,404 | +0.000000 | +0.0000% | 0.0000 → 0.0007 | 103,404 | 0 |
| 6/49 | 4+ | 200 | 176.50 | 250,008 | 5,698 | 22 | 250,008 | +0.000000 | +0.0000% | 0.0000 → 0.0005 | 250,008 | 0 |
| 6/49 | 4+ | 300 | 296.50 | 383,269 | 9,379 | 37 | 383,269 | +0.000000 | +0.0000% | 0.0380 → 0.0420 | 404,541 | 21,272 |
| 6/49 | 4+ | 400 | 392.50 | 468,412 | 12,231 | 49 | 468,412 | +0.000000 | +0.0000% | 0.0067 → 0.0169 | 507,945 | 39,533 |
| 6/49 | Orice premiu | 100 | 96.50 | 103,404 | 2,964 | 12 | 933,324 | +0.000000 | +0.0000% | 0.0000 → 0.0002 | 933,324 | 0 |
| 6/49 | Orice premiu | 200 | 176.50 | 250,008 | 5,698 | 22 | 2,069,298 | +0.000000 | +0.0000% | 0.0000 → 0.0003 | 2,069,298 | 0 |
| 6/49 | Orice premiu | 300 | 296.50 | 382,544 | 9,379 | 37 | 2,811,512 | +0.000000 | +0.0000% | 0.0242 → 0.0336 | 3,305,226 | 493,714 |
| 6/49 | Orice premiu | 400 | 392.50 | 468,412 | 12,231 | 49 | 3,240,952 | +0.000000 | +0.0000% | 0.0070 → 0.0173 | 4,168,472 | 927,520 |
| 5/40 | 4+ | 100 | 90.50 | 38,360 | 608 | 0 | 38,360 | +0.000000 | +0.0000% | 0.0000 → 0.0026 | 38,360 | 0 |
| 5/40 | 4+ | 200 | 195.50 | 81,760 | 1,318 | 0 | 81,760 | +0.000000 | +0.0000% | 0.0000 → 0.0016 | 81,760 | 0 |
| 5/40 | 4+ | 300 | 285.50 | 118,807 → 119,292 | 1,926 | 0 | 118,807 → 119,292 | +0.012636 | +0.4082% | 0.0626 → 0.0889 | 120,120 | 828 |
| 5/40 | 4+ | 400 | 375.50 | 167,020 | 2,614 | 0 | 167,020 | +0.000000 | +0.0000% | 0.0000 → 0.0043 | 167,020 | 0 |
| 5/40 | Orice premiu | 100 | 90.50 | 38,360 | 608 | 0 | 38,360 | +0.000000 | +0.0000% | 0.0000 → 0.0003 | 38,360 | 0 |
| 5/40 | Orice premiu | 200 | 195.50 | 81,760 | 1,318 | 0 | 81,760 | +0.000000 | +0.0000% | 0.0000 → 0.0006 | 81,760 | 0 |
| 5/40 | Orice premiu | 300 | 285.50 | 118,807 → 119,292 | 1,926 | 0 | 118,807 → 119,292 | +0.012636 | +0.4082% | 0.0238 → 0.0419 | 120,120 | 828 |
| 5/40 | Orice premiu | 400 | 375.50 | 167,020 | 2,614 | 0 | 167,020 | +0.000000 | +0.0000% | 0.0000 → 0.0010 | 167,020 | 0 |
| joker | 4+ | 100 | 98.50 | 2,702 | 14 | 0 | 2,702 | +0.000000 | +0.0000% | 0.0000 → 0.0003 | 2,702 | 0 |
| joker | 4+ | 200 | 196.50 | 5,325 | 28 | 0 | 5,325 | +0.000000 | +0.0000% | 0.0163 → 0.0221 | 5,404 | 79 |
| joker | 4+ | 300 | 295.00 | 7,862 | 42 | 0 | 7,862 | +0.000000 | +0.0000% | 0.0338 → 0.0422 | 8,106 | 244 |
| joker | 4+ | 400 | 386.50 | 9,899 | 55 | 0 | 9,899 | +0.000000 | +0.0000% | 0.0616 → 0.0803 | 10,808 | 909 |
| joker | Orice premiu | 100 | 98.50 | 2,533 | 14 | 0 | 2,923,280 | +0.000000 | +0.0000% | 0.0140 → 0.0207 | 2,945,208 | 21,928 |
| joker | Orice premiu | 200 | 190.50 | 4,799 | 27 | 0 | 4,129,887 | +0.000000 | +0.0000% | 0.0440 → 0.0600 | 6,463,696 | 2,333,809 |
| joker | Orice premiu | 300 | 295.50 | 7,488 | 42 | 0 | 5,022,660 → 5,101,830 | +0.324000 | +1.5763% | 0.0949 → 0.1216 | 6,512,310 | 1,410,480 |
| joker | Orice premiu | 400 | 394.50 | 9,427 → 9,496 | 56 | 0 | 5,493,870 → 5,510,070 | +0.066298 | +0.2949% | 0.1675 → 0.1952 | 6,512,310 | 1,002,240 |

## Contribuția fiecărei etape

Am măsurat cumulativ etapele în toate cele 42 de configurații, folosind traseul normal al optimizatorului. La fiecare etapă, am păstrat portofoliul din acel moment și am renumărat separat rezultatele favorabile. Timpii sunt de la intrarea în optimizator până la portofoliul afișat în celulă. Renumărarea separată a etapelor nu intră în acești timpi. Prima coloană de rezultate pornește de la planul anterior, adică șablonul oficial când încape sau soluția greedy pe pool. Celulele fără calificativ consemnează o soluție fezabilă.

| Joc și obiectiv | Buget / cost jucat, lei | Inițial | +Beam | +LNS | +MILP | Beam extins |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5/40, 4+ | 300,00 / 285,50 | 118.807 · 0,06330 s | 118.807 · 0,07248 s | 119.292 · 0,08099 s | Geometrie prea mare | 119.292 · 0,08116 s |
| 5/40, Orice premiu | 300,00 / 285,50 | 118.807 · 0,02412 s | 118.807 · 0,03285 s | 119.292 · 0,04153 s | Geometrie prea mare | 119.292 · 0,04170 s |
| Joker, Orice premiu | 300,00 / 295,50 | 5.022.660 · 0,09731 s | 5.052.995 · 0,11141 s | 5.101.830 · 0,12350 s | Geometrie prea mare | 5.101.830 · 0,12407 s |
| Joker, Orice premiu | 400,00 / 394,50 | 5.493.870 · 0,16922 s | 5.493.870 · 0,18775 s | 5.510.070 · 0,19697 s | Geometrie prea mare | 5.510.070 · 0,19807 s |
| 6/49, 4+ | 100,00 / 96,50 | 103.404 · 0,00063 s · global | Limita deja atinsă | Limita deja atinsă | Limita deja atinsă | Limita deja atinsă |
| Joker, 4+ | 105,99 / 77,50 | 1.721 · 0,00039 s | 1.721 · 0,00057 s | 1.721 · 0,00066 s | 1.721 · 0,25797 s · local (9 amplasări) | Etapă nealeasă |

La Joker, cu bugetul de 300 lei și obiectivul «Orice premiu», căutarea beam a adăugat 30.335 de rezultate favorabile. Înlocuirea simultană de zone și refacerea portofoliului prin LNS au mai adăugat 48.835. LNS a produs și celelalte trei creșteri din tabel. La cele patru configurații câștigătoare, geometria depășea pragul MILP din cod. Căutarea beam extinsă a rulat în continuare, cu aceleași totaluri.

Pe întreg lotul, 23 de configurații atingeau deja limita globală din planul inițial. În cele șase configurații analizate de MILP, solverul a confirmat maximul între amplasările evaluate, cu aceleași totaluri. Tabelul include câte un exemplu din fiecare situație. Opțiunea `--ablation` a scriptului exportă toate cele 42 de comparații pe etape, inclusiv costul, pragurile protejate și starea certificării.

## Limitele taxei pentru al doilea bilet fizic

Am testat suma necesară pentru numărul minim de zone care cere încă un bilet, cu un ban sub limită, exact la limită și cu un ban peste. În aceste 18 comparații, optimizarea a păstrat numărătorii și costurile inițiale.

| Joc | Obiectiv | Buget lei | Cost jucat lei | Zone / bilete | 4+ | 5+ | 6+ | Obiectiv | Rest | Timp s după |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 6/49 | 4+ | 384.99 | 240.50 | 1 / 1 | 165,648 | 7,230 | 30 | 165,648 | 0 | 0.0011 |
| 6/49 | 4+ | 385.00 | 240.50 | 1 / 1 | 165,648 | 7,230 | 30 | 165,648 | 0 | 0.0002 |
| 6/49 | 4+ | 385.01 | 240.50 | 1 / 1 | 165,648 | 7,230 | 30 | 165,648 | 0 | 0.0002 |
| 6/49 | Orice premiu | 384.99 | 120.50 | 1 / 1 | 154,533 | 3,705 | 15 | 1,262,328 | 0 | 0.0006 |
| 6/49 | Orice premiu | 385.00 | 120.50 | 1 / 1 | 154,533 | 3,705 | 15 | 1,262,328 | 0 | 0.0001 |
| 6/49 | Orice premiu | 385.01 | 120.50 | 1 / 1 | 154,533 | 3,705 | 15 | 1,262,328 | 0 | 0.0001 |
| 5/40 | 4+ | 225.99 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0004 |
| 5/40 | 4+ | 226.00 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0001 |
| 5/40 | 4+ | 226.01 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0001 |
| 5/40 | Orice premiu | 225.99 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0003 |
| 5/40 | Orice premiu | 226.00 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0002 |
| 5/40 | Orice premiu | 226.01 | 105.50 | 1 / 1 | 36,540 | 700 | 0 | 36,540 | 0 | 0.0002 |
| joker | 4+ | 105.99 | 77.50 | 2 / 1 | 1,721 | 11 | 0 | 1,721 | 925 | 0.4319 |
| joker | 4+ | 106.00 | 106.00 | 3 / 2 | 1,943 | 14 | 0 | 1,943 | 703 | 0.0760 |
| joker | 4+ | 106.01 | 106.00 | 3 / 2 | 1,943 | 14 | 0 | 1,943 | 703 | 0.0001 |
| joker | Orice premiu | 105.99 | 77.50 | 2 / 1 | 1,721 | 11 | 0 | 2,200,659 | 90,021 | 0.0150 |
| joker | Orice premiu | 106.00 | 106.00 | 3 / 2 | 1,906 | 14 | 0 | 2,212,647 | 78,033 | 0.2711 |
| joker | Orice premiu | 106.01 | 106.00 | 3 / 2 | 1,906 | 14 | 0 | 2,212,647 | 78,033 | 0.0002 |

Amplasările generale devin rapid prea numeroase pentru enumerarea integrală. Solverul primește numai o geometrie mică, în funcție de numărul de intersecții, pragurile protejate, alocările Joker și timpul rămas. Căutarea mai largă folosește mai multe amplasări pe cod, reține continuări diverse și poate înlocui două zone înainte de a reface portofoliul. Rezultatele de mai sus sunt probabilități combinate pentru poolul fix din fiecare scenariu. Frecvența istorică sau performanța unei metode de selectare a poolului nu intră în aceste calcule.
