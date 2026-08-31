"""Teste pentru corecturile de audit (FAILED msg, ILP cache, payload invalid, flag-uri moarte).

Fiecare test e scris ca să PICE pe codul de dinainte de fix — nu ca să confirme
ce face codul de azi.
"""
import os
import tempfile

import pytest


# --------------------------------------------------------------------------
# 1. Mesajul de FAILED chiar ajunge în coadă, pe câmpuri care EXISTĂ în schemă
# --------------------------------------------------------------------------
def test_fail_job_message_is_readable_from_status():
    """`fail_job` scrie motivul; UI-ul trebuie să-l poată citi din `get_job_status`.

    Bug-ul reparat: panoul citea `stt["error_msg"]` — cheie care nu e coloană în
    tabelul `jobs`, deci mereu None → „Job FAILED:" fără niciun motiv.
    """
    import job_queue as jq

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        jid = jq.submit_job("pipeline", "{}", db_path=db)
        jq.fetch_pending_job(db_path=db)
        assert jq.fail_job(jid, "CSV corupt: 0 extrageri valide", db_path=db)

        stt = jq.get_job_status(jid, db_path=db)
        assert stt["status"] == "FAILED"
        # cheia veche NU există în schemă — de asta era mesajul mereu gol
        assert "error_msg" not in stt
        # ...dar textul e acolo, pe câmpurile reale
        assert "CSV corupt" in str(stt["result_json"])
        assert "CSV corupt" in str(stt["log_tail"])


