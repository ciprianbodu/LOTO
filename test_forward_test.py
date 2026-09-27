"""Testul preînregistrat pe extrageri viitoare (`scripts/analysis/forward_test`).

Apără ce face testul credibil: regula nu se schimbă după înregistrare, nicio
extragere de la data înregistrării sau dinainte nu e evaluată, pool-ul unei
extrageri vine numai din extragerile cu dată anterioară, iar decizia urmează
pragurile, plafonul și testul final scrise în fișierul de înregistrare.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from scipy.stats import binomtest, hypergeom

HERE = Path(__file__).resolve().parent / "scripts" / "analysis" / "forward_test"
sys.path.insert(0, str(HERE))

import forward_test as ft  # noqa: E402
import frozen_dmd  # noqa: E402
import sprt_operating  # noqa: E402

REG = json.loads((HERE / "preregistration_2026-09-27.json").read_text(encoding="utf-8"))
ISTORIC = Path(__file__).resolve().parent / "_ISTORIC" / "loto_6_49.csv"


def test_frozen_method_is_the_registered_file():
    """O modificare a copiei înghețate schimbă regula testată: testul trebuie să cadă."""
    assert ft.file_hash(HERE / "frozen_dmd.py") == REG["frozen_method_sha256"]


def test_frozen_hash_ignores_windows_line_endings(tmp_path):
    """Git for Windows (autocrlf) scoate fișierul cu CRLF; regula rămâne aceeași."""
    lf = (HERE / "frozen_dmd.py").read_bytes().replace(b"\r\n", b"\n")
    crlf = tmp_path / "frozen_dmd.py"
    crlf.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert ft.file_hash(crlf) == REG["frozen_method_sha256"]


def test_registration_parameters_are_the_published_ones():
    """Fiecare parametru al deciziei e fixat aici: o editare a JSON-ului cade."""
    assert REG["method"] == "dmd_forecast" and REG["game"] == "loto_6_49"
    assert (REG["max_num"], REG["draw_n"], REG["target_hits"]) == (49, 6, 3)
    assert REG["start_after_date"] == "2026-09-27"
    assert REG["max_forward_draws"] == 1000
    assert REG["history_rows_at_registration"] == 2587
    assert REG["history_last_date_at_registration"] == "2026-09-24"
    assert sorted(REG["sprt"]) == ["12", "6"]
    assert REG["sprt"]["6"]["p1"] == 26 / 777
    assert REG["sprt"]["12"]["p1"] == 146 / 777
    for k in ("6", "12"):
        p0 = float(hypergeom(49, int(k), 6).sf(2))
        assert math.isclose(REG["sprt"][k]["p0"], p0, rel_tol=1e-12)
        assert REG["sprt"][k]["alpha"] == 0.0125 and REG["sprt"][k]["beta"] == 0.2
    assert all(a["before_first_evaluated_draw"] for a in REG["amendments"])


def test_frozen_method_reproduces_the_registered_ranking_and_scores():
    """Pe istoricul de la înregistrare, copia dă clasamentul și scorurile scrise în
    JSON (care erau identice bit cu bit cu metoda din aplicație la acea dată).
    Metoda din aplicație se poate schimba ulterior fără să atingă testul."""
    dates, draws = ft.load_history(ISTORIC)
    n = REG["history_rows_at_registration"]
    assert ft.history_hash(dates, draws, n) == REG["history_sha256"]
    assert frozen_dmd.ranking(draws[:n], 49)[:12] == REG["golden_top12_at_registration"]
    got = frozen_dmd.scores(draws[:n], 49)
    want = {int(k): v for k, v in REG["scores_at_registration"].items()}
    assert got.keys() == want.keys()
    assert all(math.isclose(got[k], want[k], rel_tol=1e-9, abs_tol=1e-12) for k in want)


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


def _real_history_plus(tmp_path, extra_lines):
    path = tmp_path / "real.csv"
    text = ISTORIC.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(text + "\n" + "\n".join(extra_lines) + "\n", encoding="utf-8")
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


@pytest.mark.parametrize(
    "line",
    ["01-10-2026,4,4,4,7,7,70", "01-10-2026,4,7,18,19,25,50", "01-10-2026,4,7,18,19,25"],
)
def test_an_invalid_draw_stops_the_evaluation(tmp_path, line):
    with pytest.raises(ValueError, match="nevalid"):
        ft.load_history(_real_history_plus(tmp_path, [line]))


def test_a_repeated_draw_stops_the_evaluation(tmp_path):
    """O extragere copiată de două ori ar conta de două ori în SPRT."""
    path = _real_history_plus(tmp_path, ["01-10-2026,4,7,18,19,25,27"] * 2)
    with pytest.raises(ValueError, match="repetată"):
        ft.load_history(path)


def test_a_late_row_before_the_start_date_is_reported(tmp_path):
    """Un rând datat 25-26.09 nu schimbă primele 2.587 de rânduri, dar intră în
    istoricul fiecărei extrageri evaluate: trebuie semnalat."""
    clean = _real_history_plus(tmp_path, ["27-09-2026,1,2,3,4,5,6"])
    dates, draws = ft.load_history(clean)
    assert ft.integrity_warnings(REG, dates, draws, HERE / "frozen_dmd.py") == []
    late = _real_history_plus(tmp_path, ["26-09-2026,13,17,21,33,39,45"])
    dates, draws = ft.load_history(late)
    warnings = ft.integrity_warnings(REG, dates, draws, HERE / "frozen_dmd.py")
    assert any("2026-09-26" in w for w in warnings)
    moved = _real_history_plus(tmp_path, ["20-09-2026,13,17,21,33,39,45"])
    dates, draws = ft.load_history(moved)
    warnings = ft.integrity_warnings(REG, dates, draws, HERE / "frozen_dmd.py")
    assert any("2588 extrageri" in w for w in warnings)


def test_sequential_decision_follows_the_registered_thresholds():
    p = REG["sprt"]["12"]
    win = ft.Sprt(p["p0"], p["p1"], p["alpha"], p["beta"])
    for _ in range(200):
        win.add(True)
    assert win.decision == ft.CONFIRMED
    at = win.decided_at
    win.add(False)
    assert win.decided_at == at  # decizia nu se mai schimbă
    lose = ft.Sprt(p["p0"], p["p1"], p["alpha"], p["beta"])
    for _ in range(1000):
        lose.add(False)
    assert lose.decision == ft.REJECTED
    assert lose.decided_at == math.ceil(lose.lower / lose.lose)


def test_final_binomial_test_decides_an_open_pool_at_the_cap():
    p = REG["sprt"]["6"]
    for hits, confirmed in ((30, True), (29, False)):
        t = ft.Sprt(p["p0"], p["p1"], p["alpha"], p["beta"])
        t.n, t.hits = 1000, hits  # nedecis: LLR în bandă
        t.final_test()
        want = binomtest(hits, 1000, p["p0"], alternative="greater").pvalue
        assert math.isclose(t.final_p, want, rel_tol=1e-12)
        assert t.decision.startswith(ft.CONFIRMED) is confirmed
        assert t.decided_at == 1000


def test_the_cap_closes_the_test_and_applies_the_final_test(tmp_path):
    future = [(f"{d:02d}-10-2026", [7, 8, 9, 10, 11, 12]) for d in range(1, 6)]
    dates, draws = ft.load_history(_history(tmp_path, future))
    reg = dict(REG, max_forward_draws=3)
    res = ft.evaluate(reg, dates, draws)
    assert res["n"] == 3 and res["capped"]
    assert all(t.final_p is not None for t in res["tests"].values())
    assert res["next_pools"] == {} and "plafonul" in res["next_note"]


def test_combined_verdict_follows_the_registered_rule():
    def sprt(decision):
        t = ft.Sprt(0.1, 0.2, 0.0125, 0.2)
        t.decision = decision
        return t

    confirmed, rejected, running = ft.CONFIRMED, ft.REJECTED, "continuă"
    assert ft.verdict({6: sprt(confirmed), 12: sprt(running)}) == "metodă confirmată"
    assert ft.verdict({6: sprt(rejected), 12: sprt(rejected)}) == "metodă respinsă"
    assert ft.verdict({6: sprt(rejected), 12: sprt(running)}) == "în curs"
    final_no = "fără avantaj (test final, p=0.2000)"
    assert ft.verdict({6: sprt(final_no), 12: sprt(rejected)}) == "metodă respinsă"


def test_next_pool_is_announced_only_for_an_evaluated_draw(tmp_path):
    dates, draws = ft.load_history(ISTORIC)
    res = ft.evaluate(REG, dates, draws)
    if dates[-1] < ft.dt.date(2026, 9, 27):
        assert res["next_pools"] == {} and "prima extragere evaluată" in res["next_note"]
    dates, draws = ft.load_history(_history(tmp_path, []))
    res = ft.evaluate(REG, dates, draws)
    assert res["next_pools"][6] == sorted(frozen_dmd.ranking(draws, 49)[:6])
    assert "2026-09-27" in res["next_note"]


def test_operating_table_matches_the_protocol():
    """Cifrele din PREREGISTRATION.md vin din calculul exact, nu din simulare."""
    res = sprt_operating.table(REG)
    six, twelve = res["6"], res["12"]
    assert round(100 * six["H1"]["confirm_by_cap"], 1) == 63.1
    assert round(100 * twelve["H1"]["confirm_by_cap"], 1) == 71.2
    assert round(100 * six["H0"]["confirm_with_final_test"], 1) == 1.1
    assert round(100 * twelve["H0"]["confirm_with_final_test"], 1) == 1.3
    assert res["type1_bound_total"] <= 0.025
    assert six["H1"]["median_to_confirmation"] == 743
    assert twelve["H1"]["median_to_confirmation"] == 600
    assert six["H0"]["median_to_rejection"] == 263
    assert twelve["H0"]["median_to_rejection"] == 189
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    for fragment in ("63,1%", "71,2%", "743", "600", "263", "189", "2,4%"):
        assert fragment in text


def test_script_reports_without_writing_anything(tmp_path, capsys):
    csv = _history(tmp_path, [("01-10-2026", [7, 8, 9, 10, 11, 12])])
    before = sorted(p.name for p in HERE.iterdir())
    assert ft.main(["--csv", str(csv)]) == 0
    out = capsys.readouterr().out
    assert "Extrageri evaluate (după 2026-09-27): 1" in out
    assert "Verdict: în curs" in out
    assert "Pool 6 pentru următoarea extragere" in out
    assert sorted(p.name for p in HERE.iterdir() if p.name != "__pycache__") == [
        n for n in before if n != "__pycache__"
    ]


def test_script_survives_a_windows_console_encoding(tmp_path):
    """Pe Windows, ieșirea redirecționată e cp1252: „ATENȚIE” nu are voie să crape."""
    csv = _history(tmp_path, [("01-10-2026", [7, 8, 9, 10, 11, 12])])
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    run = subprocess.run(
        [sys.executable, str(HERE / "forward_test.py"), "--csv", str(csv)],
        capture_output=True, env=env, timeout=120,
    )
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    assert "ATEN" in run.stdout.decode("utf-8", "replace")


def test_replication_reads_calendar_dates_and_never_looks_ahead(tmp_path):
    """Replicarea citea datele ca text: un fișier ZZ-LL-AAAA ieșea amestecat și
    extrageri viitoare intrau în istoricul extragerii evaluate."""
    import external_replication as er

    rng = random.Random(9)
    rows, day = [], ft.dt.date(2001, 1, 3)
    for _ in range(230):
        rows.append((day.strftime("%d-%m-%Y"), rng.sample(range(1, 50), 6)))
        day += ft.dt.timedelta(days=3 if day.weekday() == 2 else 4)
    path = tmp_path / "germania.csv"
    _write_csv(path, rows)
    dates, draws = er.load(path)
    assert dates == sorted(dates)
    hits = er.replicate(dates, draws)
    assert hits[6].size == 230 - er.WARMUP
    # Primul pool evaluat vine exact din primele 200 de extrageri.
    first = set(frozen_dmd.ranking(draws[: er.WARMUP], 49)[:6])
    assert hits[6][0] == len(first & set(int(x) for x in draws[er.WARMUP]))


def test_replication_verdict_uses_the_amended_alpha_and_a_two_sided_interval():
    import numpy as np
    import external_replication as er

    assert er.ALPHA == 0.0125
    res = er.summarize(np.array([3] * 30 + [0] * 970), 6)
    assert res["ci95"][1] < 1.0
    assert math.isclose(res["random_rate"], REG["sprt"]["6"]["p0"], rel_tol=1e-12)
