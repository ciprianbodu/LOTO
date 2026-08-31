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


# --------------------------------------------------------------------------
# 6. Audit 2026-08-31: I/O live, recovery fresh-start, scrieri atomice
# --------------------------------------------------------------------------
def test_bench_progress_reads_only_log_tail(tmp_path, monkeypatch):
    """Logul de zeci de MB nu este recitit integral la fiecare tick UI."""
    import app_nicegui as app_ui
    from pathlib import Path

    log = tmp_path / "bench_full.log"
    log.write_text(
        ("x" * (1024 * 1024))
        + "\n[50/100] [loto_6_49/frequency/60%/REAL/CPU] gata\n",
        encoding="utf-8",
    )

    def _full_read_forbidden(*_args, **_kwargs):
        raise AssertionError("Path.read_text ar citi integral bench_full.log")

    monkeypatch.setattr(Path, "read_text", _full_read_forbidden)
    frac, text = app_ui._bench_progress_from(log)
    assert frac == pytest.approx(0.5)
    assert "50/100" in text
    assert "frequency" in text


def test_live_folds_reader_caches_unchanged_file(tmp_path, monkeypatch):
    """Între două flush-uri ale bench-ului, folds.csv este parsată o singură dată."""
    import app_nicegui as app_ui

    folds = tmp_path / "folds.csv"
    folds.write_text("game,method\nloto_6_49,frequency\n", encoding="utf-8")
    app_ui._BENCH_FOLDS_CACHE.update({"signature": None, "df": None})

    real_read_csv = app_ui.pd.read_csv
    calls = []

    def _counted_read_csv(*args, **kwargs):
        calls.append(args[0])
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(app_ui.pd, "read_csv", _counted_read_csv)
    first = app_ui._read_bench_folds_cached(folds)
    second = app_ui._read_bench_folds_cached(folds)

    assert len(calls) == 1
    assert second is first


def test_target_data_ready_fails_closed(tmp_path, monkeypatch):
    """Un folds corupt/blocat nu produce bannerul fals «cache rapid»."""
    import app_nicegui as app_ui

    bench_dir = tmp_path / "bench_results"
    bench_dir.mkdir()
    (bench_dir / "folds.csv").write_text("corupt", encoding="utf-8")
    monkeypatch.setattr(app_ui, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        app_ui.pd, "read_csv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")),
    )

    assert app_ui._target_data_ready() is False


def test_fresh_start_recovery_is_display_only(monkeypatch):
    """START_8000 nu finalizează automat jobul recent (mail/WF/shutdown)."""
    import app_nicegui as app_ui

    monkeypatch.setattr(app_ui, "get_latest_completed_job", lambda: {
        "id": 77, "result_json": "payload", "completed_at": "2026-08-31 10:00:00",
    })
    monkeypatch.setattr(app_ui, "decode_queue_result", lambda _raw: ([], 0))
    monkeypatch.setattr(app_ui, "_save_settings", lambda: None)
    monkeypatch.setattr(app_ui, "_save_report_file", lambda: None)
    monkeypatch.setitem(app_ui.SETTINGS, "last_finalized_job_id", 0)
    monkeypatch.setitem(app_ui.STATE, "active_job_id", None)
    monkeypatch.setitem(app_ui.STATE, "results", None)
    monkeypatch.setitem(app_ui.STATE, "results_recovered", None)

    app_ui._recover_completed_job(allow_finalize=False)

    assert app_ui.STATE["active_job_id"] is None
    assert app_ui.STATE["results"] == ([], 0)
    assert "job #77" in app_ui.STATE["results_recovered"]
    assert app_ui.SETTINGS["last_finalized_job_id"] == 77


def test_freshness_signature_stamp_uses_atomic_writer(tmp_path, monkeypatch):
    """Stampila CSV nu rescrie best_methods.json prin Path.write_text."""
    import json
    import ui_shared
    from loto_enterprise.benchmark import freshness

    bm = tmp_path / "best_methods.json"
    bm.write_text(json.dumps({"auto_pilot_per_pool": {}}), encoding="utf-8")
    monkeypatch.setattr(
        freshness, "compute_csv_signature",
        lambda gk: (f"{gk}.csv", "abc123", 10),
    )
    real_atomic = ui_shared.atomic_write_json
    calls = []

    def _counted_atomic(path, obj, **kwargs):
        calls.append(path)
        return real_atomic(path, obj, **kwargs)

    monkeypatch.setattr(ui_shared, "atomic_write_json", _counted_atomic)
    freshness.write_signatures_to_best_methods(str(bm))

    assert calls == [bm]
    saved = json.loads(bm.read_text(encoding="utf-8"))
    assert saved["_meta"]["csv_signatures"]["loto_6_49"]["hash"] == "abc123"


def test_requirements_txt_delegates_to_authoritative_cpu_list():
    text = open("requirements.txt", encoding="utf-8").read()
    assert "-r requirements_base.txt" in text
    active = "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    assert "streamlit" not in active
    assert "numba" not in active

