# Audit findings — UI / job queue / worker / walk-forward

Scope: `app_nicegui.py`, `worker.py`, `job_queue.py`, `ui_shared.py`,
`loto_enterprise/core/walk_forward_adapter.py`, `loto_enterprise/core/backtesting.py`,
`loto_enterprise/benchmark/decision.py`, `loto_enterprise/core/method_selector.py`.

Skipped (already fixed / out of scope): recover skip when `last_finalized` matches,
nearest-k 🏆 without `pool_substituted`, `recommend_optimal_config` ensemble-only,
scorer fallback from ensemble, CACHE_VERSION v18→v19.

---

## P0

### 1. New Generate / Auto-Pilot during live WF does not stale the old WF → shutdown race
- **Where:** `app_nicegui.py:314-333` (`submit_generation`), `app_nicegui.py:448-453` (`_on_bench_finished`), `app_nicegui.py:847-866` (WF `finally`)
- **Evidence:** `submit_generation` clears `results` / `retro` / `wf_status` but does **not** set `wf_user_cancel=True` and does **not** bump `wf_seq`. `_on_bench_finished` only gates on `autopilot_after_bench`, `active_job_id`, and `datasets` — not on live WF. After COMPLETED, `active_job_id` is already `None`, so Auto-Pilot can start while WF thread `my_seq=N` is still running. That thread’s `finally` sees `_stale=False` and `_user_cancelled=False` → calls `_finalize_pipeline()` → `_maybe_shutdown()` while the new job is RUNNING.
- **Impact:** PC can shut down mid-generation; old WF can also rewrite `STATE["retro"]` into the cleared dict.
- **Fix:** In `submit_generation` (and any path that replaces an in-flight validation): `STATE["wf_user_cancel"]=True` **or** bump `wf_seq` before clearing state. In `_on_bench_finished` / Auto-Pilot: skip (or defer) when `STATE.get("wf_status")` or an active `wf_seq` worker is alive. Mirror the cancel path at `app_nicegui.py:673`.

---

## P1

### 2. `last_finalized_job_id` claimed before mail/WF → crash loses mail forever
- **Where:** `app_nicegui.py:934-963`, recover `app_nicegui.py:4117-4139`
- **Evidence:** On COMPLETED, claim sets `SETTINGS["last_finalized_job_id"]=job_id` and `_save_settings()` **before** `_maybe_send_results_email()` and `_start_walk_forward()`. If the process dies between claim and mail, next boot hits `jid == already` and only redisplays — **no mail, no WF, no shutdown**.
- **Fix:** Split flags (`mail_sent_job_id` / `wf_done_job_id`) or set `last_finalized` only after mail attempt; keep a separate “claimed for display” id to prevent double claim. Recover’s `already` branch should still be able to resume missing mail/WF when those sub-flags are unset and age ≤ window.

