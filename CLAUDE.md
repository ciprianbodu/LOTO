# CLAUDE.md - ghid operational si roadmap

## 1. Scop

Aplicatia optimizeaza pool-uri si sisteme de acoperire pentru Loto 6/49, Loto
5/40 si Joker. Fluxul este exclusiv CPU:

1. valideaza istoricul;
2. compara metode de scoring prin walk-forward;
3. alege un scorer sau ensemble pentru fiecare joc si dimensiune de pool;
4. genereaza pool-ul prin top-N canonic;
5. transforma pool-ul in bilete prin covering design;
6. raporteaza separat hiturile de pool, hiturile pe bilet, acoperirea si costul.

Aplicatia nu prezice extrageri si nu poate garanta mai multe castiguri. Pentru un
pool aleator de aceeasi marime, probabilitatea de a contine cel putin trei numere
depinde de geometria jocului si de marimea pool-ului. Scoring-ul este evaluat ca
ipoteza empirica; wheeling-ul optimizeaza acoperirea numerelor deja selectate.

## 2. Starea curenta

Snapshot verificat la 2026-08-31:

- branch de productie: `main`;
- UI: NiceGUI, `app_nicegui.py`, port 8000;
- runtime tinta: ultimul Python 3.14.x stabil;
- venv: `D:\_BUILD\_LOTO\.venv`, in afara OneDrive;
- registry: 111 metode CPU in `METHODS`;
- curare reversibila: 43 metode in `curated_methods.json`;
- selectie per joc: 20/20/20 pentru 6/49, 5/40 si Joker Urna 1;
- tombstone permanent: 74 nume in `disabled_methods.json`;
- covering designs locale: 52 fisiere, toate validate la 100% la ultimul audit;
- cache benchmark: `v16`;
- cache walk-forward: `v22`;
- cache rezultat worker: `v3`;
- teste: 26 fisiere `test_*.py`.

Nu copia aceste numere in cod. Renumara inainte de a le cita:

```powershell
python -c "from loto_enterprise.benchmark.methods import METHODS; print(len(METHODS))"
python -c "from loto_enterprise.benchmark.curated import load_curated,load_per_game; print(len(load_curated()), {k:len(v) for k,v in load_per_game().items()})"
python -c "from loto_enterprise.benchmark.disabled import load_disabled; print(len(load_disabled()))"
```

## 3. Arhitectura

```text
app_nicegui.py
  -> submit_job(config_json)
  -> job_queue.py / loto_jobs.db
  -> worker.py, proces separat
  -> loto_engine.run_institutional_pipeline()
  -> scoring -> pool -> wheeling
  -> rezultat comprimat in coada
  -> ui_shared.decode_queue_result()
  -> UI + raport + walk-forward
```

Worker-ul este independent de UI si poate termina jobul dupa inchiderea paginii.
UI-ul face polling la o secunda, fara reload complet.

### Module cu responsabilitate unica

| Modul | Responsabilitate |
|---|---|
| `app_nicegui.py` | UI, configurare, submit, polling, randare, raport si orchestrare WF |
| `worker.py` | consumator SQLite, executie pipeline, cache rezultat, requeue la oprire |
| `job_queue.py` | contractul persistent UI-worker |
| `loto_engine.py` | validare productie, scoring, pool, auto-invert si audit |
| `wheeling_methods.py` | algoritmi de covering design si dispatcher |
| `ui_shared.py` | I/O atomic, lock-uri, payload queue, worker si loguri |
| `loto_enterprise/benchmark/runner.py` | folduri walk-forward si metrici per pool |
| `loto_enterprise/benchmark/decision.py` | gate vs random, Wilson, ensemble si decizie per pool |
| `loto_enterprise/core/method_selector.py` | citire decizie, sanitizare, decorelare si blend runtime |
| `loto_enterprise/core/ranking.py` | singurul tie-break acceptat pentru top-N |
| `loto_enterprise/core/score_validation.py` | validarea comuna a scorurilor bench/productie |
| `loto_enterprise/core/walk_forward_adapter.py` | WF onest, cache, agregare si acoperire |
| `loto_enterprise/core/draw_validation.py` | contract comun pentru extrageri valide |
| `_ISTORIC/` | sursa versionata a datelor de benchmark |

