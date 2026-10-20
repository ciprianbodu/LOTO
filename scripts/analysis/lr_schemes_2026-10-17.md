# Schemele reduse ale Loteriei Române — 2026-10-17

Utilizatorul joacă la 6/49 schemele reduse predefinite ale Loteriei Române. Până
acum aplicația știa numai codul și numărul de variante, iar panoul avertiza că
garanția schemelor nu e documentată. Analiza de mai jos pornește de la
desfășurările oficiale.

## Sursa și verificarea

Broșurile loto.ro („Scheme reduse predefinite: desfășurări și indexuri de
câștiguri”) tipăresc, pentru fiecare cod, grila variantelor și indexul de
câștiguri:

- `extras_brosura-649.pdf`, `extras_brosura_Loto540.pdf` și
  `extras_brosura_joker.pdf`, toate din `https://www.loto.ro/images/upload/pages/`;
- amprentele sha256 sunt în antetul fiecărui fișier și în `index_oficial.json`.

`import_lr_schemes.py` citește grila din coordonatele cuvintelor (`pdftotext
-bbox`) și recalculează indexul din variante. Schema se scrie numai dacă indexul
recalculat coincide exact cu cel tipărit:

- 6/49: numărul de cazuri pe categoriile I–IV;
- 5/40: categoriile 5/5, 5/6, 4/6, cu al șaselea număr extras enumerat;
- Joker: intervalele și procentele din tabelul fără numărul din câmpul 2. La
  Joker se verifică și lista de numere din stânga grilei.

Toate cele 33 de scheme cu variante întregi au trecut verificarea:

- 6/49: 7 scheme;
- 5/40: 16 scheme;
- Joker: 10 scheme.

Cele 18 scheme 6/49 cu variante de 2, 3 sau 4 numere (pentru combinatele
compuse) nu sunt importate; importatorul le listează ca sărite. Broșurile 5/40
și Joker au numai scheme cu variante întregi.

Poziția 1 a schemei este cel mai mic număr jucat. Broșura 6/49, exemplul 3,
pune „numerele teoretice” 1..13 în dreptul numerelor jucate în ordine
crescătoare. Broșurile 5/40 și Joker nu spun ordinea; aplicația presupune aceeași
regulă și o spune în panou. Pe extrageri uniforme, așezarea nu schimbă șansele.

Indexul tipărit nu fixează singur desfășurarea: o reetichetare a pozițiilor îl
păstrează, iar tabelul Joker pe intervale acceptă și câteva variante schimbate.
Importatorul scrie de aceea și amprenta variantelor verificate (`rows_sha256`),
pe care testul o compară cu fișierele.

## Ce garantează schemele 6/49

„k → g sigur” înseamnă: din k numere ieșite dintre cele jucate, cel puțin g ajung
pe aceeași variantă în toate cazurile. Procentul arată partea cazurilor în care
se ajunge mai sus. Șansele sunt exacte, pe o extragere uniformă, pentru cel puțin
o variantă.

| Cod | Numere | Variante | Lei | 3+ | 4+ | 5+ | 6 | Garanții |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 48 | 9 | 12 | 96 | 6,67% | 0,74% | 0,021% | 0,000086% | 4→4; 5→4 (5: 57,14%); 6→5 (6: 14,29%) |
| 49 | 10 | 15 | 120 | 9,03% | 1,11% | 0,026% | 0,00011% | 4→3 (4: 92,86%); 5→4 (5: 35,71%); 6→4 (5+: 92,86%, 6: 7,14%) |
| 50 | 10 | 30 | 240 | 9,03% | 1,18% | 0,052% | 0,00021% | 4→4; 5→4 (5: 71,43%); 6→5 (6: 14,29%) |
| 56 | 11 | 66 | 528 | 11,74% | 1,79% | 0,11% | 0,00047% | 4→4; 5→4 (5: 85,71%); 6→5 (6: 14,29%) |
| 57 | 12 | 22 | 176 | 14,80% | 1,79% | 0,041% | 0,00016% | 4→3 (4: 66,67%); 5→4 (5: 16,67%); 6→4 (5+: 88,10%, 6: 2,38%) |
| 58 | 12 | 132 | 1.056 | 14,80% | 2,57% | 0,22% | 0,00094% | 4→4; 5→5; 6→5 (6: 14,29%) |
| **59** | **16** | **112** | **896** | **29,81%** | **7,43%** | **0,207%** | **0,0008%** | **4→3 (4: 92,31%); 5→4 (5: 15,38%); 6→4 (5+: 85,31%, 6: 1,40%)** |

