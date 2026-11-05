# Rafinarea coverului cu plafon la pool > 19 — 2026-10-31

Cerința: mai multe hituri 4+ și 5+. Rata pool-ului rămâne hipergeometrică; la
pool ≤ 16 cu cover complet g4, 4+ pe bilet e deja la maximul pool-ului, iar 5+
e aproape de optim (recoacerea din 2026-10-16 nu a găsit îmbunătățiri
certificate; ILP-ul exact pe pool 7–15 confirmă „fără câștig” acolo unde a
terminat). Pârghia rămasă cu câștig măsurat: **pool mare cu plafon** (permis
din 2026-10-30), unde construcția greedy din `covering.large_pool` lăsa
grupe de 4 neîmpachetate (ex. pool 20, plafon 152: 2.064 din 2.280 posibile).

## Ce s-a livrat

`covering.large_pool._refine_capped_cover`: căutare locală deterministă peste
schimbări de câte un bilet, după construcția greedy, **numai când coverul e
parțial (oprit de plafon)** și `plafon ≤ 4096` și `garanție < bilet`. Se acceptă
numai mutări cu `(grupe de g noi acoperite, grupe de g+1 noi acoperite)`
lexicografic pozitive, deci acoperirea garanției (raportată exact) nu scade
niciodată. Păstrează: numărul de bilete, biletele distincte, pozițiile jucate.
Fără scoruri, fără date, fără RNG global — designul rămâne funcție pură a
geometriei, cache-uit per proces (`_canonical_cover`). Nu apelează
`wheel_hit_profile`/`wheel_hit_probabilities` (interzise pe ruta large).

Chei: `lp1` → `lp2` peste 19 în `_wheel_sig` (WF) și în cheia de cache a
worker-ului; testele aferente actualizate. Fără bump global de versiune.

## Cifre exacte (extragere uniformă, din mulțimea grupelor acoperite)

| Joc | v | plafon | grupe 4 acoperite | 4+ pe bilet | 5+ pe bilet |
|---|---:|---:|---|---|---|
| 6/49 | 20 | 152 | 2064 → 2146 (max 2280) | 9,2742% → 9,5691% (+3,2%) | 0,2751% → 0,2774% (+0,8%) |
| 6/49 | 24 | 152 | 2233 → 2256 | 10,9053% → 11,0181% (+1,0%) | +0,2% |
| 6/49 | 28 | 152 | 2270 → 2280 (max) | 11,9316% → 12,0218% (+0,8%) | +0,1% |
| 6/49 | 49 | 152 | 2275 → 2280 (max) | 13,4455% → 13,4934% (+0,4%) | +0,1% |
| 6/49 | 20 | 404 | 4062 → 4158 | 15,2852% → 15,5646% (+1,8%) | 0,6784% → 0,6860% (+1,1%) |
| 6/49 | 28 | 404 | 5787 → 5934 (max 6060) | 23,1799% → 23,6849% (+2,2%) | +0,6% |
| 5/40 | 20 | 404 | 1946 → 2020 (max) | 18,0295% → 18,5000% (+2,6%) | 0,3664% → 0,3684% (+0,5%) |
| Joker | 20 | 404 | 1946 → 2020 (max) | 5,1425% → 5,3102% (+3,3%) | 0,0331% (invariant la bilet de 5) |

La pick 5 peste v ≈ 24 greedy-ul împachetează deja perfect (cap·5 grupe de 4),
deci rafinarea nu mai găsește nimic — la fel la 6/49 peste v ≈ 36. Cost: ≤ ~2 s
pe geometrie, o dată per proces (cache `lru_cache`).

Verificat la pool 20 (unde aplicația mai are șanse exacte): 4+ și 5+ pe bilet
nu scad la niciun prag 3..6 (`test_refined_cover_raises_exact_hit_chances_at_pool_20`).

## Peisajul pool × plafon (6/49, 4+ pe bilet, înainte de rafinare)

La același număr de variante, pool mai mare = mai multe 4+ pe bilet (grupele de
4 au mai mult loc să fie împachetate fără suprapunere): plafon 152 → 7,96% la
v=16, 9,27% la v=20, 13,45% la v=49; plafon 404 → 15,29% la v=20, 32,72% la
v=49. Rata pool-ului la v=49 e 100% (degenerată), deci metricile relevante
sunt acoperirea raportată și hiturile pe bilete din walk-forward.

## Limite rămase

- Pool 17–19 cu plafon rămân pe ruta clasică (greedy cu scoruri, dependent de
  pasul WF); acolo greedy-ul lasă și el grupe neîmpachetate (pool 17, plafon
  152: 1.789/2.280), dar rafinarea pozițională cache-uită nu se poate aplica
  fără a schimba semantica per pas. Nerlivrat.
- La pool ≤ 16, coverul complet g4: 4+ pe bilet e la maximul pool-ului; 5+ e
  aproape de optim — recoacerea din 2026-10-16 nu a găsit îmbunătățiri
  certificate, iar ILP-ul exact (`scripts/analysis/gen_refined_covers_ilp.py`,
  HiGHS) confirmă „fără câștig” la pool 7–9 și 11 unde a terminat; la v ≥ 10
  modelul e prea mare pentru HiGHS (fără soluție fezabilă în 600 s).
- Designurile cu buget `H_16_6_4` (65..151) și `H_16_5_4` (321..403) rămân cele
  din 2026-10-16/22.

## Reproducere

```
python -m pytest -q TESTE/test_large_pool_refine.py TESTE/test_large_pool_wheels.py TESTE/test_large_pool_views_wf.py
python -m pytest -q   # suita completă
```

Teste noi: `test_large_pool_refine.py` (11 teste: invariante, determinism, fără
RNG global, șanse exacte la pool 20, sărirea peste cover complet/sistem
complet/plafon mare, semnătura `lp2`).
