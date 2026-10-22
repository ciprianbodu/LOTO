"""Auditul global din 2026-10-22, grupul „date”: constatările C1-C4.

Testele fiecărei constatări pică pe codul de dinainte (55ea7f6) și trec după
corectură; cazurile de control (celelalte jocuri din registru, numere pe care
int() le citește) trec pe ambele.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

import scan_all_methods_hits as sah
import update_csv as uc
from loto_enterprise.benchmark import runner
from loto_enterprise.core.lotteries import GAMES, GAMES_BY_ID
from ui_runtime import _next_draw_date_for, _weekdays_text
from verifica_istoric import check_history

ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# C1: TIPOS Loto (Slovacia) are extragere și vinerea, din 07-03-2025.
# --------------------------------------------------------------------------- #
def test_slovak_loto_next_draw_includes_friday():
    sk = GAMES_BY_ID["sk_loto"]
    # După extragerea de miercuri 14-10-2026 urma vineri 16-10, nu duminică 18-10.
    assert _next_draw_date_for(sk.draw_weekdays, dt.date(2026, 10, 15)) == "16-10-2026"
    assert _next_draw_date_for(sk.draw_weekdays, dt.date(2026, 10, 22)) == "23-10-2026"
    assert _weekdays_text(sk.draw_weekdays) == "Miercuri/Vineri/Duminică"


@pytest.mark.parametrize("lottery", GAMES, ids=[g.game_id for g in GAMES])
def test_registry_weekdays_match_the_weekly_draws_in_the_history(lottery):
    """Zilele cu extragere în cel puțin 40 de zile din ultimul an al arhivei
    sunt exact zilele din registru (Austria: vinerea bonus e la două săptămâni,
    deci nu intră)."""
    dates = pd.to_datetime(
        pd.read_csv(ROOT / lottery.csv, usecols=["date"])["date"], format="%d-%m-%Y"
    )
    days = dates[dates > dates.max() - dt.timedelta(days=365)].drop_duplicates()
    counts = days.dt.weekday.value_counts()
    weekly = {int(d) for d, n in counts.items() if n >= 40}
    assert weekly == set(lottery.draw_weekdays)


# --------------------------------------------------------------------------- #
# C2: un CSV românesc existent, dar fără nicio dată citibilă, e NEACTUALIZAT.
# --------------------------------------------------------------------------- #
_CURRENT = {
    "joker": ("date,n1,n2,n3,n4,n5,joker\n01-10-2026,1,2,3,4,5,7\n", "2026-10-01 1 2 3 4 5 + 7"),
    "loto_5_40": ("date,n1,n2,n3,n4,n5,n6\n01-10-2026,1,2,3,4,5,6\n", "2026-10-01 1 2 3 4 5 6"),
}


@pytest.mark.parametrize(
    "damaged",
    [
        b"date,n1,n2,n3,n4,n5,n6\n",  # doar antetul (sincronizare întreruptă)
        b"",  # fișier gol
        b"date,n1,n2,n3,n4,n5,n6\n01-10-2026,1,2,3,4,5,\xff\xfe\n",  # nu e UTF-8
    ],
    ids=["header_only", "zero_bytes", "bad_utf8"],
)
def test_update_all_reports_an_unreadable_csv_as_not_updated(
    tmp_path, monkeypatch, capsys, damaged
):
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    (istoric / uc.GAME_CONFIGS["loto_6_49"]["csv_name"]).write_bytes(damaged)
    for key, (text, _page) in _CURRENT.items():
        (istoric / uc.GAME_CONFIGS[key]["csv_name"]).write_text(text, encoding="utf-8")
    url_to_game = {cfg["recent_url"]: key for key, cfg in uc.GAME_CONFIGS.items()}
    fetched = []

    def _page(url):
        fetched.append(url_to_game[url])
        return _CURRENT[url_to_game[url]][1]

    monkeypatch.setattr(uc, "_find_istoric_dir", lambda: istoric)
    monkeypatch.setattr(uc, "_get_page_text", _page)
    assert uc.update_all() == 0
    out = capsys.readouterr().out
    assert fetched == ["joker", "loto_5_40"]  # 6/49 sărit înainte de fetch, ca înainte
    assert (istoric / "loto_6_49.csv").read_bytes() == damaged
    assert "SAR peste" in out
    assert "NEACTUALIZATE: Loto 6/49 (vezi motivul de mai sus)." in out
    assert "Toate jocurile sunt la zi" not in out
    # START_8000.bat avertizează în consolă când log-ul conține unul dintre cuvinte.
    # ACTUALIZARI.bat cauta numai „EROARE”, START_8000 si „NEACTUALIZATE”.
    assert "EROARE verificare" in out and "NEACTUALIZATE" in out


# --------------------------------------------------------------------------- #
# C3: verifica_istoric refuză numerele pe care actualizatoarele (int()) nu le citesc.
# --------------------------------------------------------------------------- #
L649 = "_ISTORIC/loto_6_49.csv"
JOKER = "_ISTORIC/joker.csv"
STARS = "_ISTORIC/externe/euromillions_5din50_stele.csv"
GERMANY = "_ISTORIC/externe/germania_lotto_6aus49.csv"
BASE = {
    L649: "date,n1,n2,n3,n4,n5,n6\n01-10-2026,1,2,3,4,5,6\n04-10-2026,7,8,9,10,11,12\n",
    JOKER: "date,n1,n2,n3,n4,n5,joker\n02-10-2026,1,2,3,4,5,20\n",
    STARS: "date,n1,n2,n3,n4,n5,s1,s2\n29-09-2026,4,31,12,44,7,8,12\n",
    GERMANY: "date,n1,n2,n3,n4,n5,n6\n01-10-2026,1,2,3,4,5,6\n",
}


def _history(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return name


@pytest.mark.parametrize(
    "name, row, reason",
    [
        (L649, "05-10-2026,1,2,3,4,5,6.0\n", "randul 4: numere invalide pentru 6/49"),
        (L649, "05-10-2026,1e0,2,3,4,5,13\n", "randul 4: numere invalide pentru 6/49"),
        (JOKER, "05-10-2026,6,7,8,9,10,7.0\n", "randul 3: numere invalide pentru joker"),
        (STARS, "03-10-2026,1,2,3,4,5,1.0,2\n", "randul 3: numere invalide pentru 5/50"),
        (GERMANY, "22-10-2026,1.0,2,3,9,10,11\n", "randul 3: numere invalide pentru 6/49"),
    ],
    ids=["6_49_6.0", "6_49_1e0", "joker_7.0", "stele_1.0", "germania_1.0"],
)
def test_number_cells_that_int_cannot_read_are_refused(tmp_path, name, row, reason):
    got = check_history(_history(tmp_path, name, BASE[name] + row), tmp_path, dt.date(2026, 10, 22))
    assert got is not None and got.startswith(reason), got


@pytest.mark.parametrize("cell", ["13", "+13", "013"])
def test_number_cells_that_int_reads_still_pass(tmp_path, cell):
    row = f"05-10-2026,1,2,3,4,5,{cell}\n"
    assert check_history(_history(tmp_path, L649, BASE[L649] + row), tmp_path) is None


# --------------------------------------------------------------------------- #
# C4: scanul de triaj taie istoricul la începutul zilei țintei, ca bench-ul.
# --------------------------------------------------------------------------- #
def test_scan_history_excludes_draws_from_the_target_day(tmp_path, monkeypatch):
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    start = dt.date(2026, 1, 1)
    lines = ["date,n1,n2,n3,n4,n5,n6"]
    for i in range(20):
        # Rândurile 13 și 14 sunt două extrageri din aceeași zi (14-01-2026).
        day = start + dt.timedelta(days=i if i < 14 else i - 1)
        lines.append(f"{day:%d-%m-%Y}," + ",".join(str(i + j) for j in range(1, 7)))
    (istoric / "loto_6_49.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    game = next(g for g in runner.discover_games(str(istoric)) if g.key == "loto_6_49")

    seen: list[int] = []
    real = runner.call_method

    def spy(name, history, max_num, **kw):
        seen.append(len(history))
        return real(name, history, max_num, **kw)

    monkeypatch.setattr(runner, "call_method", spy)
    # Același inițializator și aceeași copie pickle a jocului, fără procese.
    monkeypatch.setattr(sah, "ProcessPoolExecutor", ThreadPoolExecutor)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(var, "1")
    for name in ("_G_TRAIN", "_G_TEST", "_G_GAME", "_G_POOLS"):
        monkeypatch.setattr(sah, name, getattr(sah, name))

    rows = sah.scan_game(game, (10,), ["frequency"], 1)
    assert [r["failed"] for r in rows] == [False], rows
    # 20 rânduri, 30% test: prima țintă e rândul 14, iar rândul 13 e din ziua ei.
    assert seen == [13]
