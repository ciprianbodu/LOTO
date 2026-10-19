# Alte combinatii pentru poolurile 9-16 la 6/49 - 2026-10-19

Selectorul are rezultate numeric mai bune decat Markov in aceasta evaluare, dar superioritatea nu este demonstrata statistic. Productia ramane neschimbata.

Banca fixata: **208 combinatii**, plus Markov si frequency. Selectorul a trecut pragul comparatiei in ambele regiuni: **nu**. Combinatii statice cu diferenta pozitiva, Holm < 0,05 in ambele regiuni si fara fallback pe intervalul final: **0**.

Ratele sunt media celor opt indicatori 4+ pentru poolurile 9..16, cu limita de consecutive 2. Sunt hituri in pool, nu pe bilet; cele opt pooluri suprapuse sunt evaluate impreuna, fara a le considera probe independente.

## Selectorul pe ultimele 20% din zile

| Regiune | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | CI95 diferenta pp |
|---|---:|---:|---:|---:|---:|---|
| Romania | 529 | 4.584121% | 3.827977% | +0.756144 | 0.125440 | [-0.470944, 2.011724] |
| Bulgaria | 142 | 2.992958% | 2.200704% | +0.792254 | 0.316360 | [-2.024648, 3.697183] |

## Indicatori secundari, acelasi traseu de selectie

Mediile sunt peste toate cele opt dimensiuni de pool; nu sunt rate pe bilet.

| Regiune | Selectie | Metoda | 3+ | Media hiturilor | Zero hituri |
|---|---|---|---:|---:|---:|
| Romania | limita 2 | Selector | 20.014178% | 1.560255 | 17.060491% |
| Romania | limita 2 | Markov | 17.344045% | 1.511815 | 16.824197% |
| Romania | top-N brut | Selector | 19.163516% | 1.569471 | 15.382798% |
| Romania | top-N brut | Markov | 17.438563% | 1.526701 | 16.422495% |
| Bulgaria | limita 2 | Selector | 19.630282% | 1.588908 | 14.612676% |
| Bulgaria | limita 2 | Markov | 12.764085% | 1.444542 | 16.109155% |
| Bulgaria | top-N brut | Selector | 18.309859% | 1.560739 | 15.316901% |
| Bulgaria | top-N brut | Markov | 14.788732% | 1.458627 | 16.725352% |

## Formulele alese exclusiv din trecut

| Tara | Origine | Interval evaluat | Formula | Eligibile | Fallback ulterior |
|---|---:|---|---|---:|---:|
| romania | 70% | 2019-01-24..2021-10-10 | combo_residual_a050__season_period2 | 210 | 0 |
| romania | 80% | 2021-10-14..2024-04-21 | combo_residual_a050__knn_draw_similarity | 210 | 0 |
| romania | 90% | 2024-04-25..2026-10-18 | combo_rank_m050__markov_self_state | 210 | 0 |
| bulgaria | 70% | 2024-09-29..2025-06-01 | combo_residual_a050__naive_bayes_last | 210 | 0 |
| bulgaria | 80% | 2025-06-05..2026-02-05 | combo_rank_m025__knn_feature_pooled | 210 | 0 |
| bulgaria | 90% | 2026-02-08..2026-10-15 | combo_rank_m025__ewma_cold_hl30 | 210 | 0 |

## Romania, pe fiecare pool

| Pool | Selector 4+ | Markov 4+ | Selector % | Markov % | Referinta aleatoare 4+ |
|---:|---:|---:|---:|---:|---:|
| 9 | 7/529 | 4/529 | 1.3233% | 0.7561% | 0.7395% |
| 10 | 10/529 | 6/529 | 1.8904% | 1.1342% | 1.1846% |
| 11 | 12/529 | 8/529 | 2.2684% | 1.5123% | 1.7878% |
| 12 | 16/529 | 12/529 | 3.0246% | 2.2684% | 2.5737% |
| 13 | 25/529 | 19/529 | 4.7259% | 3.5917% | 3.5648% |
| 14 | 33/529 | 30/529 | 6.2382% | 5.6711% | 4.7817% |
| 15 | 41/529 | 38/529 | 7.7505% | 7.1834% | 6.2420% |
| 16 | 50/529 | 45/529 | 9.4518% | 8.5066% | 7.9600% |

## Statice: diagnostic retrospectiv

Primele 12 dupa rata romaneasca sunt afisate DUPA rezultat. Nu inlocuiesc selectorul fixat anterior. Toate combinatiile sunt in CSV/JSON; Holm se aplica intregii banci separat in fiecare regiune. Aceste clasamente retrospective nu sunt dovezi prospective.

