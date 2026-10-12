# Designuri cu buget pentru 65+ bilete, 2026-10-12

## Concluzie

1. **Peste 64 de bilete, căutarea din proces lăsa loc.** `budget_climb`
   (12.000 de mutări, acceptare strict nedescrescătoare la fiecare prag și
   mărime a intersecției) se oprește într-un optim local. O recoacere lungă pe
   aceeași funcție exactă (`scripts/analysis/budget_anneal.c`, 3 milioane de
   mutări, ~50 s pe buget în C) găsește bilete cu P(4+ pe un bilet) mai mare
   la 6/49, pool 16, garanția 4: 100 de bilete 6,030% → 6,735% (+11,68%),
   110 bilete 6,401% → 7,315% (+14,29%), 112 bilete +15,56%. La 100 de bilete
   rezultatul e o împachetare perfectă: 1.500 de grupe de 4 acoperite, fără
   două bilete cu 4 numere comune.
2. **În Python nu se poate face în proces.** Aceeași căutare cu numpy costă
   ~295 µs pe mutare (de 20 de ori mai mult decât în C), adică ~15 minute pe
   geometrie; la 100.000 de mutări (sub 40 s) prinde doar 10-15% din câștig.
   De aceea designurile sunt calculate offline și livrate ca fișiere, cum
   sunt și coverele complete: `covering_designs/budget/H_<v>_<pick>_<g>.txt`,
   o secțiune `B n` cu n bilete pe poziții pentru fiecare buget.
3. **Producția le certifică la fiecare apel.** `covering.budget_designs`
   pune designul pe pozițiile clasamentului (1 = numărul cel mai bine clasat)
   și îl acceptă numai cu regula etapei de recoacere: același număr de bilete
   distincte, numerele jucate rămân jucate, P(cel puțin t pe un bilet) nu
   scade la niciun prag de la 3 la numărul extras, crește strict la cel puțin
   unul, acoperirea garanției nu scade. Altfel rămâne wheel-ul din proces.
   Costul în proces: citirea fișierului o dată (memorată pe data modificării)
   și două calcule exacte de probabilități, ~2 ms.
4. **Livrat: 6/49, garanția 4, pool 13-16, toate cele 194 de bugete** între 65
   și designul complet (exclusiv), fiecare certificat față de wheel-ul de
   producție pe pool canonic:

   | Pool | Bugete | Design complet | 4+ mediana | 4+ maxim | ≥ 5% | ≥ 2% |
   |---:|---|---:|---:|---:|---:|---:|
   | 16 | 65..171 (107) | 172 | +7,25% | +15,56% (112 bilete) | 62 | 99 |
   | 15 | 65..129 (65) | 130 | +3,63% | +9,61% (69 bilete) | 20 | 59 |
   | 14 | 65..85 (21) | 86 | +5,48% | +7,80% (82 bilete) | 15 | 21 |
   | 13 | 65 (1) | 66 | +2,52% | +2,52% | 0 | 1 |

   Câteva bugete, P(4+ pe un bilet) producție → design, apoi 5+ și grupele
   de 4 acoperite din 1.820 (pool 16):

   | Pool | Bilete | 4+ înainte | 4+ după | Relativ | 5+ | Grupe de 4 |
   |---:|---:|---:|---:|---:|---|---|
   | 16 | 65 | 4,611% | 4,693% | +1,79% | 0,1192% → 0,1199% | 935 → 958 |
   | 16 | 80 | 5,314% | 5,579% | +4,99% | 0,1459% → 0,1482% | 1120 → 1200 |
   | 16 | 100 | 6,030% | 6,735% | +11,68% | 0,1798% → 0,1852% | 1309 → 1500 |
   | 16 | 110 | 6,401% | 7,315% | +14,29% | 0,1967% → 0,2037% | 1407 → 1650 |
   | 16 | 120 | 6,687% | 7,522% | +12,48% | 0,2131% → 0,2195% | 1483 → 1704 |
   | 16 | 130 | 6,959% | 7,635% | +9,71% | 0,2292% → 0,2346% | 1555 → 1734 |
   | 16 | 150 | 7,379% | 7,616% | +3,22% | 0,2607% → 0,2609% | 1666 → 1729 |
   | 16 | 171 | 7,733% | 7,858% | +1,61% | 0,2929% → 0,2930% | 1760 → 1793 |
   | 15 | 80 | 4,862% | 5,216% | +7,28% | 0,1430% → 0,1456% | 1021 → 1110 |
   | 14 | 75 | 4,229% | 4,514% | +6,74% | 0,1315% → 0,1318% | 871 → 938 |

   3+ e la maximul pool-ului (coverul de 3 e complet) și rămâne acolo; 6 din
   6 nu depinde de aranjare. Niciun prag nu scade în niciunul din cele 194 de
   designuri, iar numărul grupelor de 4 acoperite nu scade.
