# „Bilet complet” cu garanția completă: rearanjarea tuturor variantelor, 2026-10-12

## Concluzie

1. **Când garanția e completă înainte de a umple biletele, pragul-țintă nu se
   mai poate mișca.** Un cover complet pune orice grup de g numere din pool pe
   o variantă, deci P(cel puțin g pe o variantă) este exact rata pool-ului
   (hipergeometrică) în orice aranjare. Până azi locurile rămase se umpleau cu
   grupe de g+1 din același pool, iar schimburile de profil atingeau numai
   locurile rămase, cu baza garanției înghețată ca prefix.
2. **Pragul următor se poate ridica.** Etapa nouă
   (`covering.anneal_hits.improve_full_cover_variants`) recoace TOATE
   variantele, cu două schimbări față de recoacerea din 2026-10-11: pragul
   urmărit întâi e g+1, iar o mutare care ar strica acoperirea 100% e respinsă
   pe loc. Se ține minte cel mai bun design care trece certificarea, nu cel cu
   obiectivul penalizat maxim. Certificarea e cea comună: același număr de
   variante distincte, numerele jucate rămân jucate, nicio scădere la vreun
   prag de la 3 la numărul extras, creștere strictă la cel puțin unul,
   acoperirea garanției nu scade (aici rămâne 100%).
3. **Măsurat pe pool canonic (`bench_full_cover_anneal.py`):** 178 de
   configurații (6/49 cu garanția 3 și 4, 5/40 cu garanția 4, Joker Urna 1 cu
   garanția 3; pool 8..12; 2..10 bilete). Pârghia (baza completă înainte de
   buget) apare în 67; etapa se aplică în 48, cu câștig la g+1 în 39:

   | Celulă | Prag | Configurații | Cu câștig | Maxim | Mediana celor cu câștig | Timp |
   |---|---|---:|---:|---:|---:|---:|
   | 6/49, garanția 3 | 4+ | 33 | 18 | +6,02% | +2,98% | 0,9 s |
   | 6/49, garanția 4 | 5+ | 16 | 10 | +6,92% | +4,76% | 0,6 s |
   | 5/40, garanția 4 | 5+ | 7 | 0 | 0 | 0 | 0,6 s |
   | Joker Urna 1, garanția 3 | 4+ | 11 | 11 | +5,65% | +2,10% | 0,9 s |

   Exemple: 6/49, garanția 3, pool 10, 5 bilete (11 variante bază din 15):
   4+ 0,9885% → 1,0465% (+5,87%), 3+ neschimbat la 9,0271%, 5+ 0,0263% →
   0,0265%. 6/49, garanția 4, pool 10, 10 bilete: 5+ 0,0484% → 0,0517%
   (+6,92%). Joker, pool 9, 9 bilete: 4+ 0,2608% → 0,2755% (+5,65%).
   În toate cele 48 de aplicări niciun prag nu scade, acoperirea garanției
   rămâne 100%, iar acoperirea grupelor de g+1 crește.
4. **5/40 cu garanția 4: nimic.** 3+ și 4+ sunt la maxim, iar „5 din 5” pe o
   variantă de 5 cu 6 extrase nu a găsit nicio așezare certificată mai bună
   (0 din 7, cum a ieșit și în căutarea cu 3 seed-uri și 60.000 de mutări).
5. **Ce se schimbă în UI.** După rearanjare, primele `n_base` variante nu mai
   formează singure coverul; garanția o asigură toate variantele împreună.
   Rezultatul poartă `refined = True`, `guarantee_variants` = toate
   variantele, iar rezumatul spune „garanția e completă cu toate cele N,
   rearanjate pentru grupele de g+1: acoperite X%”. Fără pârghie (pool 16 cu
   1-3 bilete, de exemplu) nimic nu se schimbă. Construcția de referință
   (`LOTO_WHEEL_METHOD=greedy`) rămâne neatinsă.

## Limite

- Câștigul e geometric, pe extrageri uniforme: rata pool-ului nu se schimbă
  și nu există nicio afirmație predictivă.
- Pe pool-uri reale punctul de plecare depinde de scoruri (greedy-ul
  ordonează țintele după suma scorurilor), deci câștigul variază de la pas la
  pas; verificarea adversarială din ecranul de căutare a măsurat pe 25 de pași
  reali câștig în 23, mediana +4,47%.
- Rezultatul e memorat pe geometrie și pe variantele de plecare, în poziții;
  în „📜 Istoric hits” reluarea plătește 0,5-1,3 s la pașii cu altă pornire.
- Fără bump de cache: biletul fizic se calculează la cerere și nu intră în
  cheia WF sau în cache-ul worker-ului.

Reproducere: `python scripts/analysis/bench_full_cover_anneal.py` (3-4 min pe
2 procese) scrie `full_cover_anneal_2026-10-12.json`.
