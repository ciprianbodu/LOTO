# CLAUDE.md — orientare rapidă pentru asistent (citește ASTA, nu tot proiectul)

## Ce e proiectul
App de optimizare pool-uri loto (6/49, 5/40, Joker) cu benchmark de metode de
scoring **exclusiv CPU** (statistice/ML sklearn/geometrice/**graf-network**/coverage) +
wheeling (set-cover) + walk-forward.
Cifre reale (verificate 2026-08-25): **111 metode** în `METHODS` (73 blacklistate
au fost **eliminate din cod** + tombstone `omnius` → **74** în `disabled_methods.json`
— NU le reintroduce). Peste ele **curarea reversibilă** (`curated_methods.json`) lasă
**30 efectiv rulate** de bench (`ALL_SPEC_METHODS`) — 29 de producție + `random`
ca baseline; **top 10 per joc** în `curated_methods.json` ``per_game`` (decizie + clasament).
Nu cita din memorie „184"/„~130"/„108"/„102"/„107"/„180" — renumără (vezi „Curare de metode").
**Loteria e aleatoare** — e instrument de optimizare a acoperirii, nu predicție.
Diferențele dintre metode sunt în mare parte ZGOMOT (câștigătorul e instabil) — vezi memoria.
UI = **NiceGUI** (`app_nicegui.py`), pe port 8000. Lansator: `START_8000.bat`.

## Arhitectură / flux de date
```
UI (app_nicegui.py, NiceGUI) ──submit_job(config_json)──> job_queue.py (SQLite: loto_jobs.db)
                                                              │
worker.py (proces SEPARAT, daemon)  ──fetch──────────────────┘
   └─ run_institutional_pipeline()  în  loto_engine.py  (scoring → pool → wheeling)
   └─ rezultat (pickle+zstd+b64, fallback pickle+b64) ──> job_queue ──> UI îl decodează (ui_shared.decode_queue_result)
```
- **Worker e independent de UI** (supraviețuiește închiderii UI; termină jobul în fundal). Pornit minimizat ca „LOTO WORKER".
- UI face polling la 2s (`_tick`), fără reload (stare persistentă pe server).
- `ui_shared.py` = utilitare partajate (scriere atomică, file_lock, decode rezultat, lansare worker, loguri). **Fără import de proiect → importabil de oriunde.**

## Fișiere cheie
| Fișier | Rol |
|---|---|
| `app_nicegui.py` | TOT UI-ul + orchestrare (submit, autopilot, render rezultate, raport). Fișierul principal de editat. |
| `worker.py` | daemon care consumă joburi; handler SIGTERM→requeue; LOG_FILE="loto.log" |
| `loto_engine.py` | engine-ul de generare (`run_institutional_pipeline`); ~1350 linii; NU strica bit-identitatea |
| `wheeling_methods.py` | (în RĂDĂCINA repo, NU în `loto_enterprise/core/`) algoritmi de wheeling alternativi: `wheel_ilp`, `wheel_annealing`, `wheel_genetic`, `wheel_lajolla`, `wheel_union34` + `generate_wheel` (dispatcher) |
| `loto_enterprise/core/ranking.py` | `rank_by_score` — tie-break CANONIC „top-N după scor" (sursă unică de adevăr) |
| `job_queue.py` | coadă SQLite; `DB_PATH="loto_jobs.db"` |
| `ui_shared.py` | helpere neutre (atomic_write_json/text, file_lock, ensure_worker_running) |
| `loto_enterprise/benchmark/` | benchmark: `runner.py`, `decision.py`, `methods*.py`, `bench_cache.py` |
| `bench_all_methods.py` | CLI bench; `ALL_SPEC_METHODS` = `available` minus blacklist, apoi **∩ curated** (vezi „Curare de metode") |
| `_ISTORIC/` | datele CSV cu extragerile (VERSIONATE în git) |
| `loto_enterprise/core/walk_forward_adapter.py` | walk-forward pt UI (`run_honest_walk_forward`); `CACHE_VERSION` PROPRIU (`v14`), separat de cel din `bench_cache.py`; `CACHE_DIR = Path("bench_results")` (relativ → ÎN repo/OneDrive) |
| `best_methods.json` | decizia bench per joc/pool: winner + `ensemble` + `ensemble_dropped_redundant` (membri săriți ca redundanți: `{method, vs, r, reason:"perf_signature"}`) + `low_confidence` + sim_depth (gitignore). `ensemble_dropped_redundant` și `low_confidence` sunt chei NOI, scrise doar de decizia curentă — un fișier generat înainte nu le are; rescrie-l cu `update_best_methods_with_auto_pilot()`. `method_selector.recommend_optimal_config` le propagă pe listă albă (UI/Auto-Pilot); `ensemble_dropped_redundant` rămâne telemetrie de debug |
| `disabled_methods.json` | tombstone metode eliminate din cod (74 incl. `omnius`); merge-only, **IREVERSIBIL** |
| `curated_methods.json` | **curare REVERSIBILĂ**: `active` = 30 (bench); `per_game` = 10/joc (decizie+clasament). Șterge/golește `active` → revine la tot. Citit de `loto_enterprise/benchmark/curated.py` |
| `bench_results/folds.csv` | output brut walk-forward al bench-ului (OVERWRITE la fiecare Re-Bench) |
| `raport_complet.txt` | raport generat (gitignore) |
| `requirements_base.txt` | dependențe venv (exclusiv CPU) — instalat de `ACTUALIZARI.bat` |

## Benchmark (cum funcționează)
- **111 metode** în `METHODS` (74 tombstone în `disabled_methods.json`, eliminate
  din registry). Compoziție (verificată 2026-08-25): 2 de bază
  în `methods.py` + **7** module de extensii — `methods_classical` (12), `methods_ml` (11),
  `methods_coverage` (1), **`methods_graph`** (31),
  **`methods_search_649`** (29, `SEARCH_649_NEW`), **`methods_top649`** (20, `TOP649_METHODS`),
  **`methods_math_extra`** (5, `MATH_EXTRA_METHODS`).
  (`methods_omnius` a fost ELIMINAT — vezi nota OMNIUS de mai jos.)
  Curare (`curated_methods.json`) → **30** rulate la bench; **10 per joc** în `per_game`.
  Renumără cu: `python -c "from loto_enterprise.benchmark.methods import METHODS; print(len(METHODS))"`
  și `python -c "import bench_all_methods as b; print(len(b.ALL_SPEC_METHODS), b.CURATION_INFO)"`.
- Scorer = `fn(draws_2d, max_num) -> {nr: scor_normalizat}`. Registry: `"nume": (fn, "family", trained, "desc")`.
- **Exclusiv CPU** — GPU/neural/torch/TimesFM/NeuralForecast eliminate complet (2026-07).
- `ALL_SPEC_METHODS` (în bench_all_methods.py) = `available` minus blacklist, **apoi ∩ curated** (dinamic, ambele filtre se compun).

### Curare de metode (`curated_methods.json`) — REVERSIBILĂ, ≠ blacklist
- **Ce e**: un al doilea filtru peste `ALL_SPEC_METHODS`, în rădăcina repo. Dacă fișierul
  există și `active` e nevidă, bench-ul rulează DOAR acel subset. Absent/gol/invalid →
  comportamentul de dinainte (toate metodele `available` minus blacklist). Cod:
  `loto_enterprise/benchmark/curated.py` (`load_curated`, `is_curation_active`,
  `apply_curation`, `curated_meta`, `log_curation`, `REQUIRED_METHODS`).
- **Deosebirea de `disabled_methods.json`**: blacklist-ul e MERGE-ONLY și permanent
  (regula de aur 6) — o metodă intrată acolo nu se mai scoate. Curarea e o SELECȚIE
  ACTIVĂ, complet reversibilă. **NU muta curarea în blacklist** — ar face tăierea a 90+
  metode ireversibilă.
- **ANULARE (cum revii la toate metodele)**: șterge `curated_methods.json` (sau golește
  lista `active` la `[]`), apoi rulează un **Re-Bench**. `ALL_SPEC_METHODS` redevine
  automat cele **111**. Nimic nu se pierde între timp: metodele tăiate de curare rămân
  în `METHODS`, nefolosite. Fără re-bench, `bench_results/folds.csv` și `best_methods.json`
  rămân cele vechi — decizia continuă să aleagă din metodele DIN folds, nu din curare.
- ⚠️ **Criteriul e ACOPERIREA DE SEMNAL DISTINCT, NU clasamentul.** Tăierea pe performanță
  e ZGOMOT, măsurat pe datele reale: overlap top-15 între prima și a doua jumătate a
  ferestrelor = **13-20%** (joker 3/15, 5/40 3/15, 6/49 2/15) → „top 15" nu e o
  proprietate stabilă a metodelor; iar din 45 de celule joc×pool doar **19** metode
  distincte câștigă vreo celulă, **4 dintre ele fiind câștigate de baseline-ul `random`**
  → „a câștigat o celulă" nu e dovadă de calitate. Deci selecția păstrează metode cu
  scoruri NECORELATE între ele (|Spearman| < `method_selector.MAX_MEMBER_CORR` = 0.95) și
  elimină clonele. **Nu re-selecta setul pe baza clasamentului** — vezi și regula de aur 6.
- **Ce NU schimbă curarea**: rata de hituri 3+/4+. `P(≥3 din pool de K)` e hipergeometrică
  — depinde doar de K și de joc (pool 10: 6/49 = 9.03%, 5/40 = 8.93%, joker = 6.47%), nu
  de CARE numere sunt în pool. Pârghiile reale rămân dimensiunea pool-ului și acoperirea
  pool→bilete (wheeling).
- **Ce schimbă real**: (1) ensemble-ul nu mai e no-op — 0/120 perechi au |r| ≥ 0.95 în
  vreun joc (cea mai corelată: `graph_katz_low ~ graph_rwr_recent` = 0.9096 pe 6/49),
  deci top-3-ul ales de `decision.py` supraviețuiește întreg decorelării din
  `method_selector`, în loc să colapseze la 1 membru activ; (2) clasamentul UI devine
  citibil (16 rânduri în loc de 107); (3) bench mai rapid.
- **MĂSURAT după primul re-bench cu curare (2026-07-27, 384 folduri)** — nu predicție:
  5/40 = ensemble de 3 membri pe toate cele 15 pool-uri; 6/49 = 3 membri pe 12/15
  pool-uri, 1 membru pe 3; joker_urna1 = 3 membri pe 5/15, 2 pe 6/15, 1 pe 4/15 și
  **1 pool cu `low_confidence=True`**. Deci „ensemble plin pe toate jocurile" NU se
  confirmă peste tot: cu 15 candidați, la unele pool-uri trec gate-ul de consistență
  doar 1-2 metode. Renumără după fiecare re-bench, nu cita de aici.
- ⚠️ **Efect secundar real: `ENSEMBLE_MIN_SIGNATURE_POINTS = 5` nu mai e o gardă inertă.**
  Cu 107 metode semnătura era (106, 60) și garda nu se declanșa niciodată; cu 16 metode
  numărul de CALIFICATE per pool e 2-4 → sub prag → dedup-ul pe semnătura de performanță
  din `decision.py` e SĂRIT sistematic, iar `ensemble_dropped_redundant` rămâne gol.
  Nu e regresie (curarea garantează deja necorelarea pe axa SCORURI, iar
  `method_selector._select_decorrelated` rămâne activ), dar comentariul „garda e INERTĂ"
  din `decision.py` era stale și a fost corectat.
  ⚠️ **Onest despre viteză**: 88.7% din economie vine din 4 metode scumpe (`omnius` 65.3%
  din tot bench-ul, `ml_catboost`, `croston_sba`, `croston_classic`) — tăind DOAR acele 4
  se obținea deja factor ~8.9x. Restul drumului (103→16) aduce doar factorul suplimentar
  ~3.5x. Reducerea la 15 se justifică prin decorelare, nu prin viteză.
  (Măsurătoare ISTORICĂ, 2026-07: `omnius` a fost eliminat din proiect în 2026-08-09.)
- **Metode STRUCTURAL obligatorii în `active`** (`curated.REQUIRED_METHODS`):
  `random` și `frequency`. `frequency` = `decision.SAFE_FALLBACK_SCORER`. `random` NU e
  candidat de producție (`decision.EXCLUDED_FROM_PRODUCTION`) dar e indispensabil ca
  BASELINE: `decision._windows_method_beats_random()` întoarce `(0, 0)` dacă rândul
  `random` lipsește din folds.csv → `n_total == 0` → `continue` pe TOATE metodele →
  `qualifying` gol → `low_confidence` pe toate jocurile. Fără `random` gate-ul de
  consistență nu funcționează deloc.
- **Override explicit**: `--methods a,b,c` și `--quick` ocolesc curarea (deliberat).
- **Plase de siguranță** în `apply_curation`: nume necunoscute/blacklistate se sar cu
  WARNING; dacă lista nu lasă NICIO metodă validă, curarea e ignorată și rulează tot
  (mai bine tot decât bench gol). Telemetria ajunge în `best_methods.json._meta.curated`
  și în banner-ul UI de Re-Bench (`_curation_banner_info` în `app_nicegui.py`).
- Re-Bench: walk-forward pe folduri → `bench_results/folds.csv` (OVERWRITE) → `decision.py` → `best_methods.json` (winner + sim_depth per joc/pool). **Țintă hituri = `BENCH_HIT_TARGET` (env `LOTO_BENCH_TARGET`, default 3+)**; clasamentul arată și 3+ și 4+.
  ⚠️ `LOTO_BENCH_TARGET` acceptă REALMENTE doar **3** sau **4**: `runner.py` emite exclusiv coloanele `rate_3plus_*` / `rate_4plus_*`. Orice altă valoare (ex. 5) face `decision._resolve_rate_col` să cadă TĂCUT pe metrica 4+ (doar un WARNING în log + `rate_col_mismatch=True`) — decizia se ia atunci pe altă țintă decât cea cerută.
- **Decizie robustă (Wilson) + ensemble**: decision.py filtrează întâi pe **gate-ul de consistență** — o metodă e „calificată" doar dacă bate baseline-ul `random` în ≥60% din ferestre (`CONSISTENCY_THRESHOLD = 0.60`) — apoi sortează calificatele după limita inferioară Wilson a ratei T+ (pooled pe n_test; evenimente 4+ rare → media brută favoriza „1 hit norocos") și scrie `ensemble` (top-`ENSEMBLE_MAX_METHODS`=3 calificate, pondere ∝ Wilson) în `auto_pilot_per_pool[kN]`. Dacă NICIO metodă nu trece gate-ul → ramură de fallback cu `low_confidence=True` (alegerea e conservatoare, nu „cea mai consistentă").
  - ⚠️ **Ordinea din clasamentul UI ≠ ordinea deciziei.** Criteriul PRINCIPAL e același (Wilson_lb), dar tie-break-ul secundar diferă: decizia sortează `(Wilson_lb, lift mediu ponderat vs random, consistență n_beat/n_total)` (`decision.py`, `qualifying.sort`), UI-ul `(Wilson_lb, rata brută medie, avg_hits)` (`app_nicegui.py`). La ratele T+ egalitățile EXACTE pe Wilson sunt frecvente (succesele sunt întregi), deci pentru o bună parte din listă ordinea afișată poate diferi de cea a deciziei. Nici gate-ul de consistență nu e reflectat în clasament.
  - **Decorelare pe DOUĂ straturi, cu criterii DIFERITE, deliberat**: (1) `decision.py` pe axa RATE (semnătură de performanță, Pearson **SEMNAT**, `ENSEMBLE_MAX_CORR = 0.99`) — membrii ANTI-corelați sunt PĂSTRAȚI ca fiind complementari, cei săriți ajung în `ensemble_dropped_redundant` cu `reason="perf_signature"`; (2) `method_selector._select_decorrelated` pe axa SCORURI (Spearman în **MODUL**, `MAX_MEMBER_CORR = 0.95`) — aici anti-corelația NU e complementaritate (după min-max blend-ul devine monoton în membrul mai greu), deci și `r ≤ -0.95` elimină. Consecință: un ensemble pe care decizia îl păstrează poate fi redus la runtime, iar ponderile Wilson din `best_methods.json` nu sunt neapărat cele folosite efectiv.
  - Engine-ul (`_scores_via_bench_winner` → `method_selector.get_ensemble_for_game` + `combine_ensemble_scores`) combină scorurile min-max-normalizate **după** filtrul de varianță (membri goi/plați) și **după** decorelarea greedy — un top-3 NOMINAL poate colapsa la 1 membru ACTIV. Observabil în `audit["ensemble_active"]` / `["ensemble_dropped"]` / `["ensemble_dropped_correlated"]`; `describe_ensemble` = același calcul, pentru UI.
  - **Bit-identitate (nuanțat, regula de aur 1)**: scoruri BRUTE, bit-identic cu apelul direct al scorer-ului, DOAR pentru un ensemble **NOMINAL** de exact 1 membru. Dacă nominal >1 dar decorelarea lasă 1 singur membru ACTIV, scorurile lui sunt min-max normalizate — rank-preserving (același pool), dar **NU** bit-identice la nivel de scor (`audit["ensemble_single_active_normalized"]`).
  - Re-rulare decizie fără bench: `update_best_methods_with_auto_pilot()`.
- **Wheeling** (`wheeling_methods.py` din rădăcină; env `LOTO_WHEEL_METHOD`). **Sursa unică a listei de valori = `wheeling_methods.py` (docstring-ul de modul + dict-ul `WHEEL_METHODS`)**: `ilp|annealing|genetic|lajolla|union34`, iar `greedy` **și orice valoare necunoscută** cad pe `_greedy_fallback`.
  - `max_variants == 0` (fără cap de bilete) → implicit **`lajolla`**: design cunoscut-optim din `covering_designs/` când există pt C(pool, pick, guarantee); altfel cade pe ILP, iar ILP pe greedy. Lanț monoton: niciodată mai multe bilete decât înainte, aceeași garanție 100%. Măsurat pe pool 12/garanție 4: 6/49 54→41, 5/40+Joker 123→113.
  - `max_variants > 0` (buget fix) → rămâne **greedy** (neschimbat/bit-identic).
  - `_LAJOLLA_DIRS` e ancorat la **modul** (`Path(__file__).parent`), nu la CWD; căile relative rămân doar ca override. Cu CWD străin designul se pierdea TĂCUT → 55 bilete în loc de 41 pe 6/49 pool 12 g4.
  - `wheel_ilp` alege între ILP și greedy pe **(acoperire, apoi bilete)** — nu doar pe număr. Greedy-ul (`generate_combinatorial_wheel`) se oprește la **1000 de iterații**, deci pe `guarantee == pick` întoarce 1001 bilete la 33% acoperire; comparat doar pe număr, câștiga împotriva unui ILP 100%.
  - `guarantee == pick` = **sistem complet**, cerere legitimă și deliberată din UI (nu o clampa!). În schimb garanția **internă** a WF (`_wf_guarantee`) e plafonată la `pick-1` — altfel pool ≥ 15 la 5/40+Joker degenera. `_wheel_sig` e neschimbat pentru pool ≤ 14 → cache-urile WF existente rămân valide.
  - **`wheel_union34`** = UNIUNEA coverelor ILP pt `guarantee=3` ȘI `guarantee=4` (dedup pe tuple sortate) → garanție SIMULTANĂ 3-din-3 și 4-din-4 la o fracție din costul sistemului complet (pool 10/bilet 6 → 30 bilete vs 210). `guarantee>4` doar loghează WARNING; componentele rămân mereu 3 și 4.
  - ⚠️ **Niciun filtru post-wheel** pe path-ul de producție (`audit.filters_disabled`). Helperul `wheeling_methods.filter_preserving_coverage` (elimină bilete DOAR dacă rămân redundante) **există dar nu are call-site de producție** (doar teste) — de folosit OBLIGATORIU dacă vreodată se reactivează un filtru post-wheel. `compute_coverage_pct` = API pt revalidarea acoperirii.
- **Teste** (rădăcina repo, `pytest`) — inclusiv `test_wheeling.py` (acoperire 100%, fallback La Jolla→ILP→greedy, `filter_preserving_coverage`). După orice modificare la wheeling, revalidează și cu `compute_coverage_pct`.
- **Cache fold** (`D:\_BUILD\_LOTO\.bench_cache`, în AFARA OneDrive; env `LOTO_BENCH_CACHE_DIR`): keyed pe `CACHE_VERSION`+csv_hash+game+method+pct+is_random. **Bump `CACHE_VERSION` din `bench_cache.py` (acum `v12`) când se schimbă output-ul unei metode** (altfel servește stale). Date noi = re-bench COMPLET (hash pe tot istoricul).
- `method_selector.recommend_optimal_config(game_key, pool)` → metoda+sim_depth; cheie = `loto_6_49`/`loto_5_40`/`joker_urna1`, NU eticheta scurtă.
- **Alias metode legacy**: `METHOD_ALIASES` în methods.py (`ml_catboost_cpu` → `ml_catboost` etc.) — compatibilitate best_methods.json vechi până la re-bench.

## Convenții / mediu
- **O singură stație: ALF-LUPTATORI**. **Python 3.14.6** (`py -3.14`). venv hardcodat `D:\_BUILD\_LOTO\.venv` (în afara OneDrive). Fără logică multi-stație.
- **Scrieri atomice** (`ui_shared.atomic_write_json`, tmp+fsync+os.replace) pt toate fișierele de stare JSON (OneDrive poate corupe la scriere parțială).
- **Git: totul pe `main`.** Eu (asistent) push pe main; `START_8000.bat` face `git pull` automat la pornire. `best_methods.json`/`pool_history.json`/`raport_complet.txt` sunt gitignore. **`_ISTORIC/` E VERSIONAT** (sursă de adevăr pt bench/analiză); `START_8000.bat`+`ACTUALIZARI.bat` fac auto-commit+push (`:push_istoric`) când `update_csv.py` aduce extrageri noi.
- ⚠️ Repo-ul e în OneDrive → `.git` poate fi corupt de sync. Recuperare: `git reset --hard HEAD && git pull origin main --no-edit`.
- Loguri: `loto.log` (engine/worker), `bench_full.log` (bench, scris de bench însuși). Vizibile în consola DEBUG din UI.
- **Mediu venv**: `ACTUALIZARI.bat` instalează din `requirements_base.txt` (nicegui, pandas, sklearn, statsmodels, xgboost, lightgbm, catboost etc.) — **fără torch/CUDA/neural**.
- **Python 3.14.6 API**: cache pickle → `compression.zstd` (`py314_io.py`); job worker → `pack_queue_result`/`decode_queue_result`; UI HTML → t-strings PEP 750 (`render_html_safe`); tipuri `list[str]`/`X | None`; WF paralel → `itertools.batched`; compresie scor → `compression.zlib`.

## Decizii/feature-uri importante (acumulate)
- **Pool max 16** (UI input + clamp la load) — ca inversarea să meargă pe toate jocurile (univers mic la 5/40=40, joker=45).
- **Auto-invert / Pool 2** = 2 treceri cu ACEEAȘI metodă/ensemble câștigătoare: Pool 1 normal, apoi Pool 2 = re-rulare cu `manual_blacklist=Pool1` (excludere strictă, „Hard Enforcement"). Toggle UI „Inversare automată" (`auto_invert_val`, DEFAULTS=False; starea persistată poate fi ON). UI afișează AMBELE pool-uri. Dacă pool prea mare → engine sare inversarea (audit.manual_inversion.skipped) → UI avertizează că Pool 2 = Pool 1. Restaurat 2026-08-09 după ce fusese scos dintr-o interpretare greșită a „niciun manual blacklist".
- **OMNIUS — ELIMINAT COMPLET** (biletul în 2026-07, metoda de scoring în 2026-08-09).
  - **Modulul `methods_omnius.py` NU MAI EXISTĂ** — nu-l căuta, nu-l reintroduce. Odată cu el au dispărut `score_omnius`, `pick_omnius_ticket`, `_omnius_candidates` și familia `meta-adaptive` (azi zero metode).
  - **De ce a fost scos** (motiv de CORECTITUDINE, nu de gust): `_omnius_candidates()` își lua candidații direct din `METHODS`, filtrând doar printr-un denylist propriu (`random`, `omnius`, `time_llm`) + câteva familii — **fără să citească `disabled_methods.json` sau `curated_methods.json`**. Măsurat: pondera intern **149 de metode, din care 50 BLACKLISTATE**, adică repunea în pool-ul de producție metode legendate ca slabe → **încălca regula de aur 6**, și ocolea complet curarea (doar 12 din cele 17 curate erau printre candidați). Dacă vreodată reintroduci un meta-selector, **filtrează-i candidații prin blacklist + curare**.
  - Performanța nu-l justifica oricum: locul 6/17 la `joker_urna1` k12, `Wilson_lb = 0.1052` identic cu `ml_logistic`, dar **13.44 s/fereastră vs 0.15** (89% din bench-ul de Joker, 26 din 29 s de generare).
  - Ce a dispărut mai devreme, odată cu biletul: `_omnius_for_pool` (**funcție inexistentă — nu o căuta**), `engine._last_pool_scores`, `audit['omnius_pool_scores']`, `omnius_hits`/`omnius_ticket` din flat-ul WF.
  - ⚠️ `best_methods.json` generat ÎNAINTE de eliminare încă listează `omnius` în ensemble-urile Joker (k8/k11/k12/k13). Nu crapă: `method_selector.get_ensemble_for_game` sare membrii necunoscuți cu WARNING și renormalizează ponderile (3 membri → 2, ~50/50). Dispare de tot la primul Re-Bench.
  - Parametrul mort `omnius_ticket` din `build_retrospective_pool_hits_flat` a fost **șters** (2026-08-09); call-site-urile din UI nu-l mai pasau.
  - **NU re-adăuga afișarea biletului OMNIUS** — e o decizie deliberată a utilizatorului.
- **sim_depth PER JOC** la Auto-Pilot (`_build_config_json(sim_depth_per_game)`); manual = slider global.
- **Auto-chain**: Re-Bench terminat → pornește automat Auto-Pilot (toggle `autopilot_after_bench`, detectat în `_tick`).
- **Walk-forward** (`loto_enterprise/core/walk_forward_adapter.py`: `run_honest_walk_forward` → `loto_enterprise/core/backtesting.run_retroactive_backtest`): validare **doar Pool 1** (honest WF); Pool 2 când auto-invert e ON = istoric **retrospectiv** (pool+wheel curent pe aceleași extrageri, `_ensure_retrospective_pool2_flat`) — fără dublarea timpului WF. `hits_union` (pool) per extragere; `progress_cb` + bară determinată; **depth = 30%** (`WF_DEPTH_PERCENT` în app_nicegui.py). Backtesting acceptă încă `auto_invert` pe pas (2× pipeline) dacă e cerut explicit; UI-ul nu-l folosește pe path-ul WF.
  - ⚠️ **Pașii de WF/backtest NU scriu `pool_history.json`** (`track_pool_variation=False` în ambele call-site-uri din `backtesting.py`; default `True` = producție). Cheia e `{joc}_{pool}_{pass}` — EXACT cheia de producție — deci înainte cei ~1940 de pași ai unui ciclu WF suprascriau intrarea reală și `pool_variation` din raport compara producția cu backtestul; în plus ~25 de procese scriau concurent același `.tmp` (nume fix în `ui_shared.atomic_write_*`) → 216 erori de tracker în `loto.log`. **Nu folosi `enable_adaptive_persistence` ca poartă** — producția (`worker.py`) îl pasează tot `False`.
  - **Cache WF**: `walk_forward_adapter.CACHE_DIR = Path("bench_results")` — cale RELATIVĂ, deci **ÎN repo/OneDrive**, spre deosebire de cache-ul de bench (`D:\_BUILD\_LOTO\.bench_cache`, în AFARA OneDrive); **nu are env de override**. Folderul e gitignorat (`bench_results/*`, cu excepția `folds.csv` + `report.json`).
  - `CACHE_VERSION` PROPRIU în `walk_forward_adapter.py` (acum **`v17`**, separat de cel din `bench_cache.py`, acum `v12`) — bump la orice schimbare a STRUCTURII flat-ului, a pool-ului GENERAT, SAU a wheel-ului (algoritm + garanția internă a WF). Semnătura `_decision_sig` include scorer/ensemble/target **plus** `_wheel_sig` (`LOTO_WHEEL_METHOD` sau `lajolla` + `g{_wf_guarantee}`) — fără wheel în cheie, trecerea ILP→La Jolla servea cache vechi cu costuri umflate. ⚠️ Bump-ul **doar schimbă numele fișierului** — pickle-urile vechi rămân pe disc; curăță-le cu `purge_stale_wf_cache(dry_run=False)` (doar versiuni ≠ curentă) sau `clear_walk_forward_cache()` (TOATE, inclusiv curentă → rulează-l ÎNAINTE de primul WF nou).
  - Acoperirea WF e **acumulativă între sesiuni** (la hit parțial se re-rulează ca să EXTINDĂ acoperirea, iar dacă rularea nouă acoperă mai puțin se păstrează cache-ul vechi) → un bump de `CACHE_VERSION` aruncă acoperirea acumulată, nu doar timpul unei rulări. După un bump, prima validare 6/49 (ultimul în ordinea WF) va fi PARȚIALĂ; crește temporar `wf_budget_min` dacă vrei acoperire comparabilă.
  - **Ordine WF**: Joker → 5/40 → 6/49 (`_ordered_wf_game_items`) — 6/49 ultim primește restul bugetului.
  - **Buget de timp**: `wf_budget_min` (implicit **90 min**, persistat în UI) + `should_cancel`. La depășire → oprire PARȚIALĂ (extrageri recente) și pipeline continuă (mail/shutdown). Fallback-ul la câmp golit = `DEFAULTS["wf_budget_min"]` (nu o constantă separată).
  - **Paralelizare WF**: pașii stateless (walk-forward UI) rulează pe **~80% nuclee** (`ProcessPoolExecutor`, `LOTO_WF_CPU_FRAC=0.80`); BLAS single-thread per proces ca la bench.
- **Familie graf/network** (`methods_graph.py`, **31 metode** numpy/CPU): graf de co-apariție → centralitate/spectral/comunități/random-walk. `_adj` = ASOCIERE (lift centrat), NU co-apariție brută (altfel degenerează în frecvență).
  **6** valori distincte de `family` (renumără: `Counter(v[1] for v in GRAPH_METHODS.values())`): `graph-centrality` (15), `graph-community` (5), `graph-distance` (4), `graph-spectral` (3), `graph-walk` (3) și `graph/network (numpy)` (1 — metoda `graph_649_katz_community`). ⚠️ Ultimul string e ambiguu: e ȘI eticheta de afișare a întregii familii în UI, ȘI valoarea reală de `family` a acelei unice metode.
- **Scoring producție**: bench-winner ensemble → fallback frecvență (fără TimesFM/torch).
- **Tie-break canonic — `loto_enterprise/core/ranking.rank_by_score`** (sursă UNICĂ de adevăr).
  Regula: sortare DESCRESCĂTOARE după tripletul `(scor, freq, număr)`, unde `freq` e OPȚIONAL (toate call-site-urile actuale pasează `None` → 0.0 pentru toți). „Număr mare întâi" evită degenerarea „1,2,3…K" la scoruri egale.
  ⚠️ **Precondiție: scoruri FINITE.** Regula nu se aplică deloc la `NaN`: comparația de tuple face scurt-circuit pe primul element, deci tie-break-ul pe număr nu mai intră niciodată, iar rezultatul devine dependent de ORDINEA DE INSERARE în dict (verificat: același conținut inserat în altă ordine dă alt top-3; la all-NaN, ordinea returnată e pur și simplu ordinea dictului). Nici `_normalize` din registry nu filtrează NaN — un singur NaN otrăvește tot dict-ul (`vmin`/`vmax` devin NaN). **Metodă nouă = garantează scoruri finite.**
  Deleagă efectiv la ea: bench (`runner._top_k`) și producție (`pool_selection.select_pool_from_scores`) — singurele două căi ACTIVE (al treilea consumator istoric, `pick_omnius_ticket`, a dispărut odată cu `methods_omnius.py`). Înainte existau 3 tie-break-uri divergente → pool-ul VALIDAT de bench diferea de cel GENERAT (6/16 numere pe un scorer cu 2 nivele).
  ⚠️ **REGULĂ OBLIGATORIE pentru cod nou**: orice selecție „top-N după scor" (bench, engine, UI, analiză) apelează `rank_by_score` — **nu scrie `sorted(..., reverse=True)[:k]` propriu**. Path-ul principal de producție e migrat (vezi regula de aur 8). Modulul e pur stdlib (importabil din `benchmark/` și `core/`, picklabil pt ProcessPoolExecutor). Filtrarea (blacklist, interval `1..max_num`) rămâne la apelant.
  `pool_selection.select_pool_from_scores` = top-N pur, fără diversificare decade/paritate și fără tie-break pe frecvență (scoase 2026-07). `draw_matrix` a rămas în semnătură doar pt compat — NU influențează selecția.
- **Final pipeline** (`_finalize_pipeline`, după WF): mail rezultate (`mail_on_complete` + `mail_config.json` gitignored / env SMTP) + auto-shutdown (`shutdown_on_complete`, `shutdown /s /t 60` anulabil). Fiecare pas izolat în try/except; log `[FINALIZE]`/`[MAIL]`/`[SHUTDOWN]`.
- **Recuperare job la pornire** (`_recover_completed_job`): job terminat cât UI-ul era jos (get_active_job vede doar PENDING/RUNNING). `completed_at` (job_queue) + fereastră 10min → finalizare completă; mai vechi → doar afișare marcată „recuperat". `last_finalized_job_id` (persistat) = finalizare O SINGURĂ DATĂ.
- **Doar Re-Bench Full** (Quick scos — suprascria decizia cu doar 4 metode).
- `_METHOD_DESC` (app_nicegui) = descrieri lizibile pt metode la afișarea 🏆. `_method_library` = categorie în clasament.

## Reguli de aur (NU strica)
1. **Bit-identitate engine**: orice modificare în `loto_engine.py` pe path-ul de generare → verifică pool+variante IDENTICE cu un baseline (rulează pipeline pe un CSV din `_ISTORIC/` înainte/după).
2. Nu sparge contractul worker↔UI (config_json / pickle result).
3. Scrieri JSON de stare → mereu `atomic_write_json`.
4. Test minimal după orice edit: `python3 -m py_compile <fisier>` + (pt UI) pornește pe un port liber și verifică HTTP 200 (sleep ~15s, importurile sunt grele).
5. Commit pe main cu mesaj clar; push; (pe web: creează PR draft dacă nu există).
6. **Blacklist / tombstone** (`disabled_methods.json`): 74 metode LEGENDATE ca slabe
   (73 scoase din registry + `omnius`), **ELIMINATE din `METHODS`**. NU le reactiva,
   NU le re-introduce în registry și NU le folosi — nici când adaugi metode NOI.
   Loader-ul + bench-ul filtrează încă pe listă ca plasă. Populare istorică:
   `prune_methods.py --apply`. Merge-only. Helperii folosiți de blend-uri curate
   (`score_gap_poisson` etc.) rămân neînregistrați — nu le reînregistra sub numele vechi.
   - ⚠️ **Tăierea pe performanță e ZGOMOT**: pe loto fiecare metodă câștigă vreo celulă joc×pool. `prune --top N` rankează după `max rate_4plus` peste pool-uri — metrică ≠ decizia reală (`rate_3plus` la pool-ul jocului) → poate dezactiva IREVERSIBIL câștigători reali. Măsurat: overlap top-15 între jumătățile de date = 13-20%, iar `random` câștigă 4 din 45 de celule.
   - ✅ **Vrei mai puține metode? Folosește `curated_methods.json`, NU blacklist-ul** (vezi „Curare de metode"): același efect pe bench, dar REVERSIBIL, iar criteriul e redundanța (|Spearman| ≥ 0.95), nu clasamentul.
7. **Baseline-urile NU au voie să devină scorer de producție**: `random` (nedeterminist, fără sămânță) e REFERINȚĂ de comparație, nu candidat — vezi `decision.EXCLUDED_FROM_PRODUCTION = {"random"}`. Nu-l scoate din set și nu-l lăsa să ajungă în `best_methods.json`/`ensemble`, oricât de bine ar arăta pe folds. (`frequency` rămâne permis: e baseline DETERMINIST și e `SAFE_FALLBACK_SCORER`. `recency` a fost blacklistată și **eliminată din METHODS** — vezi regula 6.)
   - ⚠️ Corolar: `random` trebuie totuși să RULEZE în bench. E în `curated.REQUIRED_METHODS` — dacă îl scoți din `curated_methods.json`, gate-ul de consistență din `decision.py` se rupe complet (`low_confidence` pe toate jocurile), nu doar „lipsește o linie din clasament".
8. **Top-N după scor** → în cod NOU, mereu `core.ranking.rank_by_score` (vezi „Tie-break canonic"); nu scrie sortare proprie.
   Path-ul principal e migrat: trunchierea pool-ului, Urna 2 Joker și completarea pe `manual_blacklist` folosesc `rank_by_score`. Mai rămâne un `sorted(..., reverse=True)` activ în `_get_timesfm_pool` (completare defensivă din blacklist când pool-ul e incomplet) și unul în `generate_combinatorial_wheel` (ordonare pool pt wheel, nu top-N subset). Migrarea lor pe egalități schimbă output-ul → încalcă regula 1; de făcut într-un commit dedicat, nu în treacăt.

## Verificare rapidă (mediu container fără sklearn by default)
```bash
python3 -m py_compile app_nicegui.py worker.py loto_engine.py
LOTO_UI_PORT=8099 python3 app_nicegui.py & # apoi curl localhost:8099 → 200
```
E2e worker: init_job_queue → submit_job(_build_config_json) → fetch_pending_job → worker._run_pipeline_job → complete_job → decode_queue_result.

## Vezi și
`INSTRUCTIUNI.md` = ghidul pentru UTILIZATOR (flux de folosire, în română).
