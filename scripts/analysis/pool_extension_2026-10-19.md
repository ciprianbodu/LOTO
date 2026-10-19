# Cautare extinsa: Loto 5/40 pool 9-15 si Joker Urna 1 pool 9-19

32 de definitii noi si combinatii cu Markov pentru fiecare joc, evaluate separat. Sunt hituri in pool, nu castiguri sau hituri garantate pe bilet. Istoricele sunt reutilizate retrospectiv; rezultatele nu sunt dovezi prospective.

## Selectorul ales numai din trecut

Ratele de mai jos sunt media indicatorilor 4+ peste toate poolurile cerute in joc, pe ultimele 20% din zile, cu limita de consecutive 2. Fiecare joc are propria familie de pooluri; nu sunt observatii independente.

| Joc | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | Holm2 | CI95 diferenta pp |
|---|---:|---:|---:|---:|---:|---:|---|
| Loto 5/40 | 361 | 6.133755% | 5.856747% | +0.277008 | 0.424950 | 0.849900 | [-2.240954; +2.817112] |
| Joker Urna 1 | 446 | 3.872809% | 4.810436% | -0.937627 | 0.888300 | 0.888300 | [-2.472142; +0.573365] |

Holm2 corecteaza cele doua comparatii primare. Un prag trecut aici este un semnal retrospectiv conditionat pe traseele observate; nu promoveaza automat metoda si nu demonstreaza avantaj viitor.

## Loto 5/40

Interval: 2023-05-18..2026-10-18. Geometrie: 6 numere extrase din 40, bilet de 5. La 5/40 se folosesc TOATE cele sase numere extrase; la Joker numai Urna 1.

Poarta selectorului fata de Markov, dupa Holm2: **netrecuta**. Definitii fixe cu diferenta pozitiva, Holm64<0,05 si fara fallback primar: **0**. Configuratia scorarii in productie ramane neschimbata.

| Pool | Selector 4+ | Markov 4+ | Selector% | Markov% | Referinta aleatoare 4+ | Metoda din configuratia inghetata | Acea metoda 4+ |
|---:|---:|---:|---:|---:|---:|---|---:|
| 9 | 5/361 | 8/361 | 1.3850% | 2.2161% | 1.6304% | hot_consistency | 9/361 (2.4931%) |
| 10 | 6/361 | 9/361 | 1.6620% | 2.4931% | 2.5823% | cusum_burst | 14/361 (3.8781%) |
| 11 | 15/361 | 12/361 | 4.1551% | 3.3241% | 3.8516% | hot_consistency | 18/361 (4.9861%) |
| 12 | 21/361 | 17/361 | 5.8172% | 4.7091% | 5.4765% | hot_consistency | 23/361 (6.3712%) |
| 13 | 26/361 | 26/361 | 7.2022% | 7.2022% | 7.4883% | online_logit_sgd | 29/361 (8.0332%) |
| 14 | 37/361 | 33/361 | 10.2493% | 9.1413% | 9.9099% | pair_lift_last | 38/361 (10.5263%) |
| 15 | 45/361 | 43/361 | 12.4654% | 11.9114% | 12.7549% | pair_lift_last | 50/361 (13.8504%) |

Metodele configuratiei au fost alese pe istoricul complet reutilizat. Coloana lor este DOAR diagnostic: aceleasi reguli topN/consecutive 2, fara penalizari sau restrangerea bazei. Nu este o reluare a selectiei istorice de productie si nici o validare independenta. Referinta aleatoare este hipergeometrica; aceasta runda testeaza primar fata de Markov.

### Indicatori secundari pe acelasi traseu

