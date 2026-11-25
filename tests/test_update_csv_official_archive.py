"""Verifică în izolare completarea arhivei recente cu rezultatele oficiale."""
from datetime import date
from types import SimpleNamespace

import pytest

import update_csv as updater
from loto_enterprise.core import official_prizes


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 25)


def run_update(tmp_path, monkeypatch, game, stored, recent, official):
    histories = {
        "loto_6_49": ("22-11-2026,44,23,12,37,19,18",),
        "joker": ("22-11-2026,14,15,29,18,25,20",),
        "loto_5_40": ("22-11-2026,37,12,9,20,21,6",),
    }
    histories[game] = stored
    pages = {
        "loto_6_49": "2026-11-22 44 23 12 37 19 18",
        "joker": "2026-11-22 14 15 29 18 25 + 20",
        "loto_5_40": "2026-11-22 37 12 9 20 21 6",
    }
    pages[game] = recent
    for key, rows in histories.items():
        config = updater.GAME_CONFIGS[key]
        tail = "joker" if config["has_joker"] else "n6"
        csv_path = tmp_path / config["csv_name"]
        csv_path.write_text("\n".join((f"date,n1,n2,n3,n4,n5,{tail}", *rows, "")), encoding="utf-8")
    target = tmp_path / updater.GAME_CONFIGS[game]["csv_name"]
    before = target.read_bytes()
    monkeypatch.setattr(updater, "date", FixedDate)
    monkeypatch.setattr(updater, "_find_istoric_dir", lambda: tmp_path)
    monkeypatch.setattr(updater, "_get_page_text", {
        config["recent_url"]: pages[key] for key, config in updater.GAME_CONFIGS.items()
    }.__getitem__)

    def fetch(key, year, month, *, timeout):
        assert (key, year, month, timeout) == (game, 2026, 11, updater.TIMEOUT_S)
        return official if isinstance(official, str) else "<html></html>"

    def parse(key, html, year, month):
        assert (key, html, year, month) == (game, "<html></html>", 2026, 11)
        return [SimpleNamespace(draw_date=day, numbers=numbers, joker=joker) for day, numbers, joker in official]

    monkeypatch.setattr(official_prizes, "_fetch_month_html", fetch)
    if not isinstance(official, str):
        monkeypatch.setattr(official_prizes, "parse_month_html", parse)
    return updater.update_all(), target, before


@pytest.mark.parametrize("stored_latest", [False, True])
def test_joker_official_archive_adds_or_confirms_latest_with_joker_ball(tmp_path, monkeypatch, capsys, stored_latest):
    final_row = "22-11-2026,14,15,29,18,25,20"
    stored = ["19-11-2026,44,7,18,15,16,06"]
    if stored_latest:
        stored.append(final_row)
    official = [
        ("2026-11-19", (44, 7, 18, 15, 16), 6),
        ("2026-11-22", (14, 15, 29, 18, 25), 20),
    ]
    count, target, before = run_update(
        tmp_path, monkeypatch, "joker", stored, "2026-11-19 44 7 18 15 16 + 6", official,
    )
    assert count == (0 if stored_latest else 1)
    assert target.read_text(encoding="utf-8").splitlines()[1:] == [
        "19-11-2026,44,7,18,15,16,06", final_row,
    ]
    if stored_latest:
        assert target.read_bytes() == before
    status = capsys.readouterr().out
    assert "loto.ro confirmă până la 22-11-2026" in status
    assert "NEACTUALIZATE" not in status


def test_joker_official_archive_refuses_conflicting_joker_ball_in_overlap(tmp_path, monkeypatch, capsys):
    official = [
        ("2026-11-19", (44, 7, 18, 15, 16), 7),
        ("2026-11-22", (14, 15, 29, 18, 25), 20),
    ]
    count, target, before = run_update(
        tmp_path, monkeypatch, "joker", ["19-11-2026,44,7,18,15,16,6"],
        "2026-11-19 44 7 18 15 16 + 6", official,
    )
    assert count == 0 and target.read_bytes() == before
    status = capsys.readouterr().out
    assert "diferă" in status and "+ 6" in status and "+ 7" in status


@pytest.mark.parametrize(("day", "reason"), [
    (22, None),
    (21, "loto.ro nu confirmă o extragere din 21-11-2026"),
    (19, "arhiva secundară loto49.ro"),
    (26, "arhiva secundară loto49.ro"),
])
def test_540_invalid_secondary_row_requires_a_valid_official_replacement(tmp_path, monkeypatch, capsys, day, reason):
    recent = f"2026-11-19 37 9 19 20 33 17 2026-11-{day} 37 12 9 20 21 9"
    official = [
        ("2026-11-19", (37, 9, 19, 20, 33, 17), None),
        ("2026-11-22", (37, 12, 9, 20, 21, 6), None),
    ]
    count, target, before = run_update(
        tmp_path, monkeypatch, "loto_5_40", ["19-11-2026,37,9,19,20,33,17"], recent, official,
    )
    status = capsys.readouterr().out
    if reason is None:
        assert count == 1
        assert target.read_text(encoding="utf-8").splitlines()[-1] == "22-11-2026,37,12,9,20,21,6"
        assert "NEACTUALIZATE" not in status
    else:
        assert count == 0 and target.read_bytes() == before
        assert reason in status and "NEACTUALIZATE: Loto 5/40" in status


