# CLAUDE.md — orientare rapidă pentru asistent (citește ASTA, nu tot proiectul)

## Ce e proiectul
App de optimizare pool-uri loto (6/49, 5/40, Joker) cu benchmark de ~180 metode de
scoring (ML/foundation/statistice/geometrice) + wheeling (set-cover) + walk-forward.
**Loteria e aleatoare** — e instrument de optimizare a acoperirii, nu predicție.
UI = **NiceGUI** (`app_nicegui.py`), pe port 8000. Lansator: `START_8000.bat`.

## Arhitectură / flux de date
```
UI (app_nicegui.py, NiceGUI) ──submit_job(config_json)──> job_queue.py (SQLite: loto_jobs.db)
                                                              │
worker.py (proces SEPARAT, daemon)  ──fetch──────────────────┘
   └─ run_institutional_pipeline()  în  loto_engine.py  (scoring → pool → wheeling)
   └─ rezultat (pickle+b64) ──> job_queue ──> UI îl decodează (ui_shared.decode_queue_result)
```
- **Worker e independent de UI** (supraviețuiește închiderii UI; termină jobul în fundal). Pornit minimizat ca „LOTO WORKER".
- UI face polling la 2s (`_tick`), fără reload (stare persistentă pe server).
- `ui_shared.py` = utilitare partajate (scriere atomică, file_lock, decode rezultat, lansare worker, loguri). **Fără import de proiect → importabil de oriunde.**

## Fișiere cheie
| Fișier | Rol |
|---|---|
| `app_nicegui.py` | TOT UI-ul + orchestrare (submit, autopilot, render rezultate, raport). Fișierul principal de editat. |
| `worker.py` | daemon care consumă joburi; handler SIGTERM→requeue; LOG_FILE="loto.log" |
| `loto_engine.py` | engine-ul de generare (`run_institutional_pipeline`); ~2500 linii; NU strica bit-identitatea |
| `job_queue.py` | coadă SQLite; `DB_PATH="loto_jobs.db"` |
| `ui_shared.py` | helpere neutre (atomic_write_json/text, file_lock, ensure_worker_running) |
| `loto_enterprise/benchmark/` | benchmark: `runner.py`, `decision.py`, `methods*.py`, `bench_cache.py` |
| `bench_all_methods.py` | CLI bench; `ALL_SPEC_METHODS` = toate metodele `available` din registry |
| `_ISTORIC/` | datele CSV (gitignore) | `best_methods.json` | decizia bench (gitignore) |
| `raport_complet.txt` | raport generat (gitignore) |

## Benchmark (cum funcționează)
- ~180 metode în `METHODS` (methods.py + extensii: methods_classical/ml/torch_extra/torch_advanced, încărcate de `_load_extra_methods`).
- Scorer = `fn(draws_2d, max_num) -> {nr: scor_normalizat}`. Registry: `"nume": (fn, "family", trained, "desc")`.
- `ALL_SPEC_METHODS` (în bench_all_methods.py) = ce testează Re-Bench Full (toate metodele disponibile din `METHODS`, dinamic).
- Re-Bench: walk-forward pe folduri → `folds.csv` (OVERWRITE, nu append) → `decision.py` → `best_methods.json` (`auto_pilot_per_pool[kN]` = câștigător + sim_depth per joc/pool).
- **Cache fold** (`.bench_cache`, keyed pe csv_hash+method+pct+game+is_random): refolosit dacă datele nu s-au schimbat → re-bench rapid. NU amestecă (per metodă).
- `method_selector.recommend_optimal_config(game_key, pool)` → metoda+sim_depth; cheie = `loto_6_49`/`loto_5_40`/`joker_urna1`, NU eticheta scurtă.

