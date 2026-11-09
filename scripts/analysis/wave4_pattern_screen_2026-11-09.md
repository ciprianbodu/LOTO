# Ecranul valului 4 — 356 de metode × tipare de predicție 5+ (2026-11-09)

## Întrebarea

„Testează sute de metode avansate de statistică și probabilități pentru
identificarea tiparelor de predicție pentru +5 numere, începând cu pool de 6
numere."

## Protocol (fixat înainte de rezultate, în docstring-ul harness-ului)

- **Jocuri**: 6/49 (V=49, k=6; 2530 zile / 2599 extrageri), 5/40 (V=40, k=6;
  1731 zile / 1750), Joker Urna 1 (V=45, k=5; 2139 zile / 2202).
- **Cauzalitate pe zi**: scorul unei zile vede strict zilele anterioare;
  extragerile din aceeași zi (69 de zile duble la 6/49) primesc același scor
  și sunt numărate ca probe separate contra aceluiași pool.
- **Ferestre**: warmup 200 de zile; confirmare = ultimele 30% (n = 733 / 479 /
  607 extrageri); secundar = 100% post-warmup (n = 2399 / 1549 / 2002).
- **Celule**: pooluri 6 / 10 / poolul de producție (16 / 19 / 15) × ținte
  3+ / 4+ / 5+ + media hiturilor; 24 de celule per joc-versiune.
- **Teste**: binomial exact unilateral vs rata hipergeometrică exactă;
  z pe media hiturilor; corecție Holm (α=0.05) peste cele 356 de metode,
  separat per celulă.
- **Control anti-scurgere**: două permutări ale ordinii zilelor (semințe
  20261109, 20261110) — orice semnificație acolo calibrează podeaua de zgomot.

## Putere statistică a priori (calculată înainte de rulare)

| Celulă | Baseline/extragere | Așteptate (confirm) | Minim pentru Holm | Verdict a priori |
|---|---|---|---|---|
| 6/49 pool 6, ținta 5+ | 1.85e-5 | 0.014 | ≥2 (≈140× baseline) | **nevalidabil empiric** |
| 6/49 pool 6, ținta 4+ | 9.87e-4 | 0.72 | ≥7 | aproape nevalidabil |
| 6/49 pool 16, ținta 5+ | 1.09e-2 | 8.0 | ≥22 | validabil |

Ținta 5+ la pool 6 este geometric prea rară pentru istoricul existent: nici o
metodă nu poate fi confirmată statistic acolo, oricât de bună ar fi. Ecranul
răspunde onest prin 3+/4+/media hiturilor la pool 6 și prin 5+ la poolurile
mari.

## Banca (356 de predictori, grile fixate a priori)

`methods_wave4_bank_2026_11_09.py`: 16 semnale de bază (uniform, frecvență
Laplace, EWMA 10/30/120/250, Dirichlet, Beta-Bernoulli, hazard gap, z-score
gap, tranziții Markov, self-repeat, Hawkes, BOCPD, Fourier, anti-EWMA30) +
grile: Holt amortizat (12), Weibull (6), Dirichlet λ×prior (20),
Beta-Bernoulli (20), EWMA half-life (7) + anti (3), Markov decay×smooth (9),
Hawkes α×β (9), BOCPD (3), Fourier fereastră×armonici (6), faze periodice (3),
CUSUM (3), Hedge η×set-experți (12), KDE gap (2), entropie (2), logistic
online (4), mixturi liniare/geometrice pe perechi (168), triple (40),
agregatori de rang (3), anti-mixturi (8). Semnalele de bază se calculează o
singură dată per istorie (rezolvitor memoizat).

Verificare locală (`_TESTE/test_wave4_bank.py`, 380 de teste): shape/finite/
rânduri=1 pentru toate cele 356, **cauzalitate prin invarianță la prefix**
(modificarea zilelor viitoare nu schimbă traiectoria trecută, 21 de metode
reprezentative), determinism, echivalența banca-default ≡ banca-cu-resolver.
Ancora `s_uniform` (pool fix 1..K) pe 1830 de zile de test: 39 vs 35.4
așteptate la 3+, 1 vs 1.87 la 4+, 0 vs 0.035 la 5+ — harness-ul măsoară corect.