La toate codurile, 3 numere ieșite dintre cele jucate dau 3 pe o variantă. Deci
3+ pe o variantă are aceeași rată ca 3+ în pool.

## Cod 59 față de variantele aplicației (6/49, 16 numere)

Pentru aceleași 16 numere și același număr de variante (112), cel mai bun
wheel al aplicației are șanse identice la fiecare prag: 7,43% la 4+, 0,207% la
5+, 0,0008% la 6. Cod 59 este la fel de bun ca cel mai bun design pe care îl
găsește aplicația pentru 112 variante.

Coverul complet al aplicației (garanția 4, fără plafon) are 152 de variante,
adică 1.216 lei:

| | Cod 59 | Cover complet g4 |
|---|---:|---:|
| Variante / cost | 112 / 896 lei | 152 / 1.216 lei |
| 4 ieșite din 16 → 4 pe o variantă | 92,31% | 100% |
| 5 ieșite din 16 → 5 pe o variantă | 15,38% | 19,41% |
| 4+ pe o variantă, la o extragere | 7,43% | 7,96% |
| 5+ pe o variantă, la o extragere | 0,207% | 0,251% |
| Lei pe o extragere cu 4+ (așteptat) | 12.057 | 15.276 |

Coverul complet prinde mai des, iar Cod 59 costă mai puțin pentru fiecare
extragere cu 4+.

Istoricul walk-forward cuprinde 777 de extrageri 6/49, cu pool de 16 numere
ales la fiecare pas numai din extragerile anterioare. Pe aceste extrageri s-a
jucat schema pe pool-ul de atunci:

| Extrageri cu | Pool | Cod 59 (112 var.) | Cover g4 (152 var.) | Cod 59 așteptat |
|---|---:|---:|---:|---:|
| 3+ | 226 | 226 | 226 | 231,6 |
| 4+ | 65 | 60 | 65 | 57,7 |
| 5+ | 8 | 0 | 1 | 1,6 |
| 6 | 0 | 0 | 0 | 0,006 |

- Diferența la 4+ (60 față de 65) e în limita indexului. 57 de extrageri au
  avut exact 4 numere în pool: la 92,31% erau așteptate 52,6, iar Cod 59 a
  prins 4 pe o variantă la 52. Celelalte 8 au avut 5 în pool, unde 4 pe o
  variantă e sigur, și le-a prins pe toate.
- La aceleași 8 extrageri, Cod 59 ar fi pus toate cele 5 numere pe aceeași
  variantă, în medie, la 1,2 dintre ele (2/13 la fiecare). Observat: la niciuna.
  Pe extrageri uniforme, zero din 8 are probabilitatea de circa 26%
  ((11/13)^8).
- Rezultatul schemei depinde de pool. Pool-ul nu bate hazardul (226 de
  extrageri cu 3+, față de 231,6 așteptate).

## 5/40 și Joker

La 5/40 se extrag 6 numere, iar varianta are 5. Pentru pool de 16 numere sunt
două coduri:

- **Cod 37**: 348 de variante, 1.740 lei. Bate wheel-ul aplicației cu tot
  atâtea variante: 4+ 14,91% față de 14,58%; 3+ și 5 sunt egale.
- **Cod 38**: 443 de variante, 2.215 lei. Ajunge la aceleași 4+ (16,03%) ca
  coverul complet al aplicației, care are numai 404 variante (2.020 lei). La 5,
  Cod 38 dă 0,393%, iar coverul complet 0,363%.
- În istoricul walk-forward (523 de extrageri), Cod 37 a prins 4+ în 81 de
  extrageri, Cod 38 în 86, iar coverul de 404 variante tot în 86. Cu 5 pe o
  variantă: Cod 37 și Cod 38 câte una (27-06-2021); coverul de 404 variante, trei.

La Joker, Loteria Română nu are schemă pentru 16 numere. Codurile disponibile
acoperă 7–13 și 15 numere. La 15 numere, Cod 12 are 118 variante; wheel-ul
aplicației cu 118 variante dă 4+ 1,69%, față de 1,58% la Cod 12.

