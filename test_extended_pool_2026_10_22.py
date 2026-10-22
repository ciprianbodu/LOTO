"""Pool extins pentru 4+ (2026-10-22): 17-18 numere pe același număr de variante.

Opțiune separată, implicit oprită: pool-ul de 16 cu garanția 4 completă rămâne
regula. Pornită, motorul pune un design partial de 4 pe primele 17 sau 18 numere
și îl certifică la fiecare apel față de coverul complet pe 16 (§7).
"""

import functools
import json
import random
from itertools import combinations
from math import comb

import pandas as pd
import pytest

import job_queue as jq
import worker
from covering import extended_pool as xp
from covering import probability as prob
from covering.dispatch import generate_wheel
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.full_ticket import build_full_ticket
from ui_shared import decode_queue_result

# joc: (bilet, extrase, univers, variante)
GAMES = {"6/49": (6, 6, 49, 152), "5/40": (5, 6, 40, 404), "joker": (5, 5, 45, 404)}
LINEAR = {n: float(n) for n in range(1, 50)}


# --------------------------------------------------------------------------- #
# Șansa exactă până la 20 de numere
# --------------------------------------------------------------------------- #
def _random_masks(v, pick, n, seed):
    rng = random.Random(seed)
    return tuple(
        sorted({sum(1 << p for p in rng.sample(range(v), pick)) for _ in range(n)})
    )


@pytest.mark.parametrize("v,pick", [(6, 5), (9, 6), (12, 5), (16, 6)])
def test_numpy_table_equals_the_python_loop_up_to_16(v, pick):
    masks = _random_masks(v, pick, 25, v * 7 + pick)
    assert prob._hit_counts_numpy(v, masks) == prob._hit_counts.__wrapped__(v, masks)


def _brute_ticket_odds(v, tickets, draw_n, max_num):
    """P(cel puțin t pe o variantă), prin enumerarea submulțimilor pool-ului."""
    sets = [frozenset(t) for t in tickets]
    total = comb(max_num, draw_n)
    out = {t: 0 for t in range(1, draw_n + 1)}
    for k in range(0, min(v, draw_n) + 1):
        weight = comb(max_num - v, draw_n - k)
        for sub in combinations(range(v), k):
            best = max(len(s.intersection(sub)) for s in sets)
            for t in range(1, best + 1):
                out[t] += weight
    return {t: c / total for t, c in out.items()}


@pytest.mark.parametrize("v,pick,draw_n,max_num", [(17, 6, 6, 49), (18, 5, 6, 40), (20, 5, 5, 45)])
def test_exact_odds_over_16_match_brute_force(v, pick, draw_n, max_num):
    rng = random.Random(v)
    tickets = [rng.sample(range(v), pick) for _ in range(12)]
    got = prob.wheel_hit_probabilities(
        list(range(1, v + 1)), [[p + 1 for p in t] for t in tickets], draw_n, max_num
    )
    want = _brute_ticket_odds(v, tickets, draw_n, max_num)
    for t in range(1, draw_n + 1):
        assert got["ticket"][t] == pytest.approx(want[t], rel=1e-12, abs=1e-15)


def test_exact_odds_stop_at_20_numbers():
    with pytest.raises(ValueError):
        prob.wheel_hit_probabilities(list(range(1, 22)), [[1, 2, 3, 4, 5]], 5, 45)


# --------------------------------------------------------------------------- #
# Designurile livrate și certificarea
# --------------------------------------------------------------------------- #
def _base_cover(pick, draw_n, max_num, scores=None):
    base = list(range(1, 17))
    scores = scores or {n: float(30 - n) for n in base}
    wheel, cov = generate_wheel(
        "lajolla", base, pick, 4, 0, {n: scores[n] for n in base}, None, draw_n, max_num
    )
    assert cov == 100.0
    return wheel


