# CLAUDE.md — orientare rapidă pentru asistent (citește ASTA, nu tot proiectul)

## Ce e proiectul
App de optimizare pool-uri loto (6/49, 5/40, Joker) cu benchmark de metode de
scoring **exclusiv CPU** (statistice/ML sklearn/geometrice/**graf-network**/coverage) +
wheeling (set-cover) + walk-forward.
Cifre reale (verificate 2026-07-20): **180 metode înregistrate** în `METHODS`, din care
**73 blacklistate** (`disabled_methods.json`) → **107 efectiv rulate** de bench
(`ALL_SPEC_METHODS`). Nu cita din memorie „~130"/„108"/„102" — renumără.
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
| `loto_engine.py` | engine-ul de generare (`run_institutional_pipeline`); ~2250 linii; NU strica bit-identitatea |
| `wheeling_methods.py` | (în RĂDĂCINA repo, NU în `loto_enterprise/core/`) algoritmi de wheeling alternativi: `wheel_ilp`, `wheel_annealing`, `wheel_genetic`, `wheel_lajolla`, `wheel_union34` + `generate_wheel` (dispatcher) |
| `loto_enterprise/core/ranking.py` | `rank_by_score` — tie-break CANONIC „top-N după scor" (sursă unică de adevăr) |
| `job_queue.py` | coadă SQLite; `DB_PATH="loto_jobs.db"` |
| `ui_shared.py` | helpere neutre (atomic_write_json/text, file_lock, ensure_worker_running) |
| `loto_enterprise/benchmark/` | benchmark: `runner.py`, `decision.py`, `methods*.py`, `bench_cache.py` |
| `bench_all_methods.py` | CLI bench; `ALL_SPEC_METHODS` = toate metodele `available` din registry |
| `_ISTORIC/` | datele CSV cu extragerile (VERSIONATE în git) |
| `best_methods.json` | decizia bench: winner + ensemble + sim_depth per joc/pool (gitignore) |
| `disabled_methods.json` | blacklist metode (73 în acest moment); merge-only |
| `bench_results/folds.csv` | output brut walk-forward al bench-ului (OVERWRITE la fiecare Re-Bench) |
| `raport_complet.txt` | raport generat (gitignore) |
| `requirements_base.txt` | dependențe venv (exclusiv CPU) — instalat de `ACTUALIZARI.bat` |

