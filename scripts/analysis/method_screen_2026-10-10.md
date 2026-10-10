# 1.002 metode noi față de liderul clasamentului, 2026-10-10

## Concluzie

1. **Nicio metodă nouă nu intră în aplicație.** Am testat 1.002 scoreri noi
   (840 la Joker Urna 2, unde familiile de co-apariție nu există) pe cele 16
   celule ale clasamentului: 15.870 de teste. Din dezvoltare am înghețat 48 de
   candidați. Pe confirmare, unul singur bate liderul cu p brut sub 0,05
   (0,031); după corecția Holm peste cei 48, p-ul ajustat e 1,00. Față de
   hazard, Holm minim e 0,84. Regula din protocol cerea Holm sub 0,05 în ambele
   comparații și replicare externă.
2. **Căutarea nu iese din zgomot.** Pe istorii pur aleatoare, aceeași căutare
   găsește în 14 din 16 celule un maxim de dezvoltare cel puțin la fel de bun
   (p-ul căutării între 0,18 și 1,00).

   Excepția e Joker Urna 1, pool 16: 4+ are p 0,010 și 3+ are p 0,055. Acolo
   candidații cad pe confirmare. Cel mai bun la 4+ pe dezvoltare,
   `anti_markov_w1000_lag4`, are 7,20% pe dezvoltare și doar 3,47% pe
   confirmare, sub hazard (4,68%) și sub lider (7,27%).
3. **Ratele liderilor sunt peste hazard în 15 din 16 celule pe confirmare.**
   Liderii au fost însă aleși pe un bench care include și perioada de
   confirmare, deci rata lor acolo nu e o validare. Comparația rămâne cea
   cerută: o metodă nouă trebuia să-i depășească pe extrageri pe care nu le-a
   văzut la alegere.
4. **Un singur semnal consecvent, prea slab ca să treacă.** Metoda e
   `ens_win100+winall+ewma0.01`, pe Joker Urna 1, pool 8, 3+. Combină prin rang
   mediu frecvența pe 100 de zile, frecvența pe tot istoricul și o EWMA lentă.
   - Dezvoltare: 4,68%.
   - Confirmare: 4,96%, față de 3,14% la lider și 3,27% hazard. Față de hazard
     p brut e 0,017, față de lider 0,031. A atins tinta în 20 de extrageri în
     care liderul a ratat-o, iar invers s-a întâmplat de 9 ori.
   - Extern (Austria, Belgia, Ungaria, EuroMillions): 477 de evenimente, față
     de 430 așteptate (p 0,010).

   După Holm nu trece (0,84 față de hazard, 1,00 față de lider). Din cele 36
   de replicări externe, 3 au p sub 0,05; din întâmplare s-ar aștepta 1,8.
   Metoda rămâne de urmărit pe extrageri viitoare și nu se promovează.

## Date

- Istoricul de la commit-ul `2a0d614`, cu extragerile din 08-10-2026, și
  `bench_results/folds.csv` versionat (liderii).
- Amprentele sha256 ale celor 13 CSV-uri și ale `folds.csv` sunt în
  `method_screen_2026-10-10.json`, câmpul `data_sha256`. Scriptul refuză să
  scrie rezultatul dacă se schimbă în timpul rulării. A rulat dintr-o copie
  separată a repo-ului, ca să nu fie atins de alte operații.

## Protocol

Fixat în antetul scriptului `method_screen_2026-10-10.py`, înainte de rulare.

- **Tăiere.** Scorul unei zile vede numai zilele anterioare. Primele 200 de
  zile sunt warmup, următoarele 70% dezvoltare, ultimele 30% confirmare.
- **Candidați.** 19 familii noi, toate echivariante: scorul urmează numărul, nu
  eticheta lui. Nu conțin filtre structurale. Exemple:
  - frecvență cu nucleu putere;
  - diferențe EWMA;
  - golul față de propriile goluri;
  - Markov pe fereastră, auto-tranziție;
  - co-apariție pe fereastră, completarea perechilor fierbinți, PageRank;
  - Holt, CUSUM, panta;
  - analog k-NN, hazard pe fereastră, spectru propriu;
  - ansambluri pe rang (366), ridge online (50).
- **Celule.** Ca în clasamentul aplicației:
  - 6/49 și Joker Urna 1, pe trei pool-uri, la 3+ și 4+;
  - 5/40 la 4+;
  - Urna 2, top-1.
- **Liderul celulei.** Câștigătorul deciziei din `folds.csv` la ținta celulei.
  Rulează ca în producție: `call_method` pe rândurile dinaintea zilei,
  `rank_by_score`, rezerva `frequency` la scor inutilizabil. Rezerva n-a fost
  folosită în nicio zi.
- **Selecție pe dezvoltare.** Doar candidați fără dependență de egalități și
  stabili pe ambele jumătăți. Primii 2 după z al evenimentului și primul după z
  al hiturilor (la Urna 2, primii 3). Selecția se îngheață.
- **Nul al căutării.** 200 de istorii sintetice uniforme, cu aceeași structură
  de zile, pe fiecare joc. Pe fiecare se reface tot ecranul.
