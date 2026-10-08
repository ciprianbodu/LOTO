"""verifica_istoric.py: validarea CSV-urilor inainte de commit-ul automat.

`scripts/launcher_git.ps1 -Mode PushHistory` o ruleaza pe fisierele care au
trecut de verificarea git (numai randuri adaugate). Un motiv = fisier refuzat,
ramas local; de aceea istoricele versionate trebuie sa treaca toate.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from loto_enterprise.core.lotteries import GAMES
from verifica_istoric import check_history, main

ROOT = Path(__file__).resolve().parent
L649 = "_ISTORIC/loto_6_49.csv"
JOKER = "_ISTORIC/joker.csv"
STARS = "_ISTORIC/externe/euromillions_5din50_stele.csv"
BASE = {
    L649: "date,n1,n2,n3,n4,n5,n6\n01-10-2026,1,2,3,4,5,6\n04-10-2026,7,8,9,10,11,12\n",
    JOKER: "date,n1,n2,n3,n4,n5,joker\n02-10-2026,1,2,3,4,5,20\n",
    STARS: "date,n1,n2,n3,n4,n5,s1,s2\n29-09-2026,4,31,12,44,7,8,12\n",
}
NOT_REGISTERED = "nu e istoricul unui joc din registrul loteriilor"


def _history(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return name


@pytest.mark.parametrize("lottery", GAMES, ids=[lot.game_id for lot in GAMES])
def test_versioned_histories_pass(lottery):
    """Un istoric respins aici ar bloca orice commit automat al fisierului."""
    assert check_history(lottery.csv, ROOT) is None


@pytest.mark.parametrize(
    "name, row",
    [
        (L649, "05-10-2026,49,1,13,22,30,41\n"),
        (JOKER, "05-10-2026,45,1,13,22,30,1\n"),
        (STARS, "03-10-2026,50,1,13,22,30,1,2\n"),
        # Aceleasi numere ca un rand mai vechi, dar nu ca cel anterior.
        (L649, "05-10-2026,1,2,3,4,5,6\n"),
        # A doua extragere a aceleiasi zile.
        (L649, "04-10-2026,13,14,15,16,17,18\n"),
    ],
)
def test_appended_valid_rows_pass(tmp_path, name, row):
    assert check_history(_history(tmp_path, name, BASE[name] + row), tmp_path) is None


@pytest.mark.parametrize(
    "name, row, reason",
    [
        (
            L649,
            "10.09.2026;1;2;3;4;5;6\n",
            "randul 4: astept 7 valori separate prin virgula, am gasit 1",
        ),
        (
            L649,
            "05-10-2026,1,2,3,4,5\n",
            "randul 4: astept 7 valori separate prin virgula, am gasit 6",
        ),
        (L649, "10.09.2026,1,2,3,4,5,6\n", "randul 4: data nu e ZZ-LL-AAAA"),
        (L649, "05-10-2026,1,2,3,4,5,50\n", "randul 4: numere invalide pentru 6/49"),
        (L649, "05-10-2026,1,2,3,4,5,5\n", "randul 4: numere invalide pentru 6/49"),
        (L649, "05-10-2026,1,2,3,4,5,6.5\n", "randul 4: numere invalide pentru 6/49"),
        (L649, "05-10-2026,1,2,3,4,5,x\n", "randul 4: numere invalide pentru 6/49"),
        (JOKER, "05-10-2026,1,2,3,4,5,21\n", "randul 3: numere invalide pentru joker"),
        (STARS, "03-10-2026,1,2,3,4,5,7,7\n", "randul 3: numere invalide pentru 5/50"),
        # §4.1: aceleasi numere principale ca randul anterior, in orice ordine.
        (L649, "05-10-2026,12,11,10,9,8,7\n", "randul 4 repeta numerele randului 3"),
        (JOKER, "05-10-2026,5,4,3,2,1,7\n", "randul 3 repeta numerele randului 2"),
        # Anul tastat gresit: ar deveni ultima extragere pe toate statiile.
        (L649, "27-09-2062,6,7,18,43,40,22\n", "randul 4: data e in viitor"),
        # strptime accepta ziua fara zero; actualizatoarele compara data scrisa.
        (L649, "5-10-2026,13,14,15,16,17,18\n", "randul 4: data nu e ZZ-LL-AAAA"),
        (
            L649,
            "03-10-2026,13,14,15,16,17,18\n",
            "randul 4: data e inaintea randului 3 (04-10-2026)",
        ),
    ],
)
def test_malformed_appended_row_is_refused_with_its_line(tmp_path, name, row, reason):
    name = _history(tmp_path, name, BASE[name] + row)
    assert reason in check_history(name, tmp_path)


def test_dates_may_reach_tomorrow_but_not_later(tmp_path):
    """O zi de toleranta pentru ceasul statiei, ca update_externe."""
    name = _history(tmp_path, L649, BASE[L649] + "05-10-2026,13,14,15,16,17,18\n")
    assert check_history(name, tmp_path, today=dt.date(2026, 10, 4)) is None
    got = check_history(name, tmp_path, today=dt.date(2026, 10, 3))
    assert got.startswith("randul 4: data e in viitor: 05-10-2026")


def test_blank_lines_are_ignored_like_the_history_loader(tmp_path):
    name = _history(tmp_path, L649, BASE[L649] + "\n05-10-2026,1,2,3,4,5,7\n")
    assert check_history(name, tmp_path) is None


@pytest.mark.parametrize(
    "text, reason",
    [
        ("﻿" + BASE[L649], "antet \\ufeffdate"),
        (BASE[L649].replace(",", ";"), "antet date;n1"),
        ("", "antet lipsa"),
    ],
)
def test_header_must_match_the_geometry(tmp_path, text, reason):
    got = check_history(_history(tmp_path, L649, text), tmp_path)
    assert got.encode("ascii", "backslashreplace").decode().startswith(reason)


def test_unregistered_missing_and_non_utf8_files_are_refused(tmp_path):
    copy = _history(tmp_path, "_ISTORIC/loto_6_49 (1).csv", BASE[L649])
    assert check_history(copy, tmp_path) == NOT_REGISTERED
    assert check_history(L649, tmp_path).startswith("nu se poate citi")
    (tmp_path / L649).write_bytes(BASE[L649].encode("utf-16"))
    assert check_history(L649, tmp_path) == "nu e text UTF-8"


def test_main_prints_one_ascii_line_per_argument_in_order(
    tmp_path, monkeypatch, capsys
):
    _history(tmp_path, L649, BASE[L649] + "05-10-2026,1,2,3,4,5,ș\n")
    _history(tmp_path, JOKER, BASE[JOKER])
    monkeypatch.chdir(tmp_path)
    assert main([JOKER, L649, "_ISTORIC/externe/README.md"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ok"
    assert lines[1].startswith("randul 4: numere invalide pentru 6/49")
    assert lines[1].isascii() and "\\u0219" in lines[1]
    assert lines[2] == NOT_REGISTERED
    assert len(lines) == 3
