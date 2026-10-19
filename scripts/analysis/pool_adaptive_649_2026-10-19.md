# Metode adaptive noi pentru poolurile 9-16 la 6/49 - 2026-10-19

Selectorul nu a trecut poarta comparatiei fixate in ambele tari. Selectorul nu si-a demonstrat superioritatea fata de markov_lag2 pe obiectivul agregat stabilit.

Banca fixata: **32 algoritmi** = 16 definitii in 3 familii noi + 16 combinatii de rang 50% cu Markov. Markov si frequency sunt controale separate. Definitii cu diferenta pozitiva, Holm < 0,05 in ambele tari si fara fallback pe intervalul final: **0**.

Obiectivul primar este media celor opt indicatori 4+ pentru poolurile 9..16, cu limita de consecutive 2, pe ultimele 20% din zile. Sunt hituri in pool, nu pe bilet. Cele opt pooluri suprapuse nu sunt opt probe independente.

## Selectorul cauzal, intervalul primar

| Tara | Interval | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | CI95 diferenta pp |
|---|---|---:|---:|---:|---:|---:|---|
| Romania | 2021-10-14..2026-10-18 | 529 | 3.355388% | 3.827977% | -0.472590 | 0.763640 | [-1.813054; +0.858902] |
| Bulgaria | 2025-06-05..2026-10-15 | 142 | 4.665493% | 2.200704% | +2.464789 | 0.066370 | [-0.528169; +5.545775] |

## Romania: fiecare dimensiune de pool, limita 2

Aceste descompuneri sunt descriptive; nu selecteaza alta formula sau alt pool.

| Pool | Selector 4+ | Markov 4+ | Selector % | Markov % | Diferenta pp | Hazard 4+ |
|---:|---:|---:|---:|---:|---:|---:|
| 9 | 4/529 | 4/529 | 0.7561% | 0.7561% | +0.0000 | 0.7395% |
| 10 | 8/529 | 6/529 | 1.5123% | 1.1342% | +0.3781 | 1.1846% |
| 11 | 9/529 | 8/529 | 1.7013% | 1.5123% | +0.1890 | 1.7878% |
| 12 | 13/529 | 12/529 | 2.4575% | 2.2684% | +0.1890 | 2.5737% |
| 13 | 19/529 | 19/529 | 3.5917% | 3.5917% | +0.0000 | 3.5648% |
| 14 | 22/529 | 30/529 | 4.1588% | 5.6711% | -1.5123 | 4.7817% |
| 15 | 30/529 | 38/529 | 5.6711% | 7.1834% | -1.5123 | 6.2420% |
| 16 | 37/529 | 45/529 | 6.9943% | 8.5066% | -1.5123 | 7.9600% |

## Bulgaria: fiecare dimensiune de pool, limita 2

Aceste descompuneri sunt descriptive; nu selecteaza alta formula sau alt pool.

| Pool | Selector 4+ | Markov 4+ | Selector % | Markov % | Diferenta pp | Hazard 4+ |
|---:|---:|---:|---:|---:|---:|---:|
| 9 | 2/142 | 1/142 | 1.4085% | 0.7042% | +0.7042 | 0.7395% |
| 10 | 2/142 | 1/142 | 1.4085% | 0.7042% | +0.7042 | 1.1846% |
| 11 | 3/142 | 1/142 | 2.1127% | 0.7042% | +1.4085 | 1.7878% |
| 12 | 6/142 | 2/142 | 4.2254% | 1.4085% | +2.8169 | 2.5737% |
| 13 | 8/142 | 3/142 | 5.6338% | 2.1127% | +3.5211 | 3.5648% |
| 14 | 9/142 | 5/142 | 6.3380% | 3.5211% | +2.8169 | 4.7817% |
| 15 | 11/142 | 6/142 | 7.7465% | 4.2254% | +3.5211 | 6.2420% |
| 16 | 12/142 | 6/142 | 8.4507% | 4.2254% | +4.2254 | 7.9600% |

## Indicatori secundari pe acelasi traseu

Mediile sunt peste cele opt pooluri. Top-N brut foloseste exact aceleasi formule alese cu limita 2; nu exista o a doua selectie pe rezultatele brute.