## Toate schemele față de aplicație, la același număr de variante

Codurile 5/40 45, 46, 47 și 48 (17, 17, 18 și 19 numere) nu sunt comparate:
pool-ul aplicației are cel mult 16 numere, iar profilul exact se calculează
numai până la 16. Lipsesc și din `lr_schemes_2026-10-17.json`.

Verdictul compară probabilitățile exacte la fiecare prag (3 până la
min(bilet, extrase)), cu tot atâtea variante:

- „identic”: aceleași șanse la fiecare prag;
- „LR domină”: schema nu e sub aplicație la niciun prag;
- „aplicația domină”: aplicația nu e sub schemă la niciun prag;
- „compromis”: fiecare câștigă la alt prag.

| Joc | Cod | Numere | Variante | Verdict |
|---|---:|---:|---:|---|
| 6/49 | 48, 56, 57, 58, 59 | 9, 11, 12, 12, 16 | 12, 66, 22, 132, 112 | identic |
| 6/49 | 50 | 10 | 30 | LR domină (5+ 0,052% față de 0,050%) |
| 6/49 | 49 | 10 | 15 | compromis (LR 4+ 1,11%; aplicația 5+ 0,0266%) |
| 5/40 | 15, 17, 18, 25 | 7, 9, 10, 11 | 9, 30, 51, 66 | identic |
| 5/40 | 37 | 16 | 348 | LR domină (4+ 14,91% față de 14,58%) |
| 5/40 | 26, 35 | 12, 15 | 113, 243 | aplicația domină, la diferențe mici (Cod 35: 4+ 11,17% față de 11,16%) |
| 5/40 | 16, 27, 28, 36, 38 | 8, 13, 14, 15, 16 | 21, 173, 255, 327, 443 | coverul complet al aplicației are mai puține variante (20, 157, 229, 294, 404), aceleași 3+ și 4+, 5 mai mic |
| Joker | 45, 25, 23 | 7, 9, 10 | 5, 30, 51 | identic |
| Joker | 15 | 11 | 22 | LR domină (4+ 0,344% față de 0,328%) |
| Joker | 35, 34, 14, 13, 12 | 8, 9, 12, 13, 15 | 6, 9, 38, 54, 118 | aplicația domină (Cod 34: 3+ 4,40% față de 3,94%) |
| Joker | 24 | 10 | 14 | compromis (aplicația 3+ 5,93% față de 5,74%) |

La Joker, verdictul privește șansele necondiționate. Schemele LR de mai sus dau
sigur 4 pe o variantă când ies 5 dintre numerele jucate; wheel-ul aplicației cu
tot atâtea variante, numai în 92,86% (Cod 34) până la 99,33% (Cod 12) din
cazuri. Câștigul aplicației vine din cazul cu exact 4 numere ieșite (Cod 12:
43,22% față de 39,78%), mult mai frecvent decât cel cu 5.

Rezultatul complet, cu toate garanțiile și toate wheel-urile pe fiecare garanție,
este în `lr_schemes_2026-10-17.json`. Se reproduce cu:

```text
python scripts/analysis/import_lr_schemes.py --pdf-dir <broșuri>
python scripts/analysis/analyze_lr_schemes.py --wf <cache WF 6/49, 5/40, Joker>
```

## Ce s-a schimbat în aplicație

- `covering/lr_schemes.py` încarcă cele 33 de scheme din
  `covering_designs/lr/`. Un fișier stricat este sărit separat.
- Panoul de rezultate arată, pentru pool-ul generat:
  - codul LR potrivit;
  - ce garantează;
  - șansele exacte;
  - diferența față de wheel-ul aplicației;
  - desfășurarea pe numerele de azi.

  Avertismentul „garanția nu e documentată” nu mai apare. Pentru un pool fără
  cod, panoul spune pentru câte numere există scheme.
- „📜 Istoric hits” rejoacă schema pe pool-ul fiecărei extrageri walk-forward și
  o compară cu variantele WF și cu șansa exactă.
- Raportul și mailul numesc codul LR al pool-ului.

Nimic din acestea nu schimbă pool-ul, scorerul sau variantele generate. Șansa
pool-ului rămâne hipergeometrică. O schemă hotărăște numai cum ajung pe aceeași
variantă numerele prinse în pool.
