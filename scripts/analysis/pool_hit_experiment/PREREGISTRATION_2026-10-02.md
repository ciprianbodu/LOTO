# Experiment preînregistrat: mai multe hituri ÎN POOL (2026-10-02)

Scris și comis ÎNAINTE de orice evaluare. Parametrii exacți, lista metodelor și
amprentele datelor sunt în `preregistration_2026-10-02.json`; codul care rulează
evaluarea este `pool_hit_experiment.py` (mod `run`). Nu se schimbă nimic din
acestea după prima rulare; orice abatere se raportează ca abatere.

## Întrebare și ipoteze

Poate o regulă de selecție construită peste metodele existente să pună în pool
mai multe numere extrase decât un pool aleator de aceeași mărime?

- H0 (pentru fiecare candidat × celulă): rata evenimentului „cel puțin t numere
  extrase în pool” este cel mult rata hipergeometrică exactă a unui pool aleator.
- H1: rata este mai mare (test unilateral).

Așteptarea a priori, din auditurile anterioare și din replicarea `dmd_forecast`
pe 21.821 de extrageri străine: niciun avantaj. Experimentul este construit ca
să poată detecta un avantaj, nu ca să-l găsească.

## Reguli candidate (4)

Universul de metode: cele 47 de metode de producție din `METHODS` minus
`EXCLUDED_FROM_PRODUCTION` (lista exactă în JSON). Toate 47 sunt rulate; nu se
subeșantionează metode. Clasamentele trec prin `core.ranking.rank_by_score`
(regula canonică); un scor inutilizabil cade pe `frequency`, ca în producție.

- **C1_meta_best** – selector meta: pentru fiecare țintă alege metoda cu media
  cea mai mare a numerelor nimerite în pool-ul de aceeași mărime pe ultimele 300
  de ținte anterioare (data strict mai mică); egalitate → ordine alfabetică;
  fără trecut → `frequency`.
- **C2_borda_all** – media rangurilor (Borda) tuturor celor 47 de metode;
  egalitate → numărul mai mare.
- **C3_dev_best** – metoda fixă cu cea mai mare rată a evenimentului pe setul de
  dezvoltare românesc (egalitate → media hiturilor, apoi nume). Pentru celulele
  străine și `ro_649` vine din dezvoltarea pe Loto 6/49; pentru Joker și 5/40 din
  dezvoltarea jocului respectiv, la același pool.
- **C4_meta_top5_borda** – Borda pe cele 5 metode cu cea mai bună medie de
  hituri pe ultimele 300 de ținte anterioare.

Media hiturilor (nu rata 3+) e folosită de selectorii meta pentru putere
statistică: e o statistică mai densă pe aceleași extrageri anterioare.

## Date și separare

- Fiecare țintă este evaluată walk-forward: scorerul vede numai extragerile cu
  dată strict anterioară (toată ziua țintei e exclusă, ca în runner). O țintă
  cere cel puțin 200 de extrageri anterioare.
- **Dezvoltare**: țintele din primele 70% din rândurile fiecărui istoric
  românesc (6/49, 5/40, Joker Urna 1). Singurul lucru „ales” pe dezvoltare este
  metoda C3. W = 300 și top 5 sunt fixate aici, fără căutare.
- **Test ținut deoparte**:
  - istoricele străine 6-din-N nefolosite în replicarea `dmd_forecast`:
    Austria 6/45, Belgia 6/45, Ungaria 6/45 (celula `ext_645`); Cehia 6/49,
    Slovacia 6/49, Bulgaria Toto 2 6/49 (celula `ext_649`); toate țintele cu
    ≥200 extrageri anterioare;
  - ultimele 30% din rândurile românești (6/49, 5/40, Joker Urna 1).
  Limită declarată: metodele din registry au fost scrise văzând întregul istoric
  românesc, deci ultimele 30% românești NU sunt un holdout extern al formulelor;
  numai celulele străine sunt cu adevărat în afara eșantionului.
- Rândurile folosite sunt exact primele `rows` rânduri ale fiecărui fișier, cu
  amprenta SHA-256 (antet + rânduri, LF) din JSON. `test_pool_hit_experiment.py`
  verifică amprentele; extragerile adăugate ulterior nu intră.

| Set | Fișier | Rânduri | SHA-256 |
|---|---|---|---|
| ro_649 | `_ISTORIC/loto_6_49.csv` | 2589 | `f55fde7e883ccc8b0100eb2f6525896cf5b1eb2044b4efe29791132a6163b967` |
| ro_540 | `_ISTORIC/loto_5_40.csv` | 1740 | `8408b5d1e1ada22d125d85b3d4cfcb81dce871c874811dafc536f3ab3b411a47` |
| ro_joker | `_ISTORIC/joker.csv` | 2192 | `4f89fab6717545f7fb1cca05ef004d58f222906869ea97bb5700dac79e252bea` |
| at_645 | `austria_lotto_6aus45.csv` | 3687 | `7be3b356c344e56d71dfc28dc9d20374825db9ad4c6429cc6521cbaf21645927` |
| be_645 | `belgia_lotto_6din45.csv` | 1566 | `ac91f8f3fc08f5ec99217acf3389bbbd1a54732553b469d89024a1d12185f154` |
| hu_645 | `ungaria_hatoslotto_6din45.csv` | 1852 | `2786c4044e56390b13029945a94f9dededa9dd626e7f3c8b1d71ff8af30e7f35` |
| cz_649 | `cehia_sportka_6din49.csv` | 7624 | `bf67ef757e2af1d3eb80ee79c1cb4f0c0cbc7d6a4bfaf24f29b59c8f99d3ee7f` |
| sk_649 | `slovacia_loto_6din49.csv` | 5168 | `ee28c3e886059dae87c40b394fc263f8febfa8681ff0cfe6b621aefac0f56df9` |
| bg_649 | `bulgaria_toto2_6din49.csv` | 703 | `26edbe6e1a00f5c0eeaac0d2cf153f71a52e89f9159908c3782c5aa35c91639c` |

## Metrică, referință, test

- Celule (8): `ext_645` și `ext_649` la pool 6 și 12, prag 3+; `ro_649` pool 6
  și 12, 3+; `ro_joker` (Urna 1, 5 din 45) pool 11, 3+; `ro_540` (6 extrase din
  40) pool 11, 4+.
- Referința: rata hipergeometrică exactă P(X ≥ t), X ~ Hyp(N, extrase, pool).
- Test: binomial exact unilateral (`scipy.stats.binomtest`, `greater`) pe
  țintele celulei puse la un loc (aceeași geometrie în celulă).
- Corecție Holm peste toate cele 4 × 8 = 32 de teste, α = 0,05 familial.
- Raportare: rata, referința, p brut, p Holm, interval Wilson 95%, plus rata pe
  dezvoltare (informativ). Rezultatele pe fiecare loterie sunt secundare.

## Regula de decizie

Un candidat „supraviețuiește” numai dacă p Holm < 0,05 într-o celulă de test.
Supraviețuirea doar pe ultimele 30% românești se raportează separat și mai slab
(holdout temporal, nu extern). Nimic nu intră în producție din acest experiment;
un supraviețuitor ar fi doar propus pentru calea normală (metodă în registry,
Re-Bench, poartă vs random, apoi test pe extrageri viitoare).

## Cost

47 de metode × ~27.000 de ținte, 4 procese; estimat sub o oră. Nicio
subeșantionare de metode sau de ținte.
