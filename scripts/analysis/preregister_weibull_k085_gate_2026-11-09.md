# Poarta preînregistrată — `weibull_k0.85`, 6/49, pool 16, garanție 6 (2026-11-09)

## De ce

Ecranul valului 4 (2026-11-09, 356 de metode) a găsit un singur candidat
direcțional promițător la ținta 5+: `weibull_k0.85` cu **19 vs 8,0 așteptate**
la 6/49 pool 16 (2,4× baseline), dar p = 5,8e-4 nu a trecut corecția Holm
(1,4e-4). Singura cale corectă de confirmare: **poartă fixată a priori pe
extrageri viitoare nevăzute**. Acest commit ESTE preînregistrarea — protocolul
de mai jos nu se mai modifică; numărarea începe strict cu extragerile după
2026-11-09 (prima eligibilă: joi 2026-11-12).

## Protocolul (fixat)

| Element | Valoare |
|---|---|
| Metodă | `traj_weibull_hazard(ind, κ=0.85)` (hazard Weibull al gap-ului), cauzală pe zi |
| Pool | top-16 canonic al scorului zilei |
| Garanție | **6 → sistem complet C(16,6) = 8008 variante**: 6 în pool ⇒ exact o variantă cu 6 (garantat combinatorial) |
| Contabilizare variante | exactă, prin C(h,j)·C(16−h,6−j) — fără enumerare |
| Cost | 8008 × 8 lei = **64.064 lei/extragere**, cumulat |
| H0 | extrageri uniforme ⇒ rata 5+ = hipergeometrica p0 = 0,0108805779 |
| Orizont | primele **104 extrageri** eligibile (~1 an) |
| Criteriu de succes | **≥5 evenimente 5+** (P(X≥5\|H0) = 0,00576 ≤ α=0,01) |
| Ținte secundare (descriptive) | 4+ (p0=0,0796), 6 în pool (p0=5,73e-4), media hiturilor (μ0=1,959), distribuția variantelor |

**Putere onestă**: la rata din screening (2,59%) puterea este doar 0,134; la
4% este 0,403. Poarta este conservatoare — acceptat și fixat aici. Premiile în
bani nu sunt preînregistrate (depind de fondul de câștiguri al fiecărei
extrageri); se contabilizează hiturile și costul.

## Operare

```powershell
python scripts/analysis/preregister_weibull_k085_gate_2026_11_09.py update   # idempotent
python scripts/analysis/preregister_weibull_k085_gate_2026_11_09.py status
```

Starea se acumulează local: `D:\_BUILD\_LOTO\gate_weibull_k085_2026_11_09_state.json`
(override `LOTO_RUNTIME_DIR`). Scriptul evaluează doar zilele cu dată strict
după 2026-11-09, neevaluate anterior.

## Verificare la înregistrare

- 386 de teste locale verzi (`_TESTE/test_preregister_gate.py` +
  `_TESTE/test_wave4_bank.py`): distribuția combinatorială sumează la 8008
  pentru orice h; h=6 ⇒ exact 1 variantă cu 6, 60 cu 5, 675 cu 4; pragul
  porții e cel mai mic cu P≤0,01; pool-ul e determinist și cauzal
  (invarianță la prefix); update-ul e idempotent.
- Stare inițială: 0/104 extrageri evaluate (istoricul curent se termină la
  2026-11-05), cost cumulat 0 lei — poarta pornește curată.
