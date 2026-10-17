# Ecran geometric și matematic, 2026-10-05

344 de scoreri, România 6/49, pool 12, eveniment = cel puțin 3 numere în pool.
Nu se promovează nicio metodă.

## Protocol (fixat înainte de rezultat)

- Istoric `loto_6_49.csv`: 2590 extrageri, 2521 zile (1993-08-08 .. 2026-10-04).
- Scorul unei zile folosește numai zilele anterioare.
- Warmup 200 zile. Dezvoltare: 1659 extrageri. Confirmarea (ultimele 30%) nu a fost citită.
- Baseline pereche: `frequency` din producție.
- Baseline hipergeometric pentru pool 12, 6 din 49, cel puțin 3: 14,80%.
- Un candidat iese dacă locul 12 cade într-un grup de scoruri egale în peste 50% din zile.
- Stabil = lift pozitiv pe ambele jumătăți ale dezvoltării.
- Nulul căutării: 40 de permutări ale etichetelor 1..49. Pe fiecare se păstrează cel mai bun lift dintre toți candidații. Promovarea cere ca liftul real să depășească toate aceste maxime, apoi Holm < 0,05 pe confirmarea românească, pe 6/49 externe și pe 5/40. Nu s-a ajuns acolo.

## Ce s-a măsurat

Familii, toate cauzale:

- netezire exponențială (α = 0,03 / 0,08 / 0,15 / 0,30) compusă cu nuclee spațiale: linie, cerc, grilă 7×7, rest modulo 7, rest modulo 10, la patru scale;
- aceleași nuclee cu semn opus (găuri) și amestecuri fixe 35% / 70% cu nivelul EWMA;
- pantă pe 4 blocuri, rafală scurtă-minus-lungă, centroid și complement pe linia numerelor;
- rata pe aceeași zi a săptămânii;
- frecvență condiționată de tercila sumei și a amplitudinii zilei precedente.

## Rezultat

| | rată 3+ | extrageri |
|---|---:|---:|
| hipergeometric | 14,80% | — |
| `frequency` pe dezvoltare | 14,47% | 1659 |
| cel mai bun candidat stabil | 16,46% | 1659 |

Câștigătorul dezvoltării, `a0.08__mix0.7_mod10_s1.2` (EWMA α=0,08 amestecat 70% cu un nucleu modulo 10, σ=1,2), are +33 extrageri față de `frequency`. Testul McNemar necorectat are p = 0,051. 45 de candidați sunt „stabili” pe cele două jumătăți; următorii sunt cercuri și linii, toți în aceeași bandă de +26..+30.

Dintre 40 de căutări identice pe etichete amestecate, 31 au găsit un câștigător cu lift cel puțin la fel de mare. Mediana maximului nul este +39 extrageri, maximul +54. Liftul real (+33) este în interiorul nulului căutării.

Confirmarea, loteriile externe și 5/40 nu au fost evaluate: protocolul se oprește când dezvoltarea nu iese din zgomotul selecției. O metodă aleasă aici ar fi un fals pozitiv.

Script: `scripts/analysis/spatial_math_screen_2026-10-05.py`.