## Convenții / mediu
- **O singură stație: ALF-LUPTATORI** (GPU NVIDIA). venv hardcodat `D:\_BUILD\_LOTO\.venv` (în afara OneDrive). Fără logică multi-stație.
- **Scrieri atomice** (`ui_shared.atomic_write_json`, tmp+fsync+os.replace) pt toate fișierele de stare JSON (OneDrive poate corupe la scriere parțială).
- **Git: totul pe `main`.** Eu (asistent) push pe main; `START_8000.bat` face `git pull` automat la pornire. `best_methods.json`/`_ISTORIC`/`pool_history.json`/`raport_complet.txt` sunt gitignore.
- ⚠️ Repo-ul e în OneDrive → `.git` poate fi corupt de sync. Recuperare: `git reset --hard HEAD && git pull origin main --no-edit`.
- Loguri: `loto.log` (engine/worker), `bench_full.log` (bench, scris de bench însuși). Vizibile în consola DEBUG din UI.

## Decizii/feature-uri importante (acumulate)
- **Pool max 16** (UI input + clamp la load) — ca inversarea să meargă pe toate jocurile (univers mic la 5/40=40, joker=45).
- **Auto-invert** = 2 treceri: Pool 1 normal, apoi Pool 2 = re-rulare cu `manual_blacklist=Pool1` (excludere strictă, „Hard Enforcement"). UI afișează AMBELE pool-uri. Dacă pool prea mare → engine sare inversarea (audit.manual_inversion.skipped) → UI avertizează că Pool 2 = Pool 1.
- **OMNIUS** = cel mai bun bilet SEPARAT per pool (top draw_n după scor). `_omnius_for_pool`.
- **sim_depth PER JOC** la Auto-Pilot (`_build_config_json(sim_depth_per_game)`); manual = slider global.
- **Auto-chain**: Re-Bench terminat → pornește automat Auto-Pilot (toggle `autopilot_after_bench`, detectat în `_tick`).
- **Walk-forward** (`walk_forward_adapter.run_honest_walk_forward` → `backtesting.run_retroactive_backtest`, are `progress_cb`): validare Faza 1 (pool normal) chiar și la auto-invert; bară de progres determinată.
- **Doar Re-Bench Full** (Quick scos — suprascria decizia cu doar 4 metode).
- `_METHOD_DESC` (app_nicegui) = descrieri lizibile pt metode la afișarea 🏆.

## Reguli de aur (NU strica)
1. **Bit-identitate engine**: orice modificare în `loto_engine.py` pe path-ul de generare → verifică pool+variante IDENTICE cu un baseline (rulează pipeline pe un CSV din `_ISTORIC/` înainte/după).
2. Nu sparge contractul worker↔UI (config_json / pickle result).
3. Scrieri JSON de stare → mereu `atomic_write_json`.
4. Test minimal după orice edit: `python3 -m py_compile <fisier>` + (pt UI) pornește pe un port liber și verifică HTTP 200 (sleep ~15s, importurile sunt grele).
5. Commit pe main cu mesaj clar; push; (pe web: creează PR draft dacă nu există).
6. **Blacklist metode** (`disabled_methods.json`): metode LEGENDATE ca slabe. NU le reactiva, NU le re-introduce și NU le folosi — nici când adaugi metode NOI. Bench-ul le exclude automat (`bench_all_methods` filtrează prin `disabled.load_disabled()`). Populare pe baza rezultatelor: `python prune_methods.py --apply` (după un bench COMPLET). Merge-only.

## Verificare rapidă (mediu container fără sklearn/torch by default)
```bash
python3 -m py_compile app_nicegui.py worker.py loto_engine.py
LOTO_UI_PORT=8099 python3 app_nicegui.py &  # apoi curl localhost:8099 → 200
```
E2e worker: init_job_queue → submit_job(_build_config_json) → fetch_pending_job → worker._run_pipeline_job → complete_job → decode_queue_result.

## Vezi și
`INSTRUCTIUNI.md` = ghidul pentru UTILIZATOR (flux de folosire, în română).
