"""Interfața pe țări: rezolvarea jocului, filtrarea submisiei, textele de bench.

Contract (AGENTS.md §4.1, designul selectorului de țară):
- România (implicit) rămâne byte-identică: config_json, input_hash, etichete;
- un rezultat se descrie din ecoul worker-ului (`country`, `game_id`), nu din
  numele fișierului; un rezultat fără ecou este România;
- submisia conține numai istoricele țării și jocurilor alese; un task străin
  poartă `country` și `game_label` = id-ul din registru.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import app_nicegui as app
from loto_enterprise.core import lotteries as L
from loto_enterprise.core.full_ticket import build_full_ticket
from test_country_worker import queue  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent
RO_CSV = ROOT / "_ISTORIC/loto_6_49.csv"
AT = L.GAMES_BY_ID["at_lotto"]
DE = L.GAMES_BY_ID["de_lotto"]


@pytest.fixture
def ui_state(monkeypatch):
    """SETTINGS/STATE izolate; România implicit."""
    monkeypatch.setattr(app, "SETTINGS", dict(app.SETTINGS))
    import ui_runtime

    monkeypatch.setattr(ui_runtime, "SETTINGS", app.SETTINGS)
    state = {**app.STATE, "datasets": [], "dataset_game": {}}
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(ui_runtime, "STATE", state)
    app._sync_ui_namespace()
    app.SETTINGS["country_val"] = "RO"
    app.SETTINGS["games_val"] = []
    app.SETTINGS["restrict_base_enabled_val"] = False
    yield state
    app._sync_ui_namespace()


# --------------------------------------------------------------------------- #
# Rezolvarea jocului
# --------------------------------------------------------------------------- #


def test_spec_resolution_prefers_echo_then_registry_then_binding(ui_state):
    # Fără ecou și fără legătură: ghicitul românesc de dinainte.
    assert app._game_spec_for("loto_6_49.csv").game_id == "6/49"
    assert app._game_spec_for("joker.csv").game_id == "joker"
    assert app._game_spec_for("ceva.csv").game_id == "6/49"
    # Id-ul din registru și cheia de bench.
    assert app._game_spec_for("de_lotto") is DE
    assert app._game_spec_for("joker_urna2").game_id == "joker"
    # Ecoul worker-ului bate cheia.
    assert app._game_spec_for("6/49", {"game_id": "at_lotto"}) is AT
    # Legătura explicită a fișierului bate ghicitul.
    ui_state["dataset_game"]["germania_lotto_6aus49.csv"] = "de_lotto"
    assert app._game_spec_for("germania_lotto_6aus49.csv") is DE
    # Geometria pentru apelanții vechi; niciodată identitatea românească.
    assert app._game_label_for("germania_lotto_6aus49.csv") == "6/49"
    assert app._game_label_for("at_lotto") == "6/45"
    assert app._game_label_for("loto_5_40.csv") == "5/40"


def test_titles_prices_and_training_note(ui_state):
    assert app._game_title("6/49") == "România · Loto 6/49"
    assert app._game_title("x", {"game_id": "de_lotto"}) == "Germania · Lotto 6aus49"
    assert app._fmt_price(L.GAMES_BY_ID["6/49"]) == "8 Lei/variantă"
    assert app._fmt_price(L.GAMES_BY_ID["hu_hatos"]) == "500 HUF/variantă"
    assert app._fmt_price(L.GAMES_BY_ID["eu_euromillions"]) == "tarif necunoscut"
    assert app._training_only_note(L.GAMES_BY_ID["6/49"]) == ""
    assert (
        app._training_only_note(DE)
        == "doar antrenament: nu se joacă online din România"
    )


def test_hypergeometric_params_come_from_the_registry():
    import ui_results

    assert ui_results._hypergeo_params("at_lotto") == (6, 45)
    assert ui_results._hypergeo_params("eu_euromillions") == (5, 50)
    assert ui_results._hypergeo_params("6/49") == (6, 49)
    assert ui_results._hypergeo_params("loto_5_40") == (6, 40)
    assert ui_results._hypergeo_params("joker_urna2") is None
    assert ui_results._ticket_pick("at_lotto") == 6
    assert ui_results._ticket_pick("joker") == 5


def test_next_draw_dates_follow_the_game_weekdays():
    wed = date(2026, 9, 30)  # miercuri
    assert app._next_draw_date_for((3, 6), wed) == "01-10-2026"  # joi
    assert app._next_draw_date_for(DE.draw_weekdays, wed) == "30-09-2026"
    assert app._weekdays_text((2, 5)) == "Miercuri/Sâmbătă"


def test_settings_sanitize_unknown_country_and_foreign_games(ui_state):
    app.SETTINGS["country_val"] = "zz"
    app.SETTINGS["games_val"] = ["de_lotto", "6/49"]
    app._sanitize_country_settings()
    assert app.SETTINGS["country_val"] == "RO"
    assert app.SETTINGS["games_val"] == ["6/49"]
    app.SETTINGS["country_val"] = "de"
    app.SETTINGS["games_val"] = ["6/49"]
    app._sanitize_country_settings()
    assert (app.SETTINGS["country_val"], app.SETTINGS["games_val"]) == ("DE", [])
    assert [g.game_id for g in app._selected_games()] == ["de_lotto"]


# --------------------------------------------------------------------------- #
# Submisia
# --------------------------------------------------------------------------- #


def _ro_df():
    return pd.read_csv(RO_CSV).tail(30).reset_index(drop=True)


def test_romanian_config_is_unchanged_by_foreign_datasets(ui_state):
    ui_state["datasets"] = [("loto_6_49.csv", _ro_df())]
    before = app._build_config_json()
    # Un istoric străin încărcat nu intră în submisia României.
    loaded, errors = app._load_registry_histories([AT])
    assert errors == [] and loaded[0][1] == "at_lotto"
    assert app._build_config_json() == before
    cfg = json.loads(before)
    assert [ds["fname"] for ds in cfg["datasets"]] == ["loto_6_49.csv"]
    assert "country" not in cfg["datasets"][0]["tasks"][0]
    # Jocurile bifate filtrează și în România.
    app.SETTINGS["games_val"] = ["joker"]
    assert json.loads(app._build_config_json())["datasets"] == []


def test_foreign_submission_carries_identity_and_only_its_games(ui_state):
    ui_state["datasets"] = [("loto_6_49.csv", _ro_df())]
    app._load_registry_histories([AT, DE])
    app.SETTINGS["country_val"] = "AT"
    cfg = json.loads(app._build_config_json())
    assert [ds["fname"] for ds in cfg["datasets"]] == ["austria_lotto_6aus45.csv"]
    task = cfg["datasets"][0]["tasks"][0]
    assert (task["game_label"], task["country"]) == ("at_lotto", "AT")
    # Restrângerea străină intră în hash numai activă, cu cheia jocului.
    free = cfg["input_hash"]
    app.SETTINGS["restrict_base_min_at_lotto_val"] = 5
    app.SETTINGS["restrict_base_max_at_lotto_val"] = 40
    assert json.loads(app._build_config_json())["input_hash"] == free
    app.SETTINGS["restrict_base_enabled_val"] = True
    on = json.loads(app._build_config_json())
    assert on["input_hash"] != free
    t = on["datasets"][0]["tasks"][0]
    assert (t["restrict_base_min"], t["restrict_base_max"]) == (5, 40)
    # Pragul românesc de aceeași geometrie nu atinge jocul străin.
    assert app._active_restrict_base("de_lotto") == (0, 0)


def test_registry_loader_rejects_an_invalid_history(ui_state, tmp_path, monkeypatch):
    bad = tmp_path / "_ISTORIC" / "externe" / "bad.csv"
    bad.parent.mkdir(parents=True)
    bad.write_text("date,n1,n2,n3,n4,n5,n6\n01-01-2020,1,2,3,4,5,99\n", encoding="utf-8")
    fake = L.Lottery(**{**AT.__dict__, "csv": "_ISTORIC/externe/bad.csv"})
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    loaded, errors = app._load_registry_histories([fake])
    assert loaded == [] and "invalide" in errors[0]
    assert ui_state["datasets"] == []


def test_restrict_rows_and_base_tables_follow_the_country(ui_state):
    assert app._restrict_games_for("RO") == app._RESTRICT_BASE_GAMES
    assert app._restrict_games_for("AT") == (("at_lotto", "at_lotto", 45),)
    assert app._base_table_games("RO") == app._BASE_TABLE_GAMES
    assert app._base_table_games("DE") == (
        ("Germania · Lotto 6aus49", "externe/germania_lotto_6aus49.csv", 6, 49),
    )


def test_bench_texts_name_the_country(ui_state, tmp_path, monkeypatch):
    texts = app._country_bench_texts("AT")
    assert texts["intro"].startswith("Austria (Lotto 6 aus 45): un singur bench")
    assert "Austria:" in texts["curation"] and "× 1 joc" in texts["curation"]
    assert "Austria" in texts["eta"]
    if not L.decision_path_for("AT").exists():
        assert texts["freshness"] == (
            "Austria nu are încă bench; până atunci generarea folosește frequency."
        )


# --------------------------------------------------------------------------- #
# Bilet complet
# --------------------------------------------------------------------------- #


def test_full_ticket_uses_the_registry_layout():
    data = {"hard_core": list(range(1, 11)), "guarantee": 3, "audit": {}}
    t = build_full_ticket("de_lotto", data, 1)
    assert t["error"] is None and t["per_ticket"] == 12 and len(t["variants"]) == 12
    assert all(len(v) == 6 for v in t["variants"])
    em = build_full_ticket("eu_euromillions", data, 1)
    assert em["error"] == "bilet nemodelat: EuroMillions · EuroMillions"
    # România: comportamentul de dinainte.
    assert build_full_ticket("6/49", data, 1)["per_ticket"] == 3


# --------------------------------------------------------------------------- #
# E2E: UI → coadă → worker → decodare → etichete UI (joc 6/45)
# --------------------------------------------------------------------------- #


def test_foreign_6_45_task_end_to_end(ui_state, queue):  # noqa: F811
    df = pd.read_csv(ROOT / AT.csv).tail(300).reset_index(drop=True)
    ui_state["datasets"] = [("austria_lotto_6aus45.csv", df)]
    ui_state["dataset_game"]["austria_lotto_6aus45.csv"] = "at_lotto"
    app.SETTINGS["country_val"] = "AT"
    app.SETTINGS["pool_size_val"] = 10
    cfg = json.loads(app._build_config_json())
    rb, _status = queue(cfg)
    ((fname, outs),) = rb
    assert fname == "austria_lotto_6aus45.csv"
    data = outs["at_lotto"]
    assert {k: data[k] for k in ("country", "game_id", "bench_key", "geometry")} == {
        "country": "AT",
        "game_id": "at_lotto",
        "bench_key": "at_lotto",
        "geometry": "6/45",
    }
    assert len(data["hard_core"]) == 10
    assert all(1 <= n <= 45 for n in data["hard_core"])
    assert all(len(v) == 6 for v in data["variants"])
    assert data["audit"]["bench_winner"]["at_lotto"]["method"] == "frequency"
    # UI: titlu, geometrie WF și opțiuni WF din ecou.
    assert app._game_title("at_lotto", data) == "Austria · Lotto 6 aus 45"
    assert app._game_spec_for("at_lotto", data).geometry == "6/45"
    opts = app._wf_generation_options(data)
    assert (opts["country"], opts["game_key"]) == ("AT", "at_lotto")
    assert not app._echo_mismatch("at_lotto", data)
    assert app._echo_mismatch("at_lotto", {k: v for k, v in data.items() if k != "game_id"})
    t = build_full_ticket("at_lotto", data, 1)
    assert t["error"] is None and t["per_ticket"] == 12


def test_mail_names_method_and_rating_for_the_pool(monkeypatch):
    import app_nicegui as app
    from loto_enterprise.core.lotteries import lottery_by_id

    spec = lottery_by_id("6/49")
    entry = {"rationale": "dmd_forecast: rată 3+ @ k12 = 0.162 (Wilson_lb=0.151), beat random (hipergeometric 0.1480) in 3/4 windows on the same 3+ target (lift +0.0100)",
             "baseline_rate": 0.1480, "target_label": "3+"}
    monkeypatch.setattr(app, "_decision_entry", lambda k, pool: entry)
    data = {"pool_size": 12, "audit": {"bench_winner": {"loto_6_49": {"method": "dmd_forecast"}}}}
    lines = app._mail_method_lines(spec, data)
    assert lines[0] == "METODĂ: dmd_forecast (câștigătoarea bench-ului la pool 12)"
    assert "16.20%" in lines[1] and "14.80%" in lines[1] and "3/4" in lines[1]
    data["audit"]["bench_winner"]["loto_6_49"]["fallback"] = True
    assert "fără bench" in app._mail_method_lines(spec, data)[0]


def test_mail_names_last_draw_with_the_best_pool_result():
    from loto_enterprise.core.lotteries import lottery_by_id

    spec = lottery_by_id("6/49")
    df = pd.DataFrame(
        {
            "date": ["01-09-2026", "04-09-2026", "08-09-2026"],
            **{f"n{i}": [i, i + 10, i + 20] for i in range(1, 7)},
        }
    )
    line = app._mail_best_draw_line(spec, df, [1, 2, 3, 4, 21, 22, 23, 24])
    assert "4 numere din pool" in line and "08-09-2026" in line
    assert "de 2 ori" in line and "3 extrageri" in line
    assert "indisponibil" in app._mail_best_draw_line(spec, None, [1])

def test_play_note_replaces_the_training_only_note():
    bg = L.GAMES_BY_ID["bg_toto2"]
    assert app._training_only_note(bg) == bg.play_note
    assert "agenție" in app._training_only_note(bg)


def test_autoload_loads_missing_and_changed_histories_once(ui_state):
    ui_state["datasets"] = []
    ui_state.pop("dataset_mtime", None)
    loaded, errors = app._autoload_histories([AT])
    assert errors == [] and [g for _n, g, _k in loaded] == ["at_lotto"]
    # Fișier neschimbat: nu se reîncarcă.
    assert app._autoload_histories([AT]) == ([], [])
    # Schimbat pe disc (alt mtime): se reîncarcă.
    name = Path(AT.csv).name
    ui_state["dataset_mtime"][name] -= 1
    assert [g for _n, g, _k in app._autoload_histories([AT])[0]] == ["at_lotto"]


def test_loading_records_the_load_time(ui_state):
    ui_state.pop("dataset_loaded_at", None)
    app._load_registry_histories([AT])
    stamp = ui_state["dataset_loaded_at"][Path(AT.csv).name]
    assert len(stamp) == len("28-09-2026 04:10")


def test_new_generation_makes_a_running_walk_forward_stale(ui_state, monkeypatch):
    ui_state["datasets"] = [("loto_6_49.csv", _ro_df())]
    ui_state.update(wf_seq=3, wf_running=True, retro={"x": [1]}, active_job_id=None)
    monkeypatch.setattr(app, "ensure_worker_running", lambda: None)
    monkeypatch.setattr(app, "submit_job", lambda kind, cfg: 7)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    monkeypatch.setattr(app.ui, "notify", lambda *a, **k: None)
    app.submit_generation()
    assert ui_state["wf_seq"] == 4 and ui_state["wf_running"] is False
    assert ui_state["retro"] == {} and ui_state["active_job_id"] == 7
