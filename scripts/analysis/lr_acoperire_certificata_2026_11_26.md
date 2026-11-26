# Acoperirea portofoliilor LR la cost egal

Am renumărat 174 de portofolii din arhiva `Teste_LOTO_2026_11_25.zip`. Cele 42 de cazuri din grila de referință includ trei portofolii fiecare, iar cele 24 de cazuri din generator includ câte două. Am desfășurat fiecare cod LR pe numerele zonei ordonate crescător, am asociat fiecărei variante bila Joker efectiv plătită și am inclus duplicatele și taxa pe fiecare bilet fizic în cost. Probabilitatea folosește reuniunea rezultatelor favorabile ale tuturor variantelor.

În grilă, candidatul a mărit numărătorul obiectivului în 8/42 de rânduri, reprezentând 7 situații distincte. Pe biletele generatorului de la `e6cad789115d22ac839fc5e9390d3838bc4c9bfb`, câștigul apare în 7/24 de rânduri, reprezentând 6 situații distincte. La 5/40, obiectivele „4+” și „Orice premiu” descriu aceeași reuniune și aceleași bilete la 300 lei.

| Joc și obiectiv | Buget | Cost plătit, lei | Rezultate favorabile înainte → după | Probabilitate înainte → după | Diferență în puncte procentuale | Creștere relativă |
|---|---:|---:|---:|---:|---:|---:|
| 6/49, 4+ | 300 | 296,50 | 383.269 → 383.414 | 2,740804% → 2,741841% | 0,001037 | 0,0378% |
| 6/49, 4+ | 400 | 392,50 | 468.412 → 468.744 | 3,349672% → 3,352046% | 0,002374 | 0,0709% |
| 5/40, 4+ și orice premiu | 300 | 285,50 | 119.292 → 119.402 | 3,107874% → 3,110739% | 0,002866 | 0,0922% |
| Joker, 4+ | 200 | 196,50 | 5.325 → 5.384 | 0,435847% → 0,440676% | 0,004829 | 1,1080% |
| Joker, 4+ | 300 | 295,00 | 7.862 → 7.920 | 0,643498% → 0,648246% | 0,004747 | 0,7377% |
| Joker, 4+ | 400 | 386,50 | 9.899 → 9.944 | 0,810225% → 0,813908% | 0,003683 | 0,4546% |
| Joker, orice premiu, 2 bile eligibile, numai grila | 300 | 295,50 | 5.101.830 → 5.111.685 | 20,879036% → 20,919367% | 0,040331 | 0,1932% |

Numitorii sunt 13.983.816 pentru 6/49, 3.838.380 pentru 5/40, respectiv 1.221.759 și 24.435.180 pentru cele două obiective Joker. Cu cinci bile Joker eligibile, la 300 lei și „Orice premiu”, ambele portofolii acoperă 6.720.220 din 24.435.180 de rezultate la costul de 296,00 lei. La 6/49 și 400 lei, numărătorul de 5+ crește de la 12.231 la 12.274. Cele 49 de variante distincte dau 49/13.983.816 la 6/6 în ambele cazuri. Pragurile protejate sunt probabilitățile necondiționate de 4+, 5+ și 6 acolo unde pragul poate apărea pe o singură variantă. Garanțiile condiționate și categoriile individuale de premiu pot evolua diferit.

## Motorul folosit în aplicație

`covering/lr_expanded_search.py` pornește de la portofoliul complet ales de motorul anterior, la costul deja atins. Catalogul păstrează amplasările existente. Fiecare cod poate primi cel mult 64 de amplasări suplimentare prin maximum 4.096 de încercări cu sămânța fixă a experimentului. Căutarea pornește din până la opt amplasări pe cod, păstrează 12 portofolii intermediare și poate reface simultan două zone pentru cel mult zece perechi. Din cele două secunde alocate căutării, extensia catalogului se poate întinde până la 0,5 secunde și etapa beam până la 1,2 secunde de la început. Renumărarea independentă a portofoliului ales precedă adoptarea.

`covering/lr_pool_budget.py` aplică rafinarea la construcția biletelor. Generatorul, selecția dinamică, Re-Bench pe portofolii, Fixed Share și walk-forward folosesc acest punct comun. Pozițiile se mapează pe pool-ul ales pentru ziua respectivă. `covering/lr_expanded_templates.py` verifică martorii arhivați, tariful, capacitatea biletului, catalogul, costul, gruparea zonelor și sloturile Joker. Versiunile motorului și evaluatorului intră în semnăturile deciziilor. O limită superioară deja atinsă încheie căutarea. Verdictul „Maxim demonstrat” presupune atingerea unei limite dovedite pentru domeniul jucat.

## Rezultatele predictive și registrul prospectiv

Cele 217 extrageri pe joc, pe 208 date între 07.11.2024 și 22.11.2026, dau următoarele frecvențe retrospective pentru cel puțin o variantă plătită cu 4+ pe urna principală.

| Joc | Frequency | BOCPD | Stacking | Fixed Share |
|---|---:|---:|---:|---:|
| 6/49 | 2 | 0 | 2 | 1 |
| 5/40 | 1 | 0 | 0 | 1 |
| Joker | 2 | 1 | 0 | 2 |

