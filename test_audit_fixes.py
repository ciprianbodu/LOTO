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
