# Mai multe hituri pe bilete la același cost: recoacerea pe șansa exactă, 2026-10-11

## De unde pornește

Utilizatorul a cerut cod care să dea mai multe hituri. Rata de hit a
pool-ului nu se poate ridica prin cod: pentru extrageri uniforme e
hipergeometrică, iar toate testele de până acum (131 de teste de aleatorism pe
14 loterii, peste 2.000 de scoreri în trei ecrane, replicare pe 10 loterii
străine) spun că extragerile sunt uniforme. Singurul loc unde codul schimbă
numărul de hituri e așezarea numerelor pe bilete, la același pool și același
număr de bilete: cât de des cel puțin un bilet atinge 3+, 4+, 5+.

Aplicația optimiza deja așezarea (hitcover, schimburile de profil,
`budget_climb` peste 64 de bilete). Întrebarea de aici: a ajuns la maxim?

## Măsurătoarea

`bench_anneal_hits.py`, rezultatul în `anneal_hits_2026-10-11.json`. Pe
fiecare joc (6/49, 5/40, Joker Urna 1), pool 10, 12, 14, 16, garanție 3 și 4,
bugete sub designul complet (bugetele „Biletului complet” pentru 1-3 bilete
fizice, apoi 7, 12, 20, 40, 64 de bilete): 155 de configurații. Pe fiecare,
wheel-ul de producție dinainte de etapa nouă, apoi cu ea, pe același pool
canonic. Probabilitățile sunt exacte, prin enumerarea intersecțiilor cu
pool-ul (`covering.probability`).

O primă explorare, fără constrângeri (recoacere pe ținta singură, două
porniri, 15.000 de mutări), a arătat loc de câștig în 35 din 59 de
configurații, până la 2,8% relativ, dar jumătate din aceste designuri
pierdeau puțin la alt prag. Regula pentru producție a fost fixată apoi: nimic
nu scade.

## Etapa nouă

`covering/anneal_hits.py`: pornește de la biletele construite de hitcover și
schimburile de profil, mută câte un număr al unui bilet și acceptă, cu
temperatura în scădere, și mutări care pierd puțin, ca să iasă din optimul
local al schimburilor una câte una. Obiectivul e șansa exactă pe geometria
jocului, cu ținta (garanția) întâi; orice scădere sub biletele de plecare, la
orice prag afișat sau la acoperirea garanției, e penalizată de o sută de ori
în căutare. Buget fix de mutări (20.000, mai puține la bugete mari), generator
seedat din geometrie, rezultat memorat pe geometrie: același pe toți pașii
walk-forward și pe toate stațiile.

Intră în producție numai certificat din nou, exact: același număr de bilete
distincte, numerele jucate rămân jucate, P(cel puțin t pe un bilet) nu scade
la niciun t de la 3 la numărul de numere extrase, crește strict la cel puțin
unul, acoperirea clasică a garanției nu scade. Altfel biletele rămân.

Se aplică pe calea motorului la bugete de 1..64 de bilete (alegerea automată,
`hitcover`) și pe „Bilet complet” când garanția nu e completă în buget.
`greedy` cerut explicit rămâne construcția de referință. Cheia WF primește
sufixul `sa1` pe aceste bugete; worker v14.

## Rezultat

| | Configurații | Bilete schimbate | Câștig la țintă | Maxim | Mediana celor schimbate |
|---|---:|---:|---:|---:|---:|
| 6/49 | 49 | 26 | 22 | 1,59% | 0,50% |
| 5/40 | 49 | 36 | 34 | 1,85% | 0,09% |
| Joker Urna 1 | 57 | 36 | 27 | 2,76% | 0,44% |
| Total | 155 | 98 | 83 | 2,76% | 0,20% |

Câștigurile sunt relative, la pragul țintă. Nicio configurație nu scade la
vreun prag afișat și nicio acoperire de garanție nu scade. Timp: 1,6 s în
medie pe geometrie, cel mult 3,9 s, apoi din memorie.

Biletele fizice din pool 16, cele pe care le joacă utilizatorul:

| Joc | Bilete | Variante | 3+ înainte | 3+ după | 4+ înainte | 4+ după |
|---|---:|---:|---:|---:|---:|---:|
| 6/49, garanție 3 | 1 | 3 | 5,466% | 5,530% | 0,296% | 0,296% |
| 6/49, garanție 3 | 3 | 9 | 13,431% | 13,554% | 0,880% | 0,880% |
| 6/49, garanție 4 | 3 | 9 | 13,431% | 13,664% | 0,880% | 0,881% |
| 5/40, garanție 4 | 1 | 4 | 6,714% | 7,007% | 0,310% | 0,314% |
| 5/40, garanție 4 | 3 | 12 | 17,599% | 17,934% | 0,936% | 0,937% |
| Joker, garanție 3 | 3 | 6 | 3,840% | 3,888% | 0,099% | 0,099% |
| Joker, garanție 4 | 2 | 4 | 2,508% | 2,608% | 0,065% | 0,066% |

Cele mai mari câștiguri pe calea motorului: Joker pool 14, 40 de bilete, 3+
15,15% → 15,57%; Joker pool 16, 20 de bilete, 10,68% → 10,88%; 5/40 pool 14,
7 bilete, 4+ 0,537% → 0,547%.

## Ce înseamnă

Cu ~105 extrageri pe an, +1% relativ la o rată de 13% înseamnă cam o
extragere câștigătoare în plus la șapte ani. E un câștig real, exact și
gratuit, dar mic. Nu există alt loc în cod unde hiturile să poată crește:
rata pool-ului e a hazardului, iar media variantelor câștigătoare și șansa
marelui premiu nu depind de așezare.

## Ce nu acoperă

- Pool-uri sub 10 și bugete peste 64 de bilete (acolo lucrează `budget_climb`).
- Designurile complete (fără plafon): acolo hiturile pe bilete sunt cele ale
  pool-ului, deci maximul.
- Dominanța pe numărători la fiecare mărime a intersecției, regula etapelor
  anterioare: etapa nouă certifică pe probabilitățile geometriei jocului, la
  pragurile afișate. O numărătoare la 1-2 hituri poate scădea.