| Selectie | Metoda | 3+ | 4+ | 5+ | Media hiturilor | Zero hituri |
|---|---|---:|---:|---:|---:|---:|
| limita2 | Selector | 23.941433% | 6.133755% | 0.791452% | 1.767313 | 12.623664% |
| limita2 | Markov | 22.041947% | 5.856747% | 0.672734% | 1.747131 | 10.961615% |
| limita2 | Configuratie (diagnostic) | 25.999209% | 7.162643% | 1.028888% | 1.859517 | 9.616146% |
| topN brut | Selector | 23.268698% | 6.252473% | 0.672734% | 1.749110 | 13.019391% |
| topN brut | Markov | 22.833399% | 6.094183% | 0.554017% | 1.760190 | 10.486743% |
| topN brut | Configuratie (diagnostic) | 25.484765% | 7.993668% | 0.831025% | 1.856747 | 9.893154% |

### Alegeri pe ferestrele trecute

| Origine | Interval evaluat | Definitie aleasa | Eligibile | Fallback ulterior |
|---:|---|---|---:|---:|
| 70% | 2020-09-20..2023-05-14 | lag2_pair_contrast_k20_a50 | 18 | 0 |
| 80% | 2023-05-18..2025-01-30 | mix_markov50__rank_bilinear_markov_frequency_g3_all | 34 | 0 |
| 90% | 2025-02-02..2026-10-18 | mix_markov50__rank_bilinear_markov_frequency_g3_h256 | 33 | 0 |

### Toate definitiile fixe, fara alegerea unui castigator dupa rezultat

Holm32 corecteaza toate definitiile din joc, iar Holm64 toate perechile definitie-joc din aceasta extensie. P-urile sunt fata de Markov, pentru obiectivul agregat 4+.

| Definitie | Rata 4+ | Diferenta pp | p brut | Holm32 | Holm64 | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| bocpd_daily_h128_k100 | 5.738029% | -0.118718 | 0.547490 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h128_k20 | 5.381876% | -0.474871 | 0.647930 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h512_k100 | 5.540166% | -0.316581 | 0.605390 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h512_k20 | 4.748714% | -1.108033 | 0.807210 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k100_a25 | 6.094183% | +0.237436 | 0.381690 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k100_a50 | 6.054610% | +0.197863 | 0.432600 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k20_a25 | 6.133755% | +0.277008 | 0.367000 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k20_a50 | 5.658884% | -0.197863 | 0.600520 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h128_k100 | 5.698457% | -0.158290 | 0.565390 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h128_k20 | 5.896320% | +0.039573 | 0.498840 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h512_k100 | 5.025722% | -0.831025 | 0.790420 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h512_k20 | 5.381876% | -0.474871 | 0.690960 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k100_a25 | 5.738029% | -0.118718 | 0.702400 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k100_a50 | 6.133755% | +0.277008 | 0.366270 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k20_a25 | 6.331619% | +0.474871 | 0.190850 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k20_a50 | 6.331619% | +0.474871 | 0.268890 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_all | 5.777602% | -0.079145 | 0.537200 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_h256 | 5.144440% | -0.712307 | 0.727690 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_all | 5.658884% | -0.197863 | 0.571370 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_h256 | 5.025722% | -0.831025 | 0.757070 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_all | 5.342303% | -0.514444 | 0.671760 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_h256 | 7.123071% | +1.266324 | 0.186440 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_all | 6.133755% | +0.277008 | 0.418580 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_h256 | 6.212901% | +0.356154 | 0.405700 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g3_all | 4.075979% | -1.780768 | 0.913250 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g3_h256 | 6.173328% | +0.316581 | 0.424360 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g5_all | 6.133755% | +0.277008 | 0.427870 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g5_h256 | 6.806490% | +0.949743 | 0.264750 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g3_all | 5.738029% | -0.118718 | 0.546740 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g3_h256 | 6.569054% | +0.712307 | 0.321340 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g5_all | 5.579739% | -0.277008 | 0.593290 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g5_h256 | 4.511278% | -1.345469 | 0.856310 | 1.000000 | 1.000000 | 0 |

## Joker Urna 1

Interval: 2022-08-04..2026-10-18. Geometrie: 5 numere extrase din 45, bilet de 5. La 5/40 se folosesc TOATE cele sase numere extrase; la Joker numai Urna 1.

