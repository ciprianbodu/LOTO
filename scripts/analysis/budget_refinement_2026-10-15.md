# Rafinarea designurilor cu buget, 6/49 pool 16 â€” 2026-10-15

La acelaÈ™i pool È™i acelaÈ™i numÄƒr de variante, **51 din cele 87 de bugete rÄƒmase
sub noul cover complet de 152 variante sunt Ã®mbunÄƒtÄƒÈ›ite**: 38 cresc È™ansa de
cel puÈ›in un 4+, iar 13 cresc numai 5+, cu 4+ egal. Celelalte 36 rÄƒmÃ¢n identice.
Nu scade niciun prag 3+..6+ È™i nici numÄƒrul grupelor de 4 acoperite. Certificarea
este separatÄƒ pentru 6/49 È™i 6/45, deoarece fiÈ™ierul este comun celor douÄƒ
geometrii. Nu se schimbÄƒ pool-ul, scorerul sau costul unui buget.

| Variante | Grupe de 4, Ã®nainte â†’ dupÄƒ / 1.820 | P(4+), Ã®nainte â†’ dupÄƒ | CÃ¢È™tig relativ 4+ | P(5+), Ã®nainte â†’ dupÄƒ |
|---:|---:|---:|---:|---:|
| 65 | 958 â†’ 975 | 4,692997% â†’ 4,705518% | +0,267% | 0,119903% â†’ 0,120389% |
| 136 | 1.654 â†’ 1.752 | 7,333220% â†’ 7,703248% | +5,046% | 0,238797% â†’ 0,243653% |
| 137 | 1.658 â†’ 1.755 | 7,348323% â†’ 7,714575% | +4,984% | 0,240249% â†’ 0,245162% |
| 150 | 1.729 â†’ 1.794 | 7,616405% â†’ 7,861831% | +3,222% | 0,260916% â†’ 0,264549% |
| 151 | 1.734 â†’ 1.797 | 7,635284% â†’ 7,873159% | +3,115% | 0,262353% â†’ 0,266029% |

Mediana cÃ¢È™tigului relativ 4+ **numai Ã®n cele 38 de bugete unde 4+ creÈ™te**
este 0,288%; maximul este 5,046%. Sunt probabilitÄƒÈ›i exacte pe extrageri
uniforme, nu rezultate istorice È™i nu puncte procentuale. Un buget de 100 de
variante, de exemplu, Ã®È™i pÄƒstreazÄƒ designul anterior. Cele 20 de secÈ›iuni
152..171 nu mai sunt folosite: la aceste plafoane Ã®ncape noul cover complet.

## MetodÄƒ È™i dovadÄƒ

Optimizarea anterioarÄƒ cÄƒuta fiecare buget separat. Acum se refolosesc
construcÈ›iile bune Ã®ntre bugete, offline: se eliminÄƒ succesiv varianta cu
pierderea marginalÄƒ exactÄƒ cea mai micÄƒ sau se adaugÄƒ varianta cu cel mai mare
cÃ¢È™tig marginal admis. CÄƒutarea este deterministÄƒ. ÃŽn ordinea eliminÄƒrilor,
3+ are prioritate, apoi 4+, 5+ È™i acoperirea grupelor de 4. AdÄƒugÄƒrile urmÄƒresc
4+, cu pragurile È™i acoperirea candidatului de referinÈ›Äƒ pÄƒstrate.

Fiecare candidat trebuie sÄƒ domine numÄƒrÄƒtorile Ã®ntregi exacte la 3+, 4+, 5+
È™i 6+ pentru **ambele** geometrii È™i sÄƒ nu piardÄƒ acoperire de 4. Se verificÄƒ
È™i cu `_certify`: acelaÈ™i numÄƒr de variante distincte, toate numerele jucate
pÄƒstrate, nicio probabilitate Ã®n scÄƒdere È™i cel puÈ›in o creÈ™tere strictÄƒ.
Certificarea finalÄƒ comparÄƒ direct cu fiÈ™ierul iniÈ›ial. Aceste certificate sunt
pe probabilitÄƒÈ›ile jocurilor, nu pe fiecare mÄƒrime a intersecÈ›iei separat.

