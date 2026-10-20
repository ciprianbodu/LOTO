# Schemele LR: unde se mai pot obține hituri — 2026-10-20

Cerința: cod care să dea mai multe hituri. Utilizatorul joacă la 6/49 schema
redusă Cod 59 (16 numere, 112 variante, 896 lei). Rata pool-ului rămâne
hipergeometrică, deci singurul loc pentru mai multe hituri este așezarea
numerelor pe variante, la același cost. Toate cifrele sunt exacte, pe extrageri
uniforme (`lr_schemes_2026-10-20.json`, `analyze_lr_schemes.py`,
`prove_lr_optimality.py`).

## Cod 59 este optim la fiecare prag

Cod 59 este o împachetare perfectă:

- nicio grupă de 4 numere nu apare pe două variante;
- cele 112 variante acoperă 112 × 15 = 1.680 de grupe de 4 din 1.820, fiecare o
  singură dată;
- fiecare dintre cele 16 poziții apare pe 42 de variante.

Pentru orice așezare de 112 variante pe 16 numere, marginea superioară a șansei
de a prinde cel puțin t pe o variantă se obține numărând, pentru fiecare k numere
ieșite din pool, câte k-submulțimi pot conține o t-submulțime de pe o variantă
(`covering.lr_schemes._upper_bound`):

| Prag | Margine | Motivul | Cod 59 |
|---|---:|---|---:|
| 3+ | 29,81% | 3 numere ieșite sunt mereu pe o variantă | 29,81% |
| 4+ | 7,43% | cel mult 1.680 de grupe de 4 acoperite | 7,43% |
| 5+ | 0,207% | cel mult 672 de grupe de 5; la 6 ieșite, cel mult 11 × 672 − 5 × 112 = 6.832 de combinații | 0,207% |
| 6 | 0,0008% | 112 variante distincte | 0,0008% |

Cod 59 atinge marginea la fiecare prag. Nicio altă așezare de 112 variante pe
aceleași 16 numere, a aplicației sau a oricui, nu prinde mai des la vreun prag.
Wheel-ul aplicației cu plafonul 112 are exact aceleași șanse.

Completarea lui Cod 59 până la garanția completă (4 sigur la 4 ieșite):

- au rămas 140 de grupe de 4 neacoperite;
- orice variantă nouă de 6 numere acoperă cel mult 3 dintre ele;
- sunt necesare cel puțin 47 de variante în plus (159 în total), mai multe decât
  cele 152 ale coverului complet al aplicației.

O variantă adăugată lângă Cod 59 aduce cel mult 0,011 puncte procentuale la 4+.
În medie, o variantă a schemei aduce 0,066 puncte. Trecerea la coverul de 152 de
variante (+320 lei) aduce 4+ de la 7,43% la 7,96%, adică 0,013 puncte pe
variantă: de cinci ori mai puțin pe leu decât Cod 59.

## Toate schemele: ce e dovedit și ce nu

Marginea e o margine, nu un maxim atins: o schemă egală cu ea e optimă la acel
prag, iar una sub ea nu e neapărat sub maximul posibil. Revizia adversarială a
arătat cazul: Joker Cod 45 nu atinge marginea la 4+ (0,0795%), dar niciuna dintre
cele 4.845 de așezări de 5 variante pe 7 numere (primul bloc fixat; orice
așezare se reetichetează așa) nu trece de 0,0733%, cât are schema. Pragurile sub
margine au trecut deci printr-o a doua verificare (`prove_lr_optimality.py`,
rezultat în `covering_designs/lr/optimal_offline.json`):

- dacă wheel-ul aplicației cu tot atâtea variante prinde mai des, la oricare
  garanție 2..bilet, se știe că se poate mai bine;
- altfel, căutare exhaustivă când încape, sau programare liniară în numere
  întregi (HiGHS, decalaj 0) până la 10 numere.