| Tara | Selectie | Metoda | 4+ | 3+ | Media hiturilor | Zero hituri |
|---|---|---|---:|---:|---:|---:|
| Romania | limita 2 | Selector | 3.355388% | 18.241966% | 1.545132 | 17.107750% |
| Romania | limita 2 | Markov | 3.827977% | 17.344045% | 1.511815 | 16.824197% |
| Romania | top-N brut | Selector | 3.662571% | 18.100189% | 1.542297 | 16.776938% |
| Romania | top-N brut | Markov | 3.946125% | 17.438563% | 1.526701 | 16.422495% |
| Bulgaria | limita 2 | Selector | 4.665493% | 15.933099% | 1.491197 | 20.070423% |
| Bulgaria | limita 2 | Markov | 2.200704% | 12.764085% | 1.444542 | 16.109155% |
| Bulgaria | top-N brut | Selector | 5.105634% | 15.669014% | 1.501761 | 19.190141% |
| Bulgaria | top-N brut | Markov | 2.552817% | 14.788732% | 1.458627 | 16.725352% |

## Alegerile fixate din fiecare fereastra trecuta

Originea 70% produce numai diagnosticul 70..80%; originile 80% si 90% produc intervalul primar 80..100%. Alegerea se tine fixa pana la originea urmatoare.

| Tara | Origine | Interval evaluat | Formula aleasa | Eligibile | Fallback ulterior |
|---|---:|---|---|---:|---:|
| Romania | 70% | 2019-01-24..2021-10-10 | lag2_pair_contrast_k100_a25 | 18 | 0 |
| Romania | 80% | 2021-10-14..2024-04-21 | lag2_pair_contrast_k20_a50 | 34 | 0 |
| Romania | 90% | 2024-04-25..2026-10-18 | frequency | 34 | 0 |
| Bulgaria | 70% | 2024-09-29..2025-06-01 | lag2_pair_contrast_k20_a25 | 18 | 0 |
| Bulgaria | 80% | 2025-06-05..2026-02-05 | mix_markov50__rank_bilinear_markov_ewma_hl30_g3_all | 34 | 0 |
| Bulgaria | 90% | 2026-02-08..2026-10-15 | mix_markov50__rank_bilinear_markov_ewma_hl30_g3_all | 34 | 0 |

## Algoritmii cu definitie fixa: diagnostic retrospectiv

Tabelul pastreaza ordinea ID-urilor, fara a proclama castigatorul dupa rezultatul final. Scorurile algoritmilor online se pot schimba folosind trecutul; definitiile si parametrii lor au fost fixati in manifest. Holm include toti cei 32 algoritmi separat in Romania si Bulgaria; controalele sunt excluse. Diferentele sunt fata de Markov, in puncte procentuale.