@pytest.mark.parametrize("game", sorted(GAMES))
@pytest.mark.parametrize("v", xp.EXTENDED_SIZES)
def test_every_delivered_design_certifies(game, v):
    pick, draw_n, max_num, budget = GAMES[game]
    assert xp.extended_budget(pick) == budget
    pool = list(range(1, v + 1))
    scores = {n: float(30 - n) for n in pool}
    incumbent = _base_cover(pick, draw_n, max_num, scores)
    assert len(incumbent) == budget
    candidate, audit = xp.apply(pool, scores, incumbent, v, pick, draw_n, max_num)
    assert candidate is not None, audit.get("reason")
    assert audit["applied"] and audit["variants"] == budget
    assert len({tuple(t) for t in candidate}) == budget
    assert {n for t in candidate for n in t} == set(pool)
    assert audit["base_pool"] == list(range(1, 17))
    top = min(pick, draw_n)
    for t in range(3, top + 1):
        c = audit["chances"][str(t)]
        assert c["extended"] >= c["base"] - 1e-15
    assert audit["chances"]["4"]["extended"] > audit["chances"]["4"]["base"]
    groups = audit["groups4"]
    assert groups["base"] == comb(16, 4)
    assert groups["base"] <= groups["extended"] < groups["pool_total"] == comb(v, 4)
    assert audit["guarantee3_complete"] is True


def test_published_gains_at_4plus():
    """Cifrele din sidebar, raport și AGENTS.md."""
    want = {
        ("6/49", 17): 0.08499139,
        ("6/49", 18): 0.09320038,
        ("5/40", 17): 0.17219582,
        ("5/40", 18): 0.17551154,
        ("joker", 17): 0.05094622,
        ("joker", 18): 0.05165258,
    }
    for (game, v), p4 in want.items():
        pick, draw_n, max_num, _ = GAMES[game]
        pool = list(range(1, v + 1))
        scores = {n: float(30 - n) for n in pool}
        _cand, audit = xp.apply(
            pool, scores, _base_cover(pick, draw_n, max_num, scores), v, pick, draw_n, max_num
        )
        assert audit["chances"]["4"]["extended"] == pytest.approx(p4, abs=5e-9)


def test_certification_rejects_with_a_reason():
    pick, draw_n, max_num, _ = GAMES["6/49"]
    incumbent = _base_cover(pick, draw_n, max_num)
    blocks, _sig = xp.load_design(18, pick, draw_n, max_num)
    candidate = [list(b) for b in blocks]

    why, _ = xp.certify(incumbent, candidate[:-1] + [candidate[0]], pick, draw_n, max_num)
    assert why == "designul are variante repetate"
    why, _ = xp.certify(incumbent[:-1], candidate, pick, draw_n, max_num)
    assert why.startswith("varianta de bază pe 16 numere are 151 variante, nu 152")
    # Baza pe alte 16 numere decât cele jucate de design.
    shifted = [[n + 30 for n in t] for t in incumbent]
    why, _ = xp.certify(shifted, candidate, pick, draw_n, max_num)
    assert why == "cele 16 numere de bază nu sunt toate în pool-ul extins"
    # Același design față de el însuși: niciun câștig strict.
    why, _ = xp.certify(candidate, candidate, pick, draw_n, max_num)
    assert why == "niciun câștig strict"
    # Ultima variantă strânsă pe prima: 4 numere deja împreună, deci mai puține
    # grupe de 4 noi și 4+ mai mic (5/40, aceleași 20 de numere jucate).
    spread = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15], [16, 17, 18, 19, 20]]
    why, _ = xp.certify(spread + [[1, 6, 11, 16, 2]], spread + [[1, 2, 3, 4, 6]], 5, 6, 40)
    assert why == "șansa ar scădea la un prag"


def test_apply_without_option_or_with_a_narrow_pool_keeps_the_base():
    pick, draw_n, max_num, _ = GAMES["6/49"]
    incumbent = _base_cover(pick, draw_n, max_num)
    cand, audit = xp.apply(list(range(1, 19)), {}, incumbent, 0, pick, draw_n, max_num)
    assert cand is None and audit["reason"] == "mărime nelivrată"
    cand, audit = xp.apply(list(range(1, 17)), {}, incumbent, 18, pick, draw_n, max_num)
    assert cand is None and audit["reason"].startswith("pool-ul are 16 numere, nu 18")
    assert audit["base_pool"] == list(range(1, 17))


def test_a_corrupt_design_file_is_skipped(tmp_path, monkeypatch):
    pick, draw_n, max_num, budget = GAMES["6/49"]
    name = xp.design_name(18, pick, draw_n, max_num, budget)
    text = (xp.DESIGN_DIR / name).read_text(encoding="utf-8")
    (tmp_path / name).write_text(text.replace("\n1 ", "\n1 1 ", 1), encoding="utf-8")
    monkeypatch.setattr(xp, "DESIGN_DIR", tmp_path)
    assert xp.load_design(18, pick, draw_n, max_num) is None
    assert xp.design_signature(18, pick, draw_n, max_num) is None
    (tmp_path / name).write_text(text, encoding="utf-8")
    assert xp.load_design(18, pick, draw_n, max_num) is not None


