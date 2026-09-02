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


def test_per_draw_cache_never_reuses_static_fold(monkeypatch):
    from loto_enterprise.benchmark import bench_cache

    monkeypatch.setattr(bench_cache, "_CACHE_VARIANT", {"block_size": 99999, "seed": 1234})
    args = ("same-history", "frequency", 30, "joker_urna1", False)
    static_key = bench_cache._fold_key(*args)
    bench_cache.set_cache_variant(1, 1234)
    assert bench_cache._fold_key(*args) != static_key
