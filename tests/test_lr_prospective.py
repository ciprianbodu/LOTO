"""Prospective LR evidence stays local, causal and append-only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time
from hashlib import sha256
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
    first = official_prizes.PrizeDraw("loto_6_49", "2026-11-26", main, None, (), source, supplementary=False)
    other = tuple(n for n in range(1, 50) if n not in main)[:6]
    second = replace(first, numbers=other, supplementary=True)
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
    assert one["event_id"] != two["event_id"]
    assert len(ledger.read_prospective_report()["active_evaluations"]) == 2

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
        supplementary=False,
    )
    assert ledger.append_official_draws([draw], archive_sha256=ARCHIVE_HASH) == 1
    assert ledger.append_official_draws(
        [replace(draw, numbers=(*reversed(numbers), extra))], archive_sha256=ARCHIVE_HASH,
    ) == 0
    clock[0] = _moment(hour=20, minute=1)
    assert ledger.append_official_draws(
        [replace(draw, numbers=(*numbers[:4], extra, numbers[-1]))], archive_sha256=HASH,
    ) == 1
    first, second = _read(local, "evaluations")
    assert first["evaluation_contract_version"] == second["evaluation_contract_version"] == (1 if legacy else 2)
    assert first["grade"]["prize_counts"]["I"] >= 1
    assert "I" not in second["grade"]["prize_counts"]
    assert second["grade"]["prize_counts"]["II"] >= 1
    report = ledger.read_prospective_report(include_audit=True)
    assert report["active_evaluations"][0]["revision_id"] == second["revision_id"]
    assert report["superseded_evaluations"][0]["revision_id"] == first["revision_id"]
    assert second["supersedes_revision_id"] == first["revision_id"]


def test_normal_import_evaluates_only_pending_official_targets_without_new_csv_rows(monkeypatch, local, capsys):
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
        "joker", "2026-11-26", main, joker, (), official_prizes.ARCHIVE_URLS["joker"], supplementary=False,
    )
    monkeypatch.setattr(official_prizes, "parse_month_html", lambda *_, draw_dates, preserve_event_identity: [draw] if draw.draw_date in draw_dates else [])
    monkeypatch.setattr(update_csv, "GAME_CONFIGS", {})
    monkeypatch.setattr(update_csv, "_find_istoric_dir", lambda: local.parent)
    assert update_csv.update_all() == 0
    fetch.assert_called_once_with("joker", 2026, 11)
    grade = _read(local, "evaluations")[0]["grade"]
    assert grade["max_hits"] == 5 and grade["prize_counts"]["I"] >= 1
    assert _read(local, "predictions")[0]["game"] == "joker"
    assert "1 active" in capsys.readouterr().out
    clock[0] = _moment(hour=20, minute=1)
    corrected = replace(draw, joker=1 + joker % 20)
    official = Mock(return_value="corrected official page")
    monkeypatch.setattr(official_prizes, "_fetch_month_html", official)
    monkeypatch.setattr(official_prizes, "parse_month_html", lambda *_, **__: [corrected])
    assert update_csv.update_all() == 0
    assert "1 active" in capsys.readouterr().out
    active, = ledger.read_prospective_report()["active_evaluations"]
    assert active["joker"] == corrected.joker and "I" not in active["grade"]["prize_counts"]


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


def _register_observable_649(monkeypatch, local):
    clock = [_moment()]
    monkeypatch.setattr(ledger, "_clock", lambda: clock[0])
    plan = _plan()
    assert ledger.register_lr_recommendations([plan], registry_path=local)[0]
    clock[0] = _moment(hour=20)
    main = expand_plan(plan).variants[0][0]
    return official_prizes.PrizeDraw(
        "loto_6_49", "2026-11-26", main, None, (), official_prizes.ARCHIVE_URLS["loto_6_49"],
        supplementary=False,
    )


def test_rectification_supersedes_and_old_page_does_not_reactivate(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    corrected = replace(draw, numbers=tuple(n for n in range(1, 50) if n not in draw.numbers)[:6])
    assert ledger.append_official_draws([draw], archive_sha256=ARCHIVE_HASH, observed_at=_moment(hour=19)) == 1
    immutable_prediction, = _read(local, "predictions")
    original, = _read(local, "evaluations")
    assert ledger.append_official_draws([corrected], archive_sha256=HASH) == 1
    assert ledger.append_official_draws([draw], archive_sha256=ARCHIVE_HASH) == 0
    assert ledger.append_official_draws([corrected], archive_sha256=HASH) == 0
    result = ledger.read_prospective_report(registry_path=local, include_audit=True)
    active, = result["active_evaluations"]
    obsolete, = result["superseded_evaluations"]
    assert result["predictions_count"] == 1 and not result["conflicts"]
    assert active["event_id"] == obsolete["event_id"]
    assert active["revision"] == 2 and active["supersedes_revision_id"] == obsolete["revision_id"]
    assert not active["grade"]["hit6"] and obsolete["grade"]["hit6"]
    assert _read(local, "predictions") == [immutable_prediction]
    assert _read(local, "evaluations")[0] == original
    with sqlite3.connect(local) as db:
        for table in ("official_events", "official_event_keys", "official_revisions"):
            for statement in (
                f"DELETE FROM {table}",
                f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(statement)


def _official_649_html(rows):
    page = [
        '<select name="select-year"><option value="2026" selected>2026</option></select>',
        '<select name="select-month"><option value="11" selected>11</option></select>',
    ]
    payouts = "".join(
        f"<tr><td>{category} (6)</td><td>REPORT</td><td>-</td><td>-</td></tr>"
        for category in ("I", "II", "III")
    )
    for numbers, label, attrs in rows:
        images = "".join(f'<img src="/images/bile/{n}.png">' for n in numbers)
        page.append(
            f'<div class="rezultate-extrageri-content" {attrs}>'
            f'<div class="button-open-details">Detalii castiguri {label} la 6/49 din 26.11.2026</div>'
            f'<div class="numere-extrase">{images}</div>'
            f'<table class="results-table">{payouts}</table></div>'
        )
    return "".join(page)


def test_official_page_keeps_two_same_combination_events(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    html = _official_649_html([
        (draw.numbers, "EXTRAGERI SPECIALE", 'data-draw-id="loto-26-s" data-draw-order="2"'),
        (draw.numbers, "", 'data-extragere-id="loto-26-p" data-ordine-extragere="1"'),
    ])
    assert len(official_prizes.parse_month_html("6/49", html, 2026, 11)) == 1
    monkeypatch.setattr(official_prizes, "_fetch_month_html", lambda *_: html)
    assert ledger.refresh_official_evaluations() == (2, [])
    assert ledger.refresh_official_evaluations() == (0, [])
    results = ledger.read_prospective_report()["active_evaluations"]
    assert len({ev["event_id"] for ev in results}) == 2
    assert {ev["identity"]["supplementary"] for ev in results} == {False, True}
    assert {ev["identity"]["official_id"] for ev in results} == {"loto-26-p", "loto-26-s"}
    assert {ev["identity"]["draw_order"] for ev in results} == {1, 2}
    assert all(ev["grade"]["hit6"] and ev["revision"] == 1 for ev in results)


def test_same_result_roles_distinguish_events_without_source_ids(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    extras = replace(draw, supplementary=True)
    assert ledger.append_official_draws([extras, draw], archive_sha256=ARCHIVE_HASH) == 2
    assert ledger.append_official_draws([draw, extras], archive_sha256=ARCHIVE_HASH) == 0
    active = ledger.read_prospective_report()["active_evaluations"]
    assert len({row["event_id"] for row in active}) == 2


def test_new_verified_order_and_id_are_aliases_and_conflicting_page_is_audited(monkeypatch, local):
    observation = _register_observable_649(monkeypatch, local)
    assert ledger.append_official_draws([observation], archive_sha256=ARCHIVE_HASH, observed_at=_moment(hour=18)) == 1
    original, = ledger.read_prospective_report()["active_evaluations"]
    identified = replace(observation, official_id="verified", draw_order=1)
    assert ledger.append_official_draws([identified], archive_sha256=ARCHIVE_HASH, observed_at=_moment(hour=19)) == 0

    corrected = replace(identified, numbers=tuple(n for n in range(1, 50) if n not in identified.numbers)[:6])
    by_id = replace(corrected, draw_order=None, supplementary=None)
    by_role = replace(observation, official_id=None, draw_order=None)
    for _ in range(2):
        assert ledger.append_official_draws([by_id, by_role], archive_sha256=HASH) == 0
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["conflicts"]) == 2 and report["active_evaluations"] == [original]
    assert not report["superseded_evaluations"]
    assert ledger.append_official_draws([by_id], archive_sha256="d" * 64) == 1
    accepted, = ledger.read_prospective_report()["active_evaluations"]
    assert accepted["event_id"] == original["event_id"]


def test_unverified_role_conflict_does_not_guess_from_even_identical_numbers(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    html = _official_649_html([(draw.numbers, "EXTRAGERI SPECIALE", "")] * 2)
    monkeypatch.setattr(official_prizes, "_fetch_month_html", lambda *_: html)
    assert ledger.refresh_official_evaluations()[0] == 0
    assert ledger.refresh_official_evaluations()[1]
    report = ledger.read_prospective_report(include_audit=True)
    assert not report["active_evaluations"] and not report["event_revisions"]
    assert len(report["conflicts"]) == 1
    assert ledger.append_official_draws([replace(draw, supplementary=None)], archive_sha256=HASH) == 0
    assert len(ledger.read_prospective_report()["conflicts"]) == 2


def test_migration_keeps_legacy_rows_byte_exact_and_concurrent_writers_settle_once(monkeypatch, local):
    stamped = ledger._snapshot(_plan(), {}, _moment(), {}, {"git_sha": None, "source_sha256": HASH})
    prior_evaluation = '{ "date": "2026-11-26", "numbers": [1,2,3,4,5,6], "grade": {} }'
    prior_prediction = json.dumps(stamped, ensure_ascii=False)
    with sqlite3.connect(local) as db:
        db.executescript(ledger._BASE_SCHEMA)
        db.execute("INSERT INTO predictions VALUES (?, ?, ?, ?)",
                   (stamped["id"], stamped["game"], stamped["target_day"], prior_prediction))
        db.execute("INSERT INTO evaluations VALUES (?, ?, ?)",
                   (stamped["id"], "legacy-content-key", prior_evaluation))
    draw = official_prizes.PrizeDraw(
        "loto_6_49", "2026-11-26", (15, 17, 22, 24, 36, 38), None, (), official_prizes.ARCHIVE_URLS["loto_6_49"],
        supplementary=False,
    )
    monkeypatch.setattr(ledger, "_clock", lambda: _moment(year=2027, month=1, day=3, hour=20))
    html = _official_649_html([(draw.numbers, "", "")])
    fetch = Mock(return_value=html)
    monkeypatch.setattr(official_prizes, "_fetch_month_html", fetch)
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: ledger.refresh_official_evaluations(), range(2)))
    assert sorted(outcomes) == [(0, []), (1, [])]
    with sqlite3.connect(local) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert db.execute("SELECT payload FROM predictions").fetchone() == (prior_prediction,)
        assert db.execute("SELECT payload FROM evaluations WHERE draw_id='legacy-content-key'").fetchone() == (prior_evaluation,)
    fetch.reset_mock()
    assert ledger.refresh_official_evaluations() == (0, []) and not fetch.called
    assert ledger.refresh_official_evaluations(include_settled=True) == (0, [])
    fetch.assert_called_once_with("loto_6_49", 2026, 11)
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["active_evaluations"]) == len(report["legacy_evaluations"]) == 1
    assert report["legacy_pending_count"] == 1


def test_error_between_two_official_events_rolls_back_all_writes(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    grader = ledger.grade_draw

    def fail_second(portfolio, numbers, joker, **kwargs):
        if numbers[0] == 1:
            raise RuntimeError("interrupted")
        return grader(portfolio, numbers, joker, **kwargs)

    if draw.numbers[0] == 1:
        draw = replace(draw, numbers=(4, 9, 10, 17, 19, 29))
    monkeypatch.setattr(ledger, "grade_draw", fail_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        ledger.append_official_draws(
            [draw, replace(draw, numbers=(1, 2, 3, 4, 5, 6), supplementary=True)],
            archive_sha256=ARCHIVE_HASH,
        )
    report = ledger.read_prospective_report(include_audit=True)
    assert report["predictions_count"] == 1 and not report["active_evaluations"] and not report["event_revisions"]
    assert not report["event_observations"]


def test_concurrent_observed_old_page_cannot_supersede_current_revision(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    old_page = replace(draw, numbers=(8, 9, 10, 11, 12, 13))
    current = replace(draw, numbers=(4, 7, 16, 28, 40, 48))
    assert ledger.append_official_draws([draw], archive_sha256=ARCHIVE_HASH, observed_at=_moment(hour=17)) == 1
    assert ledger.append_official_draws([current], archive_sha256=HASH, observed_at=_moment(hour=19)) == 1
    assert ledger.append_official_draws([old_page], archive_sha256="e" * 64, observed_at=_moment(hour=18)) == 0
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["event_revisions"]) == 2 and len(report["conflicts"]) == 1
    assert report["active_evaluations"][0]["numbers"] == list(current.numbers)


@pytest.mark.parametrize("identical_supplement", [False, True])
def test_legacy_offline_report_and_links_from_exact_old_archive(monkeypatch, local, identical_supplement):
    stamped = ledger._snapshot(_plan(), {}, _moment(), {}, {"git_sha": None, "source_sha256": HASH})
    numbers = (9, 14, 21, 22, 40, 45)
    rows = [(numbers, "", "")]
    if identical_supplement:
        rows.append((numbers, "EXTRAGERI SPECIALE", ""))
    html = _official_649_html(rows)
    prior_evaluation = json.dumps({
        "numbers": numbers, "joker": None, "official_archive_sha256": sha256(html.encode()).hexdigest(),
    })
    with sqlite3.connect(local) as db:
        db.executescript(ledger._BASE_SCHEMA)
        db.execute("INSERT INTO predictions VALUES (?, ?, ?, ?)",
                   (stamped["id"], stamped["game"], stamped["target_day"], json.dumps(stamped)))
        db.execute("INSERT INTO evaluations VALUES (?, ?, ?)",
                   (stamped["id"], "old-balls-only", prior_evaluation))
    assert ledger.read_prospective_report() == {
        "predictions_count": 1, "active_evaluations": [], "conflicts": [], "legacy_pending_count": 1,
    }
    monkeypatch.setattr(ledger, "_clock", lambda: _moment(hour=20))
    monkeypatch.setattr(official_prizes, "_fetch_month_html", lambda *_: html)
    assert ledger.refresh_official_evaluations() == (len(rows), [])
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["active_evaluations"]) == len(rows)
    assert report["legacy_pending_count"] == int(identical_supplement)
    legacy, = report["legacy_evaluations"]
    assert legacy["verified_revision_id"] == (
        None if identical_supplement else report["active_evaluations"][0]["revision_id"]
    )
    with sqlite3.connect(local) as db:
        assert db.execute("SELECT payload FROM evaluations WHERE draw_id='old-balls-only'").fetchone() == (prior_evaluation,)


def test_legacy_page_cannot_overwrite_the_first_verified_corrected_event(monkeypatch, local):
    stamped = ledger._snapshot(_plan(), {}, _moment(), {}, {"git_sha": None, "source_sha256": HASH})
    balls = (9, 14, 21, 22, 40, 45)
    old_html = _official_649_html([(balls, "", "")])
    corrected_html = _official_649_html([((1, 3, 6, 13, 27, 49), "", "")])
    with sqlite3.connect(local) as db:
        db.executescript(ledger._BASE_SCHEMA)
        db.execute("INSERT INTO predictions VALUES (?, ?, ?, ?)",
                   (stamped["id"], stamped["game"], stamped["target_day"], json.dumps(stamped)))
        db.execute("INSERT INTO evaluations VALUES (?, ?, ?)", (stamped["id"], "old-balls-only", json.dumps({
            "numbers": balls, "joker": None, "official_archive_sha256": sha256(old_html.encode()).hexdigest(),
        })))
    monkeypatch.setattr(ledger, "_clock", lambda: _moment(hour=20))
    monkeypatch.setattr(official_prizes, "_fetch_month_html", Mock(side_effect=[corrected_html, old_html]))
    assert ledger.refresh_official_evaluations() == (1, [])
    accepted = ledger.read_prospective_report()["active_evaluations"]
    count, problems = ledger.refresh_official_evaluations()
    assert not count and problems
    report = ledger.read_prospective_report(include_audit=True)
    assert report["active_evaluations"] == accepted and report["legacy_pending_count"] == 1
    assert len(report["event_revisions"]) == len(report["conflicts"]) == 1


def test_verified_supplement_orders_remain_distinct_and_role_alone_becomes_ambiguous(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    ordered = [replace(draw, supplementary=True, draw_order=order) for order in (2, 3)]
    assert ledger.append_official_draws(ordered, archive_sha256=ARCHIVE_HASH) == 2
    assert ledger.append_official_draws(list(reversed(ordered)), archive_sha256=ARCHIVE_HASH) == 0
    changed = replace(draw, supplementary=True, numbers=(2, 4, 13, 19, 24, 29))
    assert ledger.append_official_draws([changed], archive_sha256=HASH) == 0
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["active_evaluations"]) == 2 and len(report["conflicts"]) == 1
    assert not report["superseded_evaluations"]


def test_id_only_and_role_only_need_common_evidence_before_being_counted_as_separate(monkeypatch, local):
    first = _register_observable_649(monkeypatch, local)
    id_only = replace(first, supplementary=None, official_id="new-id", numbers=(3, 4, 6, 8, 19, 43))
    assert ledger.append_official_draws([first, id_only], archive_sha256=ARCHIVE_HASH) == 0
    assert ledger.append_official_draws([first], archive_sha256=HASH, observed_at=_moment(hour=18)) == 1
    assert ledger.append_official_draws([id_only], archive_sha256="e" * 64) == 0
    assert len(ledger.read_prospective_report()["active_evaluations"]) == 1
    enriched = replace(id_only, supplementary=False)
    assert ledger.append_official_draws([enriched], archive_sha256="d" * 64) == 1
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["active_evaluations"]) == len(report["superseded_evaluations"]) == 1


@pytest.mark.parametrize("identity", [{}, {"official_id": "principal-26"}, {"draw_order": 1}])
def test_repeated_principal_is_idempotent_but_conflicting_results_are_rejected(monkeypatch, local, identity):
    draw = replace(_register_observable_649(monkeypatch, local), **identity)
    repeated = replace(draw, numbers=tuple(reversed(draw.numbers)))
    assert ledger.append_official_draws([draw, repeated], archive_sha256=ARCHIVE_HASH) == 1
    changed = replace(draw, numbers=(3, 4, 6, 8, 19, 43))
    assert ledger.append_official_draws([draw, changed], archive_sha256=HASH) == 0
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["active_evaluations"]) == 1 and len(report["conflicts"]) == 2
    assert not report["superseded_evaluations"]


def _ingest(draw, page, hour, minute=0):
    problems = []
    added = ledger.append_official_draws(
        [draw], archive_sha256=sha256(page.encode()).hexdigest(),
        observed_at=_moment(hour=hour, minute=minute), problems=problems,
    )
    return added, problems


def _other_result(draw):
    return replace(draw, numbers=tuple(n for n in range(1, 50) if n not in draw.numbers)[:6])


def test_delayed_result_cannot_displace_latest_reconfirmation(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    assert _ingest(draw, "A", 17) == (1, [])
    assert _ingest(draw, "A again", 20) == (0, [])
    assert _ingest(draw, "A again", 20) == (0, [])
    changed = _other_result(draw)
    for hour in (19, 20):
        count, problems = _ingest(changed, "old B", hour)
        assert count == 0 and problems
    report = ledger.read_prospective_report(include_audit=True)
    assert [r["numbers"] for r in report["active_evaluations"]] == [list(draw.numbers)]
    assert len(report["event_revisions"]) == 1
    assert [r["disposition"] for r in report["event_observations"]] == [
        "revision", "confirmation", "conflict", "conflict",
    ]
    confirm = report["event_observations"][1]
    assert confirm["observed_at"] == _moment(hour=20).isoformat()
    assert all(r["event_id"] == confirm["event_id"] for r in report["conflicts"])
    with sqlite3.connect(local) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO official_observations SELECT * FROM official_observations")


def test_return_needs_ordered_official_edition_and_retains_original_score(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    one = replace(draw, result_version=10)
    two = replace(_other_result(draw), result_version=11)
    assert _ingest(one, "v10", 17) == (1, [])
    assert _ingest(two, "v11", 18) == (1, [])
    assert _ingest(one, "v10", 19) == (0, [])
    for dubious in (draw, replace(draw, result_version=11), replace(draw, result_version=9)):
        added, errors = _ingest(dubious, "unverified" + str(dubious.result_version), 19)
        assert added == 0 and errors
    assert _ingest(replace(draw, result_version=12), "v12", 19) == (1, [])
    assert _ingest(replace(draw, result_version=12), "v12", 19) == (0, [])
    # Old page with the same balls as the active revision must not raise its watermark.
    assert _ingest(one, "v10", 20) == (0, [])
    audit = ledger.read_prospective_report(include_audit=True)
    first, second, third = audit["event_revisions"]
    assert [r["revision"] for r in (first, second, third)] == [1, 2, 3]
    assert first["content_sha256"] == third["content_sha256"] != second["content_sha256"]
    assert third["revision_id"] != first["revision_id"]
    assert third["supersedes_revision_id"] == second["revision_id"]
    assert second["supersedes_revision_id"] == first["revision_id"]
    active, = audit["active_evaluations"]
    assert active["revision_id"] == third["revision_id"] and active["grade"]["hit6"]
    assert [e["grade"]["hit6"] for e in audit["superseded_evaluations"]] == [True, False]
    assert len(audit["conflicts"]) == 3 and audit["event_observations"][-1]["disposition"] == "historical"
    assert _ingest(replace(two, result_version=13), "v13", 19, 30) == (1, [])


def test_source_without_version_on_return_records_ambiguity_and_equal_time_conflict(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    second = _other_result(draw)
    assert _ingest(draw, "initial", 17) == (1, [])
    assert _ingest(second, "corrected", 19) == (1, [])
    assert _ingest(draw, "another monthly page", 20)[1]
    fourth = replace(draw, numbers=(9, 10, 11, 12, 13, 14))
    assert _ingest(fourth, "simultaneous", 19)[1]
    audit = ledger.read_prospective_report(include_audit=True)
    assert [r["numbers"] for r in audit["active_evaluations"]] == [list(second.numbers)]
    assert len(audit["event_revisions"]) == len(audit["conflicts"]) == 2


def test_parser_uses_only_explicit_result_version(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    def parse(attrs):
        return official_prizes.parse_month_html("6/49", _official_649_html([(draw.numbers, "", attrs)]),
                                                2026, 11, preserve_event_identity=True)[0]
    assert parse('data-draw-order="2"').result_version is None
    assert parse('data-draw-order="2" data-result-version="4"').result_version == 4
    with pytest.raises(official_prizes.ArchiveError, match="version"):
        parse('data-result-version="next"')


def test_concurrent_old_page_and_reconfirmations_do_not_regress_or_double_score(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    assert _ingest(draw, "first", 17) == (1, [])
    assert _ingest(draw, "confirmation", 19) == (0, [])
    with ThreadPoolExecutor(max_workers=6) as executor:
        calls = [(draw, "new confirmation", 20), (_other_result(draw), "older", 18)] * 3
        results = list(executor.map(lambda args: _ingest(*args), calls))
    assert [n for n, _ in results] == [0] * 6
    assert [bool(errors) for _, errors in results] == [False, True] * 3
    audit = ledger.read_prospective_report(include_audit=True)
    assert len(audit["active_evaluations"]) == len(audit["event_revisions"]) == len(audit["conflicts"]) == 1
    assert len(audit["event_observations"]) == 4


def test_observation_migration_rolls_back_and_concurrent_reopen_keeps_bytes(monkeypatch, local):
    # Minimal v2 database: the original revision ID and its legacy FK are live.
    draw = _register_observable_649(monkeypatch, local)
    v2 = local.with_name("schema2.sqlite3")
    stamped = _read(local, "predictions")[0]
    page = sha256(b"old official page").hexdigest()
    content = ledger._result_fingerprint("6/49", draw.numbers, None)
    source = {"game": "6/49", "date": "2026-11-26", "numbers": draw.numbers, "joker": None,
              "identity": {"official_id": None, "draw_order": None, "supplementary": False},
              "official_url": draw.source_url, "official_archive_sha256": page, "content_sha256": content,
              "revision_id": "v2-r1", "event_id": "old-event", "revision": 1,
              "supersedes_revision_id": None, "observed_at": _moment(hour=17).isoformat()}
    raw = json.dumps(source, indent=2)
    with sqlite3.connect(v2) as db:
        db.executescript(ledger._BASE_SCHEMA)
        db.executescript("""
            CREATE TABLE official_events (id TEXT PRIMARY KEY, game TEXT NOT NULL, draw_date TEXT NOT NULL);
            INSERT INTO official_events VALUES ('old-event', '6/49', '2026-11-26');
            CREATE TABLE official_event_keys (key TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES official_events(id),
                kind TEXT NOT NULL, PRIMARY KEY(key, event_id));
            CREATE TABLE official_revisions (id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES official_events(id),
                sequence INTEGER NOT NULL, content_sha256 TEXT NOT NULL, supersedes TEXT REFERENCES official_revisions(id),
                observed_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(event_id, sequence), UNIQUE(event_id, content_sha256));
            CREATE TABLE legacy_evaluation_links (prediction_id TEXT NOT NULL, legacy_draw_id TEXT NOT NULL,
                revision_id TEXT NOT NULL REFERENCES official_revisions(id), PRIMARY KEY(prediction_id, legacy_draw_id),
                FOREIGN KEY(prediction_id, legacy_draw_id) REFERENCES evaluations(prediction_id, draw_id));
            PRAGMA user_version=2;
        """)
        db.execute("INSERT INTO official_event_keys VALUES (?, 'old-event', 'role')",
                   (ledger._hash(["role", "6/49", draw.draw_date, False]),))
        db.execute("INSERT INTO official_revisions VALUES ('v2-r1','old-event',1,?,NULL,?,?)",
                   (content, source["observed_at"], raw))
        db.execute("INSERT INTO predictions VALUES (?,?,?,?)",
                   (stamped["id"], "6/49", draw.draw_date, json.dumps(stamped)))
        for revision in ("legacy", "v2-r1"):
            db.execute("INSERT INTO evaluations VALUES (?,?,?)", (stamped["id"], revision, raw))
        db.execute("INSERT INTO legacy_evaluation_links VALUES (?,'legacy','v2-r1')", (stamped["id"],))
    with monkeypatch.context() as context:
        context.setattr(ledger, "_store_observation", Mock(side_effect=RuntimeError("power loss")))
        with pytest.raises(RuntimeError, match="power loss"):
            ledger.read_prospective_report(registry_path=v2)
    with sqlite3.connect(v2) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT payload FROM official_revisions").fetchone()[0] == raw
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='official_observations'").fetchone() is None
    def reopen(_):
        return ledger.read_prospective_report(registry_path=v2, include_audit=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reopen, range(6)))
    assert all(r == results[0] for r in results)
    assert len(results[0]["event_observations"]) == len(results[0]["active_evaluations"]) == 1
    assert results[0]["legacy_evaluations"][0]["verified_revision_id"] == "v2-r1"
    with sqlite3.connect(v2) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT payload FROM official_revisions").fetchone()[0] == raw
        assert db.execute("SELECT payload FROM evaluations").fetchall() == [(raw,), (raw,)]

def test_registry_records_portfolio_refinement_when_available(monkeypatch, local):
    monkeypatch.setattr(ledger, "_clock", _moment)
    plan = _plan()
    audit = {"complete": False, "reason": "temporary", "recipes": ["r-1"]}
    assert ledger.register_lr_recommendations([plan])[0]
    plan["portfolio_refinement"] = audit
    assert ledger.register_lr_recommendations([plan])[0]
    before, after = _read(local, "predictions")
    assert "portfolio_refinement" not in before
    assert after["portfolio_refinement"] == audit and after["id"] != before["id"]


def test_inconsistent_numbers_from_identical_archive_conflict(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    assert _ingest(draw, "same archive", 17) == (1, [])
    count, errors = _ingest(_other_result(draw), "same archive", 18)
    assert count == 0 and errors
    report = ledger.read_prospective_report(include_audit=True)
    assert len(report["conflicts"]) == len(report["event_revisions"]) == 1
    assert report["active_evaluations"][0]["numbers"] == list(draw.numbers)

def test_simultaneous_observations_ordered_by_explicit_source_revision(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    for n, result in enumerate([draw, _other_result(draw), draw], start=1):
        assert _ingest(replace(result, result_version=n), str(n), 17) == (1, [])
    audit = ledger.read_prospective_report(include_audit=True)
    assert len(audit["active_evaluations"]) == 1
    assert [r["source_version"] for r in audit["event_revisions"]] == [1, 2, 3]


def test_known_superseded_bytes_remain_historical_after_source_metadata_enrichment(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    assert _ingest(draw, "old bytes", 17) == (1, [])
    assert _ingest(_other_result(draw), "corrected", 18) == (1, [])
    assert _ingest(replace(draw, result_version=21), "old bytes", 20) == (0, [])
    audit = ledger.read_prospective_report(include_audit=True)
    assert not audit["conflicts"] and len(audit["event_revisions"]) == 2
    assert audit["event_observations"][-1]["disposition"] == "historical"

def test_newly_confirmed_edition_can_resolve_prior_conflict_and_advance_watermark(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    second = _other_result(draw)
    first_return = replace(draw, result_version=12)
    assert _ingest(draw, "first", 17) == (1, [])
    assert _ingest(second, "second", 18) == (1, [])
    assert _ingest(first_return, "return", 20)[1]
    assert _ingest(replace(second, result_version=11), "second", 19) == (0, [])
    assert _ingest(first_return, "return", 20) == (1, [])
    changed = replace(draw, numbers=(8, 9, 10, 11, 12, 13))
    assert _ingest(changed, "delayed", 19, 30)[1]
    audit = ledger.read_prospective_report(include_audit=True)
    assert len(audit["event_revisions"]) == 3
    observations = [r for r in audit["event_observations"] if r.get("source_version") == 12]
    assert [r["disposition"] for r in observations] == ["conflict", "revision"]
    assert audit["active_evaluations"][0]["source_version"] == 12

def test_replaying_known_edition_after_numbered_reconfirmation_keeps_watermark(monkeypatch, local):
    draw = _register_observable_649(monkeypatch, local)
    assert _ingest(replace(draw, result_version=1), "page A1", 17) == (1, [])
    assert _ingest(replace(draw, result_version=2), "page A2", 18) == (0, [])
    assert _ingest(replace(draw, result_version=1), "page A1", 19) == (0, [])
    audit = ledger.read_prospective_report(include_audit=True)
    assert [r["disposition"] for r in audit["event_observations"]] == ["revision", "confirmation", "historical"]
    assert not audit["conflicts"]
    assert _ingest(_other_result(draw), "new result", 18, 30) == (1, [])
