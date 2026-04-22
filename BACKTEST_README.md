# Modul Backtesting LOTO

Modul pentru evaluarea performanței variantelor generate pe baza istoricului de extrageri.

## Descriere

Acest modul compară variantele generate recent cu rezultatele extragerilor anterioare din CSV pentru a calcula câte numere ar fi fost ghicite în medie în ultimele 20% (sau alt procent configurabil) dintre extrageri.

## Fișiere

- `loto_enterprise/core/backtesting.py` - Modulul principal de backtesting
- `run_backtest.py` - Script CLI pentru rularea backtestelor

## Utilizare

### 1. Din linia de comandă

```bash
# Cu variante directe din linia de comandă
python run_backtest.py --csv istoric_loto.csv --variants "5,12,23,34,41,48|3,15,27,38,44,49|1,10,20,30,40,45"

# Din fișier JSON sau text
python run_backtest.py --csv istoric_loto.csv --file variante_generate.json

# Cu joc 5/40 și evaluare pe ultimele 10%
python run_backtest.py --csv istoric_540.csv --file variante.txt --game 5/40 --percentile 10

# Salvare rezultate în JSON
python run_backtest.py --csv istoric.csv --file variante.json --output rezultate_backtest.json
```

### 2. Programatic în Python

```python
from loto_enterprise.core.backtesting import LotoBacktester, quick_backtest

# Metoda 1: Quick backtest
variants = [
    [5, 12, 23, 34, 41, 48],
    [3, 15, 27, 38, 44, 49],
    [1, 10, 20, 30, 40, 45],
]

summary = quick_backtest(
    csv_path="istoric_loto.csv",
    variants=variants,
    game_type="6/49",
    percentile=20.0
)

# Metoda 2: Control complet
backtester = LotoBacktester("istoric_loto.csv", game_type="6/49")

# Obține ultimele 20% extrageri
target_draws = backtester.get_last_percentile_draws(20.0)

# Evaluează o variantă specifică
results = backtester.evaluate_variant([5, 12, 23, 34, 41, 48], target_draws)
for r in results:
    print(f"Data {r.draw_date}: {r.hits} numere ({r.hit_numbers})")

# Evaluează toate variantele și obține sumar
summary = backtester.evaluate_variants(variants, percentile=20.0)
print(f"Medie numere ghicite: {summary.avg_hits_per_draw:.2f}")
print(f"Cel mai bun rezultat: {summary.best_draw_hits} numere")
```

### 3. Integrare cu loto_engine.py

```python
from loto_engine import LotoEngine
from loto_enterprise.core.backtesting import LotoBacktester

# Generează variante
engine = LotoEngine("6/49")
engine.load_data("istoric.csv")
variants = engine.generate_predictions()

# Evaluează performanța
backtester = LotoBacktester("istoric.csv", "6/49")
summary = backtester.evaluate_variants(variants, percentile=20.0)
backtester.print_summary(summary)
```

## Parametrii de evaluare

- `percentile`: Procentul din ultimele extrageri pentru evaluare (default 20%)
  - 20% = ultimele 20% din extragerile disponibile
  - 10% = ultimele 10% (mai recente, mai puține date)
  - 50% = ultimele 50% (date mai vechi incluse)

## Rezultate

Modulul oferă următoarele metrici:

| Metrică | Descriere |
|---------|-----------|
| `avg_hits_per_draw` | Media numerelor ghicite per extragere |
| `avg_hit_rate` | Rata de succes medie (hits / numere per extragere) |
| `best_draw_hits` | Cel mai bun rezultat (max numere ghicite) |
| `worst_draw_hits` | Cel mai slab rezultat |
| `median_hits` | Mediana numerelor ghicite |
| `std_hits` | Deviația standard |
| `distribution` | Distribuția hits-urilor (0-6 numere) |

## Interpretare rezultate

- **Excelent**: Media ≥ 3 numere ghicite
- **Bun**: Media ≥ 2 numere ghicite  
- **Moderat**: Media 1-2 numere ghicite
- **Slab**: Media < 1 număr ghicit

## Format fișiere intrare

### CSV Istoric
Fișierul CSV trebuie să conțină coloane cu numerele extrase:
```
date,n1,n2,n3,n4,n5,n6
2024-01-01,5,12,23,34,41,48
2024-01-08,3,15,27,38,44,49
...
```

### Variante (JSON)
```json
[
  [5, 12, 23, 34, 41, 48],
  [3, 15, 27, 38, 44, 49],
  [1, 10, 20, 30, 40, 45]
]
```

### Variante (text)
```
5, 12, 23, 34, 41, 48
3, 15, 27, 38, 44, 49
1, 10, 20, 30, 40, 45
```

## Exemplu output

```
============================================================
          RAPORT BACKTESTING LOTO
============================================================

Perioada evaluată: Ultimele 20 extrageri
Variante testate: 3

--- STATISTICI GENERALE ---
Medie numere ghicite per extragere: 2.15
Rată de succes medie: 35.8%
Mediană numere ghicite: 2.0
Deviație standard: 1.23
Cel mai bun rezultat: 5 numere
Cel mai slab rezultat: 0 numere

--- DISTRIBUȚIE NUMERE GHICITE ---
  5 numere:    1 ( 1.7%) █
  4 numere:    3 ( 5.0%) ██
  3 numere:   12 (20.0%) ██████████
  2 numere:   25 (41.7%) █████████████████████
  1 numere:   15 (25.0%) ████████████
  0 numere:    4 ( 6.7%) ███

--- TOP 5 REZULTATE ---
  1. 2024-03-15: 5 numere (12,23,34,41,48)
  2. 2024-02-28: 4 numere (5,12,23,41)
  3. 2024-01-22: 4 numere (23,34,41,48)
  4. 2024-03-01: 3 numere (5,12,48)
  5. 2024-02-14: 3 numere (12,23,41)
============================================================
```
