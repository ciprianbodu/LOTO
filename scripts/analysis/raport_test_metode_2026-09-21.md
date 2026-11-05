# Teste suplimentare de metode — 21.09.2026

Toate cele 52 de metode au trecut verificările de contract. Diagnosticul istoric
nu a găsit dovadă statistică de avantaj după corecția comparațiilor multiple.
Nu s-au schimbat metodele de producție, ponderile, pool-urile salvate sau decizia.

## Verificări automate

**375 de teste noi; 462 de teste trecute** împreună cu regresiile existente
pentru registry, Joker Urna 2, filtre structurale și cauzalitate.

- 52 metode × 4 geometrii: 6/49, primele 5 numere din 5/40, Joker 5/45 și Joker 1/20.
- Istorice sintetice de 0, 1, 5, 29, 30, 80, 401 și 700 extrageri: chei complete,
  scoruri finite în [0,1], determinism după apeluri intercalate și intrare nemodificată.
- Inversarea ordinii numerelor în fiecare extragere păstrează scorurile tuturor
  metodelor de scoring. Referința `random` este exceptată: sămânța sa depinde
  deliberat de reprezentarea brută a istoricului; repetabilitatea ei este testată.
- Trăsăturile modelelor sunt identice cu cele calculate numai pe prefixul trecut;
  schimbarea extragerii țintă și a viitorului nu le modifică. Etichetele sunt aliniate.
- Cele 7 metode de co-apariție/perechi produc corect scoruri plate la Urna 2 și
  sunt respinse de benchmark cu `n_eval=0`: `anti_cooc_last`, `cooc_last3`,
  `hawkes_cross`, `pagerank_cooc`, `pair_lift_last`, `pair_transition`, `rwr_last_draw`.
- Controale cu rezultate cunoscute verifică ratele, denominatorii, excluderea
  tuturor extragerilor din aceeași zi, observațiile omise și corecția Holm.

Compilarea celor trei fișiere Python noi și verificarea Ruff au trecut.
Nu a fost necesară schimbarea scorării sau invalidarea cache-urilor.

## Diagnostic pe datele locale

52 metode × 4 jocuri × 180 extrageri = **37.440 apeluri de scorare**.
Fiecare predicție folosește istoricul disponibil strict înaintea zilei țintă.
Trei ferestre disjuncte de câte 60 extrageri:

| Fereastră | 6/49 și Joker | 5/40 |
|---|---|---|
| 1 | 09.01.2025–31.07.2025 | 31.12.2024–31.07.2025 |
| 2 | 03.08.2025–26.02.2026 | 03.08.2025–26.02.2026 |
| 3 | 01.03.2026–17.09.2026 | 01.03.2026–17.09.2026 |

Au fost evaluate pool-urile **6, 8, 10, 12 și 16**, pentru 3+ și 4+;
Urna 2 folosește numai alegerea unui număr și rata 1+.
Metodele excluse din producție rămân numai referințe de diagnostic.
Rata fiecărei metode este comparată cu probabilitatea hipergeometrică exactă,
nu doar cu o singură realizare favorabilă sau nefavorabilă a metodei `random`.

Rezultate finale:

- **0 erori** de scorare; 1.260 apeluri plate la cele 7 metode inaplicabile Urnei 2,
  omise explicit, fără credit artificial pentru regula de departajare.
- **1.612 comparații planificate**; 1.605 au toate cele 180 de observații.
- 49 comparații au p brut < 0,05; **0** au p Holm < 0,05.
- Cel mai mic p Holm: **0,5232**. Familia corecției rămâne 1.612 inclusiv pentru
  metodele incomplete, cărora li se atribuie intern p=1.
- Durata rulării finale: **57,68 secunde**, patru procese CPU.

Acestea sunt rezultate de POOL, nu rate de premii pe bilete. La 5/40 scorarea
urmărește primele cinci numere; nu e o simulare a tuturor categoriilor de câștig.

## Ce arată pool-ul de 6

Maximele de mai jos sunt doar descriptive, între metodele permise global în
producție. Ele sunt alese retrospectiv din multe metode și nu sunt recomandări.

| Joc | 3+ așteptate din hazard / 180 | Maxim observat 3+ / 180 | 4+ așteptate / 180 | Maxim observat 4+ / 180 |
|---|---:|---:|---:|---:|
| 6/49 | 3,35 | 6 | 0,178 | 1 |
| 5/40, primele 5 | 3,21 | 7 | 0,141 | 1 |
| Joker Urna 1 | 2,27 | 6 | 0,087 | 1 |

Exemplele cu cele mai multe reușite 3+ nu au aceeași distribuție între ferestre:
`hot_consistency` la 6/49 are 2/2/2, `freq_window_200` la 5/40 are 1/5/1,
iar `knn_feature_pooled` la Joker are 3/0/3. Sunt ilustrări ale variației,
fără dovadă de superioritate. Pentru Urna 2, maximul permis global este 13/180,
față de 9 așteptate; nici acesta nu trece corecția statistică.

Eșantionul are putere mică pentru 4+ la pool 6: în medie este așteptată mai puțin
de o reușită. Lipsa semnificației nu demonstrează egalitatea metodelor. Istoricul
era disponibil la dezvoltarea metodelor; diagnosticul este retrospectiv, nu o
validare pe date viitoare neatinse. Nu justifică o selecție nouă pe clasament.

## Reproducere și fișiere

```powershell
python -m pytest test_method_contracts.py test_method_window_audit.py -q
python scripts/analysis/audit_method_windows.py --window-size 60 --windows 3
```

Rezultatele complete rămân local, în directorul ignorat de Git
`bench_results/method_audit_20260921/`: `rates.csv` (fiecare metodă/pool/fereastră),
`timing.csv` și `summary.json`. Scriptul refuză să salveze peste directorul rădăcină
al benchmark-ului. Hash-urile istoricului sunt verificate înainte/după fiecare joc.
Hash-urile fișierelor `best_methods.json`, `folds.csv`, `report.json`,
`pool_history.json` și `.ui_state.json` au rămas identice.

Istoricele folosite, SHA-256:

- `joker_urna1`: 2188 extrageri valide; `efd725324e5f950135246fc5ca7328a81aa0f9ad47da51dfec08ff871f346b62`.
- `joker_urna2`: 2188 extrageri valide; `efd725324e5f950135246fc5ca7328a81aa0f9ad47da51dfec08ff871f346b62`.
- `loto_5_40`: 1736 extrageri valide; `01dfa9f9a481d5b5a64f82509c598376eb8f8a89f2158bb89d94faddc971cc67`.
- `loto_6_49`: 2585 extrageri valide; `10c2435eda2bb1a13586dd2bd85cf8eb36cdabc4e6f65be71afb71b55127608b`.
