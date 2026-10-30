"""Loto 5/40 pe 17-19 numere și pool-ul pe joc (2026-10-22).

Utilizatorul joacă numai scheme reduse LR. Numai 5/40 are coduri peste 16
numere: Cod 45 și 46 pe 17, Cod 47 pe 18, Cod 48 pe 19. Fiecare joc românesc are
acum câmpul lui de pool, cu maximul jocului (19 la 5/40, 16 în rest) și implicit
cel mai mare cod LR (16 / 19 / 15). Cod 46-48 sunt covere complete de 4 și intră
ca designuri locale; Cod 45 intră prin etapa LR la plafonul 488. Pool-urile de
cel mult 16, 6/49 și Joker rămân neschimbate.
"""

from __future__ import annotations

import functools
import json
import time

import pandas as pd
import pytest

import job_queue as jq
import worker
from covering import lr_schemes as lr
from covering.designs import complete_design_size
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import lotteries as L
from loto_enterprise.core import walk_forward_adapter as wf
from loto_enterprise.core.full_ticket import build_full_ticket
from ui_shared import decode_queue_result

CODES = {17: 46, 18: 47, 19: 48}
SIZES = {17: 562, 18: 732, 19: 930}


def _scheme(code):
    return next(s for s in lr.lr_schemes("5/40") if s.code == code)


def _scores(pool):
    return {n: float((n * 7) % 11) + n / 100 for n in pool}


# --------------------------------------------------------------------------- #
# Designurile și motorul
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("v", sorted(CODES))
def test_local_designs_are_the_complete_lr_codes(v):
    from pathlib import Path

    rows = Path(f"covering_designs/C_{v}_5_4.txt").read_text(encoding="utf-8").split("\n")
    blocks = {tuple(sorted(int(x) for x in r.split())) for r in rows if r.strip()}
    scheme = _scheme(CODES[v])
    assert blocks == {tuple(sorted(b)) for b in scheme.variants}
    assert complete_design_size(v, 5, 4) == SIZES[v] == scheme.size
    # Cover complet de 4: când ies 4 din pool, 4 e sigur pe o variantă.
    assert lr.scheme_guarantees(scheme)[4][4] == 1.0


@pytest.mark.parametrize("v", sorted(CODES))
def test_engine_cover_at_17_19_is_fast_complete_and_as_good_as_the_code(v):
    pool = list(range(3, v + 3))
    start = time.perf_counter()
    wheel, cov = generate_wheel("lajolla", pool, 5, 4, 0, _scores(pool), None, 6, 40)
    assert time.perf_counter() - start < 5  # înainte, ILP-ul consuma 15 s
    assert cov == 100.0 and len(wheel) <= SIZES[v]
    odds = wheel_hit_probabilities(pool, wheel, 6, 40)["ticket"]
    assert odds[4] == pytest.approx(lr.scheme_odds(_scheme(CODES[v]))[4], abs=1e-12)


def test_ilp_is_skipped_above_16_and_kept_up_to_16(monkeypatch):
    from covering import ilp

    ilp._ILP_COVER_CACHE.clear()
    start = time.perf_counter()
    assert ilp._ilp_cover_positions(17, 5, 3, 15.0) is None
    assert time.perf_counter() - start < 1
    assert ilp._ilp_cover_positions(8, 5, 3, 15.0) is not None
    ilp._ILP_COVER_CACHE.clear()


def test_guarantee_5_with_a_small_cap_uses_lower_guarantees_at_19():
    """Portofoliul garanțiilor mai mici merge până la 19: la plafonul 50, 3+ pe o
    variantă 42,09% în loc de 18,02% (primele 50 de combinații ale sistemului complet)."""
    pool = list(range(3, 22))
    wheel, _ = generate_wheel("hitcover", pool, 5, 5, 50, _scores(pool), None, 6, 40)
    assert len(wheel) == 50
    assert wheel_hit_probabilities(pool, wheel, 6, 40)["ticket"][3] > 0.40


def test_lottery_caps_and_defaults_follow_the_largest_lr_code():
    import ui_runtime

    caps = {g.game_id: g.max_pool for g in L.GAMES}
    assert caps["5/40"] == 40 and caps["6/49"] == 49 and caps["joker"] == 45
    assert all(v == 16 for k, v in caps.items() if not L.GAMES_BY_ID[k].is_romanian)
    for game, key in ui_runtime._POOL_KEYS.items():
        largest = max(s.n for s in lr.lr_schemes(game))
        want = min(largest, L.GAMES_BY_ID[game].max_pool)
        assert ui_runtime.DEFAULTS[key] == ui_runtime._POOL_DEFAULTS[game] == want
    assert ui_runtime._POOL_DEFAULTS == {"6/49": 16, "5/40": 19, "joker": 15}