## 4. Contracte care nu se negociaza

### 4.1 Date

- O extragere valida are exact `draw_n` valori intregi, distincte si in interval.
- Engine, benchmark si walk-forward folosesc `draw_validation.py`.
- Joker Urna 2 accepta numai valori intregi 1..20.
- `_ISTORIC/` este versionat; fisierele de stare si cache nu sunt surse de adevar.

### 4.2 Scoruri si ranking

- Scorer: `fn(draws_2d, max_num) -> {numar: scor}`.
- Scorurile goale, plate, ne-numerice sau ne-finite sunt inutilizabile.
- Validarea trece prin `has_usable_score_variance`.
- Orice top-N dupa scor trece prin `core.ranking.rank_by_score`.
- Nu adauga sortari locale care pot schimba tie-break-ul dintre bench si productie.
- Fallback-ul de productie este `frequency`, determinist.
- `random` este baseline structural pentru benchmark si este interzis in productie.

### 4.3 Metode active, curate si dezactivate

- `disabled_methods.json` este merge-only si ireversibil. Nu elimina nume din el.
- Nu reintroduce `omnius`, metode GPU/neural sau alte tombstone-uri.
- `curated_methods.json` este reversibil si controleaza costul benchmarkului.
- `random` si `frequency` trebuie sa ramana in lista activa.
- Curarea se face dupa utilitate si diversitatea semnalului, nu dupa un clasament
  instabil pe o singura perioada.
- Un run CLI cu `--quick` sau `--methods` nu trebuie sa rescrie decizia de
  productie fara `--force-decision`.

### 4.4 Persistenta si concurenta

- JSON-urile de stare se scriu numai atomic, prin `ui_shared`.
- Nu scrie `pool_history.json` din pasi WF/backtest.
- Nu schimba schema `config_json` sau payload-ul queue fara migrare si teste E2E.
- Nu folosi fisiere temporare cu nume fix pentru scrieri concurente.

### 4.5 Git

- Lucrul de productie se integreaza pe `main`.
- Nu include in commit stari locale sau cache-uri fara cerere explicita.
- `best_methods.json`, `pool_history.json`, `raport_complet.txt`, logurile,
  baza SQLite si pickle-urile WF sunt runtime state.
- `bench_results/folds.csv` si `bench_results/report.json` sunt tracked, dar se
  comit numai cand reprezinta un Re-Bench complet si intentionat.
- Nu suprascrie modificarile locale ale utilizatorului.

## 5. Benchmark si decizie

### Jocuri si tinte

| Cheie | Geometrie | Tinta deciziei |
|---|---:|---|
| `loto_6_49` | 6/49 | 3+ implicit sau 4+ |
| `loto_5_40` | 5/40 | 3+ implicit sau 4+ |
| `joker_urna1` | 5/45 | 3+ implicit sau 4+ |
| `joker_urna2` | 1/20 | top-1, independent de tinta globala |

`LOTO_BENCH_TARGET` accepta numai 3 sau 4 pentru jocurile de pool. Orice alta
valoare este clampata. Urna 2 scrie si consuma `rate_1plus_k1`; baseline-ul
aleator exact este 5%.

### Selectia unei metode

Pentru fiecare joc si pool:

1. se folosesc numai folduri reale, valide si metode existente;
2. metoda trebuie sa bata `random` in cel putin 60% din ferestre pe aceeasi
   rata folosita la decizie;
3. rata este pooled pe `n_eval`, cu fallback per rand pe `n_test`;
4. incertitudinea este evaluata prin limita Wilson cu n efectiv Kish;
5. metodele calificate sunt ordonate dupa Wilson, lift si consistenta;
6. ensemble-ul nominal are maximum trei membri;
7. lipsa metricei sau lipsa metodelor calificate produce `low_confidence` si
   fallback conservator, nu o afirmatie de avantaj statistic.