| Algoritm | RO 4+ | Delta RO pp | Holm RO | BG 4+ | Delta BG pp | Holm BG | Fallback RO/BG |
|---|---:|---:|---:|---:|---:|---:|---:|
| bocpd_daily_h128_k100 | 3.355388% | -0.472590 | 1.000000 | 3.697183% | +1.496479 | 1.000000 | 0/0 |
| bocpd_daily_h128_k20 | 3.709830% | -0.118147 | 1.000000 | 4.577465% | +2.376761 | 1.000000 | 0/0 |
| bocpd_daily_h512_k100 | 3.284499% | -0.543478 | 1.000000 | 3.257042% | +1.056338 | 1.000000 | 0/0 |
| bocpd_daily_h512_k20 | 3.213611% | -0.614367 | 1.000000 | 4.665493% | +2.464789 | 1.000000 | 0/0 |
| lag2_pair_contrast_k100_a25 | 3.615312% | -0.212665 | 1.000000 | 1.496479% | -0.704225 | 1.000000 | 0/0 |
| lag2_pair_contrast_k100_a50 | 3.473535% | -0.354442 | 1.000000 | 2.640845% | +0.440141 | 1.000000 | 0/0 |
| lag2_pair_contrast_k20_a25 | 3.615312% | -0.212665 | 1.000000 | 1.496479% | -0.704225 | 1.000000 | 0/0 |
| lag2_pair_contrast_k20_a50 | 3.591682% | -0.236295 | 1.000000 | 2.288732% | +0.088028 | 1.000000 | 0/0 |
| mix_markov50__bocpd_daily_h128_k100 | 3.757089% | -0.070888 | 1.000000 | 2.992958% | +0.792254 | 1.000000 | 0/0 |
| mix_markov50__bocpd_daily_h128_k20 | 3.095463% | -0.732514 | 1.000000 | 2.992958% | +0.792254 | 1.000000 | 0/0 |
| mix_markov50__bocpd_daily_h512_k100 | 3.379017% | -0.448960 | 1.000000 | 3.169014% | +0.968310 | 1.000000 | 0/0 |
| mix_markov50__bocpd_daily_h512_k20 | 3.851607% | +0.023629 | 1.000000 | 3.080986% | +0.880282 | 1.000000 | 0/0 |
| mix_markov50__lag2_pair_contrast_k100_a25 | 3.662571% | -0.165406 | 1.000000 | 2.200704% | +0.000000 | 1.000000 | 0/0 |
| mix_markov50__lag2_pair_contrast_k100_a50 | 3.615312% | -0.212665 | 1.000000 | 1.320423% | -0.880282 | 1.000000 | 0/0 |
| mix_markov50__lag2_pair_contrast_k20_a25 | 3.686200% | -0.141777 | 1.000000 | 2.288732% | +0.088028 | 1.000000 | 0/0 |
| mix_markov50__lag2_pair_contrast_k20_a50 | 3.473535% | -0.354442 | 1.000000 | 1.496479% | -0.704225 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_all | 3.851607% | +0.023629 | 1.000000 | 4.665493% | +2.464789 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_h256 | 4.442344% | +0.614367 | 1.000000 | 2.816901% | +0.616197 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_all | 3.426276% | -0.401701 | 1.000000 | 2.728873% | +0.528169 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_h256 | 4.111531% | +0.283554 | 1.000000 | 2.640845% | +0.440141 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_all | 3.615312% | -0.212665 | 1.000000 | 3.873239% | +1.672535 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_h256 | 3.260870% | -0.567108 | 1.000000 | 3.345070% | +1.144366 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_all | 3.898866% | +0.070888 | 1.000000 | 1.760563% | -0.440141 | 1.000000 | 0/0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_h256 | 3.024575% | -0.803403 | 1.000000 | 1.056338% | -1.144366 | 1.000000 | 0/0 |
| rank_bilinear_markov_ewma_hl30_g3_all | 3.993384% | +0.165406 | 1.000000 | 4.665493% | +2.464789 | 1.000000 | 0/0 |
| rank_bilinear_markov_ewma_hl30_g3_h256 | 4.655009% | +0.827032 | 1.000000 | 2.552817% | +0.352113 | 1.000000 | 0/0 |
| rank_bilinear_markov_ewma_hl30_g5_all | 3.142722% | -0.685255 | 1.000000 | 2.992958% | +0.792254 | 1.000000 | 0/0 |
| rank_bilinear_markov_ewma_hl30_g5_h256 | 3.851607% | +0.023629 | 1.000000 | 1.056338% | -1.144366 | 1.000000 | 0/0 |
| rank_bilinear_markov_frequency_g3_all | 3.449905% | -0.378072 | 1.000000 | 4.225352% | +2.024648 | 1.000000 | 0/0 |
| rank_bilinear_markov_frequency_g3_h256 | 3.568053% | -0.259924 | 1.000000 | 3.521127% | +1.320423 | 1.000000 | 0/0 |
| rank_bilinear_markov_frequency_g5_all | 3.189981% | -0.637996 | 1.000000 | 4.665493% | +2.464789 | 1.000000 | 0/0 |
| rank_bilinear_markov_frequency_g5_h256 | 3.048204% | -0.779773 | 1.000000 | 5.193662% | +2.992958 | 1.000000 | 0/0 |

## Familii, protocol si limite

