# Pool extins pentru 4+ — căutarea offline (2026-10-22)

Designurile din `covering_designs/extended/` (17 și 18 numere; 6/49 pe 152 de
variante, 5/40 și Joker pe 404) au fost găsite offline cu scripturile de aici.
Producția nu le rulează: le citește din fișiere și le certifică la fiecare
generare (`covering/extended_pool.py`).

## Verificare (deterministă)

```
python scripts/analysis/extended_pool/verify_extended_designs.py
```

Pune fiecare design pe un clasament canonic, construiește coverul complet al
motorului pe primele 16 numere și rulează certificarea din producție. Rezultatul
așteptat, exact, pe extrageri uniforme:

| Design | 4+ pe 16 numere | 4+ extins | Grupe de 4 acoperite |
|---|---:|---:|---|
| 6/49, 17 numere, 152 | 7,960002% | 8,499139% | 1972 din 2380 |
| 6/49, 18 numere, 152 | 7,960002% | 9,320038% | 2192 din 3060 |
| 5/40, 17 numere, 404 | 16,026553% | 17,219582% | 2001 din 2380 |
| 5/40, 18 numere, 404 | 16,026553% | 17,551154% | 2020 din 3060 |
| Joker, 17 numere, 404 | 4,677518% | 5,094622% | 2002 din 2380 |
| Joker, 18 numere, 404 | 4,677518% | 5,165258% | 2020 din 3060 |

Niciun prag de la 3 la min(bilet, extrase) nu scade; 6 din 6 (6/49) și 5 din 5
(Joker) nu depind de așezare. La 5/40 cu 18 numere designul atinge marginea
superioară la 3+, 4+ și 5, deci e optim pentru 18 numere și 404 variante.

## Căutarea (stocastică)

- `lib_exact.py`: numărătoarea exactă pe 2^v submulțimi (aceeași ca
  `covering.probability` peste 16 numere), marginile superioare.
- `pipeline.py`: greedy de 4 dintr-un punct de plecare (greedy, o schemă LR sau
  un rezultat anterior), recoacere pe un surogat, apoi recoacere exactă și
  coborâre pe schimburi simple.
- `anneal.py`, `surrogate.py`: starea incrementală și surogatul.
- `run_jobs.py` + `jobs1.json`..`jobs5.json`: lanțul de joburi folosit (joburile
  2-5 pornesc din rezultatele lui `runs/`). Rulare: `python run_jobs.py jobs1.json 3`
  din acest folder, apoi `jobs2.json` etc.
- `results_table.txt`: tabelul complet al cercetării (17-20 de numere, bugetele
  100, 112, 152, 200, 348, 404).

O rerulare poate da alte designuri (recoacerea e stocastică); certificarea din
producție decide oricum la fiecare apel.
