# Rafinarea designurilor cu buget, 6/49 pool 14, 15 și 16 — 2026-10-16

La același pool și același număr de variante, **62 de bugete sunt îmbunătățite**:
- **Pool 14**: **14 din cele 15 bugete** (65..79) sunt îmbunătățite (93,3%), cu câștig relativ la 4+ de până la +1,794% și la 5+ de până la +0,334%;
- **Pool 15**: **38 din cele 52 de bugete** (65..116) sunt îmbunătățite (73,1%), cu câștig relativ la 4+ de până la +5,014% și la 5+ de până la +2,082%;
- **Pool 16**: **10 din cele 87 de bugete** (65..151) sunt îmbunătățite suplimentar prin căutare exhaustivă iterativă (`--source-stride 1 --extension-step 5 --max-rounds 2`).

Nu scade niciun prag 3+..6+ și nicio acoperire a grupelor de 4 (garanția 100% când se atinge acoperirea completă). Certificarea este separată și exhaustivă pentru **6/49** și **6/45**, deoarece fișierele sunt comune ambelor geometrii. Nu se schimbă pool-ul, scorerul sau costul unui buget.

## Câștiguri reprezentative

| Pool | Variante | Grupe de 4 acoperite / Total | P(4+), înainte → după | Câștig relativ 4+ | P(5+), înainte → după | Câștig relativ 5+ |
|---:|---:|---:|---:|---:|---:|---:|
| 14 | 65 | 845 → 850 / 1.001 | 3,990263% → 4,008803% | +0,465% | 0,113063% → 0,113441% | +0,334% |
| 14 | 70 | 896 → 901 / 1.001 | 4,491636% → 4,509893% | +0,406% | 0,126868% → 0,127209% | +0,269% |
| 14 | 78 | 986 → 991 / 1.001 | 5,348003% → 5,444004% | +1,795% | 0,147814% → 0,147890% | +0,051% |
| 15 | 65 | 903 → 918 / 1.365 | 4,334657% → 4,374020% | +0,908% | 0,116639% → 0,117624% | +0,845% |
| 15 | 80 | 1.085 → 1.134 / 1.365 | 5,618395% → 5,745580% | +2,264% | 0,154289% → 0,156488% | +1,425% |
| 15 | 100 | 1.258 → 1.295 / 1.365 | 7,163071% → 7,370381% | +2,894% | 0,201712% → 0,205910% | +2,081% |
| 15 | 115 | 1.348 → 1.363 / 1.365 | 8,367151% → 8,786711% | +5,014% | 0,238533% → 0,240353% | +0,763% |
| 16 | 65 | 975 → 975 / 1.820 | 4,705518% → 4,707406% | +0,040% | 0,120389% → 0,120389% | = |
| 16 | 70 | 1.042 → 1.042 / 1.820 | 5,099304% → 5,100913% | +0,032% | 0,133695% → 0,133695% | = |
| 16 | 125 | 1.579 → 1.579 / 1.820 | 7,422204% → 7,423134% | +0,013% | 0,227546% → 0,227641% | +0,042% |

Toate probabilitățile sunt calculate exact prin enumerare combinatorică pe extrageri uniforme cu `covering.probability.wheel_hit_probabilities`.

## Metodă și verificare

1. **Prunare descendentă**: pornește de la noile covere complete reduse din depozitul La Jolla (C_14 la 80 de variante, C_15 la 117 variante, C_16 la 152 de variante) și elimină varianta cu cea mai mică pierdere marginală exactă.
2. **Extindere ascendentă**: adaugă greedy varianta cu cel mai mare câștig marginal exact pe 4+/5+, cu toate pragurile și acoperirea garantate.
3. **Optimizare iterativă până la convergență**: scriptul `refine_budget_designs.py` a primit suport pentru `--max-rounds` (iterare automată până când nu se mai găsește nicio îmbunătățire). Toate bazinele converg în 2 runde.
4. **Certificare `_certify` independentă**: fiecare secțiune este verificată pe ambele universuri (49 și 45) conform contractului de producție: același număr de variante distincte, toate pozițiile 1..v jucate, probabilitățile 3..extrase nedescrescătoare, câștig strict la cel puțin un prag și acoperirea garanției nedescrescătoare.

## Reproducere și amprente

Comandă:
```powershell
python scripts/analysis/refine_budget_designs.py --input-dir scratch/budget_baseline --seed-dir covering_designs --output-dir scratch/refined_budget_2026_10_16 --pools 14 15 16 --source-stride 1 --extension-step 5 --max-rounds 3
```

Amprente SHA-256 (UTF-8 cu terminatori LF):

| Fișier | SHA-256 inițial | SHA-256 rafinat |
|---|---|---|
| `H_14_6_4.txt` | `ac9df52aa19abdbfe81d2922a64cf1d0c3c13c4738dc491d1529b4c5d1e7b25a` | `21cacade1148b6c82f49b799dcdaa21e0af5f6be00f9a35a8384b616f706bfdb` |
| `H_15_6_4.txt` | `06d8367496136451c2f9bfa14eaba92f563fb5aeb5c7fbd9d8de51c40b5f5c3b` | `574fb08854773ab009586577123d77bba51fa39db3526d66b01f37a08f1537d9` |
| `H_16_6_4.txt` | `73c1221df860cbbac13c9bca28d09b715ee3ca227694468ece6a33ac428379f3` | `59499070c23a8a6fdf36b273a5c2be78cbf0fdb0e0ca41b0cf5694110014f707` |

Datele complete per buget sunt înregistrate în `scripts/analysis/budget_refinement_2026-10-16.json`.