Aceste rezultate susțin păstrarea politicii predictive existente. Câștigurile de acoperire din tabelul anterior sunt combinatorice. Confirmarea unei îmbunătățiri predictive cere extrageri ulterioare selecției.

Registrul de la `8cea19eebe798c35d4c1225855647edec3cf0b31` acordă un punctaj activ pentru fiecare eveniment oficial și păstrează istoricul rectificărilor. Două trageri distincte, principală și suplimentară, pot avea exact aceeași combinație. Metadatele insuficiente produc un conflict explicit. Regresia la 5/40 ține cont de a șasea bilă, iar la Joker de urna secundară. [Migrarea și regulile registrului](lr_prospective_2026_11_25.md)

## Reproducere fără căutare

Din rădăcina repository-ului, prima comandă renumără martorii versionați. A doua verifică și arhiva originală, inclusiv cele 121 de amprente ale fișierelor din manifest.

```powershell
python -m pytest -q tests/test_lr_experimental_archive.py -k "all_174 or two_and_five"
python -O -m scripts.analysis.verify_lr_experimental_archive _TESTE/Teste_LOTO_2026_11_25.zip --witnesses testdata/lr_experimental_witnesses_2026_11_25.json --certificates covering_designs/lr/expanded_certificates.json
```

SHA-256 al arhivei este `54ec1fcc177cf34c0feecc1537e96e8ca9861a9480dbaf018f03e3f5b7acc51f`. Prototipul testat are SHA-256 `00cf74a4e774b1f725afcbf5ac0eb8b8c50f4a8be4132239de11cde68704470e`. Reluarea acestuia pe o copie izolată a reviziei inițiale cu CPython 3.15.2 pe Windows a reprodus costul, numărătorul obiectivului și toate pragurile protejate în 66/66 de rânduri. S-a adaptat numai calea către checkout. Ordinea deciziilor unei căutări limitate după timp poate depinde de calculator. Portofoliile acceptate pot fi verificate direct din biletele complete.

Pentru o nouă măsurare în generator, folosește o cale de ieșire liberă. Scriptul citește istoricele înghețate din Git, verifică amprentele și repetă fiecare calcul în același proces pentru măsurarea cache-ului cald.

```powershell
python -m scripts.analysis.replay_lr_expanded_grid --archive _TESTE/Teste_LOTO_2026_11_25.zip --out _TESTE/replay_local_nou.json
```

## Verificarea în aplicație pe Windows

Pe CPython 3.15.2, NumPy 2.5.4, SciPy 1.18.1 și pandas 3.1.1, verificarea integrată a atins martorii în 42/42 de configurații din grilă și în 24/24 de configurații din generator. Pentru toate cele 24, am verificat și construcția pe pool-ul numeric exact din arhivă. Pe 22 de rânduri generatorul a ales aceleași numere și aceleași sloturi Joker. Pe celelalte două, am transpus pozițiile martorului pe pool-ul ales la generare și am renumărat variantele. Costul și numărătorii au coincis în ambele verificări.

La Joker 4+ și 200 lei, evaluarea pe biletele rafinate a trecut de la `compact_multiscale_shrink` la rezerva `frequency`. La 300 lei a trecut de la `frequency` la `compact_multiscale_shrink`. În contraproba cu aceeași arhivă și numai rafinarea dezactivată au revenit ambele alegeri inițiale. Re-Bench, generatorul și validarea evaluează biletele rezultate din aceeași versiune a motorului. Alegerea altui expert pe acest prefix nu dovedește un avantaj predictiv pe extrageri noi.

| Operație, mediană într-o rulare cu un proces per caz | Primul apel | Repetare în același proces |
|---|---:|---:|
| Construcția grilei, 42 de cazuri | 109 ms | 3,2 ms |
| Generatorul complet, 24 de cazuri | 420 ms | 13,3 ms |

Timpii măsoară aplicația întreagă pentru operația indicată și includ costuri diferite de verificarea exclusivă a unui șablon. Pentru o geometrie nouă, căutarea are până la două secunde suplimentare, iar un alt proces poate reciti și reverifica pozițiile salvate în `LOTO_WF_CACHE_DIR`. Cheia include identitatea portofoliului de pornire și sloturile, costul și tarifele, catalogul, regulile și evaluatorul. O anulare nu publică în cache o căutare parțială.

Verificarea fluxului UI a produs 383.414 rezultate favorabile la 6/49 și 296,50 lei. Workerul SQLite a finalizat jobul, iar alternativa pregătită pentru mesajul de rezultat a folosit același motor. SMTP și oprirea sistemului au fost simulate. Pentru walk-forward am verificat trei extrageri pe două zile, inclusiv două trageri care împart decizia zilei. La 5/40, 200 lei, invers în intervalul 10–40, codul 37 cere 16 numere și după excluderea celor 16 directe rămân 15 din 31. Aplicația și validarea raportează incompatibilitatea. Proba poate fi reluată dintr-un runtime izolat.

```powershell
python -m scripts.analysis.replay_lr_expanded_workflow --out _TESTE/workflow_local_nou.json
```
