"""Worker-ul cu identitate de țară: task → registru → motor → rezultat cu ecou.

Contract (AGENTS.md §4.4, o cheie nouă în config_json cere E2E):
- task fără chei de identitate (coada veche, UI-ul românesc) = România, exact
  ca înainte; rezultatul primește doar câmpurile aditive de ecou;
- task străin (`game_label` = id, `country`) = jocul din registru, decizia
  țării lui (frequency marcat când lipsește), niciodată cheile românești;
- țară sau joc explicit necunoscut = job FAILED cu mesaj, nu România.
"""

from __future__ import annotations

import functools
import io
import json
from pathlib import Path

import pandas as pd
import pytest

import job_queue as jq
import loto_enterprise.core.method_selector as ms
import worker
from loto_engine import LotoEngine
from loto_enterprise.core.lotteries import UnknownLotteryError
from ui_shared import decode_queue_result

ROOT = Path(__file__).resolve().parent
RO_CSV = ROOT / "_ISTORIC/loto_6_49.csv"
DE_CSV = ROOT / "_ISTORIC/externe/germania_lotto_6aus49.csv"
ECHO = ("country", "game_id", "bench_key", "geometry")
LEGACY_TASK_KEYS = {
    "game_label",
    "pool_size",
    "guarantee",
    "max_variants",
    "wheel_condition",
    "recent_penalty_draws",
    "recent_penalty_factor",
    "restrict_base_max",
    "restrict_base_min",
    "max_consecutive_run",
    "lookback",
    "sim_depth_pct",
    "pure_bench_mode",
    "bench_hit_target",
}


# --------------------------------------------------------------------------- #
# Rezolvarea identității
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "task,expected",
    [
        ({"game_label": "6/49"}, "6/49"),
        ({"game_label": "Loto 6/49"}, "6/49"),
        ({"game_label": "Loto 5/40"}, "5/40"),
        ({"game_label": "JOKER"}, "joker"),
        ({"game_label": "ceva necunoscut"}, "6/49"),  # coada veche: fallback RO
        ({"game_label": "6/49", "country": "RO"}, "6/49"),
        ({"game_label": "joker", "country": "ro"}, "joker"),
        ({"game_label": "de_lotto", "country": "DE"}, "de_lotto"),
        ({"game_label": "pl_lotto", "country": "pl"}, "pl_lotto"),
        ({"game_label": "x", "game_id": "es_primitiva"}, None),  # etichetă ≠ id
        ({"game_label": "es_primitiva", "game_id": "es_primitiva"}, "es_primitiva"),
        (
            {"game_label": "", "game_id": "es_primitiva", "country": "ES"},
            "es_primitiva",
        ),
        # Un id străin fără țară nu devine românesc.
        ({"game_label": "de_lotto"}, "de_lotto"),
        ({"game_label": "6/49", "country": ""}, "6/49"),  # cheie goală = fără cheie
    ],
)
def test_resolve_task_lottery(task, expected):
    if expected is None:
        with pytest.raises(UnknownLotteryError):
            worker._resolve_task_lottery(task)
        return
    assert worker._resolve_task_lottery(task).game_id == expected


@pytest.mark.parametrize(
    "task",
    [
        {"game_label": "6/49", "country": "XX"},
        {"game_label": "de_keno", "country": "DE"},
        {"game_label": "de_lotto", "country": "RO"},  # jocul altei țări
        {"game_label": "Loto 6/49", "country": "RO"},  # explicit = id exact
        {"game_label": "6/49", "game_id": "nu_exista"},
    ],
)
def test_explicit_unknown_identity_never_falls_back_to_romania(task):
    with pytest.raises(UnknownLotteryError):
        worker._resolve_task_lottery(task)


