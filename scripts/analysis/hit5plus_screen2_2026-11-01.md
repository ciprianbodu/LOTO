# Ecran 2 de metode orientate pe hituri 4+ și 5+ (ansambluri dinamice + semnale cross-game), 2026-11-01

## Concluzie

**Nicio metodă nouă nu se promovează.** Al doilea ecran a testat 29 de scoreri
în 5 familii de ansamblu dinamic — 16 FTL la 5+ (alege zilnic scorerul de bază
cu cele mai multe evenimente 5+ recente, ±anti), 4 cross-game (suma
frecvențelor pe fereastră a celorlalte două jocuri românești, aliniate pe zi,
±anti), 3 voturi (medie de ranguri, top-K pe prag de rang), 2 stacking logistic
online pe ranguri, 4 Kalman nivel local (±anti) — pe aceleași 3 jocuri × 2
pool-uri × 2 praguri = 12 celule, 116 teste per joc.

1. **Nulul căutării nu e depășit.** p-ul căutării (max z pe 100 de istorii
   sintetice uniforme, toată banca refăcută) între 0,07 și 0,90 per celulă;
   cel mai mic, 0,069 (Joker Urna 1, pool 11, 4+), e la nivelul așteptat din
   12 celule × căutare multiplă.
2. **Confirmarea nu arată niciun avantaj.** Toți cei 36 de candidați
   înghețați au Holm = 1,000 față de lider (McNemar) și față de hazard
   (binomial vs rata hipergeometrică). Cel mai apropiat semnal: `cross_W50`
   pe Joker Urna 1 pool 16 la 5+ — 0,99% pe confirmare vs hazard 0,36%
   (p = 0,023 neajustat), dar Holm = 0,838 și McNemar față de lider
   p = 0,145 (lider 0,33%); cu 36 de teste, un p < 0,05 apare din întâmplare.
3. **Replicarea externă nu confirmă** (19 candidați eligibili; E2 cross-game
   nu are istorice paralele externe). p extern între 0,076 și 0,884.

Rata pool-ului la 4+/5+ rămâne **hipergeometrică** (6/49 pool 16: 7,96% la
4+, 1,09% la 5+; 5/40 pool 16: 16,03% / 2,94%; Joker Urna 1 pool 16: 4,68% /
0,36%). Și liderii de producție au rate pe confirmare la nivelul hazardului
(ex. 6/49 pool 16 la 5+: 1,09% = exact p0). Singurele pârghii reale pentru mai
multe hituri 4+/5+ rămân pe partea de acoperire (designuri de bilete, deja la
plafon pentru pool 16 / garanție 4) și pool-uri mai mari cu garanție parțială
(cercetare în `extended_pool/`, nefolosită — utilizatorul joacă numai scheme
LR).

## Protocol

Fixat înainte de rulare (în docstring-ul scriptului); identic cu ecranul 1,
plus:

- **Banca de bază comună:** freq50/freq200/freqall, ewma(0,05/0,2), markov
  lag-1, co-ocurență, pair-lift cu dezintegrare (halflife 30), triple top-500.
- **Echivarianță la redenumirea numerelor:** rangurile se calculează cu
  `rankdata(method="average")` pe scoruri rotunjite la 10 zecimale (rotunjirea
  elimină zgomotul float ~1e-16 care altfel rupe egalitățile exacte și schimbă
  rangurile medii cu O(1/N)); top-K pe prag de rang, nu pe lexsort cu index;
  FTL selectează pe hit-uri cu top-K echivariant. E4 stacking păstrează o
  toleranță mai largă (1e-4) — `np.linalg.solve` cu pivotare are zgomot de
  rotunjire ~1e-5 sub permutare. Verificat: 87/87 scoreri (29 × 3 jocuri)
  echivarianți, 87/87 fără leak (inclusiv familia cross-game, care vede și
  celelalte jocuri).
- **E2 cross-game:** suma ferestrelor de frecvență (W ∈ 50/200) ale celorlalte
  două jocuri românești, aliniate pe indexul de zi, mapate n→n doar pe
  1..min(N); nulul construiește sintetice toate cele 3 jocuri din același
  flux rng; fără replicare externă (nu există istorice paralele).
- **Selecție/confirmare/promovare:** ca în ecranul 1 — dev 70% / conf 30%,
  nul pe 100 de istorii per joc, McNemar vs lider + binomial vs hipergeometric
  cu Holm, promovare doar cu Holm < 0,05 pe ambele și p extern < 0,05.
  Amprente sha256 în JSON (`data_sha256`); scriptul refuză să scrie dacă
  istoricul se schimbă în timpul rulării.

## Rezultate pe scurt

| Joc | Celulă | Lider (t=4) | Lider conf | Hazard p0 | Cel mai mare z căutare (p) |
|---|---|---|---|---|---|
| 6/49 | 11, 4+ | markov_self_state | 2,73% | 1,79% | 1,34 (0,64) |
| 6/49 | 11, 5+ | markov_self_state | 0,41% | 0,13% | 1,27 (0,62) |
| 6/49 | 16, 4+ | markov_lag2 | 9,41% | 7,96% | 0,68 (0,90) |
| 6/49 | 16, 5+ | markov_lag2 | 1,09% | 1,09% | 1,87 (0,40) |
| 5/40 | 11, 4+ | hot_consistency | 4,39% | 3,85% | 1,87 (0,44) |
| 5/40 | 11, 5+ | hot_consistency | 0,21% | 0,36% | 1,60 (0,52) |
| 5/40 | 16, 4+ | neighbor_adjacent | 19,25% | 16,03% | 1,29 (0,72) |
| 5/40 | 16, 5+ | neighbor_adjacent | 2,93% | 2,94% | 1,18 (0,70) |
| Joker U1 | 11, 4+ | rwr_last_draw | 2,31% | 0,96% | 3,22 (0,07) |
| Joker U1 | 11, 5+ | rwr_last_draw | 0,00% | 0,04% | 0,65 (0,84) |
| Joker U1 | 16, 4+ | frequency | 7,08% | 4,68% | 2,39 (0,14) |
| Joker U1 | 16, 5+ | frequency | 0,33% | 0,36% | 1,80 (0,27) |

Detalii per candidat (36 înghețați, cu rate pe confirmare, McNemar, binomial,
Holm, replicare externă): `hit5plus_screen2_2026-11-01.json`.

## Reproducere

```
HIT5_NULL=100 HIT5_WORKERS=16 python scripts/analysis/hit5plus_screen2_2026-11-01.py
```

Rulare efectivă: 166 s, 16 workeri, 100 de istorii nule per joc; check-uri:
87/87 fără leak, 87/87 echivarianți. Nicio modificare în aplicație — niciun
candidat nu a trecut protocolul de promovare.