@pytest.mark.parametrize("value,expected", [(None, 0), (0, 0), ("18", 18), (17, 17), (16, 0), (19, 0), ("x", 0)])
def test_normalize_size(value, expected):
    assert xp.normalize_size(value) == expected


# --------------------------------------------------------------------------- #
# Motorul
# --------------------------------------------------------------------------- #
def _engine(game="6/49", csv="_ISTORIC/loto_6_49.csv"):
    engine = LotoEngine(game)
    engine.load_data(csv)
    engine._build_draw_matrix()
    return engine


def _linear(monkeypatch):
    monkeypatch.setattr(LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: dict(LINEAR))


def test_engine_adopts_the_extended_pool(monkeypatch):
    _linear(monkeypatch)
    engine = _engine()
    lines, *_, context, audit = engine.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=18, track_pool_variation=False
    )
    assert engine.hard_core == list(range(32, 50))
    assert len(lines) == 152 and len({tuple(v[:6]) for v in lines}) == 152
    assert {n for v in lines for n in v[:6]} == set(range(32, 50))
    ext = audit["extended_pool"]
    assert ext["applied"] and ext["base_pool"] == list(range(34, 50))
    assert audit["pipeline_stages"]["5_extended_pool"] == list(range(32, 50))
    assert 0 < context["coverage_pct"] < 100
    p = prob.wheel_hit_probabilities(engine.hard_core, [v[:6] for v in lines], 6, 49)
    assert p["ticket"][4] == pytest.approx(0.09320038, abs=5e-9)


def test_engine_default_is_unchanged(monkeypatch):
    _linear(monkeypatch)
    plain = _engine()
    a = plain.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, track_pool_variation=False
    )
    off = _engine()
    b = off.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=0, track_pool_variation=False
    )
    assert a[0] == b[0] and plain.hard_core == off.hard_core == list(range(34, 50))
    assert "extended_pool" not in a[-1] and "extended_pool" not in b[-1]
    assert a[-2]["coverage_pct"] == 100.0


def test_engine_falls_back_on_a_narrow_base(monkeypatch):
    """Baza restrânsă la exact 16 numere: nu încap 18, se joacă pool-ul de 16."""
    _linear(monkeypatch)
    engine = _engine()
    lines, *_, context, audit = engine.run_institutional_pipeline(
        pool_size=16,
        guarantee=4,
        max_variants=0,
        extended_pool=18,
        restrict_base_min=20,
        restrict_base_max=35,
        track_pool_variation=False,
    )
    assert engine.hard_core == list(range(20, 36)) and len(lines) == 152
    assert context["coverage_pct"] == 100.0
    ext = audit["extended_pool"]
    assert not ext["applied"] and "nu 18" in ext["reason"]
    assert "5_extended_pool" not in audit["pipeline_stages"]


def test_engine_falls_back_when_the_base_is_not_the_complete_cover(monkeypatch):
    _linear(monkeypatch)
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    engine = _engine()
    lines, *_, audit = engine.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=18, track_pool_variation=False
    )
    ext = audit["extended_pool"]
    assert not ext["applied"] and len(engine.hard_core) == 16
    assert ext["reason"].startswith("varianta de bază pe 16 numere are")
    assert len(lines) != 152


def test_engine_refuses_a_base_that_is_not_pool_16_g4():
    engine = _engine()
    out = engine._apply_extended_pool(
        18, [[1, 2, 3, 4, 5, 6]], 50.0, {}, {}, set(), 0,
        guarantee=3, max_variants=0, wheel_condition=3,
    )
    assert out == ([[1, 2, 3, 4, 5, 6]], 50.0)
    assert engine.audit["extended_pool"]["reason"].startswith("baza nu e coverul complet")


