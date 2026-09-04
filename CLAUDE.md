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

Snapshot verificat la 2026-09-01:

- branch de productie: `main`;
- UI: NiceGUI, `app_nicegui.py`, port 8000;
- runtime tinta: ultimul Python 3.14.x stabil;
- venv: `D:\_BUILD\_LOTO\.venv`, in afara OneDrive;
- registry: 111 metode CPU in `METHODS`;
- curare reversibila: 56 metode in `curated_methods.json`;
- selectie: 20/20/20 pentru 6/49, 5/40 si Joker Urna 1, plus 16 semnale
  distincte peste baseline pentru Joker Urna 2;
- tombstone permanent: 74 nume in `disabled_methods.json`;
- covering designs locale: 52 covere clasice `C_v_pick_t.txt` plus 99 lotto
  designs `L_v_pick_p_t.txt` (pool 6..16, pick 5 si 6), toate validate la 100%
  la ultimul audit;
- cache benchmark: `v17`;
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
| `loto_engine.py` | validare productie, scoring, pool unic, wheeling si audit |
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
- Curarea curenta cere avantaj observat fata de baseline si diversitatea
  semnalului. Este selectie reversibila pe istoric, nu garantie predictiva.
- Un run CLI cu `--quick`, `--methods`, sub trei ferestre (`--percentiles`)
  sau pe alt `--istoric` nu trebuie sa rescrie decizia de productie fara
  `--force-decision`.

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
2. toti candidatii se judeca pe acelasi set de ferestre; o metoda cu o fereastra
   lipsa (fold `failed`) iese din decizie si apare in `incomplete_methods`;
3. o metoda a carei taietura top-K cade intr-un grup de scoruri egale in cel
   putin 50% din blocuri (`tiebreak_kN`, bench v17) este exclusa ca dependenta
   de tie-break si apare in `tiebreak_dependent`; pe folds vechi fara coloana,
   poarta nu se aplica si `tiebreak_gate_applied` este `false`;
4. metoda trebuie sa bata referinta in cel putin 60% din ferestre pe aceeasi
   rata folosita la decizie; referinta este rata asteptata hipergeometric a
   unui pool aleator (`expected_random_rate`, 5% pentru Urna 2), nu o singura
   realizare `random`; randul `random` ramane in folds ca verificare de
   sanitate (`random_empirical_rate`) si este seedat determinist din istoric;
5. rata este pooled pe `n_eval`, cu fallback per rand pe `n_test`;
6. incertitudinea este evaluata prin limita Wilson cu n efectiv Kish;
7. metodele calificate sunt ordonate dupa Wilson, lift, consistenta si nume,
   deci decizia nu depinde de ordinea randurilor din `folds.csv`;
8. productia foloseste doar castigatorul unic (`ENSEMBLE_MAX_METHODS = 1`):
   ratele individuale din `folds.csv` nu dovedesc performanta unui blend
   nevalidat separat. Masurat pe Joker k11 (Re-Bench walk-forward real, 654
   extrageri): membri calificati separat, combinati, au dat 6.73% sub random
   8.53%, desi primul membru bătea random clar (11.16%). Schema `ensemble`
   din `best_methods.json` ramane cu un singur element, pentru compatibilitate
   engine/UI/cache — plafonul creste numai daca benchmarkul ajunge sa evalueze
   blendul direct (scoruri/pool per pas), nu doar ratele individuale;
9. lipsa metricei sau lipsa metodelor calificate produce `low_confidence` si
   fallback conservator, nu o afirmatie de avantaj statistic.

### Doua filtre de redundanta

- In `decision.py`: Pearson semnat pe semnaturi de performanta, prag 0.99.
- La runtime: Spearman in modul pe vectorii de scor, prag 0.95.

Ele masoara lucruri diferite. Cu productia limitata la un singur castigator
(punctul 8 de mai sus), niciun filtru nu mai are ce elimina in practica; raman
in cod pentru cazul in care plafonul creste pe baza unei dovezi ca un blend
evaluat direct bate castigatorul unic.

### Re-Bench onest (per extragere)

