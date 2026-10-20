# Audit scoring si decizie - 2026-10-18

Audit local, fara rescrierea istoricelor, foldurilor sau deciziei. Amprentele SHA-256 ale surselor sunt in JSON-ul alaturat.

**Limita snapshotului:** foldurile si decizia salvata v22 acopera extragerile pana la 15-10-2026. Istoricul curent contine si extragerea din 18-10-2026, cate un rand nou pe fiecare joc. Tabelul verifica acel snapshot, nu evalueaza extragerile noi. Cele patru metode din tabel coincid cu decizia salvata. Corectura DMD de mai jos schimba semantica scorerului si cere Re-Bench complet v23 inaintea unui clasament actualizat.

Registry-ul actual are 53 de intrari: doua baseline-uri, 50 de metode existente si un experiment exclus din productie. Numerele 52 din documentatia istorica nu descriu registrul actual.

Cele 824 de folduri acopera patru jocuri si percentilele 10/30/60/100. Cele 28 de folduri failed sunt cele sapte metode de co-aparitie pe Urna 2, cate patru ferestre: lipsa co-aparitiilor la o singura bila este raportata corect.

| Joc | Pool | Metoda | Rata pooled | Hazard | Holm p | Dovada de avantaj |
|---|---:|---|---:|---:|---:|---|
| loto_6_49 | 16 | markov_lag2 | 9.086% | 7.960% | 0.455879 | nu |
| loto_5_40 | 16 | pair_lift_last | 17.625% | 16.027% | 1.000000 | nu |
| joker_urna1 | 16 | frequency | 6.144% | 4.678% | 0.130468 | nu |
| joker_urna2 | 1 | ridge_pooled_feats | 6.307% | 5.000% | 0.823902 | nu |

Ratele pooled sunt media ponderata a ferestrelor imbricate si sunt metrica de selectie actuala. Testul binomial/Holm foloseste fereastra completa; nici acesta nu este validare externa. Nicio schimbare de scorer nu are suport nou pentru promovare. Studiile existente de 1.002 candidati (10 octombrie) si 491 de scoreri (5 octombrie), cu confirmare si replicare externa, nu au promovat predictori.

Optimizarea livrata pentru markov_pairs si markov_lag2 calculeaza numai randurile de tranzitii folosite efectiv. Pastreaza forma completa a matricei finale si aceeasi reducere numerica. Scorurile si ordinea poolurilor raman identice, deci nu necesita invalidarea cacheului.

Microbenchmark pe istoricul real, mediana a 7 loturi de cate 60 de apeluri:

| Joc / lag | Inainte ms | Dupa ms | Accelerare |
|---|---:|---:|---:|
| loto_6_49_lag1 | 0.843 | 0.473 | 1.78x |
| loto_6_49_lag2 | 0.794 | 0.325 | 2.44x |
| loto_5_40_lag1 | 0.577 | 0.261 | 2.21x |
| loto_5_40_lag2 | 0.502 | 0.204 | 2.46x |
| joker_urna1_lag1 | 0.744 | 0.338 | 2.20x |
| joker_urna1_lag2 | 0.736 | 0.346 | 2.13x |
| joker_urna2_lag1 | 0.188 | 0.193 | 0.97x |
| joker_urna2_lag2 | 0.409 | 0.177 | 2.31x |

Timpii sunt pentru scorerul individual pe statia curenta, sub incarcare concurenta. Nu reprezinta accelerarea intregii aplicatii; pentru Urna 2 valorile sunt prea mici si variabile pentru o promisiune de viteza.

Regresiile compara exact scorurile si clasamentul cu formula densa la fiecare prefix pentru cele patru geometrii; includ istorice nemodificabile si stridate, numere repetate, absente si invalide.

Corectura DMD: trunchierea la rangul sase taia un bloc de valori singulare egale. Pe Urna 2, reetichetarea numerelor schimba scorurile cu 0,0976; defectul a fost reprodus independent direct din HEAD. Acum subspatiul de la granita se pastreaza integral, deci rangul poate depasi sase. Valorile singulare <=1e-8 raman excluse. O prognoza matematic nula nu mai transforma zgomotul de rotunjire de ordin 1e-14 intr-un clasament artificial.

Regresiile acopera egalitati exacte si aproape egale, permutari adversariale, spectru distinct cu output identic, istorice scurte/constante si proiectie nula. Invalidare: bench v23, sufix WF numai pentru DMD, worker v20; copiile inghetate ale testelor prospective raman neschimbate.