def test_joker_keeps_the_urn2_number_on_every_variant(monkeypatch):
    monkeypatch.setattr(
        LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: {n: float(n) for n in range(1, 46)}
    )
    engine = _engine("joker", "_ISTORIC/joker.csv")
    lines, *_, audit = engine.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=17, track_pool_variation=False
    )
    assert audit["extended_pool"]["applied"] and len(lines) == 404
    assert len(engine.hard_core) == 17
    # Numărul Urnei 2 (aici scorul liniar și pe ea) rămâne pe fiecare variantă.
    assert all(len(v) == 6 and set(v[:5]) <= set(engine.hard_core) for v in lines)
    assert {v[5] for v in lines} == set(engine.hard_core_joker)


# --------------------------------------------------------------------------- #
# Worker, coadă, UI, WF
# --------------------------------------------------------------------------- #
def test_worker_normalization_forces_the_base():
    on = worker._normalize_task(
        {"pool_size": 10, "guarantee": 3, "max_variants": 30, "wheel_condition": 5, "extended_pool": 18},
        draw_n=6,
    )
    assert (on["pool_size"], on["guarantee"], on["max_variants"], on["wheel_condition"]) == (16, 4, 0, 4)
    assert on["extended_pool"] == 18
    off = worker._normalize_task({"pool_size": 10, "guarantee": 3, "max_variants": 30}, draw_n=6)
    assert (off["pool_size"], off["guarantee"], off["max_variants"]) == (10, 3, 30)
    assert off["extended_pool"] == 0
    assert worker._normalize_task({"extended_pool": 19}, draw_n=6)["extended_pool"] == 0


def _app_with_dataset(monkeypatch, rows=60):
    import app_nicegui as app

    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(rows).reset_index(drop=True)
    monkeypatch.setattr(app, "STATE", {**app.STATE, "datasets": [("loto_6_49.csv", df)]})
    return app


def test_config_json_is_unchanged_when_off(monkeypatch):
    app = _app_with_dataset(monkeypatch)
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 0)
    off = json.loads(app._build_config_json())
    assert "extended_pool" not in off["datasets"][0]["tasks"][0]
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 18)
    on = json.loads(app._build_config_json())
    assert on["datasets"][0]["tasks"][0]["extended_pool"] == 18
    assert on["input_hash"] != off["input_hash"]
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 19)  # nelivrat
    assert json.loads(app._build_config_json())["input_hash"] == off["input_hash"]


def test_wf_options_are_unchanged_when_off():
    import app_nicegui as app

    data = {"guarantee": 4, "wheel_condition": 4, "max_variants": 0, "audit": {}}
    plain = app._wf_generation_options(data)
    assert "extended_pool" not in plain
    assert app._wf_generation_options({**data, "extended_pool": 0}) == plain
    on = app._wf_generation_options(
        {**data, "guarantee": 3, "max_variants": 30, "extended_pool": 18}
    )
    assert on["extended_pool"] == 18
    assert (on["guarantee"], on["wheel_condition"], on["max_variants"]) == (4, 4, 0)
    # Rezultat fără ecou, cu auditul cererii: aceeași opțiune.
    audit_only = {**data, "audit": {"extended_pool": {"requested": 17, "applied": False}}}
    assert app._wf_generation_options(audit_only)["extended_pool"] == 17


def test_wf_key_carries_the_design_only_when_on():
    base = dict(guarantee=4, wheel_condition=4, max_variants=0)
    off = wf._decision_sig("6/49", 16, 100, 0, 0.5, **base)
    assert wf._decision_sig("6/49", 16, 100, 0, 0.5, **base, extended_pool=0) == off
    on17 = wf._decision_sig("6/49", 16, 100, 0, 0.5, **base, extended_pool=17)
    on18 = wf._decision_sig("6/49", 16, 100, 0, 0.5, **base, extended_pool=18)
    assert len({off, on17, on18}) == 3
    assert xp.design_signature(18, 6, 6, 49)
    assert wf._extended_sig(0, "6/49") == ""
    assert wf._extended_sig(18, "5/40") == f"|xp1:18:{xp.design_signature(18, 5, 6, 40)}"