- **Confirmare.** McNemar exact față de lider și binomial exact față de hazard,
  Holm separat peste cei 48. Replicare externă pentru 6/49 (șase 6/49 străine)
  și Joker Urna 1 (6/45 Austria, Belgia, Ungaria și EuroMillions 5/50).

## Rezultate pe celule

Rata liderului e dată pe dezvoltare și pe confirmare. „p căutare” compară
maximul z de dezvoltare cu aceeași căutare pe 200 de istorii aleatoare.

| Joc | Pool | Țintă | Lider | Lider dezv. / conf. | Hazard | Max z dezv. | p căutare | Cel mai bun nou, conf. |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 6/49 | 6 | 3+ | `cooc_last3` | 1,81% / 2,46% | 1,86% | 2,91 | 0,73 | 2,46% |
| 6/49 | 6 | 4+ | `dmd_forecast` | 0,12% / 0,55% | 0,10% | 2,63 | 1,00 | 0,14% |
| 6/49 | 11 | 3+ | `croston_interval` | 13,55% / 14,36% | 11,74% | 2,75 | 0,79 | 13,27% |
| 6/49 | 11 | 4+ | `markov_self_state` | 1,87% / 2,74% | 1,79% | 2,84 | 0,78 | 2,33% |
| 6/49 | 16 | 3+ | `ridge_pooled_feats` | 32,05% / 32,69% | 29,81% | 3,07 | 0,42 | 31,74% |
| 6/49 | 16 | 4+ | `markov_lag2` | 8,92% / 9,44% | 7,96% | 2,89 | 0,63 | 8,89% |
| 5/40 | 8 | 4+ | `hot_consistency` | 1,69% / 1,68% | 0,95% | 3,42 | 0,47 | 1,05% |
| 5/40 | 11 | 4+ | `hot_consistency` | 5,35% / 4,41% | 3,85% | 3,33 | 0,37 | 5,46% |
| 5/40 | 16 | 4+ | `pair_lift_last` | 15,67% / 19,54% | 16,03% | 2,77 | 0,76 | 17,02% |
| Joker U1 | 8 | 3+ | `theta_drift` | 4,25% / 3,14% | 3,27% | 3,71 | 0,18 | 4,96% |
| Joker U1 | 8 | 4+ | `rank_ensemble_core` | 0,29% / 0,50% | 0,22% | 4,04 | 0,32 | 0,33% |
| Joker U1 | 11 | 3+ | `pagerank_cooc` | 9,14% / 11,07% | 8,53% | 2,83 | 0,75 | 8,93% |
| Joker U1 | 11 | 4+ | `markov_pairs` | 1,15% / 1,49% | 0,96% | 2,68 | 0,93 | 0,99% |
| Joker U1 | 16 | 3+ | `cusum_burst` | 25,13% / 25,29% | 23,29% | 3,91 | 0,05 | 22,48% |
| Joker U1 | 16 | 4+ | `frequency` | 5,54% / 7,27% | 4,68% | 4,45 | 0,01 | 4,96% |
| Joker U2 | 1 | top-1 | `ridge_pooled_feats` | 5,47% / 7,27% | 5,00% | 2,41 | 0,98 | 6,94% |

Din cei 48 de candidați înghețați:
- 2 au o rată de confirmare peste lider, iar alți 2 sunt la egalitate cu el;
- 1 are p brut sub 0,05 față de lider și 3 față de hazard;
- din 36 de replicări externe, 3 au p sub 0,05.

Toate cifrele, pe fiecare candidat, sunt în JSON, la `frozen`.

## Verificări

- Pe 6/49, 5/40, Joker Urna 1 și Urna 2, înainte de rulare:
  - toți scorerii văd numai zilele anterioare: am schimbat extragerile unei
    zile, iar scorurile până la acea zi au rămas identice;
  - toți urmează numărul: după o redenumire a numerelor, scorul se mută odată
    cu numărul.
- O scurgere de date și o preferință pentru etichete, injectate intenționat,
  sunt prinse. `test_method_screen_2026_10_10.py` repetă aceste verificări pe
  o istorie sintetică.
- Două defecte găsite de verificarea de echivarianță, înainte de rulare.
  Asemănările din analogul k-NN și rangul din ansambluri despărțeau valori
  egale prin ordinea adunării. Ambele se rotunjesc acum la 9 zecimale.
- Rularea a durat 6.473 s pe 4 nuclee.

## Ce nu acoperă

- Rata pe bilete. Ecranul măsoară pool-ul, ca bench-ul. Nu aplică limita de
  consecutive, penalizarea sau restrângerea bazei.
- Pool-urile intermediare. Celulele folosesc trei mărimi pe joc, nu 6..16.
- Re-Bench-ul stației. Liderii vin din `folds.csv` versionat. Pe stație,
  clasamentul poate avea alt lider.

Reproducere: `python scripts/analysis/method_screen_2026-10-10.py`. Variabile
opționale: `METHOD_SCREEN_NULL`, `METHOD_SCREEN_WORKERS`, `METHOD_SCREEN_OUT`.
