"""Testul preînregistrat pe extrageri viitoare pentru Joker Urna 1
(`scripts/analysis/forward_test_joker`).

Apără ce face testul credibil: regula nu se schimbă după înregistrare, dă exact
pool-urile ecranului din 2026-10-10, nicio extragere de la data înregistrării
sau dinainte nu e evaluată, pool-ul unei extrageri vine numai din extragerile cu
dată anterioară, iar decizia urmează pragurile, plafonul și testul final din
fișierul de înregistrare.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import hypergeom

ROOT = Path(__file__).resolve().parent
HERE = ROOT / "scripts" / "analysis" / "forward_test_joker"
DMD = ROOT / "scripts" / "analysis" / "forward_test"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fj = _load("forward_test_joker", HERE / "forward_test_joker.py")
ft = fj.ft
frozen_ens = fj.frozen_ens
sprt_operating = _load("sprt_operating_dmd", DMD / "sprt_operating.py")
REG = json.loads((HERE / "preregistration_2026-10-11.json").read_text(encoding="utf-8"))
ISTORIC = ROOT / "_ISTORIC" / "joker.csv"


def test_frozen_method_is_the_registered_file():
    assert ft.file_hash(HERE / "frozen_ens.py") == REG["frozen_method_sha256"]


def test_registration_parameters_are_the_published_ones():
    assert REG["method"] == "ens_win100+winall+ewma0.01" and REG["game"] == "joker_urna1"
    assert (REG["max_num"], REG["draw_n"], REG["target_hits"]) == (45, 5, 3)
    assert REG["start_after_date"] == "2026-10-11"
    assert REG["max_forward_draws"] == 1000
    assert REG["history_rows_at_registration"] == 2194
    assert REG["history_last_date_at_registration"] == "2026-10-08"
    assert list(REG["sprt"]) == ["8"]
    sprt = REG["sprt"]["8"]
    assert sprt["p1"] == 30 / 605
    assert math.isclose(sprt["p0"], float(hypergeom(45, 8, 5).sf(2)), rel_tol=1e-12)
    assert sprt["alpha"] == 0.025 and sprt["beta"] == 0.2
    assert REG["amendments"] == [] or all(
        a["date"] <= REG["start_after_date"] for a in REG["amendments"]
    )


def test_frozen_method_reproduces_the_registered_ranking_and_scores():
    dates, draws = fj.load_history(ISTORIC)
    n = REG["history_rows_at_registration"]
    assert ft.history_hash(dates, draws, n) == REG["history_sha256"]
    assert frozen_ens.ranking(dates[:n], draws[:n], 45)[:12] == REG["golden_top12_at_registration"]
    got = frozen_ens.scores(dates[:n], draws[:n], 45)
    want = {int(k): v for k, v in REG["scores_at_registration"].items()}
    assert got.keys() == want.keys()
    assert all(math.isclose(got[k], want[k], rel_tol=1e-9, abs_tol=1e-12) for k in want)


def _synthetic_days(n_days, seed, two_draw_days=()):
    rng = random.Random(seed)
    rows = []
    for i in range(n_days):
        day = f"{(i % 28) + 1:02d}-{(i // 28) % 12 + 1:02d}-{2015 + i // 336}"
        rows.append((day, sorted(rng.sample(range(1, 46), 5)), rng.randint(1, 20)))
        if i in two_draw_days:
            rows.append((day, sorted(rng.sample(range(1, 46), 5)), rng.randint(1, 20)))
    return rows


def test_frozen_copy_is_the_screen_rule_on_every_row():
    """Pe o istorie sintetică cu zile duble, copia dă pool-ul de 8 al ecranului
    din 2026-10-10 la fiecare rând (acolo, pe istoricul real, 2.194 din 2.194)."""
    ms = _load("method_screen_20261010", ROOT / "scripts" / "analysis" / "method_screen_2026-10-10.py")
    rows = _synthetic_days(260, 11, two_draw_days=(40, 41, 150))
    draws = np.array([r[1] for r in rows])
    dates = np.array([np.datetime64(f"{d[6:]}-{d[3:5]}-{d[:2]}") for d, _n, _j in rows])
    day = np.zeros(len(rows), dtype=np.int64)
    day[1:] = np.cumsum(dates[1:] != dates[:-1])
    data = ms.ps.Data(draws, day, dates[np.r_[0, np.flatnonzero(np.diff(day)) + 1]], 45, ordered=False)
    S = next(S for n, _f, S in ms.bank(data) if n == REG["method"])
    tb = ms.ps.cumsum0(data.C)[:-1][data.day]
    Srow = S[data.day]
    num = np.broadcast_to(np.arange(45), Srow.shape)
    order = np.lexsort((-num, -tb, -Srow), axis=1)
    for i in range(len(rows)):
        prior = int(np.searchsorted(dates, dates[i], side="left"))
        rk = frozen_ens.ranking(dates[:prior], draws[:prior], 45)
        assert rk[:8] == [int(x) + 1 for x in order[i, :8]], i


def _write_csv(path: Path, rows):
    lines = ["date,n1,n2,n3,n4,n5,joker"]
    lines += [f"{d},{','.join(map(str, nums))},{j}" for d, nums, j in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _history(tmp_path, future):
    rows = _synthetic_days(300, 4) + [("11-10-2026", [1, 2, 3, 4, 5], 7)] + future
    path = tmp_path / "h.csv"
    _write_csv(path, rows)
    return path


def test_only_draws_after_the_registration_day_are_evaluated(tmp_path):
    future = [
        ("15-10-2026", [7, 8, 9, 10, 11], 3),
        ("15-10-2026", [13, 14, 15, 16, 17], 4),
        ("18-10-2026", [19, 20, 21, 22, 23], 5),
    ]
    dates, draws = fj.load_history(_history(tmp_path, future))
    res = fj.evaluate(REG, dates, draws)
    assert [r["date"] for r in res["ledger"]] == ["2026-10-15", "2026-10-15", "2026-10-18"]
    # Extragerea din 11-10 (ziua înregistrării) nu e evaluată, dar intră în istoric.
    n = len(draws)
    before_first = frozen_ens.ranking(dates[: n - 3], draws[: n - 3], 45)
    assert res["ledger"][0]["pool8"] == sorted(before_first[:8])
    # A doua extragere din 15-10 nu vede prima extragere din aceeași zi.
    assert res["ledger"][1]["pool8"] == res["ledger"][0]["pool8"]
    # Extragerea din 18-10 le vede pe amândouă.
    after_both = frozen_ens.ranking(dates[: n - 1], draws[: n - 1], 45)
    assert res["ledger"][2]["pool8"] == sorted(after_both[:8])


def test_hits_count_the_drawn_numbers_inside_the_pool(tmp_path):
    dates, draws = fj.load_history(_history(tmp_path, []))
    pool = sorted(frozen_ens.ranking(dates, draws, 45)[:8])
    dates, draws = fj.load_history(_history(tmp_path, [("15-10-2026", pool[:5], 9)]))
    res = fj.evaluate(REG, dates, draws)
    assert res["ledger"][0]["hits8"] == 5
    assert res["tests"][8].hits == 1


@pytest.mark.parametrize(
    "line",
    [
        "15-10-2026,7,8,9,10,11",  # fără numărul Joker
        "15-10-2026,7,8,9,10,10,3",  # număr repetat
        "15-10-2026,7,8,9,10,46,3",  # în afara intervalului
        "15-10-2026,7,8,9,10,11,21",  # Joker în afara 1..20
    ],
)
def test_an_invalid_draw_stops_the_evaluation(tmp_path, line):
    path = _history(tmp_path, [])
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        fj.load_history(path)


def test_a_repeated_draw_stops_the_evaluation(tmp_path):
    path = _history(tmp_path, [("15-10-2026", [7, 8, 9, 10, 11], 3)] * 2)
    with pytest.raises(ValueError, match="repetată"):
        fj.load_history(path)


def test_a_late_row_before_the_start_date_is_reported(tmp_path):
    """Un rând datat 09-10 sau 10-10-2026, când nu era extragere, e semnalat."""
    path = tmp_path / "real.csv"
    text = ISTORIC.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(text + "\n10-10-2026,7,8,9,10,11,3\n", encoding="utf-8")
    dates, draws = fj.load_history(path)
    warnings = ft.integrity_warnings(REG, dates, draws, HERE / "frozen_ens.py")
    assert any("2026-10-10" in w for w in warnings)
    assert not any("s-a schimbat" in w for w in warnings)


def test_the_real_history_before_registration_is_unchanged():
    dates, draws = fj.load_history(ISTORIC)
    warnings = ft.integrity_warnings(REG, dates, draws, HERE / "frozen_ens.py")
    assert warnings == []


def test_operating_table_matches_the_protocol():
    """Cifrele din PREREGISTRATION.md vin din calculul exact, nu din simulare."""
    res = sprt_operating.table(REG)["8"]
    assert round(100 * res["H1"]["confirm_by_cap"], 1) == 63.1
    assert round(100 * res["H1"]["confirm_with_final_test"], 1) == 71.4
    assert round(100 * res["H0"]["confirm_with_final_test"], 1) == 2.5
    assert round(100 * res["H0"]["reject_by_cap"], 1) == 89.8
    assert res["H1"]["median_to_confirmation"] == 735
    assert res["H0"]["median_to_rejection"] == 312
    assert res["H0"]["final_test_min_hits"] == 45
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    for fragment in ("63,1%", "71,4%", "2,5%", "89,8%", "735", "312", "45 de reușite"):
        assert fragment in text


def test_script_reports_without_writing_anything(tmp_path, capsys):
    csv = _history(tmp_path, [("15-10-2026", [7, 8, 9, 10, 11], 3)])
    before = sorted(p.name for p in HERE.iterdir() if p.name != "__pycache__")
    assert fj.main(["--csv", str(csv)]) == 0
    out = capsys.readouterr().out
    assert "Extrageri evaluate (după 2026-10-11): 1" in out
    assert "Verdict: în curs" in out
    assert "Pool 8 pentru următoarea extragere" in out
    assert "frozen_ens.py " + REG["frozen_method_sha256"][:16] in out
    assert sorted(p.name for p in HERE.iterdir() if p.name != "__pycache__") == before


def test_no_next_pool_before_the_first_evaluated_draw(tmp_path, capsys):
    # Istoricul real crește după înregistrare. Scenariul fără extrageri viitoare
    # folosește prefixul înregistrat, nu presupune că CSV-ul curent se oprește acolo.
    n = REG["history_rows_at_registration"]
    lines = ISTORIC.read_text(encoding="utf-8").splitlines()
    csv = tmp_path / "registered_history.csv"
    csv.write_text("\n".join(lines[: n + 1]) + "\n", encoding="utf-8")
    dates, draws = fj.load_history(csv)
    assert len(draws) == n
    assert ft.history_hash(dates, draws, n) == REG["history_sha256"]
    assert dates[-1].isoformat() == REG["history_last_date_at_registration"]
    assert fj.main(["--csv", str(csv)]) == 0
    out = capsys.readouterr().out
    assert "Extrageri evaluate (după 2026-10-11): 0" in out
    assert "prima extragere evaluată" in out