| Joc | Optime la fiecare prag | Optime numai la unele praguri |
|---|---|---|
| 6/49 | 48, 50, 56, 57, 58, 59 | 49: 3+ și 4+ (4+ prin ILP, 156 s); la 5+ wheel-ul aplicației cu garanția 6 dă 0,0266%, schema 0,0261% |
| 5/40 | 15, 16, 17, 25 | 18, 26, 27, 28, 36, 38: 3+ și 4+; 35, 37: 3+ |
| Joker | 15, 23, 25, 45 (4+ prin căutare exhaustivă) | 13, 12: 3+; 24: 4+; 35, 34, 14: niciun prag în afară de 5 |

La 6/49 pragul 6 și la Joker pragul 5 nu deosebesc așezările: orice variante
distincte, câte are schema, dau aceeași șansă.

Unde schema nu e dovedită optimă:

- se știe o așezare mai bună (wheel-ul aplicației) la 5/40 Cod 18, 26, 28 și 35
  (la 5, iar la Cod 35 și la 4+), la Joker Cod 35, 34, 24, 14, 13 și 12 și la
  6/49 Cod 49 la 5+;
- nu se știe la 5/40 Cod 27, 36, 37 și 38 la 5 și la Cod 37 la 4+ (14,91%,
  marginea 15,45%): geometriile sunt prea mari pentru căutare exactă, iar
  wheel-ul aplicației dă exact schema.

## Ce s-a schimbat în motor

La plafonul egal cu mărimea unei scheme LR cu tot atâtea numere și același bilet,
de la oricare joc, motorul o încearcă drept candidat
(`covering.lr_schemes.improve_with_lr_scheme`). O adoptă numai certificată
(`anneal_hits._certify`):

- același număr de variante distincte;
- numerele jucate rămân;
- niciun prag nu scade;
- un câștig strict la cel puțin un prag;
- acoperirea garanției nu scade.

Pe extrageri uniforme, numerele puse pe poziții nu schimbă șansele. Pool-urile
peste 16 numere nu trec prin etapă: șansa exactă nu se calculează acolo, iar
schemele 5/40 de 17-19 numere ridicau o eroare.

Adoptările, pe toate garanțiile 3..bilet și pe toate schemele de cel mult 16
numere, față de construcția motorului fără etapă:

| Joc, pool | Garanție, plafon | Schema | Înainte | Acum |
|---|---|---|---:|---:|
| 6/49, 10 | g5, 30 | 6/49 Cod 50 | 5+ 0,0500% | 0,0517% (+3,3%) |
| 5/40, 16 | g4, 348 | 5/40 Cod 37 | 4+ 14,581% | 14,912% (+2,3%) |
| 5/40, 11 | g4, 22 | Joker Cod 15 | 4+ 1,440% | 1,525% (+5,9%) |
| Joker, 11 | g4, 22 | Joker Cod 15 | 4+ 0,328% | 0,344% (+4,8%) |
| Joker, 16 | g4, 348 | 5/40 Cod 37 | 4+ 4,172% | 4,310% (+3,3%) |
| 5/40, 13 | g5, 173 | 5/40 Cod 27 | 5 0,1522% | 0,1535% (+0,9%) |
| 5/40, 15 | g5, 327 | 5/40 Cod 36 | 5 0,2882% | 0,2895% (+0,4%) |
| 5/40, 16 | g5, 443 | 5/40 Cod 38 | 5 0,3908% | 0,3932% (+0,6%) |

Celelalte praguri nu scad. La celelalte coduri, wheel-ul motorului e egal cu
schema (Cod 59), mai bun (Joker Cod 34) sau câștigă la alt prag (Cod 49), deci
rămâne cel de dinainte.

## Garanția egală cu biletul, peste coverul complet al garanției 4

Revizia a găsit și o cădere care nu ținea de scheme. Cu garanția egală cu
biletul și un plafon, motorul încearcă biletele unei garanții mai mici
(`covering.guarantee_portfolio`). Când plafonul trecea de coverul complet al
acelei garanții, motorul dădea coverul, cu mai puține bilete decât plafonul, iar
certificarea (același număr de bilete) îl respingea. Acum coverul se completează
până la plafon cu bilete distincte din wheel-ul livrat, apoi se certifică. Un
bilet în plus nu scade șansa la niciun prag.