### Doua filtre de redundanta

- In `decision.py`: Pearson semnat pe semnaturi de performanta, prag 0.99.
- La runtime: Spearman in modul pe vectorii de scor, prag 0.95.

Ele masoara lucruri diferite. Ensemble-ul activ poate fi mai mic decat cel scris
in `best_methods.json`; auditul trebuie sa arate membrii pastrati si eliminati.

### Joker Urna 2

Urna 2 are benchmark propriu, scorer/ensemble propriu si pool fix de un numar.
Pe setul curat verificat, 29 de metode au produs scoruri utile pe istoricul
complet, iar 14 au fost plate. Aceste cifre sunt diagnostic, nu selectie
permanenta; foldurile si datele noi pot schimba lista. Urna 2 nu se inverseaza in
Pool 2, deoarece o singura excludere nu reprezinta acelasi contract ca
inversarea pool-ului Urnei 1.

## 6. Pool si auto-invert

- Pool-ul UI este limitat la 6..16.
- Selectia este top-N pura dupa scorul validat.
- `auto_invert=False` implicit.
- Cand auto-invert este activ, Pool 2 reruleaza aceeasi decizie cu Pool 1 exclus
  strict prin `manual_blacklist`.
- Daca geometria nu permite inversarea, engine-ul o marcheaza ca `skipped`; UI
  nu trebuie sa prezinte un Pool 2 identic ca inversare reusita.
- Nu reactiva filtre post-scoring pe paritate, decade, sume sau tipare recente.
  Analizele din `scripts/analysis/` nu au demonstrat predictibilitate.

## 7. Wheeling si covering design

`wheeling_methods.py` este sursa unica pentru metodele disponibile:

- `greedy`;
- `ilp`;
- `annealing`;
- `genetic`;
- `lajolla`;
- `union34`.

Reguli:

- fara plafon de bilete, productia prefera La Jolla;
- cu `max_variants > 0`, se foloseste traseul cu buget si se recalculeaza
  acoperirea dupa completarea numerelor lipsa;
- designurile locale sunt validate la 100% inainte de utilizare;
- fallback: La Jolla -> ILP -> greedy;
- `guarantee == pick` inseamna sistem complet, nu trebuie clampat;
- `union34` foloseste un singur cover g4: acoperirea 4-din-4 implica 3-din-3;
- nu compara algoritmi numai dupa numarul de bilete; ordinea este acoperire,
  apoi cost;
- orice filtru post-wheel trebuie sa foloseasca
  `filter_preserving_coverage` si sa recalculeze `compute_coverage_pct`.

Un wheel cu acoperire sub 100% trebuie raportat explicit. La acoperire 100% si
garantie suficienta, 3+ in pool implica 3+ pe cel putin un bilet; la acoperire
partiala, hitul de pool este doar plafon pentru hitul pe bilet.

## 8. Walk-forward

- WF foloseste numai date anterioare extragerii validate.
- UI valideaza Pool 1 onest.
- Pool 2 din UI este retrospectiv pe pool-ul curent, nu un al doilea WF complet.
- `hits` = maximul pe un singur bilet.
- `hits_union` = intersectia pool-ului cu extragerea.
- `wheel_coverage=None` inseamna necunoscut, nu 100%.
- Adancimea UI este 30% din istoric.
- Bugetul implicit este 90 minute si permite rezultat partial.
- Ordinea jocurilor este Joker, 5/40, 6/49.
- Paralelizarea foloseste aproximativ 80% din nuclee, cu BLAS single-thread per
  proces.

Cache-ul WF sta momentan in `bench_results/`, deci in OneDrive. Cheia include
istoricul complet, lookback, scorer, ensemble, tinta, wheel, hash-ul designului si,
pentru Joker, decizia Urnei 2.