def test_submit_worker_result_and_wf_options_carry_the_option(tmp_path, monkeypatch):
    """AGENTS.md §4.4: o cheie nouă în config_json cere un test E2E."""
    _linear(monkeypatch)
    app = _app_with_dataset(monkeypatch, rows=300)
    for key, value in {
        "extended_pool_val": 18,
        "pool_size_val": 12,
        "guarantee_val": 3,
        "max_variants_val": 30,
        "max_consecutive_run_enabled_val": False,
    }.items():
        monkeypatch.setitem(app.SETTINGS, key, value)
    db = str(tmp_path / "jobs.db")
    jid = jq.submit_job("pipeline", app._build_config_json(), db_path=db)
    job = jq.fetch_pending_job(db_path=db, worker_token=worker.WORKER_TOKEN)
    assert job and job["id"] == jid
    monkeypatch.setattr(worker, "is_job_cancelled", functools.partial(jq.is_job_cancelled, db_path=db))
    monkeypatch.setattr(worker, "fail_job", functools.partial(jq.fail_job, db_path=db))
    monkeypatch.setattr(
        worker, "update_job_progress", functools.partial(jq.update_job_progress, db_path=db)
    )
    result_json = worker._run_pipeline_job(job)
    assert jq.complete_job(jid, result_json, db_path=db, worker_token=worker.WORKER_TOKEN)
    rb, _ = decode_queue_result(jq.get_job_status(jid, db_path=db)["result_json"])
    ((_fname, outs),) = rb
    data = outs["6/49"]
    assert data["extended_pool"] == 18
    assert data["pool_size"] == 18 and data["pool_size_requested"] == 16
    assert data["guarantee"] == 4 and len(data["variants"]) == 152
    ext = data["audit"]["extended_pool"]
    assert ext["applied"] and set(ext["base_pool"]) < set(data["hard_core"])
    opts = app._wf_generation_options(data)
    assert opts["extended_pool"] == 18 and opts["guarantee"] == 4

    # Textele: panou, raport, mail.
    from scripts.analysis.audit_output import capture_ui

    with capture_ui() as ui:
        app._render_pool_body("loto_6_49.csv", "6/49", data)
    text = ui.text()
    assert "pool extins, baza 16" in text and "Garanția 4 parțială" in text
    lines = app._extended_pool_lines(data)
    assert lines[0].startswith("Pool extins pentru 4+ (alegerea ta): 18 numere pe 152 variante")
    assert any("Garanția 3 rămâne completă" in line for line in lines)
    assert any("71.6% din grupele de 4" in line for line in lines)
    assert "Fără avantaj predictiv" in lines[-1]

    # Bilet complet: cele 16 numere ale bazei.
    ticket = build_full_ticket("6/49", data)
    assert not ticket.get("error")
    assert set(ticket["pool"]) <= set(ext["base_pool"])
    assert "Pool extins: biletul folosește cele 16 numere ale bazei" in ticket["note"]


def test_unapplied_option_names_the_reason():
    import app_nicegui as app

    data = {
        "hard_core": list(range(20, 36)),
        "variants": [],
        "audit": {
            "extended_pool": {
                "requested": 18,
                "applied": False,
                "reason": "pool-ul are 16 numere, nu 18 (bază restrânsă prea îngustă)",
                "base_pool": list(range(20, 36)),
            }
        },
    }
    (line,) = app._extended_pool_lines(data)
    assert "Pool extins 18 cerut, neaplicat" in line and "pool-ul de 16" in line
    assert app._extended_pool_lines({"audit": {}}) == []


def test_walk_forward_validates_the_extended_pool(tmp_path, monkeypatch):
    df = pd.read_csv("_ISTORIC/loto_6_49.csv").tail(30).reset_index(drop=True)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 1)
    _linear(monkeypatch)
    flat, meta = wf.run_honest_walk_forward(
        df,
        "6/49",
        pool_size=12,
        guarantee=3,
        max_variants=20,
        backtest_depth_percent=10,
        use_cache=False,
        extended_pool=18,
    )
    steps = {r.draw_index for r in flat}
    assert len(steps) == 3
    assert meta["extended_pool"] == 18 and meta["pool_size"] == 16
    assert meta["wheel_guarantee"] == 4 and meta["max_variants"] == 0
    off = wf._decision_sig("6/49", 16, 100, 0, 0.5, guarantee=4, wheel_condition=4)
    assert meta["decision_sig"] != off
    summary = meta["extended_steps"]
    assert summary["applied"] == 3 and summary["fallback"] == 0
    for di in steps:
        rows = [r for r in flat if r.draw_index == di]
        assert len(rows) == 152
        ctx = rows[0].ticket_context
        assert len(ctx["hard_core"]) == 18
    cov = wf.wheel_coverage_summary(flat)
    assert cov["extended"] == 3 and cov.get("below_100", 0) == 0

    import app_nicegui as app
    from scripts.analysis.audit_output import capture_ui

    with capture_ui() as ui:
        app._render_hits_4plus(flat, "6/49", meta, 18)
    text = ui.text()
    assert "Wheel evaluat în WF: pool extins 18" in text
    assert "nu are reluare LR" in text


