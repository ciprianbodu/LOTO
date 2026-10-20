# Selectia poolului 9-16 la 6/49 fata de markov_lag2 - 2026-10-19

Au fost evaluate 51 de metode: 47 din registry si patru agregari online noi. Finalistul fixat pe dezvoltare este `cusum_burst`. Candidati cu avantaj pe confirmare dupa Holm: **0/45**. Nu se schimba scoreri sau decizii in productie.

## Comparatia romaneasca

| Segment | Extrageri | Candidat: media ratelor 4+ | Markov: media ratelor 4+ |
|---|---:|---:|---:|
| development | 532 | 4.793233% | 3.289474% |
| confirmation | 529 | 2.599244% | 3.827977% |

Primarul este media celor opt indicatori 4+ pentru k9..16, pe fiecare extragere, cu limita de consecutive 2. Nu este rata unui singur pool sau a unei singure variante. Diferenta pe confirmare: -1.228733 puncte procentuale; p unilateral 0.950630, Holm 1.000000. CI95 pe zile: [-2.751935, 0.236351] puncte procentuale. Ipoteza testului este interschimbabilitatea diferentelor pe zile sub nul; dependenta seriala poate limita inferenta.

| Pool | Candidat 4+ / N | Markov 4+ / N | Candidat % | Markov % |
|---:|---:|---:|---:|---:|
| 9 | 2/529 | 4/529 | 0.3781% | 0.7561% |
| 10 | 3/529 | 6/529 | 0.5671% | 1.1342% |
| 11 | 4/529 | 8/529 | 0.7561% | 1.5123% |
| 12 | 7/529 | 12/529 | 1.3233% | 2.2684% |
| 13 | 13/529 | 19/529 | 2.4575% | 3.5917% |
| 14 | 18/529 | 30/529 | 3.4026% | 5.6711% |
| 15 | 24/529 | 38/529 | 4.5369% | 7.1834% |
| 16 | 39/529 | 45/529 | 7.3724% | 8.5066% |

Mediile hiturilor pe confirmare (peste k9..16): candidat 1.468809, Markov 1.511815; mediile ratelor zero: 17.202268% / 16.824197%. Sunt rezultate secundare descriptive, fara alegerea ulterioara a unei alte tinte.

## Replicare retrospectiva externa

| Tara | Extrageri | Candidat: media ratelor 4+ | Markov: media ratelor 4+ | p unilateral | Holm tari |
|---|---:|---:|---:|---:|---:|
| germania | 6313 | 3.403691% | 4.251148% | 0.999450 | 1.000000 |
| cehia | 7380 | 3.514566% | 3.414634% | 0.337340 | 1.000000 |
| slovacia | 4924 | 3.467709% | 3.724106% | 0.811790 | 1.000000 |
| polonia | 7162 | 3.645979% | 3.684376% | 0.564270 | 1.000000 |
| spania | 3946 | 3.718956% | 3.449696% | 0.206360 | 1.000000 |
| bulgaria | 451 | 3.658537% | 2.854767% | 0.185630 | 1.000000 |

Cumulat: 30176 de extrageri / 22311 de grupe tara:data; candidat 3.543793%, Markov 3.700374%; diferenta -0.156581 puncte procentuale, p 0.908140, CI95 [-0.395223, 0.072952] puncte procentuale. Nu se presupune ca cele opt pooluri ori doua trageri din aceeasi zi sunt independente.

## Cele patru ipoteze noi

| Candidat | Dezvoltare 4+ | Confirmare 4+ | Eligibil | Holm confirmare |
|---|---:|---:|---|---:|
| weighted_experts_h100_rank | 3.406955% | 4.276938% | True | 1.0 |
| weighted_experts_h100_vote | 3.594925% | 3.449905% | True | 1.0 |
| weighted_experts_h400_rank | 3.500940% | 4.347826% | True | 1.0 |
| weighted_experts_h400_vote | 3.383459% | 3.520794% | True | 1.0 |