def test_restrict_base_cap_comes_from_the_game():
    # Implicit (și pentru toate jocurile românești): plafonul istoric 49.
    assert (
        worker._normalize_task({"restrict_base_max": 50}, draw_n=6)["restrict_base_max"]
        == 49
    )
    ro_540 = worker._resolve_task_lottery({"game_label": "5/40"})
    norm = worker._task_norm({"restrict_base_max": 45, "restrict_base_min": 44}, ro_540)
    assert (norm["restrict_base_min"], norm["restrict_base_max"]) == (44, 45)
    # Alt joc: universul lui (EuroMillions 50 nu mai e tăiat la 49).
    assert (
        worker._normalize_task({"restrict_base_max": 60}, draw_n=5, max_n=50)[
            "restrict_base_max"
        ]
        == 50
    )
    de = worker._resolve_task_lottery({"game_label": "de_lotto", "country": "DE"})
    assert worker._task_norm({"restrict_base_max": 99}, de)["restrict_base_max"] == 49
    assert worker._task_norm({"guarantee": 9}, de)["guarantee"] == 6
    # Un joc 5/50 (EuroMillions, adăugat mai târziu în registru): plafonul 50.
    from dataclasses import replace

    euro = replace(de, game_id="eu_test", country="EU", geometry="5/50")
    norm = worker._task_norm({"restrict_base_max": 99, "guarantee": 9}, euro)
    assert (norm["restrict_base_max"], norm["guarantee"]) == (50, 5)


def test_cache_key_is_v12_and_covers_the_foreign_decision_file(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ms, "_DECISIONS_ROOT", tmp_path)
    ro_cfg = {"datasets": [{"tasks": [{"game_label": "6/49"}]}]}
    de_cfg = {"datasets": [{"tasks": [{"game_label": "de_lotto", "country": "DE"}]}]}
    ro_key = worker._pipeline_cache_key("h", ro_cfg)
    de_key = worker._pipeline_cache_key("h", de_cfg)
    assert ro_key.startswith("v12:h:") and de_key != ro_key
    decision = ms.decision_path_for("DE")
    decision.parent.mkdir(parents=True)
    decision.write_text('{"_meta": {"country": "DE"}, "games": {}}', encoding="utf-8")
    assert worker._pipeline_cache_key("h", ro_cfg) == ro_key  # România neatinsă
    assert worker._pipeline_cache_key("h", de_cfg) != de_key


# --------------------------------------------------------------------------- #
# E2E: config → coadă → worker → rezultat decodat
# --------------------------------------------------------------------------- #