# --------------------------------------------------------------------------- #
# Setări, config, Auto-Pilot, clasament
# --------------------------------------------------------------------------- #
def test_pool_setting_per_game_is_clamped_and_survives_a_restart(tmp_path, monkeypatch):
    import ui_runtime

    state = tmp_path / ".ui_state.json"
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)
    keys = ui_runtime._POOL_KEYS
    state.write_text(
        json.dumps({keys["5/40"]: 25, keys["6/49"]: 18, keys["joker"]: "x"}), "utf-8"
    )
    for key in keys.values():
        monkeypatch.setitem(ui_runtime.SETTINGS, key, None)
    ui_runtime._load_settings()
    assert ui_runtime.SETTINGS[keys["5/40"]] == 25
    assert ui_runtime.SETTINGS[keys["6/49"]] == 18
    assert ui_runtime.SETTINGS[keys["joker"]] == 15
    state.write_text(json.dumps({keys["5/40"]: 18}), "utf-8")
    ui_runtime._load_settings()
    assert ui_runtime.SETTINGS[keys["5/40"]] == 18
    assert set(keys.values()) <= set(ui_runtime.UI_PERSIST_KEYS)


def test_pool_size_for_reads_the_games_field(monkeypatch):
    import app_nicegui as app

    keys = app._POOL_KEYS
    monkeypatch.setitem(app.SETTINGS, keys["5/40"], 18)
    monkeypatch.setitem(app.SETTINGS, keys["6/49"], 12)
    monkeypatch.setitem(app.SETTINGS, keys["joker"], 99)
    monkeypatch.setitem(app.SETTINGS, "pool_size_val", 11)
    for key, want in (
        ("5/40", 18), ("loto_5_40", 18), ("loto_5_40.csv", 18),
        ("6/49", 12), ("loto_6_49", 12),
        ("joker", 45), ("joker_urna1", 45),
        ("at_lotto", 11), ("de_lotto", 11),
    ):
        assert app._pool_size_for(key) == want, key


def _romanian_datasets(rows=60):
    return [
        (name, pd.read_csv(f"_ISTORIC/{name}").tail(rows).reset_index(drop=True))
        for name in ("loto_6_49.csv", "loto_5_40.csv", "joker.csv")
    ]


def test_config_sends_each_game_its_own_pool(monkeypatch):
    import app_nicegui as app

    monkeypatch.setattr(app, "STATE", {**app.STATE, "datasets": _romanian_datasets()})
    for key, value in app.DEFAULTS.items():
        if key in app._POOL_KEYS.values():
            monkeypatch.setitem(app.SETTINGS, key, value)
    monkeypatch.setitem(app.SETTINGS, "games_val", [])
    cfg = json.loads(app._build_config_json())
    pools = {ds["tasks"][0]["game_label"]: ds["tasks"][0]["pool_size"] for ds in cfg["datasets"]}
    assert pools == {"6/49": 16, "5/40": 19, "joker": 15}
    monkeypatch.setitem(app.SETTINGS, app._POOL_KEYS["5/40"], 17)
    again = json.loads(app._build_config_json())
    assert again["input_hash"] != cfg["input_hash"]
    pools = {ds["tasks"][0]["game_label"]: ds["tasks"][0]["pool_size"] for ds in again["datasets"]}
    assert pools == {"6/49": 16, "5/40": 17, "joker": 15}


