"""update_externe.py: actualizarea istoricelor din _ISTORIC/externe/.

Fără rețea: `_http_get` este înlocuit cu răspunsuri înregistrate (format real,
prescurtat). Fiecare test lucrează pe o copie în tmp_path.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

import update_externe as ue
from loto_enterprise.core.lotteries import GAMES_BY_ID

ROOT = Path(__file__).resolve().parent
TODAY = dt.date(2026, 9, 27)

AT_HEADER = "date,n1,n2,n3,n4,n5,n6\n"
AT_ROWS = [
    "18-09-2026,5,6,12,35,38,39\n",
    "20-09-2026,3,4,15,31,32,41\n",
    "23-09-2026,2,14,15,17,28,37\n",
]


def _at_draw(no, date, nums):
    return {
        "drawNo": no,
        "drawDate": date,
        "results": [
            {"resultType": "LOTTO_NUMBER", "resultValues": [{"number": n} for n in nums]},
            {"resultType": "LOTTO_BONUS_NUMBER", "resultValues": [{"number": 44}]},
        ],
    }


def _at_payload(extra=(), tamper=False):
    draws = [
        _at_draw(3353, "2026-09-18", [39, 5, 6, 12, 35, 38]),
        _at_draw(3354, "2026-09-20", [3, 4, 15, 31, 32, 41 if not tamper else 42]),
        _at_draw(3355, "2026-09-23", [2, 14, 15, 17, 28, 37]),
        *extra,
    ]
    return json.dumps({"game": "LOTTO", "drawResults": draws[::-1]}).encode()


def _root(tmp_path, name, header, rows):
    lot = GAMES_BY_ID[name]
    path = tmp_path / lot.csv
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((header + "".join(rows)).encode())
    return path


def _serve(monkeypatch, responses):
    calls = []

    def fake(url, headers=None):
        calls.append(url)
        for key, body in responses.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"URL neasteptat in test: {url}")

    monkeypatch.setattr(ue, "_http_get", fake)
    return calls


def test_appends_new_draws_sorted_lf_and_atomic(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    new = [_at_draw(3356, "2026-09-25", [45, 1, 7, 9, 20, 33])]
    _serve(monkeypatch, {"win2day.at": _at_payload(new)})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 1
    assert "+1 extrageri noi: 25-09-2026" in line
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"25-09-2026,1,7,9,20,33,45\n")
    assert data.startswith((AT_HEADER + "".join(AT_ROWS)).encode())
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # niciun .tmp ramas


def test_mismatched_history_writes_nothing(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    new = [_at_draw(3356, "2026-09-25", [1, 7, 9, 20, 33, 45])]
    _serve(monkeypatch, {"win2day.at": _at_payload(new, tamper=True)})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0
    assert "nepotrivire 20-09-2026" in line and "nu scriu nimic" in line
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "nums",
    [[1, 1, 9, 20, 33, 45], [0, 7, 9, 20, 33, 45], [1, 7, 9, 20, 33, 46], [1, 7, 9, 20, 33]],
)
def test_invalid_source_rows_write_nothing(tmp_path, monkeypatch, nums):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    _serve(monkeypatch, {"win2day.at": _at_payload([_at_draw(3356, "2026-09-25", nums)])})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0 and "invalida" in line
    assert path.read_bytes() == before


def test_future_date_is_rejected(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    _serve(monkeypatch, {"win2day.at": _at_payload([_at_draw(3356, "2026-10-30", [1, 2, 3, 4, 5, 6])])})
    added, _ = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0 and path.read_bytes() == before


def test_network_error_leaves_file_unchanged(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    _serve(monkeypatch, {"win2day.at": OSError("offline")})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0
    assert "EROARE (OSError)" in line
    assert path.read_bytes() == before


def test_source_without_overlap_is_refused(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    only_new = json.dumps({"game": "LOTTO", "drawResults": [_at_draw(9, "2026-09-25", [1, 2, 3, 4, 5, 6])]})
    _serve(monkeypatch, {"win2day.at": only_new.encode()})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0 and "nicio extragere comuna" in line
    assert path.read_bytes() == before


def test_failed_replace_keeps_original_and_removes_tmp(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    _serve(monkeypatch, {"win2day.at": _at_payload([_at_draw(3356, "2026-09-25", [1, 7, 9, 20, 33, 45])])})

    def boom(src, dst):
        raise OSError("disk plin")

    monkeypatch.setattr(ue.os, "replace", boom)
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY)
    assert added == 0 and "EROARE la scriere" in line
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = _root(tmp_path, "at_lotto", AT_HEADER, AT_ROWS)
    before = path.read_bytes()
    _serve(monkeypatch, {"win2day.at": _at_payload([_at_draw(3356, "2026-09-25", [1, 7, 9, 20, 33, 45])])})
    added, line = ue.update_game(GAMES_BY_ID["at_lotto"], tmp_path, TODAY, dry_run=True)
    assert added == 0 and "dry-run" in line
    assert path.read_bytes() == before


CZ_HEADER = "date,n1,n2,n3,n4,n5,n6\n"
CZ_SOURCE = (
    "datum;rok;tyden;den;1;2;3;4;5;6;d1;1;2;3;4;5;6;d2;\n"
    "27. 9. 2026;2026;39;7;1;2;3;4;5;6;7;11;12;13;14;15;16;17\n"
    "25. 9. 2026;2026;39;5;16;11;42;26;15;19;33;44;13;45;22;18;23;40\n"
    "23. 9. 2026;2026;39;3;20;33;1;29;30;49;32;24;20;28;31;9;48;49\n"
).encode("utf-8-sig")


def test_two_draws_per_day_keep_official_order_and_complete_a_partial_day(tmp_path, monkeypatch):
    # CSV-ul are numai 1. tah din 25-09: se adaugă 2. tah, apoi ziua nouă.
    rows = ["23-09-2026,20,33,1,29,30,49\n", "23-09-2026,24,20,28,31,9,48\n", "25-09-2026,16,11,42,26,15,19\n"]
    path = _root(tmp_path, "cz_sportka", CZ_HEADER, rows)
    monkeypatch.setattr(ue.time, "sleep", lambda s: None)
    _serve(monkeypatch, {"allwyn.cz": CZ_SOURCE})
    added, _ = ue.update_game(GAMES_BY_ID["cz_sportka"], tmp_path, TODAY)
    assert added == 3
    tail = path.read_text().splitlines()[-3:]
    assert tail == [
        "25-09-2026,44,13,45,22,18,23",
        "27-09-2026,1,2,3,4,5,6",
        "27-09-2026,11,12,13,14,15,16",
    ]


def test_same_day_order_mismatch_is_refused(tmp_path, monkeypatch):
    # CSV-ul a stocat 2. tah pe locul 1. tah: nu e prefixul oficial.
    rows = ["23-09-2026,20,33,1,29,30,49\n", "23-09-2026,24,20,28,31,9,48\n", "25-09-2026,44,13,45,22,18,23\n"]
    path = _root(tmp_path, "cz_sportka", CZ_HEADER, rows)
    before = path.read_bytes()
    monkeypatch.setattr(ue.time, "sleep", lambda s: None)
    _serve(monkeypatch, {"allwyn.cz": CZ_SOURCE})
    added, line = ue.update_game(GAMES_BY_ID["cz_sportka"], tmp_path, TODAY)
    assert added == 0 and "nepotrivire 25-09-2026" in line
    assert path.read_bytes() == before


def _fdj_zip(rows):
    head = "annee;jour;date_de_tirage;x;y;boule_1;boule_2;boule_3;boule_4;boule_5;etoile_1;etoile_2\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("euromillions_202002.csv", head + "".join(rows))
    return buf.getvalue()


def test_euromillions_validates_stars_and_keeps_draw_order(tmp_path, monkeypatch):
    header = "date,n1,n2,n3,n4,n5,s1,s2\n"
    path = _root(tmp_path, "eu_euromillions", header, ["25-09-2026,12,11,38,15,49,10,12\n"])
    page = b'<a class="block" download="euromillions_202002" title="t" href="https://x.fdj.fr/doc/afe6">'
    ok = _fdj_zip([
        "1;MARDI;29/09/2026;1;1;7;3;50;21;9;11;2\n",
        "1;VENDREDI;25/09/2026;1;1;12;11;38;15;49;10;12\n",
    ])
    _serve(monkeypatch, {"historique": page, "x.fdj.fr/doc": ok})
    added, _ = ue.update_game(GAMES_BY_ID["eu_euromillions"], tmp_path, dt.date(2026, 9, 30))
    assert added == 1
    assert path.read_text().splitlines()[-1] == "29-09-2026,7,3,50,21,9,2,11"

    bad = _fdj_zip(["1;MARDI;02/10/2026;1;1;7;3;50;21;9;13;2\n", "1;M;29/09/2026;1;1;7;3;50;21;9;11;2\n"])
    before = path.read_bytes()
    _serve(monkeypatch, {"historique": page, "x.fdj.fr/doc": bad})
    added, line = ue.update_game(GAMES_BY_ID["eu_euromillions"], tmp_path, dt.date(2026, 10, 3))
    assert added == 0 and "invalida" in line and path.read_bytes() == before


def test_update_all_never_raises_and_skips_romanian_games(tmp_path, monkeypatch, capsys):
    shutil.copytree(ROOT / "_ISTORIC" / "externe", tmp_path / "_ISTORIC" / "externe")
    before = {p.name: p.read_bytes() for p in (tmp_path / "_ISTORIC" / "externe").glob("*.csv")}
    monkeypatch.setattr(ue.time, "sleep", lambda s: None)
    monkeypatch.setattr(ue, "_http_get", lambda url, headers=None: (_ for _ in ()).throw(OSError("offline")))
    assert ue.update_all(tmp_path, today=TODAY) == 0
    out = capsys.readouterr().out
    assert out.count("EROARE") == len(ue.SOURCES)
    assert "Loto 6/49" not in out
    after = {p.name: p.read_bytes() for p in (tmp_path / "_ISTORIC" / "externe").glob("*.csv")}
    assert after == before
    assert ue.main(["--root", str(tmp_path / "nu_exista")]) == 0


def test_every_foreign_game_has_a_source():
    from loto_enterprise.core.lotteries import GAMES

    foreign = {lot.game_id for lot in GAMES if not lot.is_romanian}
    assert foreign == set(ue.SOURCES)


def test_cet_midnight_follows_eu_summer_time():
    # lotto.de: 26.09.2026 00:00 Berlin (CEST) = 1790373600000 (răspuns real).
    assert ue._cet_midnight_ms(dt.date(2026, 9, 26)) == 1790373600000
    winter = ue._cet_midnight_ms(dt.date(2026, 12, 31))
    assert winter == int(dt.datetime(2026, 12, 30, 23, tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_actualizari_bat_runs_it_after_update_csv_before_push():
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    i_csv = text.index('update_csv.py" > "%UPDATE_LOG%"')
    i_ext = text.index('"%PROJECT_DIR%update_externe.py" >> "%UPDATE_LOG%" 2>&1')
    assert i_csv < i_ext < text.index("call :push_istoric")
    assert "update_externe" not in (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    assert os.path.exists(ROOT / "update_externe.py")


def test_bg_toto49_year_table_parses_rows_and_refuses_multi_draw_rows(monkeypatch):
    page = (
        "<table><tr><th>Тираж #</th><th>Дата</th><th>1-во Теглене</th></tr>"
        "<tr><td>75</td><td>24.09.2026</td><td>08 19 21 27 35 43</td></tr>"
        "<tr><td>76</td><td>27.09.2026</td><td>08 09 17 27 37 48</td></tr></table>"
    )
    rows = ue.parse_toto49_year(page)
    assert rows == [
        ue.Draw(dt.date(2026, 9, 24), (8, 19, 21, 27, 35, 43)),
        ue.Draw(dt.date(2026, 9, 27), (8, 9, 17, 27, 37, 48)),
    ]
    bad = "<tr><td>1</td><td>04.01.2012</td><td>1 2 3 4 5 6</td><td>7 8 9 10 11 12</td></tr>"
    with pytest.raises(ue.SourceError):
        ue.parse_toto49_year(bad)
    monkeypatch.setattr(ue, "_http_get", lambda url, headers=None: page.encode())
    got = ue.fetch_bg_toto2(dt.date(2026, 9, 1), dt.date(2026, 9, 27))
    assert [d.date for d in got] == [dt.date(2026, 9, 24), dt.date(2026, 9, 27)]