### 3. Persist race can wipe `last_finalized_job_id` (and other settings)
- **Where:** `app_nicegui.py:208-212` (`_save_settings`), `app_nicegui.py:3870-3872` (`_bind_save`), concurrent with `app_nicegui.py:949-953`
- **Evidence:** `_save_settings` does `atomic_write_json(UI_STATE_FILE, SETTINGS)` with no lock. Two overlapping saves: A dumps snapshot without the new `last_finalized`, B dumps with it and replaces, then A’s `os.replace` writes the older snapshot → `last_finalized` lost → next restart re-finalizes (duplicate mail/shutdown) **or** (combined with #2) inconsistent recover behavior.
- **Fix:** Serialize with `file_lock(UI_STATE_FILE)` (or a threading lock + dump under lock). Optionally persist only `UI_PERSIST_KEYS` copy taken under the lock.

### 4. Consistency gate uses `avg_hits`, decision ranks on Wilson rate T+
- **Where:** `loto_enterprise/benchmark/decision.py:485-487`, `606-619`, `714`
- **Evidence:** `_windows_method_beats_random(..., base_col)` with `base_col = f"k{pool_size}"` (avg hits). Qualification requires beating `random` on **avg_hits** in ≥60% of windows. Ranking/ensemble uses Wilson on `rate_{T}plus_kN`. A method can win on T+ rate yet fail the gate (or the reverse) → wrong `qualifying` set / spurious `low_confidence`.
- **Fix:** Pass the resolved rate column (same as `_rate_col_for`) into `_windows_method_beats_random` and `_weighted_mean_lift`, or document and intentionally dual-metric — but then UI copy claiming “same keys as decision” for consistency must match.

### 5. Pool 2 retrospective in WF thread omits `wheel_coverage`
- **Where:** `app_nicegui.py:823-829` vs lazy path `app_nicegui.py:3122-3128`
- **Evidence:** `_ensure_retrospective_pool2_flat` passes `wheel_coverage=(data.get("context") or {}).get("coverage_pct")`. The primary WF-thread call does not → every Pool 2 flat entry gets `wheel_coverage=None` → `wheel_coverage_summary` reports all `unknown` → UI treats Pool 2 pool-hits as “coverage unknown” even when production context has a real `%`.
- **Fix:** Pass the same `wheel_coverage=` kwarg in the WF-thread call site (from `data["context"]["coverage_pct"]`).

### 6. Default UI port `8080` vs launcher/docs `8000`
- **Where:** `app_nicegui.py:4227`; `START_8000.bat` sets `LOTO_UI_PORT=8000`; `CLAUDE.md` says port 8000
- **Evidence:** `ui.run(... port=int(os.environ.get("LOTO_UI_PORT", "8080")))`. Running `python app_nicegui.py` without the bat binds **8080**; bat/browser open **8000**. Easy “UI not loading” / wrong-process kill via netstat `:8000`.
- **Fix:** Change default to `"8000"` (match bat + docs).

---

## P2

### 7. Auto-Pilot treats salvaged scorer as full `fallback` → skips sim_depth / misleading notify
- **Where:** `loto_enterprise/core/method_selector.py:158-164`, `1000`; `app_nicegui.py:355-370`
- **Evidence:** Dead JSON scorer + live ensemble sets `salvaged=True` → `fallback: True`. Auto-Pilot does `if cfg and not cfg.get("fallback")` → skips that game’s sim_depth and may show “Fără decizie bench încă” even though engine will still run the salvaged ensemble from `best_methods.json`.
- **Fix:** Expose `salvaged` separately from `fallback`; Auto-Pilot should apply config when ensemble is usable (`not fallback or salvaged`).

### 8. Ensemble routinely collapses (decorrelation) vs decision’s signed-Pearson keep
- **Where:** `method_selector.py:441-445`, `599-645`, `803-885`; vs `decision.py:98`, `735-737`
- **Evidence:** Decision keeps anti-correlated members (`ENSEMBLE_MAX_CORR=0.99`, signed). Runtime `_select_decorrelated` drops `|Spearman|≥0.95` including anti-correlation. With current curated correlations (CLAUDE: e.g. `graph_personalized_pr ~ graph_rwr_recent` ≈ 0.9946), nominal top-3 often becomes 1 active member (`ensemble_single_active_normalized`). Not a logic bug, but production ensembles are often a no-op blend.
- **Fix:** Re-curate `active` for score-axis decorrelation; or align decision’s rate-axis dedup with score-axis; surface collapse loudly in Auto-Pilot notify (not only audit).

### 9. `hits_union` averages in WF UI/report weighted by ticket count, not by draw
- **Where:** `app_nicegui.py:1618-1623`, `1797-1820` (`_wf_summary`)
- **Evidence:** `avg_pool = sum(hits_union)/len(flat)` over variant×draw rows. Distribution block correctly dedupes (`1694-1702`); headline averages do not. When per-step ticket counts differ (greedy timeout / partial covers), draws with more tickets dominate the “medie/pool”.
- **Fix:** Reuse `_wf_per_draw_stats` (or dedupe on `draw_index`) for avg/best pool in `_render_walk_forward` and `_wf_summary`.

### 10. `_due_status` / 3+ history use pool hits without tying to coverage
- **Where:** `app_nicegui.py:3457-3467`, `3210-3211`
- **Evidence:** Due alerts and 3+/4+ counts use `hits_union` only. `_wf_coverage_note` warns separately; if coverage is unknown/partial, “due” can fire on pool plafonds that were not ticket hits. Known semantics, but alert copy does not mention coverage.
- **Fix:** Gate due/3+ labels on `wheel_coverage_summary` (`below_100` / `unknown`) or annotate the alert string.

### 11. Stale copy: mail “la final” / docstring says end of pipeline
- **Where:** `app_nicegui.py:1148-1151`, `3945`; comment `686`
- **Evidence:** Mail is sent immediately on COMPLETED (before WF). Checkbox still says “la final”; `_maybe_send_results_email` docstring says “la finalul pipeline-ului”. Line 686 claims “mail deja trimis mai sus” on the no-results finalize path (mail was not sent in that branch).
- **Fix:** Relabel to “după generare (înainte de walk-forward)”; fix docstring/comment.

### 12. Dead UI / contract flags
- **Where:** `app_nicegui.py:161` (`STATE["pure_bench"]`), `314`/`321` (`pure` arg unused for behavior), `295-296` (`filter_consecutives`/`smart_reduction` always False); engine forces `audit["pure_bench_mode"]=True`
- **Evidence:** No UI control; `submit_generation(pure=...)` only stores STATE; config always emits `pure_bench_mode: True`. Dead knobs in the worker↔UI contract.
- **Fix:** Remove dead STATE/param or wire them; drop always-false filter fields from new configs if truly retired (keep reader defaults for old jobs).

### 13. Worker SIGTERM: double requeue; orphan dump never reloaded
- **Where:** `worker.py:371-385`, `431-453`; `job_queue.py:487-508`
- **Evidence:** Signal handler calls `_requeue_on_terminate` then `sys.exit` → `atexit` calls it again (harmless). If `complete_job` fails (concurrent requeue), result is dumped to `/tmp/loto_orphan_result_{id}.txt` but never ingested — job is PENDING and fully re-executed (time waste; dump is debug-only).
- **Fix:** On failed complete, try to re-complete if status is PENDING with empty result, or teach fetch path to adopt orphan dump when present; register atexit OR signal, not both.

### 14. `decode_queue_result` OK for cancel `{}` / fail plain text — no new functional bug
- **Where:** `ui_shared.py:243-268`
- **Evidence:** Non-dict JSON (fail_job plain string) → `None`; empty payload → `None`. Call sites treat non-tuple as invalid. No new defect found beyond already-guarded LIVE/recover paths.

---

## Summary table

| Sev | ID | Topic | Primary locus |
|-----|----|--------|----------------|
| P0 | 1 | WF not cancelled on new Generate/Auto-Pilot → shutdown | `app_nicegui.py:314`, `448`, `847` |
| P1 | 2 | early `last_finalized` loses mail/WF on crash | `app_nicegui.py:949` |
| P1 | 3 | `_save_settings` lost update | `app_nicegui.py:208` |
| P1 | 4 | consistency gate ≠ Wilson metric | `decision.py:606` |
| P1 | 5 | Pool2 WF missing coverage | `app_nicegui.py:823` |
| P1 | 6 | port default 8080 vs 8000 | `app_nicegui.py:4227` |
| P2 | 7–13 | Auto-Pilot salvaged, ensemble collapse, avg weighting, due/coverage, stale copy, dead flags, SIGTERM orphan | (see above) |
