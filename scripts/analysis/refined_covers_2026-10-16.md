# Covere complete rafinate pentru pragul de peste garanție, 2026-10-16

## Concluzie

1. **Calea fără plafon livra coverul complet neschimbat.** Pe 6/49, pool 16,
   garanția 4 (152 de variante, configurația implicită a utilizatorului),
   rafinările din proces (`higher_hits`, schimburile de profil) nu mută nimic:
   P(3+) și P(4+) sunt deja maximul pool-ului (29,81% și 7,96%), iar 5+ rămâne
   0,2514%. Singura mișcare măsurată era la 5/40 pool 16 g3 (4+ +0,002%).
2. **La pool 15 și 16 nu există o rearanjare găsibilă.** Recoacerea în C cu
   pragul g+1 urmărit întâi și acoperirea penalizată
   (`scripts/analysis/budget_anneal.c`, al 16-lea argument) nu găsește niciun
   alt cover complet de aceeași mărime cu mai mult la g+1, pe niciun joc:
   6 rulări × 3 milioane de mutări pe fiecare geometrie, plus 4 rulări × 10
   milioane cu reîncălziri pe C(16,6,4). Coverele de acolo sunt rigide: la
   C(16,6,4) cu 152 de blocuri, fiecare bilet are în medie ~12 grupe de 4
   acoperite numai de el, iar etapa din proces (`improve_full_cover_variants`)
   nu acceptă nicio mutare.
3. **Locul teoretic rămas pe configurația utilizatorului e mic și nefolosibil
   aici.** În C(16,6,4) cu 152 de blocuri, 64 de perechi de bilete au 5 numere
   comune: 848 de grupe de 5 acoperite din cel mult 912. Un alt cover complet
   de 152 fără aceste suprapuneri ar ridica 5+ cu cel mult ~7% relativ
   (0,2514% → ~0,269%), dar căutarea locală nu găsește unul; coverul de 152
   e el însuși rezultatul unei căutări de nivel arhivă La Jolla.
4. **La pool 9..14 câștigul e real și se livrează.** 12 covere rafinate,
   fiecare certificat față de wheel-ul de producție: același număr de bilete
   distincte, toate pozițiile jucate, garanția 100%, niciun prag de la 3 la
   numărul extras în scădere, câștig strict la g+1:

   | Joc | Pool | Garanție | Bilete | Prag | Înainte | După | Relativ |
   |---|---:|---:|---:|---|---:|---:|---:|
   | 6/49 | 10 | 3 | 10 | 4+ | 0,7754% | 0,7924% | +2,19% |
   | 6/49 | 12 | 3 | 15 | 4+ | 1,2378% | 1,2528% | +1,22% |
   | 6/49 | 13 | 3 | 21 | 4+ | 1,6129% | 1,7311% | +7,33% |
   | 6/49 | 14 | 3 | 25 | 4+ | 2,0120% | 2,0428% | +1,53% |
   | 5/40 | 9 | 3 | 12 | 4+ | 0,8171% | 0,8308% | +1,68% |
   | 5/40 | 10 | 3 | 17 | 4+ | 1,1474% | 1,1618% | +1,26% |
   | 5/40 | 12 | 3 | 29 | 4+ | 1,9612% | 2,0006% | +2,01% |
   | 5/40 | 9 | 4 | 30 | 5+ | 0,0263% | 0,0264% | +0,40% |
   | 5/40 | 12 | 4 | 113 | 5+ | 0,1013% | 0,1016% | +0,26% |
   | Joker | 9 | 3 | 12 | 4+ | 0,1840% | 0,1871% | +1,69% |
   | Joker | 10 | 3 | 17 | 4+ | 0,2605% | 0,2637% | +1,23% |
   | Joker | 12 | 3 | 29 | 4+ | 0,4456% | 0,4532% | +1,71% |

   La 6/49 pool 13 g3, și 5+ crește (0,0370% → 0,0387%). 3+ rămâne la maximul
   pool-ului peste tot; marele premiu nu depinde de aranjare.

## Livrare

- Fișierele: `covering_designs/refined/R_<v>_<pick>_<g>_<extrase>_<univers>.txt`,
  poziții 1..v (1 = numărul cel mai bine clasat). Poartă extrasele și
  universul, fiindcă același cover La Jolla `C_v_5_t` servește 5/40 (6
  extrase din 40) și Joker (5 din 45), iar optimul diferă. Fișierele La Jolla
  rămân neatinse; coverele rafinate sunt derivate din ele (CC BY 4.0,
  modificate prin rearanjare).
- `covering.refined_covers` încarcă fișierul (memorat pe data modificării),
  pune designul pe pozițiile clasamentului și îl acceptă numai certificat cu
  `covering.anneal_hits._certify` față de wheel-ul construit în proces.
  Se aplică numai fără plafon, cu universul jocului dat (calea motorului și
  ramura `bf1`, când bugetul cuprinde designul complet). Biletul fizic și
  bugetele cu plafon nu se schimbă.
- Cheia WF primește `|rc<amprenta>` pe ramurile `lajolla` și `bf1` ale
  geometriilor cu cover rafinat; worker `v17`.

## Limite

- Geometrie pură, extrageri uniforme: rata pool-ului nu se schimbă, nicio
  afirmație predictivă.
- Fără optimalitate: rafinarea e o căutare locală. Pool 11 și 15-16, ca și
  garanția 4 la 6/49, nu au dat nimic certificat; asta nu dovedește că nu
  există covere mai bune de aceeași mărime.

Reproducere: `python scripts/analysis/gen_refined_covers.py [mutări] [procese] [pool-uri]`
(gcc necesar; ~45 min pe 2 procese la 3M mutări, toate pool-urile). Cifrele
fiecărei rulări sunt în `refined_covers_2026-10-16.json`.