def test_ui_failed_label_uses_existing_columns():
    """Sursa UI-ului nu mai are voie să citească `error_msg` pe ramura FAILED."""
    # doar codul, fără comentarii (comentariul explică tocmai bug-ul reparat)
    code = "\n".join(
        ln for ln in open("app_nicegui.py", encoding="utf-8").read().splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "error_msg" not in code, "UI-ul citește iar o cheie inexistentă în schema `jobs`"


# --------------------------------------------------------------------------
# 2. ILP nu memoizează eșecurile de MEDIU (timeout / excepție)
# --------------------------------------------------------------------------
def test_ilp_does_not_memoize_timeout():
    """Un timeout nu are voie să dezactiveze ILP pentru tot restul procesului.

    Înainte: `res.x is None` → `_ILP_COVER_CACHE[key] = None` permanent, deci
    toți cei ~1940 de pași de walk-forward cădeau tăcut pe greedy.
    """
    import wheeling_methods as wm

    key = (9, 5, 3)
    wm._ILP_COVER_CACHE.pop(key, None)
    # time_limit ridicol de mic → solver-ul nu apucă să întoarcă o soluție
    wm._ilp_cover_positions(*key, time_limit=1e-9)
    assert key not in wm._ILP_COVER_CACHE, (
        "timeout-ul a fost memoizat — ILP rămâne mort pentru tot procesul")


def test_ilp_still_memoizes_too_big_geometry():
    """Pragul de DIMENSIUNE rămâne memoizat: ăla chiar e o proprietate stabilă."""
    import wheeling_methods as wm

    v = 60  # C(60, 6) depășește _ILP_MAX_BLOCKS
    key = (v, 6, 4)
    wm._ILP_COVER_CACHE.pop(key, None)
    assert wm._ilp_cover_positions(*key, time_limit=15.0) is None
    assert wm._ILP_COVER_CACHE.get(key, "absent") is None


def test_ilp_success_is_memoized():
    """Cazul bun (optimizarea din #81) rămâne memoizat."""
    scipy = pytest.importorskip("scipy")  # noqa: F841
    import wheeling_methods as wm

    key = (8, 4, 3)
    wm._ILP_COVER_CACHE.pop(key, None)
    cover = wm._ilp_cover_positions(*key, time_limit=30.0)
    if cover is None:
        pytest.skip("solver-ul n-a găsit soluție în buget pe mașina asta")
    assert wm._ILP_COVER_CACHE[key] == cover
    assert wm._ilp_cover_positions(*key, time_limit=30.0) is cover  # al doilea apel = cache


# --------------------------------------------------------------------------
# 3. Payload invalid la COMPLETED nu declanșează mail / WF / shutdown
# --------------------------------------------------------------------------
def test_decode_invalid_payload_is_not_a_pair():
    """Premisa gărzii: un payload gol/corupt NU trece de testul de tuplu-de-2."""
    from ui_shared import decode_queue_result

    for bad in ("{}", "", "nu-i base64", "e30="):
        got = decode_queue_result(bad)
        assert not (isinstance(got, tuple) and len(got) == 2), bad


def test_completed_branch_guards_payload():
    """Ramura LIVE COMPLETED are aceeași gardă ca recovery-ul, ÎNAINTE de mail/WF."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index('if state == "COMPLETED":')
    j = src.index("_maybe_send_results_email()", i)
    head = src[i:j]
    assert "isinstance(payload, tuple) and len(payload) == 2" in head, (
        "garda de payload lipsește sau e după trimiterea mailului")


# --------------------------------------------------------------------------
# 4. Flag-uri moarte: nu mai poluează cheia de cache
# --------------------------------------------------------------------------
def test_pure_bench_mode_not_in_input_hash():
    """`pure` nu schimbă biletele → nu are ce căuta în `input_hash`."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index("def _build_config_json")
    body = src[i:src.index("def submit_generation", i)]
    assert 'h.update(str(pure)' not in body


def test_should_use_blacklist_docstring_is_truthful():
    """Docstring-ul nu mai are voie să pretindă că producția aplică blacklist-ul."""
    from loto_enterprise.core.method_selector import should_use_blacklist

    doc = should_use_blacklist.__doc__ or ""
    assert "NU SE APLICĂ ÎN PRODUCȚIE" in doc
    assert "production rulează blacklist-ul oricum" not in doc


def test_engine_really_ignores_blacklist():
    """Faptul din spatele docstring-ului: engine-ul chiar golește blacklist-ul."""
    src = open("loto_engine.py", encoding="utf-8").read()
    assert "blacklist = set()" in src
    assert 'self.audit["filters_disabled"] = True' in src
    # Logul NU mai pretinde că exclude numerele (filtrul e mort).
    assert "excludem temporar" not in src
    assert "NU se aplică" in src


# --------------------------------------------------------------------------
# 5. Audit 2026-08-30: pass 2, abandon, mail, NaN, worker spawn
# --------------------------------------------------------------------------
def test_auto_invert_pass2_checks_load_data():
    """Pass 2 ignora return-ul lui load_data → COMPLETED cu Pool 2 gol."""
    src = open("worker.py", encoding="utf-8").read()
    i = src.index("Auto-Invert ACTIV")
    body = src[i:src.index("effective_pool", i)]
    assert "if not engine.load_data(temp_csv_path):" in body
    assert "pass 2" in body.lower() or "Auto-Invert pass 2" in body


def test_abandon_unstarted_scopes_to_active_job():
    """Abandonul de 0% nu anulează un job aflat în lucru."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index("def _abandon_unstarted_ui_job")
    body = src[i:src.index("def status_panel", i)]
    assert "job_ids=" in body
    assert "job_ids=[int(jid)]" in body.replace(" ", "")


def test_mail_body_warns_when_pool2_equals_pool1():
    """Mail-ul trebuie să spună același lucru ca UI-ul când inversarea e sărită."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index("def _build_mail_body")
    body = src[i:src.index("def _send_test_email", i)]
    assert "INVERSARE NEAPLICATĂ" in body
    assert "identic cu Pool 1" in body


def test_normalize_nan_does_not_poison_all_scores():
    """Un singur NaN nu mai face tot dict-ul NaN (min/max otrăvite)."""
    from loto_enterprise.benchmark.methods import _normalize
    import math

    out = _normalize({1: 0.0, 2: 1.0, 3: float("nan")}, 3)
    assert all(math.isfinite(v) for v in out.values())
    assert out[1] == 0.0
    assert out[2] == 1.0
    assert out[3] == 0.0


def test_normalize_all_finite_bit_identical():
    """Pe scoruri finite, garda NaN nu schimbă output-ul."""
    from loto_enterprise.benchmark.methods import _normalize

    raw = {1: 2.0, 2: 4.0, 3: 6.0}
    out = _normalize(raw, 3)
    assert out[1] == 0.0
    assert out[2] == 0.5
    assert out[3] == 1.0


def test_fail_running_jobs_closes_connection():
    """fail_running_jobs folosește `_conn` (închide), nu `_connect` gol."""
    src = open("job_queue.py", encoding="utf-8").read()
    i = src.index("def fail_running_jobs")
    body = src[i:src.index("def get_pipeline_cache", i)]
    assert "with _conn(" in body
    assert "conn_context = _connect" not in body


def test_worker_spawn_has_cooldown():
    src = open("ui_shared.py", encoding="utf-8").read()
    assert "_WORKER_SPAWN_TS" in src
    assert "_WORKER_SPAWN_COOLDOWN_S" in src
    assert "now - _WORKER_SPAWN_TS" in src


def test_wf_decision_sig_omits_inert_use_blacklist():
    src = open("loto_enterprise/core/walk_forward_adapter.py", encoding="utf-8").read()
    i = src.index("def _decision_sig")
    body = src[i:src.index("def _cache_path", i)]
    assert "use_blacklist" not in body or "INERT" in body
    assert 'bool(c.get(\'use_blacklist\'' not in body
    assert "BENCH_HIT_TARGET" in body


def test_recovery_redisplays_already_finalized_job():
    """`last_finalized_job_id` nu are voie să ascundă rezultatele după restart.

    Înainte: `if jid == already: return` — ecranul zicea „Gata de lucru" deși
    SQLite încă ținea payload-ul COMPLETED. Acum ramura reafișează, marcat
    recuperat, fără mail/WF/shutdown.
    """
    src = open("app_nicegui.py", encoding="utf-8").read()
    start = src.index("def _recover_completed_job")
    end = src.index("def _startup", start)
    body = src[start:end]
    already = body.index("jid == already")
    after = body[already:]
    assert "results_recovered" in after
    assert "STATE[\"results\"]" in after
    assert "fără mail" in after or "fara mail" in after.lower()
    # nu mai e un `return` gol imediat după already
    first_lines = after.splitlines()[:3]
    assert not any(ln.strip() == "return" for ln in first_lines)


def test_engine_writes_pool_substituted_into_audit():
    """🏆 din rezultate citește audit.bench_winner, nu recommend_optimal_config."""
    src = open("loto_engine.py", encoding="utf-8").read()
    start = src.index("def _scores_via_bench_winner")
    body = src[start:start + 8000]
    assert "pool_substituted" in body
    assert "recommend_optimal_config" in body


def test_coverage_empty_targets_is_zero():
    """C(v,g) gol (guarantee > pool) nu e 100% acoperire."""
    from wheeling_methods import compute_coverage_pct

    assert compute_coverage_pct([], [1, 2], 4) == 0.0


# --------------------------------------------------------------------------
# 6. Audit 2026-08-31: WF stale la generare nouă, invert, urna2 onest
# --------------------------------------------------------------------------
def test_submit_generation_invalidates_running_wf():
    """Generate/Auto-Pilot cât WF rulează nu are voie să lase finally-ul vechi
    să shutdown-uiască PC-ul (active_job_id e deja None după COMPLETED)."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    submit = src[src.index("def submit_generation"):src.index("def apply_autopilot_and_generate")]
    assert "_invalidate_stale_wf(" in submit
    helper = src[src.index("def _invalidate_stale_wf"):src.index("def submit_generation")]
    assert "wf_user_cancel" in helper
    assert "wf_seq" in helper


def test_wf_finally_rechecks_seq_before_shutdown():
    """Cursă: finally citește not-stale, apoi o generare nouă invalidează, apoi
    shutdown tot pornea. Re-check imediat înainte de _finalize_pipeline."""
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index("def _start_walk_forward")
    body = src[i:src.index("def _abandon_unstarted_ui_job")]
    assert "_still_mine" in body
    assert "rulare înlocuită între timp" in body
    # Pool 2 retrospectiv din thread-ul WF trebuie să-și cară acoperirea
    assert 'wheel_coverage=(data.get("context") or {}).get("coverage_pct")' in body


def test_invert_skip_allows_exact_complement():
    """remaining == pool_size e valid (Pool 2 = complement). `>=` sărea tăcut."""
    src = open("loto_engine.py", encoding="utf-8").read()
    i = src.index("if manual_set:")
    body = src[i:src.index("self.hard_core = self._get_timesfm_pool", i)]
    assert "if len(combined) > max_n_safe - pool_size:" in body
    assert "if len(combined) >= max_n_safe - pool_size:" not in body


def test_worker_invert_clamp_is_half_universe():
    """`max_n // 2 - 1` respingea un complement exact (ex. 5/40 pool 20)."""
    src = open("worker.py", encoding="utf-8").read()
    assert "pool_max_safe = _max_num // 2" in src
    assert "pool_max_safe = _max_num // 2 - 1" not in src
    assert 'audit["pool_clamp_for_invert"] = pool_clamp_info' in src
    assert 'audit["auto_invert_applied"] = False' in src


def test_pool2_title_not_inverted_when_skipped():
    src = open("app_nicegui.py", encoding="utf-8").read()
    i = src.index("_invert_skipped")
    body = src[i:src.index("_render_pool_body(fname, game, data, skey_suffix=\"_p2\"", i)]
    assert "POOL 2 — neaplicat" in body
    else_i = body.index("else:")
    assert "POOL 2 — inversat" in body[else_i:]
    assert "POOL 2 — inversat" not in body[:else_i]


def test_urna2_is_not_marked_fallback():
    """frequency pe urna2 e scorer-ul onest, nu o degradare — 🏆 arăta 'fallback'."""
    src = open("loto_engine.py", encoding="utf-8").read()
    i = src.index('if game_key == "joker_urna2":')
    body = src[i:i + 400]
    assert "single_pick_unbenched" in body
    assert 'bench_winner_info["fallback"] = True' not in body
    urna2_log = src.split("if self.game_type == \"joker\":")[1].split("if progress_cb:")[0]
    assert "TimesFM" not in urna2_log


def test_initial_hard_core_includes_never_drawn():
    src = open("loto_engine.py", encoding="utf-8").read()
    i = src.index("def _get_initial_hard_core")
    body = src[i:src.index("def _apply_consecutive_filter")]
    assert "freq[i] > 0" not in body


