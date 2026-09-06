"""Producția și benchmarkul trebuie să recalculeze scorul la fiecare pas."""
import ast
import inspect
from pathlib import Path


def test_ui_rebench_uses_per_draw_scoring_and_keeps_random_baseline():
    # Executăm doar handlerul: fără server NiceGUI sau procese de bench reale.
    source = ast.parse(Path("app_nicegui.py").read_text(encoding="utf-8"))
    handler = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                   and n.name == "run_rebench")
    calls = []
    scope = {
        "_bench_running": lambda: False,
        "_istoric_has_data": lambda: True,
        "STATE": {"datasets": [object()]},
        "_PCTS": "10,30,60,100",
        "_launch_bench": lambda args, label: calls.append((args, label)),
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "handler", "exec"), scope)
    scope["run_rebench"]()
    args, _label = calls[0]
    assert args[args.index("--block-size") + 1] == "1"
    assert "--no-shuffled-control" in args
    assert "--methods" not in args  # nu exclude baseline-ul random sau curarea


def test_runner_defaults_to_per_draw_scoring():
    from loto_enterprise.benchmark.runner import run_benchmark

    assert inspect.signature(run_benchmark).parameters["block_size"].default == 1


def test_curated_benchmark_runs_only_relevant_methods_per_game():
    from loto_enterprise.benchmark.curated import load_curated, resolve_methods_per_game

    games = ("loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2")
    matrix = resolve_methods_per_game(load_curated(), games)

    # 20/20/20/16 semnale; random+frequency sunt adăugate dacă nu erau deja.
    assert {g: len(matrix[g]) for g in games} == {
        "loto_6_49": 22,
        "loto_5_40": 22,
        "joker_urna1": 21,  # frequency este deja unul dintre cele 20
        "joker_urna2": 18,
    }
    assert sum(map(len, matrix.values())) == 83
    for selected in matrix.values():
        assert "random" in selected
        assert "frequency" in selected


def test_runner_method_matrix_is_safe_and_deduplicated():
    from loto_enterprise.benchmark.runner import _methods_for_game

    methods = ["random", "frequency", "signal_a", "signal_b"]
    matrix = {"game_a": ["signal_b", "random", "signal_b", "unknown"]}
    assert _methods_for_game(methods, matrix, "game_a") == ["signal_b", "random"]
    assert _methods_for_game(methods, matrix, "game_without_config") == methods
    assert _methods_for_game(methods, None, "game_a") == methods


def test_per_draw_cache_never_reuses_static_fold(monkeypatch):
    from loto_enterprise.benchmark import bench_cache

    monkeypatch.setattr(bench_cache, "_CACHE_VARIANT", {"block_size": 99999, "seed": 1234})
    args = ("same-history", "frequency", 30, "joker_urna1", False)
    static_key = bench_cache._fold_key(*args)
    bench_cache.set_cache_variant(1, 1234)
    assert bench_cache._fold_key(*args) != static_key


def test_aggregate_weights_windows_and_does_not_invent_shuffled_lift():
    import pandas as pd

    from loto_enterprise.benchmark.runner import GameDef, _aggregate

    game = GameDef("game", "Game", "unused.csv", ["n1"], 40, 5)
    common = {
        "game": "game",
        "method": "signal",
        "is_random": False,
        "failed": False,
        "runtime_sec": 1.0,
        "cpu_pct_peak": 2.0,
        "cpu_pct_avg": 1.0,
        "ram_gb_peak": 0.1,
        "gpu_pct_peak": 0.0,
        "gpu_pct_avg": 0.0,
        "vram_mb_peak": 0.0,
    }
    real_rows = [
        {**common, "percentile": 10, "n_eval": 100, "k10": 1.0, "k10_bl": 0.8},
        {**common, "percentile": 40, "n_eval": 400, "k10": 3.0, "k10_bl": 2.8},
    ]
    meta = {
        "signal": {
            "available": True,
            "family": "test",
            "requires_train": False,
        }
    }

    without_control = _aggregate(
        pd.DataFrame(real_rows), [game], ["signal"], meta, {"game": ["k10"]}
    )
    stats = without_control["games"]["game"]["per_method"]["signal"]["per_pool"]["k10"]
    assert stats["avg_hits_real"] == 2.6
    assert stats["avg_hits_real_bl"] == 2.4
    assert stats["avg_hits_shuffled"] is None
    assert stats["lift_vs_shuffle"] is None
    assert without_control["games"]["game"]["winners_per_pool"]["k10"]["lift_vs_shuffle"] is None

    shuffled_rows = [
        {**real_rows[0], "is_random": True, "k10": 0.5, "k10_bl": 0.4},
        {**real_rows[1], "is_random": True, "k10": 1.5, "k10_bl": 1.4},
    ]
    with_control = _aggregate(
        pd.DataFrame(real_rows + shuffled_rows),
        [game],
        ["signal"],
        meta,
        {"game": ["k10"]},
    )
    stats = with_control["games"]["game"]["per_method"]["signal"]["per_pool"]["k10"]
    assert stats["avg_hits_shuffled"] == 1.3
    assert stats["lift_vs_shuffle"] == 1.3
