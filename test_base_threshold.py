"""Tabelul de intervale pentru restrangerea bazei: rata exacta, nu simulare.

Verificarile de aici apara doua lucruri. Primul, corectitudinea numerica: pe tot
universul rata exacta TREBUIE sa cada peste formula hipergeometrica, altfel
tabelul afisat in UI e gresit. Al doilea, onestitatea afisarii: coloana de
control pe extrageri uniforme trebuie sa produca si ea un campion, ca sa se vada
ca varful nu e o dovada de semnal.
"""

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.core.base_threshold import (
    IntervalRow,
    best_interval,
    interval_rate,
    interval_table,
    synthetic_draws,
    theoretical_rate,
)
from loto_enterprise.core.draw_validation import valid_draw_matrix

GEOMETRIES = [
    ("_ISTORIC/loto_6_49.csv", [f"n{i}" for i in range(1, 7)], 6, 49),
    ("_ISTORIC/loto_5_40.csv", [f"n{i}" for i in range(1, 6)], 5, 40),
]


def _draws(path, cols, draw_n, max_num):
    matrix, _ = valid_draw_matrix(
        pd.read_csv(path), cols, draw_n=draw_n, max_num=max_num
    )
    return matrix


@pytest.mark.parametrize("path,cols,draw_n,max_num", GEOMETRIES)
@pytest.mark.parametrize("pool_size", [6, 10, 16])
def test_rate_over_the_whole_universe_equals_the_hypergeometric_formula(
    path, cols, draw_n, max_num, pool_size
):
    """Fara restrangere, rata exacta nu poate depinde de istoric.

    Pe intervalul 1..max_num orice pool de `pool_size` numere este la fel de
    probabil, deci media peste toate pool-urile trebuie sa fie exact rata
    teoretica — oricare ar fi extragerile din CSV. Daca acest test pica, tabelul
    din UI afiseaza numere care nu se pot compara cu referinta.
    """
    draws = _draws(path, cols, draw_n, max_num)
    assert interval_rate(draws, 1, max_num, pool_size) == pytest.approx(
        theoretical_rate(max_num, pool_size, draw_n), abs=1e-9
    )


def test_rate_matches_a_direct_count_over_every_possible_pool():
    """Enumerare exhaustiva pe un univers mic — nu ne bazam pe propria formula."""
    from itertools import combinations

    rng = np.random.default_rng(3)
    draws = np.array(
        [rng.choice(np.arange(1, 13), size=4, replace=False) for _ in range(40)]
    )
    for lo, hi, pool_size, target in [(1, 12, 5, 2), (3, 10, 4, 2), (2, 9, 3, 1)]:
        pools = list(combinations(range(lo, hi + 1), pool_size))
        counted = np.mean(
            [
                np.mean([len(set(p) & set(d)) >= target for d in draws])
                for p in pools
            ]
        )
        assert interval_rate(draws, lo, hi, pool_size, target) == pytest.approx(
            counted * 100
        )


def test_rate_is_a_weighted_average_of_per_draw_probabilities():
    """Doua extrageri construite manual, verificate prin numarare directa."""
    draws = np.array([[1, 2], [4, 5]])
    # Interval 1..5, pool de 2: cele 10 perechi. Extragerea {1,2}: perechile
    # fara 1 si fara 2 sunt C(3,2)=3 -> 7/10 prind 1+. Extragerea {4,5}: simetric.
    assert interval_rate(draws, 1, 5, pool_size=2, target=1) == pytest.approx(70.0)
    # Interval 1..2: singurul pool e {1,2} — prinde tot la prima, nimic la a doua.
    assert interval_rate(draws, 1, 2, pool_size=2, target=1) == pytest.approx(50.0)
    # Interval 4..5: exact invers.
    assert interval_rate(draws, 4, 5, pool_size=2, target=1) == pytest.approx(50.0)


def test_interval_too_narrow_for_the_pool_is_rejected():
    draws = np.array([[1, 2, 3, 4, 5, 6]])
    with pytest.raises(ValueError, match="nu incape"):
        interval_rate(draws, 1, 8, pool_size=10)


@pytest.mark.parametrize("lo,hi", [(0, 10), (-1, 5), (30, 20)])
def test_invalid_interval_bounds_are_rejected(lo, hi):
    draws = np.array([[1, 2, 3, 4, 5, 6]])
    with pytest.raises(ValueError, match="interval invalid"):
        interval_rate(draws, lo, hi, pool_size=2)


def test_empty_history_is_rejected():
    with pytest.raises(ValueError, match="extrageri"):
        interval_rate(np.empty((0, 6), dtype=int), 1, 49, pool_size=10)