Un verificator independent, fÄƒrÄƒ importul funcÈ›iilor de probabilitate din
producÈ›ie, a enumerat toate submulÈ›imile pool-ului de mÄƒrimi 3..6. A recalculat
numÄƒrul favorabil de extrageri ca suma numÄƒrÄƒtorilor pe intersecÈ›ii Ã®nmulÈ›ite
cu `C(Nâˆ’16, 6âˆ’h)`. Toate cele 87 de secÈ›iuni sunt valide, cele 51 modificate
sunt certificate, zero regresii È™i zero diferenÈ›e faÈ›Äƒ de numÄƒrÄƒtorile È™i
probabilitÄƒÈ›ile din raport. Numitorii sunt `C(49,6)=13.983.816` È™i
`C(45,6)=8.145.060`; acoperirea garanÈ›iei are numitorul `C(16,4)=1.820`.

CÄƒutarea finalÄƒ a costat 224,5 s de timp total È™i 179,0 s CPU, pe un singur
fir Python. Acest cost se plÄƒteÈ™te offline. ÃŽn aplicaÈ›ie se Ã®ncarcÄƒ fiÈ™ierul
precalculat È™i se pÄƒstreazÄƒ certificarea existentÄƒ faÈ›Äƒ de wheel-ul generat
Ã®n proces. Amprenta fiecÄƒrei secÈ›iuni invalideazÄƒ numai cheia WF afectatÄƒ.

## Reproducere È™i amprente

Sursa: `scripts/analysis/refine_budget_designs.py`. Extrage fiÈ™ierul vechi
`covering_designs/budget/H_16_6_4.txt` din commitul
`1d7ee026778cbb0f758ed299f8d4bb2c2fe4577b` Ã®ntr-un director separat, denumit
mai jos `scratch/budget-baseline`. FoloseÈ™te `C_16_6_4.txt` actual, de 152 de
variante, ca sursÄƒ suplimentarÄƒ de eliminÄƒri È™i plafon exclusiv al bugetelor.

```text
python scripts/analysis/refine_budget_designs.py --input-dir scratch/budget-baseline --seed-dir covering_designs --output-dir scratch/refined-budget --pools 16 --source-stride 5 --extension-step 10
```

Comanda nu suprascrie datele de intrare sau directorul de producÈ›ie. FiÈ™ierele
rezultate folosesc LF. Parametrii, toate cele 51 de modificÄƒri, numÄƒrÄƒtorile
exacte, probabilitÄƒÈ›ile È™i timpii sunt Ã®n
`budget_refinement_2026-10-15.json`. SHA-256 pe conÈ›inut UTF-8 cu LF:

| FiÈ™ier | SHA-256 |
|---|---|
| H16 iniÈ›ial | `751cb8406587ca1b74367654edce7bc2fe9a9d767eb29a5c3b1d9a4f0914ddb7` |
| C16 complet, 152 variante | `81045b2532df1b42ee0499a7a631a4652a5384db1d872ee147382c336f403958` |
| H16 rafinat | `73c1221df860cbbac13c9bca28d09b715ee3ca227694468ece6a33ac428379f3` |

CÃ¢mpul `inputs` din JSON pÄƒstreazÄƒ È™i amprenta octeÈ›ilor fiÈ™ierului Windows
citit efectiv; `canonical_input_sha256` normalizeazÄƒ CRLF la LF È™i coincide
cu fiÈ™ierul din commit. Nu se afirmÄƒ optimalitate globalÄƒ. CÃ¢È™tigul priveÈ™te
È™ansa de cel puÈ›in o variantÄƒ cu hitul cerut; media variantelor cÃ¢È™tigÄƒtoare
la acelaÈ™i buget È™i È™ansa de 6/6 nu se schimbÄƒ prin aceastÄƒ rearanjare.
