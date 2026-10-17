"""Istoricele externe din `_ISTORIC/externe/` și paza istoricului românesc.

`discover_games` ia primul CSV din `_ISTORIC/` care conține „649” în nume, în
ordine alfabetică: un fișier străin pus lângă cel românesc ar înlocui pe tăcute
Loto 6/49 în benchmark. De aceea istoricele străine stau în subfolder, iar
nivelul de sus are voie să conțină numai cele trei fișiere românești.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
ISTORIC = ROOT / "_ISTORIC"
EXTERNE = ISTORIC / "externe"
FILES = sorted(EXTERNE.glob("*.csv"))
ALL_FILES = sorted(ISTORIC.glob("*.csv")) + FILES
# Cuvinte după care codul vechi ghicește un joc românesc din numele fișierului.
LEGACY_TOKENS = ("joker", "649", "6_49", "5_40", "5/40", "540")


def _geometry(path: Path) -> tuple[int, int, bool]:
    """(numere principale, maxim, a doua urnă) din numele fișierului."""
    name = path.stem
    if "5din50" in name:
        return 5, 50, "stars"
    if "5din45" in name:
        return 5, 45, True
    if "6din45" in name or "6aus45" in name:
        return 6, 45, False
    if "6din49" in name or "6aus49" in name:
        return 6, 49, False
    raise AssertionError(f"geometrie necunoscută în numele {path.name}")


def test_romanian_top_level_holds_only_the_three_romanian_files():
    assert sorted(p.name for p in ISTORIC.glob("*.csv")) == [
        "joker.csv", "loto_5_40.csv", "loto_6_49.csv",
    ]


def test_benchmark_discovery_still_reads_the_romanian_files():
    from loto_enterprise.benchmark.runner import discover_games

    games = {g.key: Path(g.csv_path).name for g in discover_games(str(ISTORIC))}
    assert games["loto_6_49"] == "loto_6_49.csv"
    assert games["loto_5_40"] == "loto_5_40.csv"
    assert games["joker_urna1"] == "joker.csv"


def test_external_files_exist_and_are_described():
    assert FILES, "_ISTORIC/externe/ nu are niciun istoric"
    readme = (EXTERNE / "README.md").read_text(encoding="utf-8")
    for path in FILES:
        assert f"`{path.name}`" in readme, f"{path.name} lipsește din README"


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_external_file_names_cannot_be_mistaken_for_romanian_games(path):
    name = path.name.lower()
    assert not any(tok in name for tok in LEGACY_TOKENS), name
    assert re.fullmatch(r"[a-z0-9_]+\.csv", name), name


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_external_history_is_valid_and_chronological(path):
    draw_n, max_num, has_joker = _geometry(path)
    raw = path.read_bytes()
    assert b"\r" not in raw, "terminații CRLF"
    rows = list(csv.reader(raw.decode("utf-8").splitlines()))
    second = {True: ["joker"], "stars": ["s1", "s2"]}.get(has_joker, [])
    header = ["date"] + [f"n{i}" for i in range(1, draw_n + 1)] + second
    assert rows[0] == header
    seen, per_day, prev = set(), {}, None
    for lineno, row in enumerate(rows[1:], start=2):
        assert len(row) == len(header), f"rândul {lineno}"
        day = dt.datetime.strptime(row[0], "%d-%m-%Y").date()
        assert prev is None or day >= prev, f"rândul {lineno}: ordine"
        prev = day
        nums = [int(x) for x in row[1 : 1 + draw_n]]
        assert len(set(nums)) == draw_n, f"rândul {lineno}: numere repetate"
        assert all(1 <= n <= max_num for n in nums), f"rândul {lineno}: interval"
        if has_joker is True:
            assert 1 <= int(row[-1]) <= 20, f"rândul {lineno}: joker"
        elif has_joker == "stars":
            # EuroMillions: 1-9 stele până la 06.05.2011, 1-11 până la 23.09.2016, apoi 1-12.
            top = 9 if day < dt.date(2011, 5, 10) else 11 if day < dt.date(2016, 9, 27) else 12
            stars = [int(x) for x in row[-2:]]
            assert len(set(stars)) == 2 and all(1 <= x <= top for x in stars), f"rândul {lineno}"
        key = (day, tuple(sorted(nums)))
        assert key not in seen, f"rândul {lineno}: extragere repetată"
        seen.add(key)
        per_day[day] = per_day.get(day, 0) + 1
    assert max(per_day.values()) <= 2
    assert prev >= dt.date(2026, 9, 1), "istoric oprit înainte de import"


@pytest.mark.parametrize("path", ALL_FILES, ids=[p.name for p in ALL_FILES])
def test_no_draw_repeats_the_previous_one(path):
    """Două rânduri consecutive cu aceleași numere sunt o copie, nu o extragere.

    La întâmplare, șansa pe rând este 1/C(N, k), sub 1e-6 la orice joc de aici
    (5/40: 2,6e-7). Așa a intrat 24-10-2024 la 5/40, copiat din 27-10-2024, cum
    apare și pe loto49.ro. O repetare reală se confirmă întâi pe arhiva oficială.
    """
    rows = list(csv.reader(path.read_bytes().decode("utf-8").splitlines()))
    main = [i for i, name in enumerate(rows[0]) if re.fullmatch(r"n\d+", name)]
    assert main, f"{path.name}: lipsesc coloanele n1..nK"
    prev = None
    for lineno, row in enumerate(rows[1:], start=2):
        nums = frozenset(int(row[i]) for i in main)
        assert nums != prev, f"{path.name}, rândul {lineno}: repetă rândul {lineno - 1}"
        prev = nums