| Joc, pool 16, garanția 5 | Plafon | 4+ înainte | 4+ acum | 5 înainte | 5 acum |
|---|---:|---:|---:|---:|---:|
| 5/40 | 405 | 8,296% | 16,027% | 0,2860% | 0,3639% |
| 5/40 | 442 | 8,630% | 16,027% | 0,3117% | 0,3902% |
| 5/40 | 444 | 8,652% | 16,027% | 0,3133% | 0,3917% |
| 5/40 | 500 | 9,156% | 16,027% | 0,3524% | 0,4290% |
| Joker | 450 | 2,447% | 4,678% | 0,0368% | 0,0368% |

3+ era deja maximul pool-ului (45,56% la 5/40, 23,29% la Joker). La 6/49, pool
16, garanția 6, plafonul 160 sau 200, coverul de 152 completat ar ridica 4+, dar
ar coborî 5+, deci certificarea păstrează wheel-ul. Cheia walk-forward trece de
la `lg1` la `lg2`.

## La Joker, variantele aplicației prind mai des decât codurile LR

La același cost, wheel-ul aplicației e cel puțin la fel la fiecare prag și mai
bun la cel puțin unul:

| Cod | Numere | Variante | Cod LR | Aplicația (plafon egal) |
|---|---:|---:|---|---|
| 35 | 8 | 6 | 3+ 2,72%, 4+ 0,092% | 3+ 2,83%, 4+ 0,095% (garanția 3) |
| 34 | 9 | 9 | 3+ 3,94%, 4+ 0,134% | 3+ 4,09%, 4+ 0,143% (garanția 5) |
| 14 | 12 | 38 | 3+ 10,82%, 4+ 0,548% | 3+ 10,91%, 4+ 0,577% |
| 13 | 13 | 54 | 4+ 0,765% | 4+ 0,810% |
| 12 | 15 | 118 | 4+ 1,58% | 4+ 1,69% (marginea: 1,695%) |

La agenție, variantele aplicației se joacă ca variante simple, la același preț pe
variantă. Codurile LR dau în schimb 4 sigur pe o variantă când ies 5 dintre
numerele jucate; wheel-ul aplicației, în 97,67-99,33% din cazuri. Panoul spune
amândouă.

La 5/40, câștigul aplicației la Cod 26, 28 și 35 e sub 1% relativ: panoul îl
arată, dar nu recomandă variantele simple în locul codului.

La 5/40, cu 16 numere, coverul complet al aplicației dă aceleași 3+ și 4+ ca
Cod 38, cu 404 variante în loc de 443 (195 lei mai puțin). La 5, aplicația dă
0,363%, iar Cod 38 0,393%.

## În aplicație

Panoul de cost spune, pentru fiecare cod potrivit pool-ului:

- la ce praguri schema e dovedit optimă;
- unde se știe o așezare mai bună, cu sursa ei;
- unde nu se știe, cu marginea superioară și precizarea că marginea nu e mereu
  atinsă;
- ce dă aplicația la același cost, cu recomandarea variantelor simple numai de
  la 1% câștig relativ în sus și cu garanția pe care o pierde jucătorul, sau ce
  dă cu mai puține variante, când acoperă la fel.

Wheel-urile aplicației pe mărimea fiecărei scheme, cu șansele și profilul
garanției, sunt precalculate în `covering_designs/lr/app_same_size.json`, iar
dovezile offline în `covering_designs/lr/optimal_offline.json`. Testele refac
intrările ieftine cu motorul curent, căutarea exhaustivă la Joker Cod 45 și
modelul ILP pe aceeași geometrie.

Reproducere:

    python scripts/analysis/analyze_lr_schemes.py --wf <cele trei cache-uri WF v31> \
        --out scripts/analysis/lr_schemes_2026-10-20.json \
        --app-out covering_designs/lr/app_same_size.json
    python scripts/analysis/prove_lr_optimality.py

Cheia walk-forward primește `lr<amprenta>` numai la plafonul egal cu mărimea
unei scheme pe un pool de cel mult 16 numere, deci se invalidează numai acele
chei. La câteva dintre ele schema nu se poate certifica (Cod 59 la 112, Cod 49
la 15), iar cheia se schimbă o singură dată fără schimbare de bilete. Cache-ul
worker trece la `v21`. Pool-ul și scorerul nu se schimbă.
