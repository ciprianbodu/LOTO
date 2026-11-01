# Ecran de metode noi orientate pe hituri 4+ și 5+, 2026-11-01

## Concluzie

**Nicio metodă nouă nu se promovează.** Am testat 42 de scoreri noi în 6
familii netestate până acum (triple/quadruple observate, triple care ating
ultimul joc, NMF, frecvență condiționată de cluster k-means, lift de perechi cu
dezintegrare exponențială), pe 3 jocuri × 2 pool-uri × 2 praguri (4+, 5+) =
12 celule, 168 de teste per joc.

1. **Nulul căutării nu e depășit nicăieri.** Cel mai mare z de dezvoltare pe
   date reale rămâne în distribuția nulului (100 de istorii sintetice uniforme
   per joc, întreaga bancă refăcută pe fiecare): p-ul căutării între 0,17 și
   0,98 per celulă. Nu există nici măcar un candidat care să iasă din
   întâmplare la nivelul căutării.
2. **Confirmarea (ultimele 30%) nu arată niciun avantaj.** Toți cei 36 de
   candidați înghețați au Holm = 1,000 atât față de liderul clasamentului
   (McNemar), cât și față de hazard (binomial față de rata hipergeometrică).
   Ratele pe confirmare sunt la nivelul sau sub lider: ex. 6/49 pool 16, 4+ —
   lider 9,41% vs cel mai bun candidat nou 7,37% (hazard 7,96%); 6/49 pool 16,
   5+ — lider 1,09% = hazard 1,09%, cel mai bun candidat nou 1,64% (p = 0,109).
3. **Replicarea externă nu confirmă.** p extern între 0,027 și 0,872; singura
   valoare sub 0,05 (`anti_quad_Lall`, 6/49 pool 16, 4+, p = 0,027 pe cele 6
   arhive străine 6/49) nu are corespondent în România (p = 0,742 față de
   hazard, 0,926 față de lider) — cu 24 de teste externe, o valoare sub 0,05
   apare din întâmplare de ~1,2 ori; nu se promovează.

Rata pool-ului la 4+ și 5+ rămâne **hipergeometrică**: 6/49 pool 16 → 7,96% la
4+, 1,09% la 5+; 5/40 pool 16 → 16,03% la 4+, 2,94% la 5+; Joker Urna 1 pool 16
→ 4,68% la 4+, 0,36% la 5+. Nicio metodă de scoring nu o poate depăși
demonstrabil — sub extrageri uniforme, speranța de hituri depinde doar de
mărimea pool-ului. Singurele pârghii reale pentru mai multe hituri 4+/5+ rămân
pe partea de acoperire (designuri de bilete), deja la plafon pentru pool 16 /
garanție 4 (audit 2026-10-22), și pool-uri mai mari cu garanție parțială
(cercetare în `extended_pool/`, nefolosită — utilizatorul joacă numai scheme
LR).

## Protocol

Fixat înainte de rulare (în docstring-ul scriptului):

- **Date:** România 6/49, 5/40 (șase extrase), Joker Urna 1. Scorul unei zile
  vede numai zilele anterioare; 200 de zile warmup; primele 70% din rest
  dezvoltare, ultimele 30% confirmare. Amprentele sha256 ale CSV-urilor (și
  ale `bench_results/folds.csv`) sunt în JSON, câmpul `data_sha256`; scriptul
  refuză să scrie rezultatul dacă istoricul se schimbă în timpul rulării.
- **Celule:** pool (11, 16) × tinta (4+, 5+) per joc. Liderul = câștigătorul
  deciziei din `folds.csv` la tinta 4 (decizia de producție), rulat cu
  `call_method` + `rank_by_score` + rezerva `frequency`, ca în producție.