def test_best_interval_breaks_ties_on_the_lower_bound():
    """Doua intervale la egalitate: castiga cel care incepe mai jos.

    Fara asta, rezultatul afisat ar depinde de ordinea de parcurgere, exact
    genul de tie-break pe care CLAUDE.md §4.2 il interzice la ranking.
    """
    # Univers 1..4, extrageri simetrice: toate intervalele de latime 3 sunt egale.
    draws = np.array([[1, 4], [2, 3]])
    lo, hi, _ = best_interval(draws, max_num=4, width=3, pool_size=2, target=1)
    assert (lo, hi) == (1, 3)


def test_best_interval_rejects_impossible_widths():
    draws = np.array([[1, 2, 3, 4, 5, 6]])
    with pytest.raises(ValueError, match="latime invalida"):
        best_interval(draws, max_num=49, width=5, pool_size=10)
    with pytest.raises(ValueError, match="latime invalida"):
        best_interval(draws, max_num=49, width=50, pool_size=10)


def test_control_also_produces_a_champion_narrower_than_the_full_game():
    """Pe extrageri uniforme nu exista semnal, dar campionul apare oricum.

    Asta e exact motivul pentru care UI nu are voie sa recomande un interval:
    acelasi calcul care „gaseste" un castigator pe istoricul real il gaseste
    si aici.
    """
    control = synthetic_draws(2000, draw_n=6, max_num=49, seed=7)
    rows = interval_table(control, 49, pool_size=10, draw_n=6, seed=99)
    narrow = [r for r in rows if r.width < 49]
    assert any(r.whole > rows[-1].whole for r in narrow)
    assert rows[-1].whole == pytest.approx(theoretical_rate(49, 10, 6), abs=1e-9)


@pytest.mark.parametrize("path,cols,draw_n,max_num", GEOMETRIES)
def test_table_covers_every_width_and_carries_the_control(path, cols, draw_n, max_num):
    draws = _draws(path, cols, draw_n, max_num)
    rows = interval_table(draws, max_num, pool_size=10, draw_n=draw_n)
    assert [r.width for r in rows] == list(range(10, max_num + 1))
    assert all(isinstance(r, IntervalRow) for r in rows)
    for row in rows:
        assert row.hi - row.lo + 1 == row.width
        assert row.control_hi - row.control_lo + 1 == row.width
        assert 1 <= row.lo and row.hi <= max_num
        assert 1 <= row.control_lo and row.control_hi <= max_num
    expected = theoretical_rate(max_num, 10, draw_n)
    # Ultimul rand e jocul nerestrans: toate coloanele cad pe teoretic.
    last = rows[-1]
    assert (last.lo, last.hi) == (1, max_num)
    for value in (last.whole, last.first_half, last.second_half, last.control):
        assert value == pytest.approx(expected, abs=1e-9)


def test_table_is_deterministic_and_the_seed_only_moves_the_control():
    draws = _draws(*GEOMETRIES[0])
    a = interval_table(draws, 49, pool_size=10, draw_n=6, seed=11)
    b = interval_table(draws, 49, pool_size=10, draw_n=6, seed=11)
    c = interval_table(draws, 49, pool_size=10, draw_n=6, seed=12)
    assert a == b
    assert [r.control for r in a] != [r.control for r in c]
    # Coloana reala nu depinde de seed — seed-ul atinge doar controlul.
    assert [r.whole for r in a] == [r.whole for r in c]


def test_table_needs_a_history_long_enough_to_split():
    with pytest.raises(ValueError, match="prea scurt"):
        interval_table(np.array([[1, 2, 3, 4, 5, 6]]), 49, pool_size=10, draw_n=6)


def test_ui_submenu_renders_one_table_per_game_with_its_control_column():
    """Submeniul din sidebar arata cifrele, dar niciodata singure.

    Fiecare joc primeste tabelul lui, fiindca geometria difera, iar fiecare tabel
    trebuie sa poarte coloana de control si referinta teoretica. Fara ele, un
    varf de 10.31% ar arata ca o descoperire.
    """
    from scripts.analysis.audit_output import capture_ui

    import app_nicegui as app

    render = getattr(app._render_base_interval_tables, "func", None)
    assert render is not None, "refreshable-ul trebuie sa expuna functia interna"
    app._BASE_TABLE_MEMO.clear()
    app._BASE_TABLE_OPENED["value"] = True
    with capture_ui() as ui:
        render()
    tables = [n for n in ui.walk() if n["kind"] == "table"]
    assert len(tables) == 3, "cate un tabel pentru 6/49, 5/40 si Joker Urna 1"

    text = ui.text()
    for label in ("6/49", "5/40", "Joker — Urna 1 (5/45)"):
        assert label in text
    assert "Referință teoretică" in text
    assert "coloana uniformă" in text
    # Niciun indemn: submeniul nu recomanda si nu seteaza un interval.
    for word in ("recomand", "joacă", "cel mai bun interval", "optim"):
        assert word not in text.lower()

    for table in tables:
        rows = table["kwargs"]["rows"]
        assert [r["w"] for r in rows] == sorted(r["w"] for r in rows)
        assert {"w", "span", "whole", "h1", "h2", "ctrl"} == set(rows[0])
        # Ultimul rand e jocul nerestrans: real si control cad pe aceeasi rata,
        # comparata ca numar (substring-ul ar fi lasat "9.03%" sa treaca in "19.03%").
        assert float(rows[-1]["whole"].rstrip("%")) == pytest.approx(
            float(rows[-1]["ctrl"].rsplit("·", 1)[1].strip().rstrip("%"))
        )