Re-Bench-ul din UI ruleaza implicit cu `--block-size 1`: scorerul se
recalculeaza inaintea FIECAREI extrageri testate, la fel ca validarea
walk-forward afisata dupa generare. Varianta veche (`block_size=99999`, un
singur scor per fereastra) putea alege un castigator care trecea poarta din
bench dar pierdea fata de random in WF real — cazul masurat mai sus la punctul
8. Costul e un Re-Bench mult mai lent; ETA-ul din UI se auto-calibreaza din
`runtime_sec`-ul ultimei rulari, deci prima estimare dupa schimbarea de
default e optimista.

Controlul pe istoric amestecat (`is_random=True`, ~50% din timpul de bench)
este sarit implicit in UI (`--no-shuffled-control`): alimenteaza doar
diagnosticul `lift_vs_shuffle` si tie-break-ul legacy `winners_per_pool`, nu
decizia de productie (`decision.py` filtreaza peste tot `is_random == False`).
Baseline-ul `random` (metoda, nu controlul amestecat) ramane obligatoriu si
prezent in folds.

Cheia de cache a foldurilor primeste sufixul `bs1` de indata ce `block_size`
difera de sentinel-ul istoric (99999) — care ramane in cod ca valoare implicita
a cheii, desi runner-ul si CLI-ul folosesc acum implicit 1. Niciun fold
cache-uit sub schema veche (score-once-per-fold) nu poate fi servit tacit sub
noua semantica.

### Joker Urna 2

Urna 2 are benchmark propriu, scorer/ensemble propriu si pool fix de un numar.
Pre-screeningul din 2026-09-01 a verificat toate cele 111 metode: `ml_knn_5` a
bătut controlul în 3/4 ferestre, iar `649_decade_hot` în 2/3 ferestre comune.
Decizia recalibrată nu mai este `low_confidence`. Cifrele sunt diagnostic pe
istoric, nu selecție permanentă; datele noi pot schimba rezultatul. Scanarea din
2026-09-02 arata ca `649_decade_hot` are doar doua niveluri de scor pe 1..20 si
alege mereu 20 sau 10 prin tie-break; dupa un Re-Bench cu `tiebreak_k1` poarta
din decizie o exclude, iar `ml_knn_5` (tie la granita in ~65% din blocuri) este
la randul ei candidata la excludere.

## 6. Pool unic

- Pool-ul UI este limitat la 6..16.
- Selectia este top-N pura dupa scorul validat.
- Fiecare joc genereaza si afiseaza un singur pool; configuratia, worker-ul,
  raportul, emailul si walk-forward-ul nu mai au Pool 2/auto-invert.
- Payload-urile vechi cu doua faze sunt citite compatibil folosind numai pool-ul
  normal din prima faza.
- Penalizarea dupa ultimele extrageri este o OPTIUNE de utilizator
  (`recent_penalty_draws`, `recent_penalty_factor`, implicit 3 extrageri si
  0.5): scorul unui numar extras de k ori in ultimele N extrageri se inmulteste
  cu factor^k, inainte de top-N. Se aplica identic in productie si in
  walk-forward (intra in cheia de cache WF cand e activa) si este raportata in
  `audit.recent_penalty`. Este o preferinta de compozitie a pool-ului, neutra ca
  valoare asteptata: analizele din `scripts/analysis/` nu au demonstrat
  predictibilitate pentru paritate, decade, sume sau tipare recente, iar
  penalizarea nu trebuie prezentata drept avantaj statistic.

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
- lotto design „t daca p" (`wheel_condition` > garantie): orice p numere din pool
  au cel putin t pe un bilet; mult mai ieftin (Joker pool 11: 3-daca-4 in 10
  bilete fata de 20), dar garantia se declanseaza doar cand cad p numere din
  pool. Designuri locale `L_v_pick_p_t.txt` validate la 100% inainte de
  folosire, altfel greedy pozitional + ILP; regenerare offline cu
  `scripts/analysis/gen_lotto_designs.py`. Walk-forward-ul ramane pe coverul
  clasic intern; hiturile de pool nu depind de wheel;
- nu compara algoritmi numai dupa numarul de bilete; ordinea este acoperire,
  apoi cost;
- orice filtru post-wheel trebuie sa foloseasca
  `filter_preserving_coverage` si sa recalculeze `compute_coverage_pct`.

Un wheel cu acoperire sub 100% trebuie raportat explicit. La acoperire 100% si
garantie suficienta, 3+ in pool implica 3+ pe cel putin un bilet; la acoperire
partiala, hitul de pool este doar plafon pentru hitul pe bilet.