## Benchmark (cum funcționează)
- **180 metode** în `METHODS` (methods.py + extensii: methods_classical/ml/**coverage/omnius/graph**),
  44 familii. Minus blacklist (73) → **107** rulate efectiv (`ALL_SPEC_METHODS`).
  Renumără cu: `python -c "from loto_enterprise.benchmark.methods import METHODS; print(len(METHODS))"`.
- Scorer = `fn(draws_2d, max_num) -> {nr: scor_normalizat}`. Registry: `"nume": (fn, "family", trained, "desc")`.
- **Exclusiv CPU** — GPU/neural/torch/TimesFM/NeuralForecast eliminate complet (2026-07).
- `ALL_SPEC_METHODS` (în bench_all_methods.py) = metodele `available` din `METHODS` minus blacklist (dinamic).
- Re-Bench: walk-forward pe folduri → `bench_results/folds.csv` (OVERWRITE) → `decision.py` → `best_methods.json` (winner + sim_depth per joc/pool). **Țintă hituri = `BENCH_HIT_TARGET` (env `LOTO_BENCH_TARGET`, default 3+)**; clasamentul arată și 3+ și 4+.
- **Decizie robustă (Wilson) + ensemble**: decision.py sortează după limita inferioară Wilson a ratei T+ (pooled pe n_test; evenimente 4+ rare → media brută favoriza „1 hit norocos") și scrie `ensemble` (top-3 calificate, pondere ∝ Wilson) în `auto_pilot_per_pool[kN]`. Engine-ul (`_scores_via_bench_winner` → `method_selector.get_ensemble_for_game` + `combine_ensemble_scores`) combină scorurile min-max-normalizate (variance-reduction; 1 membru = bit-identic cu vechiul comportament). Re-rulare decizie fără bench: `update_best_methods_with_auto_pilot()`.
- **Wheeling** (`wheeling_methods.py` din rădăcină; env `LOTO_WHEEL_METHOD = greedy|ilp|annealing|genetic|lajolla|union34`):
  - `max_variants == 0` (fără cap de bilete) → implicit **`ilp`**: cover MINIM la garanția **CERUTĂ** de apelant (`guarantee`), NU la sistemul complet; `wheel_ilp` compară intern cu greedy și păstrează varianta cu mai puține bilete, fallback automat la greedy dacă problema e prea mare. Schimbare INTENȚIONATĂ față de greedy (nu e bit-identică).
  - `max_variants > 0` (buget fix) → rămâne **greedy** (neschimbat/bit-identic).
  - **`wheel_union34`** = UNIUNEA coverelor ILP pt `guarantee=3` ȘI `guarantee=4` (dedup pe tuple sortate) → garanție SIMULTANĂ 3-din-3 și 4-din-4 la o fracție din costul sistemului complet (pool 10/bilet 6 → 30 bilete vs 210). `guarantee>4` doar loghează WARNING; componentele rămân mereu 3 și 4.
  - ⚠️ **Filtrul anti-anomalie e DEZACTIVAT** în pipeline (`loto_engine.py`, „Fără filtru anti-anomalie — păstrăm toate variantele"; `audit.filters_disabled`). Helperul `wheeling_methods.filter_preserving_coverage` (elimină bilete DOAR dacă rămân redundante) **există dar nu are niciun call-site** — de folosit OBLIGATORIU dacă vreodată se reactivează un filtru post-wheel, ca să nu spargă garanția. `compute_coverage_pct` = API pt revalidarea acoperirii după orice filtrare.
  - Teste: `test_decision.py`, `test_method_selector.py`, `test_reset_jobs.py`.
- **Cache fold** (`D:\_BUILD\_LOTO\.bench_cache`, în AFARA OneDrive; env `LOTO_BENCH_CACHE_DIR`): keyed pe `CACHE_VERSION`+csv_hash+game+method+pct+is_random. **Bump `CACHE_VERSION` din `bench_cache.py` (acum `v10`) când se schimbă output-ul unei metode** (altfel servește stale). Date noi = re-bench COMPLET (hash pe tot istoricul).
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
- **Auto-invert** = 2 treceri: Pool 1 normal, apoi Pool 2 = re-rulare cu `manual_blacklist=Pool1` (excludere strictă, „Hard Enforcement"). UI afișează AMBELE pool-uri. Dacă pool prea mare → engine sare inversarea (audit.manual_inversion.skipped) → UI avertizează că Pool 2 = Pool 1.
- **OMNIUS — biletul NU mai există** (2026-07): a fost scos din UI, din walk-forward și din engine.
  - Ce a rămas: metoda de scoring **`omnius`** din registry-ul de bench (`methods_omnius.py`, family `meta-adaptive`) — meta-selector care ponderează toate metodele matematice; e un scorer normal, la fel ca oricare altul.
  - Ce a dispărut: `_omnius_for_pool` (**funcție inexistentă — nu o căuta**), `engine._last_pool_scores`, `audit['omnius_pool_scores']`, `omnius_hits`/`omnius_ticket` din flat-ul WF. `pick_omnius_ticket` mai există în `methods_omnius.py` dar **nu are call-site** — nu o reintroduce în pipeline.
  - `walk_forward_adapter.build_retrospective_pool_hits_flat` păstrează un parametru `omnius_ticket` **IGNORAT** (compat pt call-site-uri; UI-ul pasează `[]`).
  - **NU re-adăuga afișarea biletului OMNIUS** — e o decizie deliberată a utilizatorului.
- **sim_depth PER JOC** la Auto-Pilot (`_build_config_json(sim_depth_per_game)`); manual = slider global.
- **Auto-chain**: Re-Bench terminat → pornește automat Auto-Pilot (toggle `autopilot_after_bench`, detectat în `_tick`).
- **Walk-forward** (`walk_forward_adapter.run_honest_walk_forward` → `backtesting.run_retroactive_backtest`): validare Faza 1 (pool normal) + **Faza 2 (auto_invert)** când e ON; `hits_union` (pool) per extragere — **fără `omnius_hits`**; `progress_cb` + bară determinată; **depth = 30%** (`WF_DEPTH_PERCENT` în app_nicegui.py). Cache Pool 2: sufix `_inv.pkl`. `CACHE_VERSION` propriu în `walk_forward_adapter.py` (acum **`v13`**, separat de cel din `bench_cache.py`) — bump la orice schimbare a STRUCTURII flat-ului.
  - **Ordine WF**: Joker → 5/40 → 6/49 (`_ordered_wf_game_items`) — 6/49 ultim primește restul bugetului.
  - **Buget de timp**: `wf_budget_min` (implicit **90 min**, persistat în UI) + `should_cancel`. La depășire → oprire PARȚIALĂ (extrageri recente) și pipeline continuă (mail/shutdown).
  - **Paralelizare WF**: pașii stateless (walk-forward UI) rulează pe **~80% nuclee** (`ProcessPoolExecutor`, `LOTO_WF_CPU_FRAC=0.80`); BLAS single-thread per proces ca la bench.
- **Familie graf/network** (`methods_graph.py`, **31 metode** numpy/CPU, familii `graph-centrality`/`-community`/`-spectral`/`-walk`/`-distance`): graf de co-apariție → centralitate/spectral/comunități/random-walk. `_adj` = ASOCIERE (lift centrat), NU co-apariție brută (altfel degenerează în frecvență). Afișat „graph/network (numpy)".
- **Scoring producție**: bench-winner ensemble → fallback frecvență (fără TimesFM/torch).
- **Tie-break canonic — `loto_enterprise/core/ranking.rank_by_score`** (sursă UNICĂ de adevăr).
  Regula: sortare DESCRESCĂTOARE după tripletul `(scor, freq, număr)`, unde `freq` e OPȚIONAL (toate call-site-urile actuale pasează `None` → 0.0 pentru toți). „Număr mare întâi" evită degenerarea „1,2,3…K" la scoruri egale.
  Deleagă la ea: bench (`runner._top_k`), producție (`pool_selection.select_pool_from_scores`) și `methods_omnius.pick_omnius_ticket`. Înainte existau 3 tie-break-uri divergente → pool-ul VALIDAT de bench diferea de cel GENERAT (6/16 numere pe un scorer cu 2 nivele).
  ⚠️ **REGULĂ OBLIGATORIE pentru cod nou**: orice selecție „top-N după scor" (bench, engine, UI, analiză) apelează `rank_by_score` — **nu scrie `sorted(..., reverse=True)[:k]` propriu**. Modulul e pur stdlib (importabil din `benchmark/` și `core/`, picklabil pt ProcessPoolExecutor). Filtrarea (blacklist, interval `1..max_num`) rămâne la apelant.
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
6. **Blacklist metode** (`disabled_methods.json`): metode LEGENDATE ca slabe. NU le reactiva, NU le re-introduce și NU le folosi — nici când adaugi metode NOI. Bench-ul le exclude automat (`bench_all_methods` filtrează prin `disabled.load_disabled()`). Populare: `python prune_methods.py --apply` (după un bench COMPLET). Merge-only.
   - ⚠️ **Tăierea pe performanță e ZGOMOT**: pe loto fiecare metodă câștigă vreo celulă joc×pool. `prune --top N` rankează după `max rate_4plus` peste pool-uri — metrică ≠ decizia reală (`rate_3plus` la pool-ul jocului) → poate dezactiva IREVERSIBIL câștigători reali.
7. **Baseline-urile NU au voie să devină scorer de producție**: `random` (nedeterminist, fără sămânță) e REFERINȚĂ de comparație, nu candidat — vezi `decision.EXCLUDED_FROM_PRODUCTION = {"random"}`. Nu-l scoate din set și nu-l lăsa să ajungă în `best_methods.json`/`ensemble`, oricât de bine ar arăta pe folds. (`frequency` rămâne permis: e baseline DETERMINIST și e `SAFE_FALLBACK_SCORER`. `recency` e DETERMINIST dar e **blacklistat** în `disabled_methods.json` — deci nu e candidat, din regula 6, nu din regula asta.)
8. **Top-N după scor** → mereu `core.ranking.rank_by_score` (vezi „Tie-break canonic"), niciodată sortare proprie.

## Verificare rapidă (mediu container fără sklearn by default)
```bash
python3 -m py_compile app_nicegui.py worker.py loto_engine.py
LOTO_UI_PORT=8099 python3 app_nicegui.py & # apoi curl localhost:8099 → 200
```
E2e worker: init_job_queue → submit_job(_build_config_json) → fetch_pending_job → worker._run_pipeline_job → complete_job → decode_queue_result.

## Vezi și
`INSTRUCTIUNI.md` = ghidul pentru UTILIZATOR (flux de folosire, în română).