- **Candidati:** 6 familii × hiperparametri, fiecare și cu semn schimbat
  (`anti_`): T1 triple completion (L ∈ 200/500/all × lift/raw), T2 quad
  completion, T10 triple-hot (triple care ating ultimul joc), T3 NMF
  (rang 3/6 × L 300/all), T4 cluster-conditional k-means (k 3/5 × L 500/all),
  T6 decayed pair lift (halflife 10/30/100). Toate echivarianțele la
  redenumirea numerelor verificate (42/42), fără leak (42/42), fără filtre
  structurale, deterministe.
- **Nul:** 100 de istorii sintetice uniforme per joc, cu aceeași structură de
  zile; pe fiecare se reface toată banca; maximul z per celulă dă p-ul căutării.
- **Selecție pe dezvoltare:** doar candidați fără dependență de egalități
  (locul K decis de număr în >50% din zile) și stabili (exces de hituri pe
  ambele jumătăți); per celulă primii 2 după z eveniment + primul după z
  hituri → 36 înghețați.
- **Confirmare:** McNemar exact unilateral față de lider și binomial exact
  unilateral față de rata hipergeometrică, Holm peste toți înghețații;
  replicare externă (6/49 → 6 arhive străine 6/49; Joker Urna 1 → 6/45 ×3 +
  5/50), z pe suma evenimentelor. Promovare doar cu Holm < 0,05 pe ambele și
  p extern < 0,05.

## Rezultate pe scurt

| Joc | Celulă | Lider (t=4) | Lider conf | Hazard p0 | Cel mai bun z căutare (p) |
|---|---|---|---|---|---|
| 6/49 | 11, 4+ | markov_self_state | 2,73% | 1,79% | 2,08 (0,40) |
| 6/49 | 11, 5+ | markov_self_state | 0,41% | 0,13% | 1,27 (0,73) |
| 6/49 | 16, 4+ | markov_lag2 | 9,41% | 7,96% | 1,50 (0,75) |
| 6/49 | 16, 5+ | markov_lag2 | 1,09% | 1,09% | 2,10 (0,33) |
| 5/40 | 11, 4+ | hot_consistency | 4,39% | 3,85% | 2,67 (0,17) |
| 5/40 | 11, 5+ | hot_consistency | 0,21% | 0,36% | 0,58 (0,95) |
| 5/40 | 16, 4+ | neighbor_adjacent | 19,25% | 16,03% | 1,13 (0,93) |
| 5/40 | 16, 5+ | neighbor_adjacent | 2,93% | 2,94% | 2,09 (0,29) |
| Joker U1 | 11, 4+ | rwr_last_draw | 2,31% | 0,96% | 2,11 (0,45) |
| Joker U1 | 11, 5+ | rwr_last_draw | 0,00% | 0,04% | 0,65 (0,97) |
| Joker U1 | 16, 4+ | frequency | 7,08% | 4,68% | 0,87 (0,98) |
| Joker U1 | 16, 5+ | frequency | 0,33% | 0,36% | 1,80 (0,54) |

Detalii per candidat (36 înghețați, cu dev z, rate pe confirmare, McNemar,
binomial, Holm, replicare externă): `hit5plus_screen_2026-11-01.json`.

Observație: liderii aleși de decizie au și ei rate pe confirmare apropiate de
hazard (ex. 6/49 16, 5+: 1,09% = exact p0) — selecția pe dezvoltare nu găsește
avantaj nici pentru metodele existente, ceea ce întărește concluzia că
pragurile 4+/5+ ale pool-ului sunt la plafonul hipergeometric.

## Reproducere

```
python scripts/analysis/hit5plus_screen_2026-11-01.py            # ~13 min, 16 workeri
HIT5_NULL=100 HIT5_WORKERS=16 python scripts/analysis/hit5plus_screen_2026-11-01.py
```

Rulare efectivă: 795 s, 16 workeri, 100 de istorii nule per joc; check-uri:
42/42 scoreri fără leak, 42/42 echivarianți. Nicio modificare în aplicație —
niciun candidat nu a trecut protocolul de promovare.