## Rezultate

Durată: ~8.5 minute (9 istorii × 43–69 s). Rezultate complete:
`wave4_pattern_screen_2026_11_09_results.json`.

### Semnificații Holm (prag per celulă 0.05/356 ≈ 1.4e-4)

| Versiune | Celule | Semnificații Holm |
|---|---|---|
| real (3 jocuri) | 72 | **1** |
| shuffle 20261109 | 72 | 1 |
| shuffle 20261110 | 72 | 1 |

- Singura semnificație reală: Joker, confirm30, pool 10, ținta 3+,
  `mix_dirichlet_trans_a0.75`: 64 vs 52.0 așteptate, p = 1.01e-4. Pe fereastra
  completă aceeași metodă are p = 2.4e-3 (nu trece Holm) — **nu se replica**.
- Controalele shuffle produc semnificații cu p comparabil (7.5e-5, 8.2e-5):
  exact podeaua de zgomot a designului (la 144 de celule shuffle × FWER 5%
  se așteaptă ~7; s-au observat 2).
- **Holm global** peste toate cele 72 de celule reale: prag ≈ 1.95e-6; minimul
  real observat 1.01e-4 ⇒ nimic semnificativ global.

### Celulele-cheie (confirm30, real)

- **6/49 pool 16, ținta 5+**: cel mai bun `weibull_k0.85` = 19 vs 8.0
  așteptate (2.4× baseline), p = 5.8e-4 — sub pragul Holm 1.4e-4. Ancora
  uniform: 14. Direcțional cel mai bun rezultat al ecranului, dar
  neconfirmat statistic.
- **6/49 pool 6, ținta 4+**: maxim 3 evenimente (Fourier), vs 0.72 așteptate,
  p = 0.037 — departe de pragul Holm; ținta 5+: 0 evenimente la toate cele
  356 de metode (conform predicției a priori).
- **6/49 pool 6, ținta 3+**: maxim 22 (`fourier_w512_k2`) vs 13.7, p = 0.022 —
  ne-Holm.
- **5/40 pool 19, ținta 5+**: maxim 41 (`cusum_h4.0`) vs 33.9, p = 0.12.
- **Joker pool 15, ținta 5+**: maxim 6 (`gmix_freq_hawkes_a0.75`) vs 1.49,
  p = 4.3e-3 — ne-Holm.
- **Media hiturilor** (toate poolurile de producție): cele mai bune medii
  (hedge_e1.0_struct 2.041 vs μ=1.959 la 6/49 k16; gmix_freq_ewma250 1.778
  la Joker k15) au p ∈ [3e-3, 2e-2] — niciuna nu trece Holm.

## Concluzie

1. **Nicio metodă din cele 356 nu depășește baseline-ul hipergeometric după
   corecția Holm**, la niciun joc, niciun pool, nicio țintă. Rata
   semnificațiilor pe datele reale (1/72) este egală cu cea pe controalele
   fără nicio structură temporală (2/144) — semnal neexploatabil.
2. **5+ la pool 6 este matematic nevalidabil** pe acest istoric (0.014
   evenimente așteptate); cerința a fost acoperită onest prin 3+/4+/medie,
   fără rezultat.
3. Cel mai promițător candidat (`weibull_k0.85`, 19 vs 8 la 6/49 pool 16) ar
   cere pentru confirmare un istoric de ~3–4 ori mai lung sau o poartă
   fixată a priori pe un lot viitor — decizie separată; **nu se promovează
   nimic în registry din acest ecran** (registry: 55 metode, neschimbat;
   deciziile de producție 16/15/19 neschimbate).
4. Concluzia este consistentă cu studiile anterioare (2026-11-04: 53 de
   metode × 5798 ținte, zero Holm; 2026-11-05: 54 de comparații pe scheme LR,
   zero Holm): tipare predictive exploatabile pentru 5+ nu sunt detectabile
   în istoricele românești cu metodele și puterea statistică disponibile.

## Reproducere

```powershell
python scripts/analysis/wave4_pattern_screen_2026_11_09.py
python -m pytest _TESTE/test_wave4_bank.py -q   # 380 teste, local
```