@pytest.mark.parametrize(("stored_latest", "count_expected"), [
    ("22-11-2026,12,37,20,9,21,06", 1),
    ("22-11-2026,6,12,9,20,21,37", 0),
])
def test_540_official_tail_checks_sixth_ball_without_reordering_first_five(
    tmp_path, monkeypatch, capsys, stored_latest, count_expected,
):
    official = [
        ("2026-11-19", (37, 9, 19, 20, 33, 17), None),
        ("2026-11-22", (37, 12, 9, 20, 21, 6), None),
        ("2026-11-23", (1, 2, 3, 4, 5, 6), None),
    ]
    stored = ["19-11-2026,9,37,19,20,33,17", stored_latest]
    recent = "2026-11-19 37 9 19 20 33 17 2026-11-22 37 12 9 20 21 9"
    count, target, before = run_update(tmp_path, monkeypatch, "loto_5_40", stored, recent, official)
    status = capsys.readouterr().out
    assert count == count_expected
    if count_expected:
        assert target.read_text(encoding="utf-8").splitlines()[1:] == [*stored, "23-11-2026,1,2,3,4,5,6"]
        assert "NEACTUALIZATE" not in status
    else:
        assert target.read_bytes() == before
        assert "a șasea bilă este 37 în CSV și 6 la loto.ro" in status



def _draw_block(heading, day, main, joker=None, *, supplementary=False):
    marker = "EXTRAGERI SPECIALE " if supplementary else ""
    balls = "".join(f'<img src="/images/bile/{number}.png">' for number in main)
    if joker is not None:
        balls += f'<img src="/images/bile/joker/{joker}.png">'
    labels = (
        ("I (5/5+J)", "II (5/5)", "III (4/5+J)", "IV (4/5)",
         "V (3/5+J)", "VI (3/5)", "VII (2/5+J)", "VIII (1/5+J)")
        if joker is not None else ("I (5/5)", "II (5/6)", "III (4/6)")
    )
    categories = "".join(
        f"<tr><td>{label}</td><td>1</td><td>10,00</td><td></td></tr>"
        for label in labels
    )
    return (
        '<div class="rezultate-extrageri-content">'
        f'<div class="button-open-details">Detalii câștiguri {marker}la {heading} din {day}</div>'
        f'<div class="numere-extrase">{balls}</div>'
        f'<table class="results-table">{categories}</table></div>'
    )


@pytest.mark.parametrize("game", ["loto_5_40", "joker"])
@pytest.mark.parametrize("stored_main", [False, True])
def test_official_archive_saves_main_before_supplement(tmp_path, monkeypatch, game, stored_main):
    heading = "joker" if game == "joker" else "5/40"
    previous, main, supplement = (1, 3, 5, 7, 9, 11), (2, 4, 6, 8, 10, 12), (13, 15, 17, 19, 21, 23)
    if game == "joker":
        previous, main, supplement = previous[:5], main[:5], supplement[:5]
        jokers = (5, 17, 17)
    else:
        jokers = (None, None, None)

    def csv_row(day, values, joker):
        return ",".join(map(str, (day, *values, *((joker,) if joker is not None else ()))))

    prior_row = csv_row("19-11-2026", previous, jokers[0])
    main_row = csv_row("22-11-2026", main, jokers[1])
    supplement_row = csv_row("22-11-2026", supplement, jokers[2])
    stored = [prior_row, main_row] if stored_main else [prior_row]
    recent = "2026-11-19 " + " ".join(map(str, previous))
    if jokers[0] is not None:
        recent += f" + {jokers[0]}"
    html = (
        '<select name="select-year"><option value="2026" selected>2026</option></select>'
        '<select name="select-month"><option value="11" selected>Noiembrie</option></select>'
        + _draw_block(heading, "22.11.2026", supplement, jokers[2], supplementary=True)
        + _draw_block(heading, "22.11.2026", main, jokers[1])
        + _draw_block(heading, "19.11.2026", previous, jokers[0])
    )

    count, target, _ = run_update(tmp_path, monkeypatch, game, stored, recent, html)

    assert count == (1 if stored_main else 2)
    assert target.read_text("utf-8").splitlines()[1:] == [prior_row, main_row, supplement_row]
    current = target.read_bytes()
    assert updater.update_all() == 0
    assert target.read_bytes() == current