- Patru BOCPD: posteriori separati pe bila, hazard zilnic 1/128 sau 1/512, prior beta de concentratie 20 sau 100 si medie 6/49. Sunt pastrate toate lungimile de regim, fara cap sau pruning. Doua extrageri in aceeasi zi folosesc o singura actualizare Beta-Binomial dupa ambele predictii.
- Patru contraste lag2: interactiunea a doi antecedenti din cele 15 perechi ale extragerii sursa, diferenta simetrica (q11+q00)-(q10+q01), shrinkage 20/100 spre marginala trecuta a raspunsului, amplitudine de rang 0,25/0,5 peste Markov. Lag2 numara randuri; tinta oricarei tranzitii folosite la antrenare are data strict anterioara prognozei.
- Opt calibratoare bilineare: rang Markov x rang frequency/EWMA30, grila 3/5 noduri, memorie totala sau half-life 256 de extrageri tinta, prior 100 observatii la fiecare nod spre 6/49. Sunt modele proprii de scoring, nu implementari ale algoritmului BBQ si nu probabilitati comune calibrate pentru sase bile extrase fara inlocuire.
- BOCPD si contrastele invata din istoricul complet de la randul zero. Calibratoarele invata numai din fluxul de scoruri disponibil de la 50% din zile (si minimum 256 de randuri anterioare). Toata ziua este prezisa inainte de actualizarea cu oricare extragere a ei. Cele 16 blenduri aditionale dau ponderi egale rangului noii metode si rangului Markov.
- In fiecare origine 70/80/90%, selectorul compara numai ultimele 20% din zilele CSV-ului complet. Egalitatile prefera Markov, apoi complexitatea declarata mai mica, apoi ID. O formula este exclusa daca fereastra trecuta contine fallback propriu sau al unui parinte efectiv consumat ori egalitati in cel putin 50% din pasi la oricare taietura top-K BRUTA. Frequency folosit numai ca rezerva nu invalideaza un BOCPD utilizabil. Poarta egalitatilor nu diagnosticheaza toate pozitiile accesate dupa limita de consecutive. Invaliditatea viitoare se numara, fara reselectie.
- Scorurile sunt echivariante la relabeling, fara label jitter; departajarea top-K canonica si limita numerica de consecutive apartin evaluatorului si nu sunt invariante la o permutare arbitrara a etichetelor.
- 99.999 de permutari paired pe zile, cu acelasi semn pentru toate poolurile si extragerile zilei; intervale bootstrap pe zile. Aceste teste presupun interschimbabilitatea etichetelor metodelor la nivelul zilei. Dependenta seriala si selectia adaptiva pot limita inferenta. P-urile sunt conditionate pe traseele observate, fara reluarea intregului selector si a cautarii sub nul.
- Romania si Bulgaria au fost cercetate in runde anterioare. Nu exista holdout virgin, validare prospectiva sau calibrare IID a intregii cautari. Rejucarea unor tinte IID cu scorurile dependente de istoric tinute fixe nu ar fi un nul valid pentru intregul pipeline si nu a fost folosita. Bulgaria aplica acelasi algoritm de selectie pe propriul trecut; nu transfera o singura formula romaneasca fixa.
- Referinta hipergeometrica este sansa unui pool uniform de aceeasi marime. Orice clasament retrospectiv al acestei banci nu demonstreaza optimalitatea globala a Markov, performanta viitoare sau necesitatea unei schimbari in productie.
- Scorurile parintilor sunt reutilizate numai cu provenienta si semnaturi identice de date/cod/versiuni si aliniere exacta a tintelor. Cache-urile studiilor vechi si fisierele protejate ale aplicatiei raman intacte.

Sursele primare inregistrate: [sursa primara 1](https://arxiv.org/abs/0710.3742), [sursa primara 2](https://ojs.aaai.org/index.php/AAAI/article/download/9602/9461). Sunt inspiratie pentru BOCPD si context pentru binning/calibrare; nu valideaza predictia loto sau formulele proprii de contrast si calibrare bilineara.

## Verificare si reproducere

Calculul pana la JSON: 56.09 s cu scoruri parinte deja in cache. Timpul nu include cautarile anterioare, validarea, exporturile ulterioare sau pornirea intregii aplicatii.

Validatorul independent: 12861 verificari, 0 esecuri, 2 arhive; verdict **trece**. Detalii: [pool_adaptive_649_2026-10-19_validation.json](pool_adaptive_649_2026-10-19_validation.json).

Suita completa in copia locala izolata: 3015 passed, 20 skipped, 272 warnings in 1464.59s (0:24:24).

Auditul aplicatiei: worker separat pentru toate cele 13 jocuri, UI HTTP 200, fisiere protejate intacte. CSV-urile sunt verificate integral: 512 de randuri ale candidatilor si 8048 de randuri din traseu. Dovezi: [pool_adaptive_649_2026-10-19_checks.json](pool_adaptive_649_2026-10-19_checks.json), [auditul aplicatiei](pool_adaptive_649_2026-10-19_application_audit.json).

Comenzile se ruleaza din radacina proiectului, cu versiunile din protocol. Rularea foloseste cache-urile vechi de scoruri, semnate si pastrate local; acestea nu sunt incluse in Git.

```powershell
python scripts/analysis/pool_adaptive_649_2026_10_19.py --register
python scripts/analysis/pool_adaptive_649_2026_10_19.py --run --workers 4
python -m scripts.analysis.validate_pool_adaptive_2026_10_19 --verify scripts/analysis/pool_adaptive_649_2026-10-19.json --output scripts/analysis/pool_adaptive_649_2026-10-19_validation.json
python scripts/analysis/render_pool_adaptive_2026_10_19.py
```