Agregare exponentiala cauzala a zece experti, prior 50% Markov, rest uniform, half-life 100/400, eta 1, share 1%, reprezentare prin rang mediu sau vot in poolurile constranse. Ponderile se actualizeaza numai dupa intreaga zi. Experiment propriu inspirat de [predictia cu experti](https://proceedings.mlr.press/v128/korotin20a.html); media scorurilor nu garanteaza depasirea unui expert. Recompensa rara 4+ este zgomotoasa. Variantele cu vot depind de restrictia numerica explicita a utilizatorului si nu sunt scoreri generici echivarianti.

## Protocol si limite

- Scoruri din prefixe cu data strict anterioara tintei, dupa minimum 256 de randuri de istoric. Aceeasi zi nu este taiata intre splituri si nu produce actualizari intre tragerile sale.
- Primele 60% din zile: antrenare; urmatoarele 20%: dezvoltare; ultimele 20%: confirmare. Datele exacte: {"train": {"n": 1277, "first": "1998-10-25", "last": "2016-07-21"}, "development": {"n": 532, "first": "2016-07-24", "last": "2021-10-10"}, "confirmation": {"n": 529, "first": "2021-10-14", "last": "2026-10-18"}}.
- Finalist unic pe dezvoltare: media ratelor 4+, apoi media hiturilor, apoi nume. Eligibilitatea foloseste numai erori si egalitati dinaintea confirmarii. Filtrul de egalitati verifica explicit taietura top-K BRUTA, ca benchmarkul existent; nu diagnosticheaza separat toate straturile de egalitate accesate dupa limita de consecutive.
- Toate metodele eligibile sunt comparate pe confirmare, Holm peste intreaga familie. Primarul are 99.999 de permutari pe zi cu acelasi semn pentru toate cele opt pooluri si toate tragerile zilei; p=(depasesc+1)/(B+1), potrivit [documentatiei SciPy](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html).
- Top-N brut, 3+, media hiturilor si zero sunt secundare. Comparatia per-k este in tabel si CSV; valorile per-k nu sunt opt teste primare independente.
- Aceste istorice romanesti SI straine au mai fost explorate. Confirmarea este retrospectiva reutilizata, nu un holdout virgin si nu dovada prospectiva. Un avantaj doar pe aceste date nu este motiv suficient de promovare.
- Manifestul a fost fixat inainte de numararea hiturilor. Un singur amendament tehnic, inregistrat inainte de evaluarea rezultatelor: normalizarea CRLF/LF in verificarea amprentelor surselor. Nicio metoda, hiperparametru, data sau regula statistica nu s-a schimbat. Protocolul initial si amendamentul sunt pastrate.
- Memoria NPZ a cercetarii este separata in bench_results/pool649_research_20261019 si ramane locala; SHA-urile datelor si surselor sunt in protocol. Git pastreaza octetii exacti ai artefactelor pentru a nu schimba identitatea manifestului.

## Verificare si reproducere

Validator: 32216 de controale, zero discrepante; 51 metode renumarate, 45 comparatii pereche, sase arhive si noua prefixe directe pentru fiecare din cei doi scoreri. Fisierele de productie protejate au aceleasi SHA inainte/dupa.

Suita completa: 2887 passed, 20 skipped, 272 warnings in 1591.20s (0:26:31).

```powershell
python scripts/analysis/pool_selection_649_2026_10_19.py --register
python scripts/analysis/pool_selection_649_2026_10_19.py --run --workers 4
python scripts/analysis/validate_pool_screen_2026_10_19.py --verify scripts/analysis/pool_selection_649_2026-10-19.json
```

Rerularea cere aceleasi surse/date semnate. Daca istoricul s-a extins, folositi checkoutul acestui studiu; nu suprascrieti manifestul pentru a reincerca selectia dupa rezultat.
