# Ecran 3 de metode — matematici avansate netestate, 2026-11-01

## Concluzie

**Nicio metodă nouă nu se promovează.** 73 de scoreri în 17 familii noi (valuri
Haar, AR(2) online, informație mutuală întârziată, matrix-profile/motif, heat
kernel pe graful de co-apariție, centralitate vector-propriu, procese punctuale
cu refractare, abatere chi2 față de uniform, z conformal, Kalman cu trend,
GARCH, dependență de coadă, FFT top-3 pe axa timp, shrinkage Beta ierarhic,
autocorelație/ciclu, hazard Weibull, HMM cu 2 stări), pe 3 jocuri × 2 pool-uri ×
2 praguri = 12 celule/joc, 292 de teste/joc, 36 de candidați înghețați.

1. **Nulul căutării nu e depășit.** p-ul căutării (max z pe 60 de istorii
   sintetice uniforme, toată banca refăcută) între 0,26 și 0,95 per celulă.
2. **Confirmarea nu arată niciun avantaj.** Toți cei 36 de candidați înghețați
   au Holm = 1,000 față de lider (McNemar) și față de hazard (binomial vs rata
   hipergeometrică). Cel mai apropiat semnal brut: `fft3_W512` pe 6/49 pool 11
   la 4+ — 2,729% pe confirmare vs hazard 1,788% (p = 0,044 neajustat), dar
   Holm = 1,000 și liderul a dat tot 2,729%; `mi_L200_lag1` are p extern 0,005,
   dar pe confirmare românească e sub hazard (7,776% vs 7,960%, p = 0,59) —
   nu replică. Cu 36 de teste, un p < 0,05 apare din întâmplare.
3. **Replicarea externă nu confirmă** niciun candidat (p extern 0,005–0,92; cel
   mai mic aparține unui candidat care nu trece pe confirmarea locală).

Verificări: leak (schimbarea unei zile nu schimbă scorurile până la acea zi) și
echivarianța (redenumirea numerelor mută scorul odată cu numărul) trec pe toți
cei 73 de scoreri, pe 6/49 real și pe istorie sintetică (`test_advanced_screen_2026_11_01.py`).
 două familii au fost scoase ÎNAINTE de rulare tocmai pentru că pică aceste
verificări: W1 pe axa numerelor (kernel/CDF pe linia 1..N = filtru structural de
poziție) și FFT pe axa etichetelor; înlocuite cu variante echivariante (chi2 per
număr, FFT pe axa timp). Un leak real prins de test în HMM (emisia folosea
extragerea zilei curente) a fost corectat înainte de rularea completă.

Rata pool-ului rămâne **hipergeometrică**; niciuna dintre aceste metode nu o
poate ridica. Liderii de producție au și ei rate pe confirmare la nivelul
hazardului. Nimic nu intră în aplicație; decizia și producția rămân neschimbate.

## Protocol

Fixat în docstring-ul scriptului înainte de rulare; identic cu
`hit5plus_screen_2026-11-01.py` / `hit5plus_screen2_2026-11-01.py`: România
6/49, 5/40, Joker Urna 1; pool (11, 16) × țintă (4+, 5+); 200 zile warmup; 70%
dezvoltare / 30% confirmare; lider = decizia din `folds.csv` la tinta 4
(6/49: `markov_self_state` k11 / `markov_lag2` k16; 5/40: `hot_consistency` k11 /
`neighbor_adjacent` k16; Joker: `rwr_last_draw` k11 / `frequency` k16); selecție
pe dezvoltare (top-2 după z eveniment + top-1 după z hituri, fără dependență de
egalități, stabil pe ambele jumătăți); nul pe 60 de istorii sintetice; McNemar +
binomial + Holm pe confirmare; replicare externă (6/49 → 6/49 străine, Joker →
6/45 și 5/50); promovare doar cu Holm < 0,05 pe ambele și p extern < 0,05.

Fișiere: `advanced_screen_2026-11-01.py` (banca + rulare),
`advanced_screen_2026-11-01.json` (rezultate complete, cu `data_sha256`),
`test_advanced_screen_2026_11_01.py` (validator: leak, echivarianță, prinderea
unui leak/bias introdus intenționat). Rulare: 760 s pe 16 procesoare.