def test_leaderboards_follow_the_pool_16_decision(monkeypatch):
    import app_nicegui as app

    monkeypatch.setitem(app.SETTINGS, "pool_size_val", 11)
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 0)
    assert app._leaderboard_pool() == 11
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 18)
    assert app._leaderboard_pool() == 16
    assert app._extended_sizes() == (17, 18)


def test_lr_schemes_stay_on_pools_of_at_most_16():
    import app_nicegui as app

    assert app._lr_schemes_for("6/49", {"hard_core": list(range(1, 19))}) == []


# --------------------------------------------------------------------------- #
# Revizia adversarială 2026-10-22
# --------------------------------------------------------------------------- #
def test_lr_guard_matters_at_5_40():
    """5/40 are scheme LR de 17-19 numere: garda pe 16 se vede numai acolo."""
    import app_nicegui as app
    import ui_hits
    from covering.lr_schemes import MAX_POOL, lr_schemes

    assert any(s.n > MAX_POOL for s in lr_schemes("5/40"))
    assert app._lr_schemes_for("5/40", {"hard_core": list(range(1, 18))}) == []
    assert app._lr_schemes_for("5/40", {"hard_core": list(range(1, 19))}) == []
    assert app._lr_schemes_for("5/40", {"hard_core": list(range(1, 17))})
    per = {0: {"pool_numbers": list(range(1, 18)), "drawn": [1, 5, 9, 20, 30, 40],
               "pool": 3, "best_ticket": 3, "label": "d0"}}
    assert ui_hits._lr_replay(per, "5/40") == []


def test_cost_panel_does_not_deny_lr_schemes_over_16():
    import app_nicegui as app
    from scripts.analysis.audit_output import capture_ui

    data = {"hard_core": list(range(1, 18)), "pool_size": 17, "variants": [], "audit": {}}
    with capture_ui() as ui:
        app._render_cost("5/40", data)
    text = ui.text()
    assert "fără schemă redusă oficială" not in text
    assert "Loteria Română are Cod" in text and "cel mult 16 numere" in text


def test_lr_replay_note_names_the_app_limit():
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    with capture_ui() as ui:
        ui_hits._render_lr_replay({}, "5/40", 18)
    text = ui.text()
    assert "Aplicația reia schemele reduse LR numai pe pool-uri de cel mult 16" in text
    assert "Loteria Română are Cod" in text


def test_adopted_audit_drops_the_stale_block_warning(monkeypatch):
    scores = dict(LINEAR)
    scores.update({n: 0.0 for n in range(1, 34)})
    scores[5] = 33.5
    monkeypatch.setattr(LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: dict(scores))
    plain = _engine()
    plain.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, track_pool_variation=False
    )
    assert plain.audit.get("pool_consecutive_warning")
    engine = _engine()
    engine.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=17, track_pool_variation=False
    )
    assert engine.audit["extended_pool"]["applied"]
    assert engine.hard_core == [5] + list(range(34, 50))
    assert engine.audit["pool_is_consecutive_block"] is False
    assert "pool_consecutive_warning" not in engine.audit


def test_consecutive_diagnostic_uses_the_raw_top_v(monkeypatch):
    import app_nicegui as app

    _linear(monkeypatch)
    engine = _engine()
    lines, *_, audit = engine.run_institutional_pipeline(
        pool_size=16, guarantee=4, max_variants=0, extended_pool=18,
        max_consecutive_run=2, track_pool_variation=False,
    )
    ext = audit["extended_pool"]
    assert ext["applied"] and len(ext["raw_top"]) == 18
    assert ext["raw_top"] == list(range(32, 50))
    text = app._consecutive_pool_diagnostic({"hard_core": engine.hard_core, "audit": audit})
    assert "În selecția inițială după scor: 18 numere." in text


