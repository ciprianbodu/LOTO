# Evaluare cronologică a selecției

Joc: joker; 64 variante distincte/extragere/strategie; obiectiv variante: 4plus; ținta selecției metodelor: 4+ în pool (minimum 4+ la 5/40, 1/1 la Urna 2).
Metode păstrate: 53. Configurația și criteriile au fost fixate înaintea evaluării exterioare.

Replay retrospectiv al procedurii, nu holdout prospectiv. Formulele metodelor au fost dezvoltate pe istoricul disponibil; rezultatul nu demonstrează avantaj predictiv.

## Rezultate observate, pe toate extragerile evaluate

| Strategie | Premiu: frecvență [95%] | 4+: frecvență [95%] | Recuperare cost, cazuri cunoscute [95%] | Cost complet | Brut estimat | Brut minus cost | Finanțe cunoscute |
|---|---|---|---|---|---|---|---|
| pool | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1344.00 lei | 0.00 lei | -1344.00 lei | 3/3 |
| universe | 100.000% [43.850–100.000] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1344.00 lei | 303.59 lei | -1040.41 lei | 3/3 |
| random_reference | 100.000% [43.850–100.000] | 0.000% [0.000–56.150] | 0.000% [0.000–56.150] | 1344.00 lei | 105.15 lei | -1238.85 lei | 3/3 |

Intervalele frecvențelor sunt descriptive (extrageri puține, selecție adaptivă și posibilă dependență); nu reprezintă teste de superioritate. Toate cele trei strategii au același cost declarat pe extragere.
Premiile lipsă și taxele pe bilete necunoscute rămân indisponibile. Netul este înainte de impozit, pe valorile unitare oficiale publicate; adăugarea unor câștigători ar putea schimba aceste valori. Biletele nu sunt dovedite ca jucate.

## Geometria variantelor pentru ultimul pas (model uniform)

| Strategie | P(cel puțin un premiu) [eroare MC 95%] | P(4+) [eroare MC 95%] | P(categoria I), exact | Cost |
|---|---|---|---|---|
| pool | 14.370% [13.696–15.071] | 1.050% [0.868–1.269] | 2.619174485e-06 | 448.00 lei |
| universe | 87.650% [86.991–88.280] | 1.210% [1.014–1.444] | 2.619174485e-06 | 448.00 lei |
| random_reference | 85.650% [84.949–86.323] | 1.120% [0.932–1.346] | 2.619174485e-06 | 448.00 lei |

Aceste probabilități descriu acoperirea pe extrageri uniforme și regulile actuale. Estimările Monte Carlo folosesc aceleași extrageri pentru strategii și un eșantion separat de cel al optimizării. Nu măsoară puterea predictivă a scorerului.
Optimizarea pe universul întreg este o căutare limitată, cu obiectiv explicit, fără scoruri istorice și fără pretenție de optim global. Probabilitatea recuperării costului viitor nu este calculabilă fără distribuția premiilor viitoare; frecvența istorică a recuperării costului apare separat în JSON.

## Urma cronologică

| Țintă | Ultima dată folosită la selecție | Metodă | Pool | Cost pool | Brut pool | Net pool |
|---|---|---|---|---|---|---|
| 2026-10-25 | 2026-10-22 | ewma_cold_hl30 | 12 | 448.00 lei | 0.00 lei | -448.00 lei |
| 2026-10-29 | 2026-10-25 | online_logit_sgd | 12 | 448.00 lei | 0.00 lei | -448.00 lei |
| 2026-11-01 | 2026-10-29 | online_logit_sgd | 12 | 448.00 lei | 0.00 lei | -448.00 lei |

Protocol SHA-256: `4d324d6fd8c73951dd975105d028645bf6b77676ef05ebe92e194b65dac677ad`.
JSON-ul însoțitor păstrează toate variantele, deciziile (inclusiv Holm/low_confidence), parametrii respinși și erorile metodelor.
Bugetul rămâne fix; nicio pierdere nu declanșează o creștere a mizei.
