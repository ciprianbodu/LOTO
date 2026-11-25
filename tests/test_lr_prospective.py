"""Prospective LR evidence stays local, causal and append-only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time
import json
import os
import sqlite3
from unittest.mock import Mock

import pytest

from covering.lr_budget import FOUR_PLUS, recommend_games
from loto_enterprise.core import lr_prospective as ledger, official_prizes
from loto_enterprise.core.lr_played_portfolio import expand_plan


HASH = "a" * 64
ARCHIVE_HASH = "c" * 64


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setenv("LOTO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(ledger, "_build_identity", lambda: {"git_sha": None, "source_sha256": "b" * 64})
    return tmp_path / "lr_prospective.sqlite3"


def _moment(year=2026, month=11, day=26, hour=14, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ledger._TIME_ZONE)


def _plan(game="6/49"):
    plan, = recommend_games(100, FOUR_PLUS, game)
    plan["forecast"] = {
        "history_hash": HASH[:16],
        "history_sha256": HASH,
        "date": "22-11-2026",
        "rows": 123,
        "filename": "registered-example.csv",
        "pool": sorted({n for ticket in plan["tickets"] for zone in ticket for n in zone["numbers"]}),
        "selection": {"method": "frequency", "weight": 1},
        "jokers": sorted({zone["joker"] for ticket in plan["tickets"] for zone in ticket if zone["joker"]}),
        "code": plan["tickets"][0][0]["code"],
        "dynamic": True,
        "inverse": False,
    }
    return plan


def _read(path, table):
    db = sqlite3.connect(path)
    try:
        return [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM {table} ORDER BY rowid")]
    finally:
        db.close()


def test_target_before_and_after_same_day_cutoff_and_known_draw():
    seen = date(2026, 11, 22)
    today = date(2026, 11, 26)
    sunday = date(2026, 11, 29)
    chosen, close = ledger.draw_target(_moment(minute=29, hour=15), seen)
    assert chosen == today and close.isoformat() == "2026-11-26T15:30:00+02:00"
    assert ledger.draw_target(_moment(minute=30, hour=15), seen)[0] == sunday
    assert ledger.draw_target(_moment(), today)[0] == sunday
    with pytest.raises(ValueError, match="deja cunoscută"):
        ledger.draw_target(_moment(), today, requested=today)
    with pytest.raises(ValueError, match="fus orar"):
        ledger.draw_target(datetime(2026, 11, 26, 14), seen)
    with pytest.raises(ValueError, match="ulterioare"):
        ledger.draw_target(_moment(), sunday)


def test_target_respects_special_rescheduled_day_without_retroactive_targeting():
    holiday = date(2026, 12, 24)
    moved = date(2026, 12, 25)
    schedules = {holiday: None, moved: time(11)}
    now = _moment(month=12, day=24)
    known = date(2026, 12, 20)
    assert ledger.draw_target(now, known, schedule=schedules)[0] == moved
    with pytest.raises(ValueError, match="absentă din program"):
        ledger.draw_target(now, known, requested=holiday, schedule=schedules)
    assert ledger.draw_target(_moment(month=12, day=25, hour=11), known, schedule=schedules)[0] == date(2026, 12, 27)
    summer = ledger.draw_target(_moment(month=7, day=2, hour=10), date(2026, 6, 28))[1]
    assert summer.utcoffset().total_seconds() == 3 * 3600


def test_register_rejects_past_date_and_missing_history_proof(monkeypatch, local):
    monkeypatch.setattr(ledger, "_clock", _moment)
    plan = _plan()
    rows, errors = ledger.register_lr_recommendations([plan], target_day=date(2026, 11, 22))
    assert not rows and "închisă" in errors["6/49"] and not local.exists()
    plan["forecast"].pop("history_hash")
    rows, errors = ledger.register_lr_recommendations([plan])
    assert not rows and "amprenta" in errors["6/49"] and not local.exists()


def test_registration_and_official_same_day_evaluations_survive_restart(monkeypatch, local):
    clock = [_moment()]
    monkeypatch.setattr(ledger, "_clock", lambda: clock[0])
    plan = _plan()
    registered, errors = ledger.register_lr_recommendations([plan], settings={"lookback": 100})
    assert not errors
    original, = _read(local, "predictions")
    assert original["history"]["sha256"] == HASH
    assert original["history"]["engine_hash"] == HASH[:16]
    assert original["history"]["source"] == "registered-example.csv"
    assert original["build"]["source_sha256"] == "b" * 64
    assert original["target_day"] == "2026-11-26"
    assert original["captured_at"].endswith("+02:00")
    assert original["settings"]["lookback"] == 100
    assert original["methods"]["selection"]["method"] == "frequency"
    assert original["main_pool"] == plan["forecast"]["pool"]
    expanded = expand_plan(plan)
    assert original["paid_variants"] == expanded.paid
    assert original["price_bani"] == expanded.price_bani

    clock[0] = _moment(minute=1)
    repeated, _ = ledger.register_lr_recommendations([deepcopy(plan)], settings={"lookback": 100})
    assert repeated == registered
    clock[0] = _moment(hour=20)
    main = expanded.variants[0][0]
    source = official_prizes.ARCHIVE_URLS["loto_6_49"]
    first = official_prizes.PrizeDraw("loto_6_49", "2026-11-26", main, None, (), source)
    other = tuple(n for n in range(1, 50) if n not in main)[:6]
    second = replace(first, numbers=other)
    assert ledger.append_official_draws([first], archive_sha256=ARCHIVE_HASH) == 1
    assert ledger.append_official_draws(
        [replace(first, numbers=tuple(reversed(main))), second], archive_sha256=ARCHIVE_HASH,
    ) == 1
    assert ledger.append_official_draws([first, second], archive_sha256=ARCHIVE_HASH) == 0
    assert _read(local, "predictions") == [original]
    one, two = _read(local, "evaluations")
    assert original["evaluation_contract_version"] == one["evaluation_contract_version"] == 2
    assert one["grade"]["hit6"] and not two["grade"]["hit6"]
    assert one["official_archive_sha256"] == ARCHIVE_HASH
    assert one["date"] == two["date"] == original["target_day"]
    assert one["numbers"] != two["numbers"]

    with sqlite3.connect(local) as db:
        for statement in (
            "UPDATE predictions SET payload='corrupted'",
            "DELETE FROM predictions",
            "INSERT OR REPLACE INTO predictions SELECT * FROM predictions",
            "UPDATE evaluations SET payload='corrupted'",
            "DELETE FROM evaluations",
            "INSERT OR REPLACE INTO evaluations SELECT * FROM evaluations",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    assert _read(local, "predictions") == [original]
    assert _read(local, "evaluations") == [one, two]


def test_two_simultaneous_generations_keep_one_snapshot(monkeypatch, local):
    monkeypatch.setattr(ledger, "_clock", _moment)
    plan = _plan()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.map(lambda _: ledger.register_lr_recommendations([deepcopy(plan)]), range(2))
    assert first == second
    assert len(_read(local, "predictions")) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_540_official_sixth_ball_remains_part_of_result_identity(monkeypatch, local, legacy):
    if legacy:
        current_snapshot = ledger._snapshot

        def old_snapshot(*args, **kwargs):
            entry = current_snapshot(*args, **kwargs)
            entry.pop("evaluation_contract_version")
            original = {k: v for k, v in entry.items() if k not in ("id", "captured_at")}
            entry["id"] = ledger._hash(original)
            return entry

        monkeypatch.setattr(ledger, "_snapshot", old_snapshot)
    clock = [_moment()]
    monkeypatch.setattr(ledger, "_clock", lambda: clock[0])
    plan = _plan("5/40")
    registered, errors = ledger.register_lr_recommendations([plan])
    assert registered and not errors
    clock[0] = _moment(hour=20)
    expanded = expand_plan(plan)
    numbers = expanded.variants[0][0]
    extra = next(n for n in range(1, 41) if n not in expanded.pool)
    draw = official_prizes.PrizeDraw(
        "loto_5_40", "2026-11-26", (*numbers, extra), None, (), official_prizes.ARCHIVE_URLS["loto_5_40"],
    )
    assert ledger.append_official_draws([draw], archive_sha256=ARCHIVE_HASH) == 1
    assert ledger.append_official_draws(
        [replace(draw, numbers=(*reversed(numbers), extra))], archive_sha256=ARCHIVE_HASH,
    ) == 0
    assert ledger.append_official_draws(
        [replace(draw, numbers=(*numbers[:4], extra, numbers[-1]))], archive_sha256=ARCHIVE_HASH,
    ) == 1
    first, second = _read(local, "evaluations")
    assert first["evaluation_contract_version"] == second["evaluation_contract_version"] == (1 if legacy else 2)
    assert first["grade"]["prize_counts"]["I"] >= 1
    assert "I" not in second["grade"]["prize_counts"]
    assert second["grade"]["prize_counts"]["II"] >= 1


def test_normal_import_evaluates_only_pending_official_targets_without_new_csv_rows(monkeypatch, local):
    import update_csv

    clock = [_moment()]
    monkeypatch.setattr(ledger, "_clock", lambda: clock[0])
    fetch = Mock(return_value="<html>official fixture</html>")
    monkeypatch.setattr(official_prizes, "_fetch_month_html", fetch)
    assert ledger.refresh_official_evaluations() == (0, [])
    assert not local.exists() and not fetch.called
    plan = _plan("joker")
    main, joker, _ = expand_plan(plan).variants[0]
    ledger.register_lr_recommendations([plan])
    assert ledger.refresh_official_evaluations() == (0, []) and not fetch.called
    clock[0] = _moment(hour=20)
    draw = official_prizes.PrizeDraw(
        "joker", "2026-11-26", main, joker, (), official_prizes.ARCHIVE_URLS["joker"],
    )
    monkeypatch.setattr(official_prizes, "parse_month_html", lambda *_, draw_dates: [draw] if draw.draw_date in draw_dates else [])
    monkeypatch.setattr(update_csv, "GAME_CONFIGS", {})
    monkeypatch.setattr(update_csv, "_find_istoric_dir", lambda: local.parent)
    assert update_csv.update_all() == 0
    fetch.assert_called_once_with("joker", 2026, 11)
    grade = _read(local, "evaluations")[0]["grade"]
    assert grade["max_hits"] == 5 and grade["prize_counts"]["I"] >= 1
    assert _read(local, "predictions")[0]["game"] == "joker"


def test_build_copies_get_source_fingerprint_without_git(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ledger, "LR_DIR", tmp_path / "catalog")
    monkeypatch.setattr(ledger, "_git_identity", lambda: None)
    code = tmp_path / "ui_lr_budget.py"
    code.write_text("file = 1", encoding="utf-8")
    before = ledger._build_identity()
    (tmp_path / "catalog").mkdir()
    tables = tmp_path / "catalog/example.txt"
    tables.write_text("table 1", encoding="utf-8")
    after = ledger._build_identity()
    assert all(len(row["source_sha256"]) == 64 and row["git_sha"] is None for row in (before, after))
    assert before != after

    metadata = tables.stat()
    tables.write_text("table 2", encoding="utf-8")
    os.utime(tables, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert tables.stat().st_mtime_ns == metadata.st_mtime_ns
    assert ledger._build_identity()["source_sha256"] != after["source_sha256"]


def test_unrelated_invalid_draw_does_not_block_official_target(monkeypatch, local):
    clock = [_moment()]
    monkeypatch.setattr(ledger, "_clock", lambda: clock[0])
    plan = _plan("5/40")
    assert ledger.register_lr_recommendations([plan])[0]
    clock[0] = _moment(hour=20)

    def table(day, numbers):
        images = "".join(f'<img src="/images/bile/{num}.png">' for num in numbers)
        payouts = "".join(
            f"<tr><td>{category} (5)</td><td>REPORT</td><td>-</td><td>-</td></tr>"
            for category in ("I", "II", "III")
        )
        return (
            '<div class="rezultate-extrageri-content">'
            f'<div class="button-open-details">Detalii castiguri la 5/40 din {day}</div>'
            f'<div class="numere-extrase">{images}</div>'
            f'<table class="results-table">{payouts}</table></div>'
        )

    official = (
        '<select name="select-year"><option value="2026" selected>2026</option></select>'
        '<select name="select-month"><option value="11" selected>11</option></select>'
        + table("22.11.2026", (37, 12, 9, 20, 21, 9))
        + table("26.11.2026", (1, 4, 6, 23, 27, 28))
    )
    with pytest.raises(official_prizes.ArchiveError, match="Invalid official drawn"):
        official_prizes.parse_month_html("5/40", official, 2026, 11)
    monkeypatch.setattr(official_prizes, "_fetch_month_html", lambda *_: official)
    assert ledger.refresh_official_evaluations() == (1, [])
    assert _read(local, "evaluations")[0]["numbers"] == [1, 4, 6, 23, 27, 28]


@pytest.mark.parametrize("delayed_at", ["write", "commit"])
def test_expired_prediction_cannot_be_written_or_committed(monkeypatch, local, delayed_at):
    early = _moment(hour=15, minute=29)
    late = _moment(hour=20)
    readings = iter((early, late) if delayed_at == "write" else (early, early, late))
    monkeypatch.setattr(ledger, "_clock", lambda: next(readings))
    entries, errors = ledger.register_lr_recommendations([_plan()])
    assert not entries and "Ora-limită a trecut" in errors["6/49"]
    assert not _read(local, "predictions")