def _fake_flat(sizes, applied, reason="pool-ul are 16 numere, nu 18 (bază restrânsă prea îngustă)"):
    from types import SimpleNamespace

    flat = []
    for di, (n, ok) in enumerate(zip(sizes, applied)):
        xpa = {"requested": 18, "applied": ok}
        if not ok:
            xpa["reason"] = reason
        ctx = {"hard_core": list(range(1, n + 1)), "audit": {"extended_pool": xpa}}
        flat.append(SimpleNamespace(draw_index=di, ticket_context=ctx, wheel_coverage=100.0))
    return flat


def test_wf_summary_counts_fallback_pool_sizes():
    s = wf.extended_steps_summary(_fake_flat([18, 12, 12], [True, False, False]))
    assert s["applied"] == 1 and s["fallback"] == 2
    assert s["fallback_pool_sizes"] == {12: 2} and s["pool_sizes"] == {18: 1, 12: 2}


def test_wf_label_says_partial_only_for_adopted_steps():
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    flat = _fake_flat([12, 12], [False, False])
    meta = {"extended_pool": 18, "wheel_guarantee": 4, "wheel_condition": 4, "max_variants": 0,
            "extended_steps": wf.extended_steps_summary(flat)}
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "6/49", meta, 12)
    text = ui.text()
    assert "parțială" not in text
    assert "Pool extins 18 cerut, neaplicat la niciun pas" in text
    assert "pool-ul de 12" in text

    mixed = _fake_flat([18, 12], [True, False])
    meta["extended_steps"] = wf.extended_steps_summary(mixed)
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(mixed, "6/49", meta, 18)
    text = ui.text()
    assert "Garanția 4 e parțială la cele" in text and "pool-ul de 12 cu garanția 4 completă" in text


def test_mixed_null_matches_the_table_reference():
    import ui_hits
    from scipy.stats import binom

    sizes = [16] * 60 + [18] * 40
    r16 = ui_hits._random_rate_hypergeo("6/49", 16, 4)
    r18 = ui_hits._random_rate_hypergeo("6/49", 18, 4)
    mean, p = ui_hits._mixed_null("6/49", 4, sizes, 10)
    assert mean == pytest.approx((60 * r16 + 40 * r18) / 100)
    # Sumă de două binomiale, calculată direct.
    want = sum(
        binom.pmf(a, 60, r16) * binom.pmf(b, 40, r18)
        for a in range(61) for b in range(41) if a + b >= 10
    )
    assert p == pytest.approx(want, rel=1e-9)
    assert ui_hits._mixed_null("6/49", 4, [], 1) == (None, None)


def test_option_reaches_only_games_with_a_design(monkeypatch):
    """6/45 și 5/50 n-au design: cu opțiunea pornită își păstrează pool-ul."""
    import app_nicegui as app
    from loto_enterprise.core import lotteries as L

    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 18)
    for gid in ("6/49", "5/40", "joker"):
        assert app._extended_for_game(L.GAMES_BY_ID[gid]) == 18
    no_design = [g for g in L.GAMES if g.geometry in ("6/45", "5/50")]
    assert no_design
    assert all(app._extended_for_game(g) == 0 for g in no_design)
    monkeypatch.setitem(app.SETTINGS, "extended_pool_val", 0)
    assert app._extended_for_game(L.GAMES_BY_ID["6/49"]) == 0


def test_foreign_task_without_design_keeps_its_settings(monkeypatch):
    import app_nicegui as app
    import ui_runtime
    from loto_enterprise.core import lotteries as L

    monkeypatch.setattr(app, "SETTINGS", dict(app.SETTINGS))
    monkeypatch.setattr(ui_runtime, "SETTINGS", app.SETTINGS)
    state = {**app.STATE, "datasets": [], "dataset_game": {}}
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(ui_runtime, "STATE", state)
    app._sync_ui_namespace()
    try:
        app.SETTINGS.update(country_val="AT", games_val=[], restrict_base_enabled_val=False)
        app._load_registry_histories([L.GAMES_BY_ID["at_lotto"]])
        off = json.loads(app._build_config_json())
        app.SETTINGS["extended_pool_val"] = 18
        on = json.loads(app._build_config_json())
        assert "extended_pool" not in on["datasets"][0]["tasks"][0]
        assert on["datasets"][0]["tasks"][0] == off["datasets"][0]["tasks"][0]
    finally:
        app._sync_ui_namespace()