Comparatie externa a coverelor: la 18-10-2026, toate cele 52 de covere clasice C locale au exact numarul de blocuri al celui mai bun design din [arhiva oficiala La Jolla, Zenodo 19735294](https://zenodo.org/records/19735294). Nu s-a identificat un design mai mic de importat. Metadatele coverdata.json (8.319.503 octeti) au MD5-ul oficial b2c626b07f216aac830d344eff5ad523; inventarul complet si amprentele fiecarui fisier sunt in cover_source_check_2026-10-18.json. Arhiva 1.2 este publicata la 24-04-2026, cu baza inghetata in martie 2026 potrivit README. Aceasta comparatie nu dovedeste minimalitatea globala si nu reprezinta o verificare a unui site live actualizat.

Verificare izolata dupa reparatii: 32 de teste trecute pentru Markov, DMD si regresia reala de reetichetare DMD; compilare Python reusita. Suita completa este verificata in etapa integrarii.

Audit complet al tuturor celulelor deciziei (tinta globala 4+, Urna 2 top-1)


Au fost recalculate toate cele 46 de celule din decizia salvata, pe acelasi snapshot de folduri. Nepotriviri de scorer: 0; nepotriviri in oricare dintre cele 16 campuri comparate (inclusiv ensemble, setari, porti si diagnostice): 0.

Celule cu avantaj dupa Holm: 0/46; low_confidence: 0; coloana metricei nepotrivita: 0; metode cu ferestre incomplete: 0 celule; date de rata lipsa: 0 celule.

Poarta egalitatilor este activa in 46 de celule; exclude 211 de perechi metoda-celula in 45 de celule. Listele exacte si toate cele 46 de inregistrari sunt in JSON.

| Joc | Pool | Scorer salvat si recalculat | Holm p | Avantaj demonstrat | Excluderi egalitati |
|---|---:|---|---:|---|---:|
| loto_6_49 | 6 | dmd_forecast | 1.000000 | nu | 3 |
| loto_6_49 | 7 | dmd_forecast | 1.000000 | nu | 3 |
| loto_6_49 | 8 | dmd_forecast | 1.000000 | nu | 4 |
| loto_6_49 | 9 | logit_pooled_feats | 1.000000 | nu | 4 |
| loto_6_49 | 10 | online_logit_sgd | 1.000000 | nu | 4 |
| loto_6_49 | 11 | markov_self_state | 1.000000 | nu | 4 |
| loto_6_49 | 12 | drift_linear | 1.000000 | nu | 4 |
| loto_6_49 | 13 | markov_lag2 | 1.000000 | nu | 7 |
| loto_6_49 | 14 | online_logit_sgd | 1.000000 | nu | 7 |
| loto_6_49 | 15 | markov_lag2 | 0.917082 | nu | 7 |
| loto_6_49 | 16 | markov_lag2 | 0.455879 | nu | 7 |
| loto_6_49 | 17 | theta_drift | 1.000000 | nu | 7 |
| loto_6_49 | 18 | drift_linear | 1.000000 | nu | 7 |
| loto_6_49 | 19 | drift_linear | 1.000000 | nu | 7 |
| loto_6_49 | 20 | frequency | 1.000000 | nu | 7 |
| loto_5_40 | 5 | croston_interval | 1.000000 | nu | 1 |
| loto_5_40 | 6 | hot_consistency | 1.000000 | nu | 3 |
| loto_5_40 | 7 | pair_lift_last | 0.102454 | nu | 3 |
| loto_5_40 | 8 | hot_consistency | 0.279643 | nu | 3 |
| loto_5_40 | 9 | hot_consistency | 0.120273 | nu | 3 |
| loto_5_40 | 10 | cusum_burst | 0.933456 | nu | 4 |
| loto_5_40 | 11 | hot_consistency | 0.490799 | nu | 5 |
| loto_5_40 | 12 | hot_consistency | 1.000000 | nu | 5 |
| loto_5_40 | 13 | online_logit_sgd | 1.000000 | nu | 5 |
| loto_5_40 | 14 | pair_lift_last | 1.000000 | nu | 5 |
| loto_5_40 | 15 | pair_lift_last | 1.000000 | nu | 5 |
| loto_5_40 | 16 | pair_lift_last | 1.000000 | nu | 5 |
| loto_5_40 | 17 | pair_transition | 1.000000 | nu | 8 |
| loto_5_40 | 18 | knn_pattern_self | 0.370419 | nu | 7 |
| loto_5_40 | 19 | knn_pattern_self | 1.000000 | nu | 7 |
| joker_urna1 | 5 | runs_persistence | 1.000000 | nu | 2 |
| joker_urna1 | 6 | gbm_stumps_pooled | 0.899220 | nu | 3 |
| joker_urna1 | 7 | pair_transition | 1.000000 | nu | 3 |
| joker_urna1 | 8 | rank_ensemble_core | 1.000000 | nu | 4 |
| joker_urna1 | 9 | rank_ensemble_core | 1.000000 | nu | 4 |
| joker_urna1 | 10 | frequency | 1.000000 | nu | 4 |
| joker_urna1 | 11 | markov_pairs | 1.000000 | nu | 4 |
| joker_urna1 | 12 | pagerank_cooc | 1.000000 | nu | 4 |
| joker_urna1 | 13 | frequency | 1.000000 | nu | 4 |
| joker_urna1 | 14 | frequency | 0.979623 | nu | 4 |
| joker_urna1 | 15 | frequency | 0.134812 | nu | 4 |
| joker_urna1 | 16 | frequency | 0.130468 | nu | 5 |
| joker_urna1 | 17 | pagerank_cooc | 0.184663 | nu | 5 |
| joker_urna1 | 18 | frequency | 0.113136 | nu | 5 |
| joker_urna1 | 19 | markov_self_state | 0.549352 | nu | 5 |
| joker_urna2 | 1 | ridge_pooled_feats | 0.823902 | nu | 0 |

Metode selectate in snapshot, cu numarul de celule: croston_interval (1), cusum_burst (1), dmd_forecast (3), drift_linear (3), frequency (7), gbm_stumps_pooled (1), hot_consistency (5), knn_pattern_self (2), logit_pooled_feats (1), markov_lag2 (3), markov_pairs (1), markov_self_state (2), online_logit_sgd (3), pagerank_cooc (2), pair_lift_last (4), pair_transition (2), rank_ensemble_core (2), ridge_pooled_feats (1), runs_persistence (1), theta_drift (1).

Celulele care foloseau DMD si trebuie reevaluate cu formula corectata: loto_6_49.k6, loto_6_49.k7, loto_6_49.k8.

Aceasta comparatie verifica fidelitatea deciziei salvate fata de foldurile ei. Nu este un Re-Bench nou si nu dovedeste superioritate viitoare. Foldurile nu contin extragerea din 18 octombrie si au fost calculate cu vechea semantica DMD; clasamentul motorului corectat cere Re-Bench complet v23.