| Formula | RO 4+ | Holm RO | Extern 4+ | Holm extern |
|---|---:|---:|---:|---:|
| combo_rank_m025__dmd_forecast | 5.482042% | 1.000000 | 3.257042% | 1.000000 |
| combo_rank_m050__markov_self_state | 5.434783% | 1.000000 | 2.288732% | 1.000000 |
| combo_residual_a050__ewma_cold_hl30 | 5.056711% | 1.000000 | 1.760563% | 1.000000 |
| combo_rrf_c60__learned | 5.056711% | 1.000000 | 2.728873% | 1.000000 |
| combo_rank_m050__hawkes_cross | 5.033081% | 1.000000 | 1.936620% | 1.000000 |
| combo_residual_a050__hawkes_cross | 5.009452% | 1.000000 | 1.320423% | 1.000000 |
| combo_residual_a050__markov_self_state | 4.962193% | 1.000000 | 2.112676% | 1.000000 |
| combo_rank_m025__hawkes_cross | 4.938563% | 1.000000 | 1.760563% | 1.000000 |
| combo_rank_m050__drift_linear | 4.938563% | 1.000000 | 2.904930% | 1.000000 |
| combo_rank_m050__ewma_cold_hl30 | 4.844045% | 1.000000 | 3.345070% | 1.000000 |
| combo_trim20__learned | 4.820416% | 1.000000 | 2.112676% | 1.000000 |
| combo_rank_m025__markov_self_state | 4.749527% | 1.000000 | 3.080986% | 1.000000 |

## Protocol si limite

- Perechi ponderate Markov+expert, agregari robuste, RRF, medii geometrice ale pozitiilor si corectii de rang reziduale. Parametrii, descriptorii si expertii sunt fixati in manifest inainte de numararea hiturilor.
- Scorurile folosesc numai date strict anterioare tintei. Consensul plat cade pe frequency si este marcat; selectorul exclude in fereastra trecuta orice formula cu fallback propriu/al parintilor ori egalitati in cel putin 50% din pasi la oricare taietura top-K BRUTA. Aceasta poarta nu diagnosticheaza toate straturile accesate dupa limita de consecutive.
- Origini 70/80/90%, fereastra anterioara 20% din zilele complete ale CSV. Intervalul 70..80% este diagnostic; primarul 80..100% permite alegerea de la 90% folosind 80..90%, prin regula fixata anterior. Formula ramane fixa in interiorul blocului. Egalitatile prefera Markov, apoi complexitatea mica, apoi ID.
- Top-N brut, 3+, media hiturilor si zero sunt secundare. Traseul brut foloseste aceleasi formule alese cu limita 2, fara a doua optimizare.
- 99.999 de permutari paired pe zile, acelasi semn pentru toate poolurile/tragerile zilei. Extern: numai Bulgaria, conform restrangerii cerute de utilizator, cu cluster tara:data. Familia Holm pe tari are o singura ipoteza. Inferenta presupune interschimbabilitate pe zile; dependenta seriala poate limita concluziile.
- P-urile sunt conditionate pe traseele observate; fara reluarea intregului selector sub nul, nu calibreaza toate alegerile adaptive si rundele de cercetare. Replicarea externa adapteaza acelasi algoritm la trecutul Bulgariei; nu transfera o singura combinatie romaneasca fixa.
- Amendament de scop cerut explicit de utilizator in timpul rularii: Romania + doar Bulgaria. Germania terminase, Cehia era la scoruri si a fost oprita, iar Bulgaria nu fusese evaluata. Protocolul si runnerul initial sunt arhivate; rezultatele celorlalte tari nu intra in noul raport. Banca, ferestrele, pragurile si regulile de selectie au ramas identice.
- Istoricele romanesti SI straine au fost explorate anterior. Nu exista holdout virgin, validare prospectiva sau reluare a intregii cautari pe istorice IID sintetice. Nu se revendica o calibrare IID a cautarii. Rezultatul nu demonstreaza optimalitatea globala a lui Markov si nu promoveaza automat o metoda in productie.
- Scorurile vechi sunt reutilizate doar cu semnaturi identice de date/cod/versiuni si feliate exact. Cache-ul vechi ramane neatins; provenienta se pastreaza in noul cache local. Fisierele de productie protejate au amprentele in JSON.
- RRF si agregarea robusta sunt ipoteze din alte domenii, fara garantii predictive pentru loto: [RRF, autorii](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf), [ForecastComb, R Journal](https://journal.r-project.org/articles/RJ-2018-052/).

## Verificare si reproducere

Calculul pana la JSON in ultima reluare: 30.36 s, cu scoruri deja in cache. Nu include calcularile anterioare intrerupte sau exportul CSV si nu masoara viteza intregii aplicatii.

Validare independenta: 57099 verificari, 0 esecuri, 2 arhive. Refacere integrala a statisticilor si a selectorului, plus noua prefixe directe pe tara; detaliile sunt in [pool_combinations_649_2026-10-19_validation.json](pool_combinations_649_2026-10-19_validation.json).

Suita completa: 2949 passed, 20 skipped, 272 warnings in 1402.42s (0:23:22).

```powershell
python scripts/analysis/pool_combinations_649_2026_10_19.py --register
python scripts/analysis/pool_combinations_649_2026_10_19.py --run --workers 4
python scripts/analysis/validate_pool_combinations_2026_10_19.py --verify scripts/analysis/pool_combinations_649_2026-10-19.json
python scripts/analysis/render_pool_combinations_2026_10_19.py
```