## 8. Walk-forward

- WF foloseste numai date anterioare extragerii validate.
- UI valideaza onest pool-ul unic.
- `hits` = maximul pe un singur bilet.
- `hits_union` = intersectia pool-ului cu extragerea.
- `wheel_coverage=None` inseamna necunoscut, nu 100%.
- Adancimea UI este 30% din istoric.
- Bugetul implicit este 90 minute si permite rezultat partial; la urmatoarea
  rulare pasii deja validati din cache-ul partial sunt sariti (`skip_indices`),
  deci acoperirea creste in loc sa se refaca de la zero.
- Ordinea jocurilor este Joker, 5/40, 6/49.
- Paralelizarea foloseste aproximativ 80% din nuclee, cu BLAS single-thread per
  proces.

Cache-ul WF sta momentan in `bench_results/`, deci in OneDrive. Cheia include
istoricul complet, lookback, scorer, ensemble, tinta, wheel, hash-ul designului si,
pentru Joker, decizia Urnei 2.

## 9. Cache si invalidare

| Strat | Versiune | Bump obligatoriu cand |
|---|---:|---|
| benchmark fold | `v17` | se schimba output-ul scorerului, `FoldResult`, validarea sau denominatoarele |
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

Pe statia curenta, auditul din 2026-09-01 a eliminat dependenta de shim-ul
Chocolatey ramas spre `C:\Python314\python.exe`. Python 3.14.7 este instalat in
`%LOCALAPPDATA%\Programs\Python\Python314`, iar venv-ul proiectului este functional.
`ACTUALIZARI.bat` detecteaza executabilul real chiar daca launcherul `py` lipseste.
Verificarea canonica este:

```powershell
%LOCALAPPDATA%\Programs\Python\Python314\python.exe --version
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

- [x] Instaleaza Python 3.14.7 si recreeaza venv-ul CPU din requirements.
- [x] Ruleaza `pip check` si `verify_imports.py`: toate modulele sunt verzi.
- [x] Intareste `ACTUALIZARI.bat`: detector fara `py`, semnatura installer,
  erori fatale la pip/import si snapshot extern arhivei versionate.
- [ ] Porneste UI + worker si executa un job E2E pe fiecare tip de joc.

Criteriu de iesire: Python 3.14 si venv functionale, toate importurile obligatorii
verzi, pytest verde, UI HTTP 200 si payload worker decodat corect.

### P0 - recalibrare dupa Urna 2 top-1

- [x] Ruleaza Re-Bench pe toate cele 56 metode curate si cele patru jocuri.
- [x] Genereaza 448 folduri Urna 2 (real + shuffled), `rate_1plus_k1` si decizia
  `joker_urna2.k1`.
- [x] Verifica daca vreo metoda depaseste random 5% prin poarta de consistenta si
  Wilson; pastreaza `low_confidence` daca dovada nu exista.
- [x] Revizuieste `folds.csv`, `report.json` si `best_methods.json` impreuna,
  apoi comite numai output-urile complete care trebuie versionate.

Rezultat 2026-09-01: la acel Re-Bench, Urna 2 avea doua metode calificate în
decizia robustă, `649_decade_hot` si `ml_knn_5`, cu `low_confidence=false`.
Metodele plate pe geometria single-pick rămân `failed` si excluse, nu sunt
transformate artificial în ranking prin tie-break. De la introducerea
`ENSEMBLE_MAX_METHODS = 1` (vezi §5, „productia foloseste doar castigatorul
unic"), productia Urnei 2 foloseste — la fel ca toate jocurile — un singur
castigator validat direct, nu un blend cu doi membri.

Criteriu de iesire: toate cele patru jocuri au folduri curente, nicio metoda cu
scor inutilizabil nu intra in decizie, iar productia consuma exact decizia afisata.

### P1 - selectie Urna 2 si paritate bench/productie

- [x] Adauga `per_game.joker_urna2` dupa test extern si pre-screening oficial pe
  toate cele 111 metode; pastreaza numai semnale peste baseline si distincte.
- [ ] Automatizeaza testul care ruleaza fiecare metoda activa pe toate geometriile
  si compara acceptarea benchmarkului cu acceptarea engine-ului.
- [x] Raporteaza separat metodele incomplete (fereastra lipsa) si dependente de
  tie-break in decizie; raman de raportat unavailable, plate si corelate.
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