@pytest.fixture
def queue(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.db")
    monkeypatch.setattr(
        worker, "is_job_cancelled", functools.partial(jq.is_job_cancelled, db_path=db)
    )
    monkeypatch.setattr(worker, "fail_job", functools.partial(jq.fail_job, db_path=db))
    monkeypatch.setattr(
        worker,
        "update_job_progress",
        functools.partial(jq.update_job_progress, db_path=db),
    )
    # Decizii izolate: best_methods.json românesc cu un câștigător real (ca să se
    # vadă că jocul străin NU îl folosește) și decisions/<CC>/ gol.
    ro_decision = tmp_path / "best_methods.json"
    ro_decision.write_text(
        json.dumps(
            {
                "games": {
                    "loto_6_49": {
                        "auto_pilot_per_pool": {
                            "k12": {
                                "scorer": "ewma_hl30",
                                "ensemble": [{"method": "ewma_hl30", "weight": 1.0}],
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ms, "_DEFAULT_CONFIG_PATH", ro_decision)
    monkeypatch.setattr(ms, "_DECISIONS_ROOT", tmp_path / "countries_root")
    monkeypatch.setattr(ms, "_CONFIG", None)
    monkeypatch.setattr(LotoEngine, "use_bench_winner", True)

    def run(config: dict | str):
        text = config if isinstance(config, str) else json.dumps(config)
        jid = jq.submit_job("pipeline", text, db_path=db)
        job = jq.fetch_pending_job(db_path=db, worker_token=worker.WORKER_TOKEN)
        assert job and job["id"] == jid
        result = worker._run_pipeline_job(job)
        status = jq.get_job_status(jid, db_path=db)
        if result is None:
            return None, status
        assert jq.complete_job(
            jid, result, db_path=db, worker_token=worker.WORKER_TOKEN
        )
        rb, n = decode_queue_result(jq.get_job_status(jid, db_path=db)["result_json"])
        return rb, status

    return run


def _app_config(monkeypatch, rows=300) -> dict:
    import app_nicegui as app

    df = pd.read_csv(RO_CSV).tail(rows).reset_index(drop=True)
    monkeypatch.setattr(
        app, "STATE", {**app.STATE, "datasets": [("loto_6_49.csv", df)]}
    )
    monkeypatch.setitem(app.SETTINGS, "pool_size_val", 12)
    return json.loads(app._build_config_json())


def _foreign_dataset(task_template: dict, rows=300, **identity) -> dict:
    df = pd.read_csv(DE_CSV).tail(rows).reset_index(drop=True)
    task = {**task_template, **identity}
    return {
        "fname": DE_CSV.name,
        "df_json": df.to_json(orient="split"),
        "tasks": [task],
    }


def _comparable(data: dict) -> dict:
    out = {k: v for k, v in data.items() if k not in ("resource_stats", *ECHO)}
    out["audit"] = {k: v for k, v in data["audit"].items() if k != "performance"}
    return out


def test_romanian_task_keeps_its_config_and_gets_only_the_echo(
    queue, monkeypatch, tmp_path
):
    cfg = _app_config(monkeypatch)
    # Config-ul românesc nu are chei noi: config_json și input_hash neschimbate.
    for ds in cfg["datasets"]:
        for task in ds["tasks"]:
            assert set(task) == LEGACY_TASK_KEYS
    rb, _status = queue(cfg)
    ((fname, outs),) = rb
    data = outs["6/49"]
    assert {k: data[k] for k in ECHO} == {
        "country": "RO",
        "game_id": "6/49",
        "bench_key": "loto_6_49",
        "geometry": "6/49",
    }
    assert "country" not in data["audit"]  # auditul românesc rămâne cel de dinainte
    assert data["audit"]["bench_winner"]["loto_6_49"]["method"] == "ewma_hl30"
    history = json.loads(
        Path(tmp_path / "pool_history.json").read_text(encoding="utf-8")
    )
    assert set(history) == {"6/49_12"}


def test_keyless_old_task_is_byte_identical_to_the_legacy_engine(
    queue, monkeypatch, tmp_path
):
    """Un job vechi din coadă (fără chei de identitate) produce exact ce producea
    motorul fără identitate (`LotoEngine("6/49")`), plus ecoul aditiv; un task
    românesc cu `country: "RO"` explicit produce același lucru."""
    cfg = _app_config(monkeypatch)
    history = tmp_path / "pool_history.json"

    history.unlink(missing_ok=True)
    rb, _ = queue(cfg)
    keyless = rb[0][1]["6/49"]

    explicit_cfg = json.loads(json.dumps(cfg))
    explicit_cfg["datasets"][0]["tasks"][0]["country"] = "RO"
    history.unlink(missing_ok=True)
    rb_explicit, _ = queue(explicit_cfg)
    assert _comparable(rb_explicit[0][1]["6/49"]) == _comparable(keyless)

    # Motorul de dinainte, fără identitate, cu aceiași parametri normalizați.
    task = cfg["datasets"][0]["tasks"][0]
    norm = worker._normalize_task(task, 6)
    df = pd.read_json(
        io.StringIO(cfg["datasets"][0]["df_json"]), orient="split", convert_dates=False
    )
    csv_path = tmp_path / "legacy.csv"
    df.to_csv(csv_path, index=False)
    history.unlink(missing_ok=True)
    legacy = LotoEngine(game_type="6/49")
    assert legacy.load_data(str(csv_path))
    lines, p10, p90, g_range, context, audit = legacy.run_institutional_pipeline(
        pool_size=norm["pool_size"],
        guarantee=norm["guarantee"],
        max_variants=norm["max_variants"],
        wheel_condition=norm["wheel_condition"],
        recent_penalty_draws=norm["recent_penalty_draws"],
        recent_penalty_factor=norm["recent_penalty_factor"],
        restrict_base_max=norm["restrict_base_max"],
        restrict_base_min=norm["restrict_base_min"],
        max_consecutive_run=norm["max_consecutive_run"],
        lookback=norm["lookback"],
        sim_depth_pct=norm["sim_depth_pct"],
        enable_adaptive_persistence=False,
        pure_bench_mode=norm["pure_bench_mode"],
    )
    assert keyless["hard_core"] == legacy.hard_core
    assert keyless["variants"] == lines
    assert (keyless["p10"], keyless["p90"], keyless["g_range"]) == (p10, p90, g_range)
    assert keyless["context"] == context
    strip = lambda a: {k: v for k, v in a.items() if k != "performance"}  # noqa: E731
    assert strip(keyless["audit"]) == strip(audit)


def test_foreign_task_runs_with_its_identity_and_flagged_frequency(
    queue, monkeypatch, tmp_path
):
    ro_cfg = _app_config(monkeypatch)
    template = {k: v for k, v in ro_cfg["datasets"][0]["tasks"][0].items()}
    cfg = {
        "input_hash": "test-foreign",
        "use_cache": False,
        "datasets": [
            ro_cfg["datasets"][0],  # job mixt: România fără chei + Germania
            _foreign_dataset(template, game_label="de_lotto", country="DE"),
        ],
    }
    rb, _ = queue(cfg)
    outs = {fname: o for fname, o in rb}
    de = outs[DE_CSV.name]["de_lotto"]
    assert {k: de[k] for k in ECHO} == {
        "country": "DE",
        "game_id": "de_lotto",
        "bench_key": "de_lotto",
        "geometry": "6/49",
    }
    winner = de["audit"]["bench_winner"]
    assert set(winner) == {"de_lotto"}  # nicio decizie românească
    assert winner["de_lotto"]["method"] == "frequency"
    assert winner["de_lotto"]["fallback"] is True
    assert (
        winner["de_lotto"]["reason"]
        == "fără decizie bench pentru Germania · Lotto 6aus49"
    )
    assert de["audit"]["country"] == "DE" and de["audit"]["game_id"] == "de_lotto"
    assert len(de["hard_core"]) == 12 and de["variants"]
    ro = outs["loto_6_49.csv"]["6/49"]
    assert ro["country"] == "RO"
    assert ro["audit"]["bench_winner"]["loto_6_49"]["method"] == "ewma_hl30"
    history = json.loads(
        Path(tmp_path / "pool_history.json").read_text(encoding="utf-8")
    )
    assert set(history) == {"6/49_12", "DE_de_lotto_6/49_12"}
    assert history["6/49_12"]["pool"] == ro["hard_core"]
    assert history["DE_de_lotto_6/49_12"]["pool"] == de["hard_core"]


def test_foreign_task_uses_its_country_decision(queue, monkeypatch, tmp_path):
    decision = ms.decision_path_for("DE")
    decision.parent.mkdir(parents=True)
    decision.write_text(
        json.dumps(
            {
                "_meta": {"country": "DE"},
                "games": {
                    "de_lotto": {
                        "auto_pilot_per_pool": {
                            "k12": {
                                "scorer": "gap_hazard",
                                "ensemble": [{"method": "gap_hazard", "weight": 1.0}],
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    template = _app_config(monkeypatch)["datasets"][0]["tasks"][0]
    cfg = {
        "input_hash": "x",
        "use_cache": False,
        "datasets": [_foreign_dataset(template, game_label="de_lotto", country="DE")],
    }
    rb, _ = queue(cfg)
    info = rb[0][1]["de_lotto"]["audit"]["bench_winner"]["de_lotto"]
    assert info["method"] == "gap_hazard" and "fallback" not in info


@pytest.mark.parametrize(
    "identity",
    [
        {"game_label": "de_lotto", "country": "XX"},
        {"game_label": "de_keno", "country": "DE"},
        {"game_label": "de_lotto", "country": "RO"},
        {"game_label": "6/49", "country": "ZZ"},
    ],
)
def test_explicit_unknown_country_or_game_fails_the_job(
    queue, monkeypatch, tmp_path, identity
):
    template = _app_config(monkeypatch)["datasets"][0]["tasks"][0]
    cfg = {
        "input_hash": "x",
        "use_cache": True,  # cheia de cache nu are voie să cadă înaintea verificării
        "datasets": [_foreign_dataset(template, **identity)],
    }
    rb, status = queue(cfg)
    assert rb is None
    assert status["status"] == jq.JOB_FAILED
    assert "necunoscut" in status["result_json"]
    assert not (tmp_path / "pool_history.json").exists()  # nimic n-a rulat