Poarta selectorului fata de Markov, dupa Holm2: **netrecuta**. Definitii fixe cu diferenta pozitiva, Holm64<0,05 si fara fallback primar: **0**. Configuratia scorarii in productie ramane neschimbata.

| Pool | Selector 4+ | Markov 4+ | Selector% | Markov% | Referinta aleatoare 4+ | Metoda din configuratia inghetata | Acea metoda 4+ |
|---:|---:|---:|---:|---:|---:|---|---:|
| 9 | 1/446 | 3/446 | 0.2242% | 0.6726% | 0.3816% | rank_ensemble_core | 2/446 (0.4484%) |
| 10 | 3/446 | 4/446 | 0.6726% | 0.8969% | 0.6222% | frequency | 4/446 (0.8969%) |
| 11 | 6/446 | 7/446 | 1.3453% | 1.5695% | 0.9562% | markov_pairs | 8/446 (1.7937%) |
| 12 | 10/446 | 11/446 | 2.2422% | 2.4664% | 1.4018% | markov_lag2 | 11/446 (2.4664%) |
| 13 | 13/446 | 14/446 | 2.9148% | 3.1390% | 1.9780% | frequency | 16/446 (3.5874%) |
| 14 | 16/446 | 20/446 | 3.5874% | 4.4843% | 2.7037% | frequency | 19/446 (4.2601%) |
| 15 | 18/446 | 24/446 | 4.0359% | 5.3812% | 3.5975% | frequency | 24/446 (5.3812%) |
| 16 | 21/446 | 30/446 | 4.7085% | 6.7265% | 4.6775% | frequency | 28/446 (6.2780%) |
| 17 | 27/446 | 32/446 | 6.0538% | 7.1749% | 5.9609% | pagerank_cooc | 33/446 (7.3991%) |
| 18 | 33/446 | 40/446 | 7.3991% | 8.9686% | 7.4637% | frequency | 35/446 (7.8475%) |
| 19 | 42/446 | 51/446 | 9.4170% | 11.4350% | 9.2002% | markov_self_state | 55/446 (12.3318%) |

Metodele configuratiei au fost alese pe istoricul complet reutilizat. Coloana lor este DOAR diagnostic: aceleasi reguli topN/consecutive 2, fara penalizari sau restrangerea bazei. Nu este o reluare a selectiei istorice de productie si nici o validare independenta. Referinta aleatoare este hipergeometrica; aceasta runda testeaza primar fata de Markov.

### Indicatori secundari pe acelasi traseu

| Selectie | Metoda | 3+ | 4+ | 5+ | Media hiturilor | Zero hituri |
|---|---|---:|---:|---:|---:|---:|
| limita2 | Selector | 19.955157% | 3.872809% | 0.693029% | 1.621484 | 14.859356% |
| limita2 | Markov | 20.342438% | 4.810436% | 0.264982% | 1.625561 | 13.575214% |
| limita2 | Configuratie (diagnostic) | 18.304117% | 4.790053% | 0.448430% | 1.593355 | 14.940889% |
| topN brut | Selector | 20.607419% | 3.832042% | 0.774562% | 1.631879 | 14.492458% |
| topN brut | Markov | 20.892784% | 5.381166% | 0.733795% | 1.665104 | 13.167550% |
| topN brut | Configuratie (diagnostic) | 18.528333% | 5.075418% | 0.468814% | 1.594986 | 15.063188% |

### Alegeri pe ferestrele trecute

| Origine | Interval evaluat | Definitie aleasa | Eligibile | Fallback ulterior |
|---:|---|---|---:|---:|
| 70% | 2020-03-15..2022-07-31 | bocpd_daily_h512_k100 | 18 | 0 |
| 80% | 2022-08-04..2024-09-08 | mix_markov50__bocpd_daily_h128_k100 | 34 | 0 |
| 90% | 2024-09-12..2026-10-18 | mix_markov50__bocpd_daily_h512_k100 | 34 | 0 |