def test_autopilot_asks_each_game_for_its_own_pool(monkeypatch):
    import app_nicegui as app
    import loto_enterprise.core.method_selector as ms

    asked = []

    def _rec(key, pool, **_kw):
        asked.append((key, pool))
        return {"scorer": f"m_{key}", "multiplicity": {"proven": False}, "sim_depth_pct": 30}

    monkeypatch.setattr(ms, "recommend_optimal_config", _rec)
    monkeypatch.setattr(app, "_selected_games", lambda: list(L.games_for_country(L.RO)))
    monkeypatch.setattr(app, "submit_generation", lambda **_kw: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    monkeypatch.setitem(
        app.STATE, "datasets", [("loto_6_49.csv", None), ("loto_5_40.csv", None), ("joker.csv", None)]
    )
    for game, size in (("6/49", 16), ("5/40", 19), ("joker", 15)):
        monkeypatch.setitem(app.SETTINGS, app._POOL_KEYS[game], size)
    app.apply_autopilot_and_generate()
    assert ("loto_6_49", 16) in asked and ("loto_5_40", 19) in asked
    assert ("joker_urna1", 15) in asked


def test_live_leaderboard_shows_each_games_pool(tmp_path, monkeypatch):
    import ui_bench

    folds = tmp_path / "folds.csv"
    pd.DataFrame({"method": ["frequency"], "game": ["loto_6_49"]}).to_csv(folds, index=False)
    seen = []
    monkeypatch.setattr(ui_bench, "_country_folds_path", lambda *a, **k: folds)
    monkeypatch.setattr(
        ui_bench, "_render_bench_leaderboard_slice", lambda df, g, p, *a, **k: seen.append((g, p))
    )
    for game, size in (("6/49", 16), ("5/40", 18), ("joker", 15)):
        monkeypatch.setitem(ui_bench.SETTINGS, ui_bench._POOL_KEYS[game], size)
    from scripts.analysis.audit_output import capture_ui

    with capture_ui():
        ui_bench._render_bench_live_leaderboard(progress=0.5)
    assert seen == [("loto_6_49", 16), ("joker_urna1", 15), ("loto_5_40", 18)]


def test_base_interval_memo_holds_every_game_and_pool():
    import app_nicegui as app

    assert app._BASE_TABLE_MEMO_MAX == 2 * sum(
        L.GAMES_BY_ID[g].max_pool - 5 for g in ("6/49", "5/40", "joker")
    )


# --------------------------------------------------------------------------- #
# Bilet complet
# --------------------------------------------------------------------------- #
def test_full_ticket_on_19_numbers_uses_the_16_best_ranked():
    pool = list(range(3, 22))
    ranking = {n: float(100 - i) for i, n in enumerate(sorted(pool, key=lambda n: -n))}
    data = {"hard_core": pool, "guarantee": 4, "audit": {"timesfm_predictions": ranking}}
    t = build_full_ticket("5/40", data, 2)
    assert not t.get("error")
    assert t["pool"] == list(range(6, 22))  # cele mai bine clasate 16 (scor = numărul)
    assert "cele mai bine clasate 16 din 19" in t["note"] and "3, 4, 5" in t["note"]
    assert len(t["variants"]) == 8


# --------------------------------------------------------------------------- #
# Cap-coadă: sidebar → coadă → worker → rezultat → panou, WF, Istoric hits
# --------------------------------------------------------------------------- #
def _linear(monkeypatch):
    monkeypatch.setattr(
        LotoEngine, "_get_timesfm_scores", lambda self, *a, **k: {n: float(n) for n in range(1, 50)}
    )


def test_submit_worker_result_panel_and_wf_options_at_19(tmp_path, monkeypatch):
    """AGENTS.md §4.4: pool-ul pe joc schimbă config_json, deci test E2E."""
    import app_nicegui as app
    from scripts.analysis.audit_output import capture_ui

    _linear(monkeypatch)
    df = pd.read_csv("_ISTORIC/loto_5_40.csv").tail(300).reset_index(drop=True)
    monkeypatch.setattr(app, "STATE", {**app.STATE, "datasets": [("loto_5_40.csv", df)]})
    monkeypatch.setitem(app.SETTINGS, "games_val", [])
    monkeypatch.setitem(app.SETTINGS, app._POOL_KEYS["5/40"], 19)
    monkeypatch.setitem(app.SETTINGS, "guarantee_val", 4)
    monkeypatch.setitem(app.SETTINGS, "max_variants_val", 0)
    monkeypatch.setitem(app.SETTINGS, "wheel_condition_val", 0)
    monkeypatch.setitem(app.SETTINGS, "max_consecutive_run_enabled_val", False)
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
    data = outs["5/40"]
    assert data["pool_size"] == data["pool_size_requested"] == 19
    assert data["hard_core"] == list(range(22, 41))
    assert len(data["variants"]) == 930 and data["context"]["coverage_pct"] == 100.0
    assert app._wf_run_kwargs("5/40", data)["pool_size"] == 19

    assert [s.code for s in app._lr_schemes_for("5/40", data)] == [48]
    with capture_ui() as ui:
        app._render_cost("5/40", data)
    text = ui.text()
    assert "Cod 48" in text and "930 var." in text
    assert "fără schemă redusă" not in text
    assert any("Cod 48" in line for line in app._lr_scheme_lines("5/40", data))


def test_walk_forward_at_19_replays_cod_48(tmp_path, monkeypatch):
    import app_nicegui as app
    from scripts.analysis.audit_output import capture_ui

    df = pd.read_csv("_ISTORIC/loto_5_40.csv").tail(40).reset_index(drop=True)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 1)
    _linear(monkeypatch)
    flat, meta = wf.run_honest_walk_forward(
        df, "5/40", pool_size=19, guarantee=4, max_variants=0,
        backtest_depth_percent=10, use_cache=False,
    )
    steps = {r.draw_index for r in flat}
    assert len(steps) == 4 and meta["pool_size"] == 19
    for di in steps:
        rows = [r for r in flat if r.draw_index == di]
        assert len(rows) == 930 and len(rows[0].ticket_context["hard_core"]) == 19
    with capture_ui() as ui:
        app._render_hits_4plus(flat, "5/40", meta, 19)
    text = ui.text()
    assert "Cod 48 (930 var.) jucat pe pool-ul de atunci, 4 extrageri" in text
    assert "reluarea cere pool-ul" not in text
    import ui_hits

    per = ui_hits._wf_per_draw_stats(flat, "5/40")
    assert [r["scheme"].code for r in ui_hits._lr_replay(per, "5/40")] == [48]