def test_ui_submenu_follows_the_pool_size_setting(monkeypatch):
    """Procentele depind de K, deci tabelul trebuie sa porneasca de la K."""
    from scripts.analysis.audit_output import capture_ui

    import app_nicegui as app

    render = app._render_base_interval_tables.func
    app._BASE_TABLE_OPENED["value"] = True
    for pool in (6, 16):
        monkeypatch.setitem(app.SETTINGS, "pool_size_val", pool)
        app._BASE_TABLE_MEMO.clear()
        with capture_ui() as ui:
            render()
        for table in (n for n in ui.walk() if n["kind"] == "table"):
            assert min(r["w"] for r in table["kwargs"]["rows"]) == pool


def test_submenu_is_lazy_and_survives_a_pool_value_below_the_allowed_minimum():
    """Inchis nu calculeaza nimic; un pool tastat sub minim nu strica tabelul.

    `ui.number` isi aplica min/max abia la blur, deci in timpul tastarii ajung
    aici valori ca 0 sau 1. Inainte de plafonare, geometria devenea invalida si
    submeniul raporta „istoric indisponibil" pentru un istoric perfect valid.
    """
    from scripts.analysis.audit_output import capture_ui

    import app_nicegui as app

    render = app._render_base_interval_tables.func
    app._BASE_TABLE_OPENED["value"] = False
    app._BASE_TABLE_MEMO.clear()
    with capture_ui() as ui:
        render()
    assert not [n for n in ui.walk() if n["kind"] == "table"]
    assert "Deschide submeniul" in ui.text()
    assert not app._BASE_TABLE_MEMO, "inchis nu are voie sa calculeze nimic"

    app._BASE_TABLE_OPENED["value"] = True
    for bad_pool in (0, 1, 99):
        app.SETTINGS["pool_size_val"] = bad_pool
        with capture_ui() as ui:
            render()
        assert "istoric indisponibil" not in ui.text()
        assert len([n for n in ui.walk() if n["kind"] == "table"]) == 3
    app.SETTINGS["pool_size_val"] = 10


def test_memo_survives_a_walk_over_every_pool_size():
    """Plafonul memo-ului trebuie sa incapa toate combinatiile joc x pool.

    Cu un plafon prea mic, memo-ul se golea inainte sa fie folosit si fiecare
    schimbare de pool platea din nou calculul complet.
    """
    import app_nicegui as app

    app._BASE_TABLE_MEMO.clear()
    app._BASE_TABLE_OPENED["value"] = True
    for pool in range(6, 17):
        for _label, csv_name, draw_n, max_num in app._BASE_TABLE_GAMES:
            assert app._base_interval_rows(csv_name, draw_n, max_num, pool) is not None
    filled = len(app._BASE_TABLE_MEMO)
    assert filled == app._BASE_TABLE_MEMO_MAX
    # A doua trecere peste aceleasi valori nu mai are voie sa evacueze nimic.
    snapshot = dict(app._BASE_TABLE_MEMO)
    for pool in range(6, 17):
        for _label, csv_name, draw_n, max_num in app._BASE_TABLE_GAMES:
            app._base_interval_rows(csv_name, draw_n, max_num, pool)
    assert app._BASE_TABLE_MEMO == snapshot


def test_diagnostic_script_still_imports_and_uses_the_shared_module():
    """Scriptul din §6 nu e atins de restul suitei — se rupe tacit la redenumiri.

    S-a si rupt: dupa trecerea modulului de la praguri la intervale, importul lui
    `exact_rate` a ramas in script, care a devenit neexecutabil fara ca pytest sa
    observe. Testul incarca modulul si ii ruleaza tabelul pe un istoric mic.
    """
    import importlib.util
    from pathlib import Path

    path = Path("scripts/analysis/bench_base_threshold.py")
    spec = importlib.util.spec_from_file_location("bench_base_threshold", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    draws = synthetic_draws(60, draw_n=6, max_num=49, seed=5)
    module.K, module.TARGET = 10, 3
    module.exact_table("test", draws, 49)  # nu trebuie sa arunce
    assert callable(module.bench_thresholds)