### Toate definitiile fixe, fara alegerea unui castigator dupa rezultat

Holm32 corecteaza toate definitiile din joc, iar Holm64 toate perechile definitie-joc din aceasta extensie. P-urile sunt fata de Markov, pentru obiectivul agregat 4+.

| Definitie | Rata 4+ | Diferenta pp | p brut | Holm32 | Holm64 | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| bocpd_daily_h128_k100 | 4.708520% | -0.101916 | 0.547450 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h128_k20 | 4.300856% | -0.509580 | 0.700400 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h512_k100 | 4.097024% | -0.713412 | 0.774630 | 1.000000 | 1.000000 | 0 |
| bocpd_daily_h512_k20 | 3.852426% | -0.958011 | 0.858160 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k100_a25 | 5.136567% | +0.326131 | 0.245920 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k100_a50 | 4.586221% | -0.224215 | 0.656890 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k20_a25 | 5.075418% | +0.264982 | 0.284940 | 1.000000 | 1.000000 | 0 |
| lag2_pair_contrast_k20_a50 | 4.790053% | -0.020383 | 0.525860 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h128_k100 | 4.300856% | -0.509580 | 0.738170 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h128_k20 | 4.239706% | -0.570730 | 0.764770 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h512_k100 | 4.362006% | -0.448430 | 0.731830 | 1.000000 | 1.000000 | 0 |
| mix_markov50__bocpd_daily_h512_k20 | 4.830819% | +0.020383 | 0.502240 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k100_a25 | 5.279250% | +0.468814 | 0.071490 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k100_a50 | 5.075418% | +0.264982 | 0.296020 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k20_a25 | 5.075418% | +0.264982 | 0.195310 | 1.000000 | 1.000000 | 0 |
| mix_markov50__lag2_pair_contrast_k20_a50 | 4.993885% | +0.183449 | 0.354050 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_all | 4.443539% | -0.366898 | 0.695160 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g3_h256 | 4.382389% | -0.428047 | 0.754930 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_all | 4.667754% | -0.142682 | 0.583660 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_ewma_hl30_g5_h256 | 5.279250% | +0.468814 | 0.262250 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_all | 4.606604% | -0.203832 | 0.636690 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g3_h256 | 4.688137% | -0.122299 | 0.609600 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_all | 4.728903% | -0.081533 | 0.552290 | 1.000000 | 1.000000 | 0 |
| mix_markov50__rank_bilinear_markov_frequency_g5_h256 | 4.239706% | -0.570730 | 0.818400 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g3_all | 4.362006% | -0.448430 | 0.676570 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g3_h256 | 4.219323% | -0.591113 | 0.779160 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g5_all | 2.914798% | -1.895638 | 0.980490 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_ewma_hl30_g5_h256 | 3.974725% | -0.835711 | 0.834110 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g3_all | 4.056258% | -0.754179 | 0.836010 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g3_h256 | 4.525071% | -0.285365 | 0.683910 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g5_all | 4.137790% | -0.672646 | 0.785800 | 1.000000 | 1.000000 | 0 |
| rank_bilinear_markov_frequency_g5_h256 | 4.117407% | -0.693029 | 0.808970 | 1.000000 | 1.000000 | 0 |

## Protocol si limite

