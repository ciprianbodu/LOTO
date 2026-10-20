# Schemele LR: unde se mai pot obține hituri — 2026-10-20

Cerința: cod care să dea mai multe hituri. Utilizatorul joacă la 6/49 schema
redusă Cod 59 (16 numere, 112 variante, 896 lei). Rata pool-ului rămâne
hipergeometrică, deci singurul loc pentru mai multe hituri este așezarea
numerelor pe variante, la același cost. Toate cifrele sunt exacte, pe extrageri
uniforme (`lr_schemes_2026-10-20.json`, `analyze_lr_schemes.py`).

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

## Toate schemele, față de maximul posibil

Marginea se aplică oricărei așezări cu tot atâtea variante pe tot atâtea numere.

| Joc | Coduri optime la fiecare prag | Coduri sub margine |
|---|---|---|
| 6/49 | 48, 50, 56, 57, 58, 59 | 49: 4+ 1,11% (cel mult 1,18%), 5+ 0,026% (0,027%) |
| 5/40 | 15, 16, 17, 25 | 18, 26, 27, 28, 36, 38: numai la 5, cu 0,2-4,1% sub margine; 35: 4+ 11,16% (cel mult 11,58%) și la 5; 37: 4+ 14,91% (15,45%) și la 5 |
| Joker | 15, 23, 25 | 45: 4+; 35, 34: 3+ și 4+; 24: 3+; 14: 3+ și 4+; 13, 12: 4+ |

Marginea nu e mereu atinsă de vreo așezare; arată numai cât loc ar putea exista.

## Ce s-a schimbat în motor

La plafonul egal cu mărimea unei scheme LR pentru pool-ul generat, motorul o
încearcă drept candidat (`covering.lr_schemes.improve_with_lr_scheme`). O
adoptă numai certificată (`anneal_hits._certify`):

- același număr de variante distincte;
- numerele jucate rămân;
- niciun prag nu scade;
- un câștig strict la cel puțin un prag;
- acoperirea garanției nu scade.

Pe extrageri uniforme, numerele puse pe poziții nu schimbă șansele.

| Joc, pool, garanție, plafon | Înainte | Acum (schema LR) |
|---|---:|---:|
| 6/49, 10 numere, g5, 30 (Cod 50) | 5+ 0,0500% | 5+ 0,0517% (+3,4%) |
| 5/40, 16 numere, g4, 348 (Cod 37) | 4+ 14,58% | 4+ 14,91% (+2,3%) |
| Joker, 11 numere, g4, 22 (Cod 15) | 4+ 0,328% | 4+ 0,344% (+4,8%) |

Celelalte praguri nu scad. La celelalte coduri, wheel-ul aplicației e egal cu
schema (Cod 59), mai bun (Joker Cod 34) sau câștigă la alt prag (Cod 49), deci
rămâne cel de dinainte.

## La Joker, variantele aplicației prind mai des decât codurile LR

La același cost, wheel-ul aplicației domină codurile Joker 35, 34, 14, 13 și 12
la fiecare prag:

| Cod | Numere | Variante | Cod LR | Aplicația (plafon egal) |
|---|---:|---:|---|---|
| 35 | 8 | 6 | 3+ 2,72%, 4+ 0,092% | 3+ 2,83%, 4+ 0,095% |
| 34 | 9 | 9 | 3+ 3,94%, 4+ 0,134% | 3+ 4,40%, 4+ 0,142% |
| 14 | 12 | 38 | 3+ 10,82%, 4+ 0,548% | 3+ 10,91%, 4+ 0,577% |
| 13 | 13 | 54 | 4+ 0,765% | 4+ 0,810% |
| 12 | 15 | 118 | 4+ 1,58% | 4+ 1,69% (marginea: 1,695%) |

La agenție, variantele aplicației se joacă ca variante simple, la același preț pe
variantă. Codurile LR dau în schimb 4 sigur pe o variantă când ies 5 dintre
numerele jucate (wheel-ul aplicației: 92,86-99,33% din cazuri). Cazul cu exact 4
numere ieșite e mult mai frecvent, iar acolo aplicația câștigă.

La 5/40, cu 16 numere, coverul complet al aplicației dă aceleași 3+ și 4+ ca
Cod 38, cu 404 variante în loc de 443 (195 lei mai puțin). La 5, aplicația dă
0,363%, iar Cod 38 0,393%.

## În aplicație

Panoul de cost spune, pentru fiecare cod potrivit pool-ului:

- dacă schema e optimă la fiecare prag sau cât e sub margine;
- ce dă aplicația la același cost, când prinde mai des, sau cu mai puține
  variante, când acoperă la fel.

Wheel-urile aplicației pe mărimea fiecărei scheme sunt precalculate în
`covering_designs/lr/app_same_size.json`; testul le reface pe cele ieftine cu
motorul curent.

Cheia walk-forward primește `lr<amprenta>` numai la plafonul egal cu mărimea
unei scheme, deci se invalidează numai acele chei. Cache-ul worker trece la
`v21`. Pool-ul și scorerul nu se schimbă.
