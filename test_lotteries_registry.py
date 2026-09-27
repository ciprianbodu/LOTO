"""Registrul loteriilor (`loto_enterprise/core/lotteries.py`): sursa unică de
identitate și geometrie.

Două garanții:
1. Consistență: fiecare tabel românesc scris de mână înainte de registru
   (motor, backtesting, walk-forward, ținta bench-ului, decizia, prospețimea,
   interfața, biletul fizic, actualizarea CSV) spune exact ce spune registrul
   pentru jocurile românești. Un rând nou în registru nu poate rămâne
   desincronizat de ele fără să pice aici.
2. Jocurile din alte țări nu pot fi confundate cu cele românești: id-uri și
   fișiere fără subșirurile după care codul vechi ghicește jocul românesc,
   chei proprii în tabelele deciziei, căi de decizie/bench separate.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from loto_enterprise.core import lotteries as L

ROOT = Path(__file__).resolve().parent
RO_GAMES = [g for g in L.GAMES if g.is_romanian]
FOREIGN_GAMES = [g for g in L.GAMES if not g.is_romanian]


def _ro(game_id: str) -> L.Lottery:
    return L.GAMES_BY_ID[game_id]


# --------------------------------------------------------------------------- #
# Registrul românesc = tabelele de dinainte
# --------------------------------------------------------------------------- #


def test_romanian_entries_keep_the_legacy_ids_keys_and_files():
    assert [(g.game_id, g.geometry, g.bench_keys, g.csv) for g in RO_GAMES] == [
        ("6/49", "6/49", ("loto_6_49",), "_ISTORIC/loto_6_49.csv"),
        ("joker", "joker", ("joker_urna1", "joker_urna2"), "_ISTORIC/joker.csv"),
        ("5/40", "5/40", ("loto_5_40",), "_ISTORIC/loto_5_40.csv"),
    ]
    for g in RO_GAMES:
        assert g.country == "RO" and g.country_name == "România"
        assert g.currency == "Lei" and g.playable_from_ro is True
        assert g.history_key_prefix == ""
        # Mail-ul românesc: joi și duminică.
        assert g.draw_weekdays == (3, 6)


def test_engine_params_match_the_registry():
    from loto_engine import LotoEngine

    legacy = {
        "6/49": {
            "max_n": 49,
            "draw_n": 6,
            "play_n": 6,
            "scheme": "2-2-2",
            "lookback": 20,
        },
        "5/40": {
            "max_n": 40,
            "draw_n": 6,
            "play_n": 5,
            "scheme": "2-1-2",
            "lookback": 25,
        },
        "joker": {
            "max_n": 45,
            "draw_n": 5,
            "play_n": 5,
            "scheme": "2-2-1",
            "lookback": 15,
            "max_joker": 20,
        },
    }
    for g in RO_GAMES:
        params = LotoEngine(g.geometry).params
        assert params == legacy[g.geometry]
        assert list(params) == list(legacy[g.geometry])  # aceeași ordine a cheilor
        assert (params["max_n"], params["draw_n"], params["play_n"]) == (
            g.max_n,
            g.draw_n,
            g.pick_n,
        )
    joker = _ro("joker")
    assert LotoEngine("joker").params["max_joker"] == joker.geo.second.max_n


def test_engine_bench_keys_match_the_registry():
    from loto_engine import LotoEngine

    for g in RO_GAMES:
        legacy = LotoEngine(g.geometry)
        explicit = LotoEngine(g.geometry, game_key=g.bench_key, country="RO")
        assert legacy._bench_game_key() == explicit._bench_game_key() == g.bench_key
        if g.bench_key_urna2:
            assert legacy._bench_game_key(True) == g.bench_key_urna2
            assert explicit._bench_game_key(True) == g.bench_key_urna2


def test_backtesting_params_match_the_registry():
    from loto_enterprise.core.backtesting import _GAME_PICK_N, LotoBacktester

    for g in RO_GAMES:
        params = LotoBacktester._get_game_params(None, g.geometry)
        assert params["max_n"] == g.max_n
        assert params["draw_n"] == g.draw_n
        assert params.get("pick_n", params["draw_n"]) == g.pick_n
        assert params["num_cols"] == [f"n{i}" for i in range(1, g.draw_n + 1)]
        assert _GAME_PICK_N[g.geometry] == g.pick_n


def _local_dict(module_path: Path, function: str, name: str) -> dict:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    getattr(t, "id", None) == name for t in sub.targets
                ):
                    return ast.literal_eval(sub.value)
    raise AssertionError(f"{function}.{name} lipsește")


def test_walk_forward_maps_match_the_registry():
    from loto_enterprise.core import walk_forward_adapter as wf

    cols_map = _local_dict(
        ROOT / "loto_enterprise/core/walk_forward_adapter.py", "_csv_hash", "cols_map"
    )
    for g in RO_GAMES:
        assert wf._WF_PICK[g.geometry] == g.pick_n
        assert wf._MAX_NUM[g.geometry] == g.max_n
        second = g.geo.second
        expected_cols = [f"n{i}" for i in range(1, g.draw_n + 1)]
        if second is not None and second.modelled:
            expected_cols += list(second.columns)
        assert cols_map[g.geometry] == expected_cols


def test_benchmark_decision_and_freshness_tables_match_the_registry():
    from loto_enterprise.benchmark import decision, freshness, hit_target

    for g in RO_GAMES:
        assert hit_target.GAME_DRAW_PICK[g.bench_key] == (g.draw_n, g.pick_n)
        assert decision.KNOWN_GAME_MAX_NUM[g.bench_key] == g.max_n
        assert hit_target.GAME_MIN_HIT_TARGET.get(g.bench_key, 3) == g.min_hit_target
        for key in g.bench_keys:
            assert freshness.GAMES_CSV_MAP[key][0] == g.csv
        if g.bench_key_urna2:
            assert hit_target.GAME_DRAW_PICK[g.bench_key_urna2] == (1, 1)
            assert decision.KNOWN_GAME_MAX_NUM[g.bench_key_urna2] == g.geo.second.max_n
    # Nicio cheie de bench românească în plus față de registru.
    ro_keys = {k for g in RO_GAMES for k in g.bench_keys}
    assert set(freshness.GAMES_CSV_MAP) == ro_keys
    assert {
        k for k in hit_target.GAME_DRAW_PICK if not L.lottery_by_bench_key(k)
    } == set()


def test_ui_maps_match_the_registry():
    import ui_bench
    import ui_results
    import ui_runtime

    assert ui_results.PRICES == {g.game_id: g.price for g in RO_GAMES}
    assert {label: m for label, _s, m in ui_runtime._RESTRICT_BASE_GAMES} == {
        g.game_id: g.max_n for g in RO_GAMES
    }
    assert ui_runtime._GAME_DISPLAY_ORDER == {
        g.game_id: g.display_order for g in RO_GAMES
    }
    assert ui_runtime._WF_GAME_ORDER == {g.game_id: g.wf_order for g in RO_GAMES}
    assert ui_bench._LABEL_TO_FOLDS_GAME == {g.game_id: g.bench_key for g in RO_GAMES}
    for g in RO_GAMES:
        assert ui_bench._BENCH_DRAW_N[g.bench_key] == g.pick_n
        assert ui_results._ticket_pick(g.game_id) == g.pick_n
        assert ui_results._hypergeo_params(g.game_id) == (g.draw_n, g.max_n)
        assert ui_runtime._game_label_for(Path(g.csv).name) == g.game_id
        assert ui_runtime._game_title(g.game_id) == g.display
    assert set(ui_results.LR_SCHEMES) <= {g.game_id for g in RO_GAMES}


def test_full_ticket_and_update_csv_tables_match_the_registry():
    import update_csv
    from loto_enterprise.core.full_ticket import PICK, TICKET_VARIANTS

    assert TICKET_VARIANTS == {g.game_id: g.per_ticket for g in RO_GAMES}
    assert PICK == {g.game_id: g.pick_n for g in RO_GAMES}
    assert set(L.LOTTERIES) == {g.game_id for g in RO_GAMES}
    assert len(update_csv.GAME_CONFIGS) == len(RO_GAMES)
    by_csv = {cfg["csv_name"]: cfg for cfg in update_csv.GAME_CONFIGS.values()}
    for g in RO_GAMES:
        cfg = by_csv[Path(g.csv).name]
        assert cfg["max_num"] == g.max_n
        assert cfg["num_main"] == g.draw_n
        assert cfg["display_name"] == g.name
        second = g.geo.second
        assert cfg["has_joker"] is bool(second and second.modelled)
        if cfg["has_joker"]:
            assert cfg["joker_max"] == second.max_n


def test_worker_legacy_label_map_matches_the_registry():
    import worker

    for g in RO_GAMES:
        assert worker._map_game_label(g.game_id) == (g.geometry, g.pick_n)
        assert worker._map_game_label(g.name) == (g.geometry, g.pick_n)


def test_adaptive_geometry_matches_the_registry():
    from loto_enterprise.core.adaptive_feedback import _GAME_GEOMETRY

    for name, geo in L.GEOMETRIES.items():
        assert _GAME_GEOMETRY[name] == (geo.draw_n, geo.max_n)


# --------------------------------------------------------------------------- #
# Jocurile din alte țări
# --------------------------------------------------------------------------- #


def test_foreign_ids_files_and_keys_cannot_be_sniffed_as_romanian():
    """`_game_label_for`, `worker._map_game_label` și `runner.discover_games`
    ghicesc jocul românesc după subșiruri; un id/fișier străin nu are voie să le
    conțină, iar id-urile intră în nume de fișiere (cache WF, folds.csv)."""
    assert FOREIGN_GAMES, "registrul trebuie să aibă și jocuri din alte țări"
    for g in FOREIGN_GAMES:
        for text in (g.game_id, g.bench_key, *g.bench_keys, Path(g.csv).name):
            assert L.ID_PATTERN.fullmatch(Path(text).stem), text
            low = text.lower()
            for bad in L.LEGACY_SNIFF_SUBSTRINGS:
                assert bad not in low, (text, bad)
        # RO CSV-urile rămân în _ISTORIC/; cele străine în subfolderul externe/,
        # pe care glob-ul ne-recursiv al bench-ului românesc nu îl vede.
        assert g.csv.startswith("_ISTORIC/externe/")
        assert g.history_key_prefix == f"{g.country}_{g.game_id}_"
        assert g.country != "RO" and len(g.country) == 2 and g.country.isupper()


def test_registry_is_unique_and_facts_are_well_formed():
    ids = [g.game_id for g in L.GAMES]
    keys = [k for g in L.GAMES for k in g.bench_keys]
    assert len(ids) == len(set(ids))
    assert len(keys) == len(set(keys))
    # Un id poate coincide numai cu propria cheie de bench (de_lotto), niciodată
    # cu cheia altui joc.
    for key in keys:
        owner = L.lottery_by_id(key)
        assert owner is None or key in owner.bench_keys
    for g in L.GAMES:
        assert g.geometry in L.GEOMETRIES
        assert g.currency in {"Lei", "EUR", "PLN", "CAD", "GBP"}
        assert g.price is None or g.price > 0
        assert g.per_ticket is None or g.per_ticket > 0
        assert g.draw_weekdays and all(0 <= d <= 6 for d in g.draw_weekdays)
        assert g.min_hit_target in (3, 4)
        assert g.playable_from_ro in (True, False, None)
        assert g.training_only is (g.playable_from_ro is not True)
        if g.price is not None:
            assert g.price_source


def test_verified_facts_for_germany_poland_spain():
    de, pl, es = (L.GAMES_BY_ID[i] for i in ("de_lotto", "pl_lotto", "es_primitiva"))
    assert (de.price, de.currency, de.per_ticket, de.draw_weekdays) == (
        1.2,
        "EUR",
        12,
        (2, 5),
    )
    assert (pl.price, pl.currency, pl.per_ticket, pl.draw_weekdays) == (
        5.0,
        "PLN",
        8,
        (1, 3, 5),
    )
    assert (es.price, es.currency, es.per_ticket, es.draw_weekdays) == (
        1.0,
        "EUR",
        8,
        (0, 3, 5),
    )
    for g in (de, pl, es):
        assert g.geometry == "6/49" and g.min_hit_target == 3  # 3 numere câștigă
        # DE/ES cer domiciliu în țară pentru jocul online; PL e neverificat.
        # Toate trei rămân istorice de antrenament.
        assert g.training_only
    assert (de.playable_from_ro, pl.playable_from_ro, es.playable_from_ro) == (
        False,
        None,
        False,
    )


@pytest.mark.parametrize("game", [g.game_id for g in L.GAMES])
def test_every_registry_csv_exists_with_the_geometry_columns(game):
    g = L.GAMES_BY_ID[game]
    path = ROOT / g.csv
    assert path.is_file(), g.csv
    header = pd.read_csv(path, nrows=0).columns.tolist()
    expected = ["date", *[f"n{i}" for i in range(1, g.draw_n + 1)]]
    second = g.geo.second
    if second is not None:
        expected += list(second.columns)
    assert header == expected


def test_foreign_bench_keys_get_their_own_geometry_in_the_decision_tables():
    """Fără ele, decizia unui joc străin ar cădea pe referința empirică, iar
    ținta per joc n-ar ști câte numere are biletul."""
    from loto_enterprise.benchmark import decision, hit_target

    for g in FOREIGN_GAMES:
        assert hit_target.GAME_DRAW_PICK[g.bench_key] == (g.draw_n, g.pick_n)
        assert decision.KNOWN_GAME_MAX_NUM[g.bench_key] == g.max_n
        assert hit_target.game_hit_target(g.bench_key, 3) == max(3, g.min_hit_target)


def test_foreign_decision_uses_the_hypergeometric_reference():
    from loto_enterprise.benchmark import decision

    base = decision.expected_random_rate(49, 6, 10, 3)
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        for method, rate in (("random", base + 0.05), ("frequency", base + 0.02)):
            rows.append(
                {
                    "game": "de_lotto",
                    "method": method,
                    "percentile": pct,
                    "n_test": n,
                    "n_eval": n,
                    "is_random": False,
                    "failed": False,
                    "runtime_sec": 0.1,
                    "k10": 1.0,
                    "rate_3plus_k10": rate,
                }
            )
    cfg = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "de_lotto", 10, 6)
    assert cfg["baseline_source"] == "hypergeometric"
    assert cfg["baseline_rate"] == pytest.approx(base)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_countries_romania_first_then_by_name():
    cc = L.countries()
    assert cc[0] == "RO"
    names = [L.country_name(c) for c in cc[1:]]
    assert names == sorted(names, key=str.casefold)
    assert set(cc) == {g.country for g in L.GAMES}
    assert [g.game_id for g in L.games_for_country("ro")] == ["6/49", "joker", "5/40"]
    assert [g.game_id for g in L.games_for_country("DE")] == ["de_lotto"]


@pytest.mark.parametrize("bad", ["", None, "XX", "ROU", "România"])
def test_unknown_country_fails_loudly(bad):
    with pytest.raises(L.UnknownLotteryError):
        L.normalize_country(bad)
    with pytest.raises(L.UnknownLotteryError):
        L.decision_path_for(bad)
    with pytest.raises(L.UnknownLotteryError):
        L.games_for_country(bad)


def test_lookup_helpers():
    assert L.lottery_by_id("de_lotto").country == "DE"
    assert L.lottery_by_id("nu_exista") is None
    assert L.require_lottery("de_lotto", "de").game_id == "de_lotto"
    with pytest.raises(L.UnknownLotteryError):
        L.require_lottery("nu_exista")
    with pytest.raises(L.UnknownLotteryError):
        L.require_lottery("de_lotto", "RO")  # jocul altei țări
    with pytest.raises(L.UnknownLotteryError):
        L.require_lottery("6/49", "DE")
    assert L.lottery_by_bench_key("joker_urna2").game_id == "joker"
    assert L.lottery_by_bench_key("es_primitiva").game_id == "es_primitiva"
    assert L.lottery_by_bench_key("x") is None
    assert L.ro_lottery_for_geometry("5/40").game_id == "5/40"
    assert L.ro_lottery_for_geometry("6/45") is None


def test_decision_and_bench_paths_keep_romania_and_separate_the_others(tmp_path):
    assert L.decision_path_for("RO") == L.PROJECT_ROOT / "best_methods.json"
    assert L.bench_out_dir_for("RO") == L.PROJECT_ROOT / "bench_results"
    assert L.decision_path_for("de", root=tmp_path) == (
        tmp_path / "decisions" / "DE" / "best_methods.json"
    )
    assert L.bench_out_dir_for("PL", root=tmp_path) == (
        tmp_path / "bench_results" / "countries" / "PL"
    )
    paths = {L.decision_path_for(c) for c in L.countries()}
    outs = {L.bench_out_dir_for(c) for c in L.countries()}
    assert len(paths) == len(outs) == len(L.countries())


def test_display_names():
    assert L.display_name("6/49") == "România · Loto 6/49"
    assert L.display_name("de_lotto") == "Germania · Lotto 6aus49"
    assert L.display_name("es_primitiva") == "Spania · La Primitiva"
    assert L.display_name("necunoscut") == "necunoscut"
    assert L.GAMES_BY_ID["pl_lotto"].key == "pl_lotto"


def test_geometry_table():
    shapes = {n: (g.draw_n, g.pick_n, g.max_n) for n, g in L.GEOMETRIES.items()}
    assert shapes == {
        "6/49": (6, 6, 49),
        "5/40": (6, 5, 40),
        "joker": (5, 5, 45),
        "6/45": (6, 6, 45),
        "5/50": (5, 5, 50),
    }
    assert L.GEOMETRIES["joker"].second.modelled is True
    assert L.GEOMETRIES["joker"].second.max_n == 20
    assert L.GEOMETRIES["5/50"].second.modelled is False  # stelele: nemodelate


def test_registry_rejects_inconsistent_entries(monkeypatch):
    bad = L.Lottery(
        game_id="xx_joker_like",
        country="XX",
        country_name="Test",
        name="Test",
        geometry="joker",
        bench_key="xx_game",  # geometria joker cere cheia Urnei 2
        csv="_ISTORIC/externe/x.csv",
        per_ticket=None,
        price=None,
        currency="EUR",
        price_source=None,
        draw_weekdays=(0,),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    )
    monkeypatch.setattr(L, "GAMES", (*L.GAMES, bad))
    with pytest.raises(ValueError, match="Urnei 2"):
        L._check_registry()
    dup = L.Lottery(**{**bad.__dict__, "geometry": "6/49", "bench_key": "de_lotto"})
    monkeypatch.setattr(L, "GAMES", (*L.GAMES[:-1], dup))
    with pytest.raises(ValueError, match="duplicat"):
        L._check_registry()


def test_registry_module_does_not_import_pandas():
    code = (
        "import sys; import loto_enterprise.core.lotteries, "
        "loto_enterprise.benchmark.hit_target; print('pandas' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"