- Aceeasi banca de 16 algoritmi: patru BOCPD zilnice (hazard 1/128 sau 1/512, prior 20 sau 100), patru contraste de interactiune lag2 (shrink 20/100, amplitudine 0,25/0,5), opt calibratoare bilineare de rang (partener frequency/EWMA30, grila 3/5, memorie completa/HL256). Pentru fiecare exista un blend de rang 50% cu Markov. Media priorului este draw_n/max_num, iar rangurile se scaleaza la universul jocului.
- Predictiile unei zile preced toate actualizarile acelei zile. Metodele pe istoric brut invata de la randul 0; calibratoarele invata din fluxul expertilor de la 50% din zile. Rang mediu la egalitati, fara zgomot de eticheta; fallback frequency exact si marcat. Invaliditatea se propaga numai prin parintii consumati.
- Selectorul compara numai fereastra anterioara 20% la originile 70/80/90%, cu limite exacte pe zilele CSV complet. Scor 4+ agregat, egalitatile prefera Markov, apoi complexitatea mica, apoi ID. Parametrii raman fixi in blocul urmator. 70..80% este diagnostic; 80..100% evaluare retrospectiva prequentiala. Invaliditatea viitoare este numarata si nu schimba alegerea anterioara.
- Pool canonic pentru FIECARE k, prin functia reala a aplicatiei. TopN brut foloseste aceleasi alegeri ca modul consecutive 2; nu exista o a doua selectie pe brut.
- 99.999 de permutari paired pe zile, semn comun tuturor poolurilor si extragerilor zilei; CI95 din 2.000 de bootstrapuri pe zile. Dependenta seriala poate limita inferenta. P-urile sunt conditionate pe traseele observate, nu calibreaza toate rundele de cautare adaptiva. Nu exista holdout virgin, test prospectiv sau nul IID al intregului pipeline.
- Urna 2 nu are pool 9..19 si nu face parte din aceasta cautare. Nu sunt adaugate alte state si nu sunt modificate studiile 6/49 inghetate. Nu se afirma optimalitatea globala a Markov.
- Cache-ul si jurnalele raman locale in TEMP; fisierele operative protejate au amprente egale inainte/dupa. Metodele configuratiei sunt cele inregistrate la inghetarea protocolului, chiar daca ulterior configuratia statiei se schimba.

Surse de inspiratie pentru BOCPD si context de calibrare: [sursa primara 1](https://arxiv.org/abs/0710.3742), [sursa primara 2](https://ojs.aaai.org/index.php/AAAI/article/download/9602/9461). Calibratorul bilinear este o ipoteza proprie, nu implementarea exacta BBQ; sursele nu valideaza predictia loto.

## Verificare si reproducere

Calcul pana la JSON: 129.68s; nu include validarea sau intreaga aplicatie.

Validator separat: 37419 de controale, 0 esecuri; CSV verificate integral: 576 de randuri de candidati si 11118 din traseu. Planul de verificari pentru scoruri/prefixe: executat. [Dovezi](pool_extension_2026-10-19_validation.json).

Statisticile, selectorul si CSV-urile sunt refacute integral. Verificarile directe ale scorurilor si poolurilor folosesc esantionul inregistrat: noua tinte canonice si trei prefixe pentru fiecare joc, toate cele 32 de definitii pe prefix, 21 de verificari directe ale expertilor si perturbarea tintei. Nu este o recalculare independenta a fiecarui scor din intregul istoric.

Suita completa in copia izolata: 3182 passed, 20 skipped, 272 warnings in 1310.41s (0:21:50).

Interfata „Clasament → Istoric hits” afiseaza numai 4+ la toate jocurile, inclusiv sumarul si reluarea biletelor. Procentele pastreaza toate extragerile la numitor. Schimbarea intra la repornirea normala a aplicatiei. [Verificari](pool_extension_2026-10-19_checks.json).

Amprentele operative au ramas egale pe durata cercetarii. Raportul local raport_complet.txt are ulterior alta amprenta, cu data dinaintea suitei finale, si este pastrat fara rescriere sau atribuirea cauzei. Decizia, setarile, istoricul poolurilor si Re-Bench-ul statiei coincid in continuare cu amprentele inregistrate.

Comenzile se ruleaza din radacina proiectului, cu versiunile semnate in protocol:

```powershell
python scripts/analysis/pool_extension_2026_10_19.py --register
python scripts/analysis/pool_extension_2026_10_19.py --run --workers 4
python -m scripts.analysis.validate_pool_extension_2026_10_19 --verify scripts/analysis/pool_extension_2026-10-19.json --output scripts/analysis/pool_extension_2026-10-19_validation.json
python scripts/analysis/render_pool_extension_2026_10_19.py
```
