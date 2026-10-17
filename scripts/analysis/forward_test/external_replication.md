# Replicarea `dmd_forecast` pe alte loterii 6/49: rezultat

Rulată la 2026-09-27, după protocolul fixat în `PREREGISTRATION.md` (secțiunea
„Replicare pe alte loterii 6/49” și amendamentele), înainte de prima extragere
evaluată a testului pe extrageri viitoare. Metoda: copia înghețată
`frozen_dmd.py`, fără nicio ajustare.

## Verdict

**Niciun avantaj față de aleator.** Analiza principală, cu toate loteriile la un
loc, nu trece pragul α = 0,0125 pe niciun pool:

| | Extrageri evaluate | 3+ în pool | Rata | Aleator | p (unilateral) | Interval 95% |
|---|---|---|---|---|---|---|
| Pool 6 | 21.821 | 427 | 1,96% | 1,86% | 0,161 | 1,78–2,15% |
| Pool 12 | 21.821 | 3.161 | 14,49% | 14,80% | 0,905 | 14,02–14,96% |

Media numerelor nimerite în pool: 0,727 la pool 6 (aleator 0,735) și 1,465 la
pool 12 (aleator 1,469).

## Pe fiecare loterie (secundar)

| Loterie | Evaluate | Pool 6 | p | Pool 12 | p |
|---|---|---|---|---|---|
| Germania, Lotto 6aus49 | 6.364 | 1,93% | 0,354 | 14,49% | 0,762 |
| Canada, Lotto 6/49 | 4.253 | 1,90% | 0,437 | 14,34% | 0,804 |
| Polonia, Lotto | 7.210 | 2,02% | 0,166 | 14,81% | 0,491 |
| Spania, La Primitiva | 3.994 | 1,93% | 0,398 | 14,05% | 0,914 |

Rata aleatoare (hipergeometrică exactă): 1,864% la pool 6 și 14,798% la pool 12.
Fără Canada (numai Uniunea Europeană, analiză făcută după rezultat, deci doar
informativă): pool 6 1,97% (p = 0,157), pool 12 14,52% (p = 0,852).

## Date

Fiecare loterie a fost verificată pe o a doua sursă independentă înainte de
rulare; detaliile sunt în `_ISTORIC/externe/README.md`. Evaluarea începe cu a
201-a extragere a fiecărei loterii (fereastra metodei), iar pool-ul vine numai
din extragerile cu dată anterioară.

| Fișier folosit | Rânduri | Perioadă | SHA-256 |
|---|---|---|---|
| `germania_lotto_6aus49.csv` | 6.564 | 09.10.1955 – 26.09.2026 | `6b60a75200624ba7657c8992b49a2e899dd105caae492ba0f7f01b1b7a4c51d9` |
| `canada_lotto_6din49.csv` | 4.453 | 12.06.1982 – 23.09.2026 | `0e3a32154adef3647b067fe0d8921eba785df8f3a51d5b10f293890099cceb8d` |
| `polonia_lotto_6din49.csv` | 7.410 | 27.01.1957 – 26.09.2026 | `ed94912c4f0021503f83bd0bd546623ca0f7c88a0976e65cea20c52f73d3c54a` |
| `spania_la_primitiva_6din49.csv` | 4.194 | 17.10.1985 – 26.09.2026 | `7559963a89931f59a4cc6b90330f6d8dab71f541083a6a9d0266c7c893315009` |

Fișierul Canadei nu se păstrează în repo: aplicația folosește numai loterii din
Uniunea Europeană. El se poate reface din fișierul oficial BCLC
(`https://www.playnow.com/resources/documents/downloadable-numbers/649.zip`,
coloanele DRAW DATE și NUMBER DRAWN 1-6, fără BONUS NUMBER, extragerile până la
23.09.2026, data scrisă ZZ-LL-AAAA), iar amprenta de mai sus confirmă copia.
Celelalte trei fișiere sunt în `_ISTORIC/externe/`, dar se actualizează cu
extrageri noi; amprenta de aici e a variantei folosite la replicare.

## Reproducere

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\forward_test\external_replication.py `
  _ISTORIC\externe\germania_lotto_6aus49.csv canada_lotto_6din49.csv `
  _ISTORIC\externe\polonia_lotto_6din49.csv _ISTORIC\externe\spania_la_primitiva_6din49.csv
```

Pe fișierele actualizate ulterior, rezultatul se schimbă prin extragerile noi;
replicarea de mai sus rămâne cea din fișierele cu amprentele din tabel.
