# Evaluare cronologică a selecției

Joc: loto_6_49; 64 variante distincte/extragere/strategie; obiectiv variante: 4plus; ținta selecției metodelor: 4+ în pool (minimum 4+ la 5/40, 1/1 la Urna 2).
Metode păstrate: 53. Configurația și criteriile au fost fixate înaintea evaluării exterioare.

Replay retrospectiv al procedurii, nu holdout prospectiv. Formulele metodelor au fost dezvoltate pe istoricul disponibil; rezultatul nu demonstrează avantaj predictiv.

## Rezultate observate, pe toate extragerile evaluate

| Strategie | Premiu: frecvență [95%] | 4+: frecvență [95%] | Recuperare cost, cazuri cunoscute [95%] | Cost complet | Brut estimat | Brut minus cost | Finanțe cunoscute |
|---|---|---|---|---|---|---|---|
| pool | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1536.00 lei | 0.00 lei | -1536.00 lei | 3/3 |
| universe | 100.000% [43.850–100.000] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1536.00 lei | 200.00 lei | -1336.00 lei | 3/3 |
| random_reference | 66.667% [20.766–93.851] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1536.00 lei | 100.00 lei | -1436.00 lei | 3/3 |

Intervalele frecvențelor sunt descriptive (extrageri puține, selecție adaptivă și posibilă dependență); nu reprezintă teste de superioritate. Toate cele trei strategii au același cost declarat pe extragere.
Premiile lipsă și taxele pe bilete necunoscute rămân indisponibile. Netul este înainte de impozit, pe valorile unitare oficiale publicate; adăugarea unor câștigători ar putea schimba aceste valori. Biletele nu sunt dovedite ca jucate.

## Geometria variantelor pentru ultimul pas (model uniform)

| Strategie | P(cel puțin un premiu) [eroare MC 95%] | P(4+) [eroare MC 95%] | P(categoria I), exact | Cost |
|---|---|---|---|---|
| pool | 29.130% [28.248–30.028] | 4.490% [4.101–4.914] | 4.576719259e-06 | 512.00 lei |
| universe | 70.080% [69.175–70.970] | 5.760% [5.320–6.234] | 4.576719259e-06 | 512.00 lei |
| random_reference | 69.730% [68.822–70.623] | 5.920% [5.474–6.400] | 4.576719259e-06 | 512.00 lei |

Aceste probabilități descriu acoperirea pe extrageri uniforme și regulile actuale. Estimările Monte Carlo folosesc aceleași extrageri pentru strategii și un eșantion separat de cel al optimizării. Nu măsoară puterea predictivă a scorerului.
Optimizarea pe universul întreg este o căutare limitată, cu obiectiv explicit, fără scoruri istorice și fără pretenție de optim global. Probabilitatea recuperării costului viitor nu este calculabilă fără distribuția premiilor viitoare; frecvența istorică a recuperării costului apare separat în JSON.

## Urma cronologică

| Țintă | Ultima dată folosită la selecție | Metodă | Pool | Cost pool | Brut pool | Net pool |
|---|---|---|---|---|---|---|
| 2026-10-25 | 2026-10-22 | gap_ratio | 16 | 512.00 lei | 0.00 lei | -512.00 lei |
| 2026-10-29 | 2026-10-25 | gap_ratio | 16 | 512.00 lei | 0.00 lei | -512.00 lei |
| 2026-11-01 | 2026-10-29 | ewma_cold_hl30 | 16 | 512.00 lei | 0.00 lei | -512.00 lei |

Protocol SHA-256: `f80e770ff58b16ef0e0add24a5dfdfe9b081065a9feeec0391d075001c4b1f17`.
JSON-ul însoțitor păstrează toate variantele, deciziile (inclusiv Holm/low_confidence), parametrii respinși și erorile metodelor.
Bugetul rămâne fix; nicio pierdere nu declanșează o creștere a mizei.