5. **Bilet de 5 (5/40 și Joker Urna 1): măsurat, nelivrat.** Sondă pe pool
   13-16, garanția 4, la un sfert, jumătate și trei sferturi din drumul până
   la designul complet (24 de bugete, 3M mutări): la bugetele mici și medii
   producția e deja o împachetare perfectă a grupelor de 4 (0 → +0,1%),
   iar lângă designul complet câștigul e de cel mult +2,6% (Joker pool 16,
   328 de bilete: 4,058% → 4,165%). Ar însemna 896 de designuri pe joc
   pentru un câștig mic; rămân pe `budget_climb`.
6. **Calea 1..64 de bilete nu se schimbă.** Varianta „ține minte cel mai bun
   design certificat” a recoacerii existente, măsurată pe cele 155 de
   configurații ale bench-ului din 2026-10-11, câștigă în 3 (6/49 pool 12,
   garanția 4, 40 de bilete: +1,85%; pool 12 g3 9 bilete: +0,62%; pool 16 g3
   7 bilete: +0,05%) și nu pierde nicăieri; nu merită invalidarea cache-ului
   WF pe toate bugetele de 1..64 de bilete (~90 de minute pe cheie).

## Protocol

- Punct de plecare: wheel-ul de producție pe pool canonic 1..v cu scoruri
  v..1 (`generate_wheel("hitcover", ..., draw_n=6, max_num=49)`: greedy,
  schimburi de profil, `budget_climb`). Două rulări în C din acest start:
  seed 1 cu 1M mutări și seed 2 cu 3M mutări (parametri: mutări țintite 0,5,
  temperatura inițială 0,05·P(țintă), raport 1000, penalizare 100, un ciclu);
  rămâne cea mai bună certificată (66 din 107 la pool 16 vin din rularea de
  3M). Cost total în C: ~3 ore CPU, pe 3 procese.
- Certificarea de generare e aceeași cu cea de producție
  (`covering.anneal_hits._certify`). Loader-ul validează fiecare secțiune:
  exact n bilete distincte, pozițiile 1..v, toate pozițiile jucate; o
  secțiune invalidă e sărită, iar fără secțiune cheia WF nu se schimbă.
- Cheia WF primește `bd<amprenta secțiunii>` după `bc1`, deci validarea pe
  istoric a unui buget de 65+ se recalculează o dată; worker `v15`.
  Walk-forward-ul plătește căutarea din proces și certificarea la fiecare
  pas, ca până acum (~5 s la primul apel pe geometrie, apoi memorat).

## Limite

- Geometrie pură, extrageri uniforme: rata pool-ului nu se schimbă și nu
  există nicio afirmație predictivă. Media biletelor câștigătoare la același
  număr de bilete e aceeași în orice aranjare; crește doar șansa de „cel
  puțin un 4+”.
- Designurile sunt certificate față de wheel-ul de producție de pe pool
  canonic. Pe un pool real, producția dă același wheel pozițional (pornirea
  `budget_climb` e pe poziții), iar certificarea de la apel garantează că
  designul nu intră dacă acolo ar pierde la vreun prag.
- Nu se afirmă optimalitate: la 65 de bilete plafonul grupelor de 4 e 975
  și designul are 958; o căutare mai lungă sau altă metodă pot găsi mai mult.

Reproducere: `python scripts/analysis/gen_budget_designs.py [procese] [mutări] [seed-uri]`
(gcc necesar; rezultatele parțiale se reiau din `budget_designs_work/`).
Cifrele fiecărui buget sunt în `budget_designs_2026-10-12.json`.