## 9. Cache si invalidare

| Strat | Versiune | Bump obligatoriu cand |
|---|---:|---|
| benchmark fold | `v16` | se schimba output-ul scorerului, `FoldResult`, validarea sau denominatoarele |
| walk-forward | `v22` | se schimba pool-ul, wheel-ul, structura flat sau semantica hiturilor |
| worker pipeline | `v3` | se schimba rezultatul serializat al pipeline-ului |

Un bump WF schimba numele fisierului, dar nu sterge cache-urile vechi. Foloseste
API-urile de inventariere/curatare, nu stergeri recursive oarbe.

## 10. Mediu si rulare

Instalarea canonica este:

1. `ACTUALIZARI.bat` - sincronizeaza `main`, instaleaza/actualizeaza Python
   3.14, recreeaza venv-ul daca patch-ul difera si instaleaza
   `requirements_base.txt`;
2. `START_8000.bat` - sincronizeaza, verifica mediul, curata procese vechi,
   porneste worker-ul si UI-ul.

`requirements_snapshot.txt` este arhiva si nu se instaleaza. Stack-ul este CPU;
nu adauga Torch, CUDA, TimesFM, NeuralForecast sau Streamlit.

Pe statia curenta, auditul din 2026-08-31 a gasit o inregistrare Chocolatey
Python 3.14.3 cu shim spre `C:\Python314\python.exe`, dar executabilul si venv-ul
proiectului lipseau. Repararea se face prin `ACTUALIZARI.bat`, apoi:

```powershell
py -3.14 --version
D:\_BUILD\_LOTO\.venv\Scripts\python.exe --version
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m pip check
```

## 11. Verificare obligatorie

### Pentru orice schimbare

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m py_compile <fisiere_modificate>
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

### Pentru UI

Porneste pe un port liber, asteapta importurile si verifica HTTP 200 plus fluxul
submit -> worker -> rezultat. Nu considera simplul import suficient.

### Pentru engine/scoring

- compara pool-ul si variantele cu un baseline inainte/dupa;
- verifica auditul pentru fallback, scorer activ si membri eliminati;
- testeaza scor gol, plat, NaN si inf;
- confirma aceeasi selectie in bench si productie.

### Pentru wheeling

- ruleaza `test_wheeling.py` si `test_covering_designs.py`;
- valideaza toate fisierele din `covering_designs/` la 100%;
- verifica geometria cu si fara `max_variants`;
- compara acoperirea inainte de numarul de bilete.

### Pentru benchmark

- confirma prezenta `random` si `frequency`;
- confirma cele patru jocuri, inclusiv `joker_urna2`;
- verifica `n_eval`, coloana tintei si metodele failed;
- nu actualiza `best_methods.json` dintr-un run partial;
- dupa un Re-Bench complet, inspecteaza `low_confidence`, mismatch-urile de
  coloana si ensemble-ul activ.

## 12. Roadmap

### P0 - restaurare operationala

- [ ] Ruleaza `ACTUALIZARI.bat` si repara instalarea Python 3.14 plus venv-ul.
- [ ] Ruleaza `pip check`, `verify_imports.py` si intreaga suita pytest.
- [ ] Porneste UI + worker si executa un job E2E pe fiecare tip de joc.

Criteriu de iesire: Python 3.14 si venv functionale, toate importurile obligatorii
verzi, pytest verde, UI HTTP 200 si payload worker decodat corect.

### P0 - recalibrare dupa Urna 2 top-1

- [ ] Ruleaza un Re-Bench complet cu cache `v16` pe toate cele 43 metode curate.
- [ ] Genereaza `rate_1plus_k1` si decizia `joker_urna2.k1`.
- [ ] Verifica daca vreo metoda depaseste random 5% prin poarta de consistenta si
  Wilson; pastreaza `low_confidence` daca dovada nu exista.
- [ ] Revizuieste `folds.csv`, `report.json` si `best_methods.json` impreuna,
  apoi comite numai output-urile complete care trebuie versionate.