# --------------------------------------------------------------------------- #
# Revizia adversarială 2026-10-22
# --------------------------------------------------------------------------- #
def _old_lotto_masks(v, pick, g, c):
    """Construcția de dinainte (bloc × țintă), referința pentru măștile noi."""
    import itertools

    targets = [frozenset(t) for t in itertools.combinations(range(v), c)]
    out = []
    for b in itertools.combinations(range(v), pick):
        bs, m = set(b), 0
        for ti, t in enumerate(targets):
            if len(bs & t) >= g:
                m |= 1 << ti
        out.append(m)
    return out


@pytest.mark.parametrize(
    "v,pick,g,c", [(9, 5, 3, 4), (10, 6, 3, 5), (12, 5, 4, 5), (11, 6, 4, 5), (8, 6, 2, 3)]
)
def test_lotto_masks_are_bit_identical_to_the_old_builder(v, pick, g, c):
    import itertools

    from covering import designs

    blocks = list(itertools.combinations(range(v), pick))
    assert designs._lotto_block_masks(v, blocks, g, c) == _old_lotto_masks(v, pick, g, c)


def test_lotto_cover_at_19_numbers_takes_seconds_not_minutes():
    from covering import designs

    designs._LOTTO_COVER_CACHE.pop((19, 5, 4, 5), None)
    start = time.perf_counter()
    cover = designs._lotto_cover_positions(19, 5, 4, 5)
    assert time.perf_counter() - start < 60  # înainte ~3 minute
    assert len(cover) == 376


def test_foreign_interval_table_uses_the_shared_pool_field(monkeypatch):
    import app_nicegui as app
    from scripts.analysis.audit_output import capture_ui

    monkeypatch.setitem(app.SETTINGS, "country_val", "AT")
    monkeypatch.setitem(app.SETTINGS, "pool_size_val", 9)
    monkeypatch.setitem(app.SETTINGS, app._POOL_KEYS["6/49"], 16)
    monkeypatch.setitem(app._BASE_TABLE_OPENED, "value", True)
    with capture_ui() as ui:
        app._render_base_interval_tables.func()
    titles = [str(n["args"][0]) for n in ui.walk() if n["kind"] == "expansion"]
    assert titles and all("pool 9," in t for t in titles), titles


def test_replay_cache_keeps_displayed_lists_over_the_row_cap(monkeypatch):
    import ui_hits

    shown = [object()] * 900_000
    shown_too = [object()] * 900_000
    old = [object()] * 900_000
    older = [object()] * 900_000
    monkeypatch.setitem(ui_hits.STATE, "retro", {"a": shown, "b": shown_too})
    monkeypatch.setattr(ui_hits, "_REPLAY_DONE", {})
    monkeypatch.setattr(ui_hits, "_REPLAY_PENDING", {})
    monkeypatch.setattr(
        "loto_enterprise.core.ticket_replay.replay_full_tickets", lambda *a, **k: {"best": {}}
    )
    keys = []
    for flat in (older, shown, old, shown_too):
        key = (id(flat), len(flat), "5/40", 1, 4)
        keys.append(key)
        ui_hits._REPLAY_PENDING[key] = (flat, "5/40", 1, 4)
        ui_hits._replay_loop()
    # Cele afișate rămân (2 × 900 de mii, peste plafon, dar în memorie oricum).
    # Dintre cele neafișate (1,8 milioane, peste 1,5), iese cea mai veche.
    assert keys[1] in ui_hits._REPLAY_DONE and keys[3] in ui_hits._REPLAY_DONE
    assert keys[0] not in ui_hits._REPLAY_DONE and keys[2] in ui_hits._REPLAY_DONE
