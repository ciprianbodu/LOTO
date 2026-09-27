"""Testul preînregistrat pe extrageri viitoare (`scripts/analysis/forward_test`).

Apără ce face testul credibil: regula nu se schimbă după înregistrare, nicio
extragere de la data înregistrării sau dinainte nu e evaluată, pool-ul unei
extrageri vine numai din extragerile cu dată anterioară, iar decizia secvențială
urmează pragurile scrise în fișierul de înregistrare.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from scipy.stats import hypergeom

HERE = Path(__file__).resolve().parent / "scripts" / "analysis" / "forward_test"
sys.path.insert(0, str(HERE))

import forward_test as ft  # noqa: E402
import frozen_dmd  # noqa: E402

REG = json.loads((HERE / "preregistration_2026-09-27.json").read_text(encoding="utf-8"))


def test_frozen_method_is_the_registered_file():
    """O modificare a copiei înghețate schimbă regula testată: testul trebuie să cadă."""
    assert ft.file_hash(HERE / "frozen_dmd.py") == REG["frozen_method_sha256"]


def test_frozen_hash_ignores_windows_line_endings(tmp_path):
    """Git for Windows (autocrlf) scoate fișierul cu CRLF; regula rămâne aceeași."""
    crlf = tmp_path / "frozen_dmd.py"
    crlf.write_bytes((HERE / "frozen_dmd.py").read_bytes().replace(b"\n", b"\r\n"))
    assert ft.file_hash(crlf) == REG["frozen_method_sha256"]


def test_frozen_method_reproduces_the_registered_ranking():
    """Pe istoricul de la înregistrare, clasamentul e cel scris în JSON (numpy stabil)."""
    dates, draws = ft.load_history(Path("_ISTORIC/loto_6_49.csv"))
    n = REG["history_rows_at_registration"]
    assert ft.history_hash(dates, draws, n) == REG["history_sha256"]
    assert frozen_dmd.ranking(draws[:n], 49)[:12] == REG["golden_top12_at_registration"]


def test_frozen_method_matches_the_app_method_at_registration():
    """La înregistrare, copia dă aceleași scoruri ca metoda din aplicație."""
    from loto_enterprise.benchmark.methods import METHODS

    dates, draws = ft.load_history(Path("_ISTORIC/loto_6_49.csv"))
    n = REG["history_rows_at_registration"]
    app = METHODS["dmd_forecast"][0](draws[:n], 49)
    assert frozen_dmd.scores(draws[:n], 49) == app


def test_registered_baselines_are_the_exact_random_rates():
    for k in ("6", "12"):
        p0 = float(hypergeom(49, int(k), 6).sf(2))
        assert math.isclose(REG["sprt"][k]["p0"], p0, rel_tol=1e-12)
        assert REG["sprt"][k]["alpha"] == 0.0125 and REG["sprt"][k]["beta"] == 0.2
    assert REG["start_after_date"] == "2026-09-27"


def _write_csv(path: Path, rows):
    lines = ["date,n1,n2,n3,n4,n5,n6"]
    lines += [f"{d},{','.join(map(str, r))}" for d, r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _history(tmp_path, future):
    rng = random.Random(4)
    past = []
    for i in range(300):
        day = f"{(i % 28) + 1:02d}-{(i // 28) % 12 + 1:02d}-{2020 + i // 336}"
        past.append((day, sorted(rng.sample(range(1, 50), 6))))
    rows = past + [("27-09-2026", [1, 2, 3, 4, 5, 6])] + future
    path = tmp_path / "h.csv"
    _write_csv(path, rows)
    return path


def test_only_draws_after_the_registration_day_are_evaluated(tmp_path):
    future = [
        ("01-10-2026", [7, 8, 9, 10, 11, 12]),
        ("01-10-2026", [13, 14, 15, 16, 17, 18]),
        ("04-10-2026", [19, 20, 21, 22, 23, 24]),
    ]
    dates, draws = ft.load_history(_history(tmp_path, future))
    res = ft.evaluate(REG, dates, draws)
    assert [r["date"] for r in res["ledger"]] == ["2026-10-01", "2026-10-01", "2026-10-04"]
    # Extragerea din 27-09 (ziua înregistrării) nu e evaluată, dar intră în istoric.
    before_first = frozen_dmd.ranking(draws[: len(draws) - 3], 49)
    assert res["ledger"][0]["pool6"] == sorted(before_first[:6])
    # A doua extragere din 01-10 nu vede prima extragere din aceeași zi.
    assert res["ledger"][1]["pool6"] == res["ledger"][0]["pool6"]
    # Extragerea din 04-10 le vede pe amândouă.
    after_both = frozen_dmd.ranking(draws[: len(draws) - 1], 49)
    assert res["ledger"][2]["pool12"] == sorted(after_both[:12])


def test_hits_count_the_drawn_numbers_inside_the_pool(tmp_path):
    dates, draws = ft.load_history(_history(tmp_path, []))
    pool = sorted(frozen_dmd.ranking(draws, 49)[:6])
    rows = [("05-10-2026", pool)]
    dates, draws = ft.load_history(_history(tmp_path, rows))
    res = ft.evaluate(REG, dates, draws)
    assert res["ledger"][0]["hits6"] == 6
    assert res["tests"][6].hits == 1 and res["tests"][6].n == 1


def test_sequential_decision_follows_the_registered_thresholds():
    p = REG["sprt"]["12"]
    win = ft.Sprt(p["p0"], p["p1"], p["alpha"], p["beta"])
    for _ in range(200):
        win.add(True)
    assert win.decision == "avantaj confirmat"
    at = win.decided_at
    win.add(False)
    assert win.decided_at == at  # decizia nu se mai schimbă
    lose = ft.Sprt(p["p0"], p["p1"], p["alpha"], p["beta"])
    for _ in range(1000):
        lose.add(False)
    assert lose.decision == "fără avantaj (respins)"
    assert lose.decided_at == math.ceil(lose.lower / lose.lose)


def test_script_reports_without_writing_anything(tmp_path, capsys):
    csv = _history(tmp_path, [("01-10-2026", [7, 8, 9, 10, 11, 12])])
    before = sorted(p.name for p in HERE.iterdir())
    assert ft.main(["--csv", str(csv)]) == 0
    out = capsys.readouterr().out
    assert "Extrageri evaluate (după 2026-09-27): 1" in out
    assert "Pool 6 pentru următoarea extragere" in out
    assert sorted(p.name for p in HERE.iterdir() if p.name != "__pycache__") == [
        n for n in before if n != "__pycache__"
    ]