Criteriu de iesire: toate cele patru jocuri au folduri curente, nicio metoda cu
scor inutilizabil nu intra in decizie, iar productia consuma exact decizia afisata.

### P1 - selectie Urna 2 si paritate bench/productie

- [ ] Adauga o lista `per_game.joker_urna2` numai dupa masurarea scorurilor pe
  folduri; selecteaza dupa validitate si diversitate, nu dupa un singur top.
- [ ] Automatizeaza testul care ruleaza fiecare metoda activa pe toate geometriile
  si compara acceptarea benchmarkului cu acceptarea engine-ului.
- [ ] Raporteaza separat metodele unavailable, failed, plate si corelate.
- [ ] Adauga un test de regresie pentru fallback-ul top-1 fara coloana
  `rate_1plus_k1`.

Criteriu de iesire: zero diferente de acceptare bench/productie si curation
Urna 2 explicabila, reversibila si reproductibila.

### P1 - acoperire si cost

- [ ] Pastreaza manifest pentru cele 52 de designuri: geometrie, hash, bilete si
  acoperire.
- [ ] Compara La Jolla, ILP si greedy pe aceeasi geometrie prin
  `(coverage, ticket_count, runtime)`.
- [ ] Cauta designuri cu mai putine bilete numai offline; promoveaza un fisier
  doar dupa validare exhaustiva la 100%.
- [ ] Adauga teste explicite pentru bugete imposibile si raportarea acoperirii
  partiale.

Criteriu de iesire: nicio regresie de acoperire, costul scade numai cu dovada
reproductibila, iar UI nu confunda pool-hit cu ticket-hit.

### P2 - reproductibilitate si operare

- [ ] Adauga CI Windows pe Python 3.14 cu pytest si smoke test NiceGUI.
- [ ] Muta cache-ul WF in `D:\_BUILD\_LOTO` sau adauga override prin env,
  cu migrare si inventariere a cache-urilor vechi.
- [ ] Adauga o comanda unica de diagnostic pentru versiuni, metode, curare,
  cache-uri, designuri si dependinte.
- [ ] Masoara timpii pe etape inainte de orice optimizare de performanta.

Criteriu de iesire: un checkout curat se instaleaza si se verifica automat, iar
OneDrive nu mai este pe traseul cache-ului greu.

### P2 - modularizare

- [ ] Extrage din `app_nicegui.py` serviciile de benchmark, WF, raport si mail.
- [ ] Extrage din `loto_engine.py` orchestration, scoring si wheeling adapters.
- [ ] Pastreaza contractele publice si adauga teste de caracterizare inaintea
  fiecarei extrageri.

Criteriu de iesire: module cu responsabilitate clara, fara schimbarea output-ului
pipeline-ului sau a contractului UI-worker.

### P3 - cercetare statistica

- [ ] Evalueaza metode noi numai prin protocol predefinit, ferestre externe si
  baseline hipergeometric.
- [ ] Raporteaza intervale de incredere, stabilitate intre perioade si cost CPU.
- [ ] Respinge metodele care castiga doar prin tie-break, leakage, selectie dupa
  rezultat sau multiple testing necontrolat.
- [ ] Nu promova filtre de paritate, decade, sume ori `numere datorate` fara
  dovada externa repetabila.

Criteriu de iesire: orice schimbare de metoda vine cu experiment reproductibil si
nu este descrisa drept garantie de castig.

## 13. Definitia de gata

O schimbare este gata numai cand:

1. contractele de date, ranking si queue raman coerente;
2. cache-urile relevante au fost invalidate;
3. testele specifice si suita completa trec pe Python 3.14;
4. UI si worker au fost verificate cand schimbarea le atinge;
5. acoperirea a fost recalculata cand se schimba biletele;
6. documentatia descrie starea curenta, nu istoricul incidentului;
7. commitul contine numai fisierele intentionate;
8. `main` si `origin/main` sunt sincronizate.
