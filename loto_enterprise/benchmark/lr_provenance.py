"""Reproducible identity of the rules used to select a budget ticket's scorer."""

import ast
import hashlib
import json
from dataclasses import asdict
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import CodeType

from loto_enterprise.benchmark import methods as registry


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=64)
def _module_digest(source):
    # AST excludes paths, comments, line endings and source locations.
    return hashlib.sha256(ast.dump(ast.parse(source)).encode()).hexdigest()


def _code_value(value):
    if isinstance(value, CodeType):
        return (value.co_code.hex(), value.co_names, value.co_varnames, value.co_freevars,
                [_code_value(item) for item in value.co_consts])
    if isinstance(value, (tuple, list)):
        return [_code_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_code_value(item) for item in value), key=str)
    if isinstance(value, dict):
        return {str(k): _code_value(v) for k, v in value.items()}
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    return type(value).__qualname__


@lru_cache(maxsize=256)
def _code_digest(code):
    return _digest(_code_value(code))


def _function_digest(fn):
    import inspect

    fn = inspect.unwrap(fn)
    if not inspect.isfunction(fn):
        return _digest((type(fn).__module__, type(fn).__qualname__))
    constants = {key: _code_value(fn.__globals__[key]) for key in fn.__code__.co_names
                 if key.isupper() and key in fn.__globals__}
    # Mutable cells may hold telemetry, counters or memoized prior calls.
    # Frozen scalar/tuple parameters are part of the formula.
    closure = [value if isinstance(value, (type(None), bool, int, float, str, bytes, tuple, frozenset))
               else type(value).__qualname__ for cell in fn.__closure__ or () if (value := cell.cell_contents) is not None]
    return _digest((fn.__module__, fn.__qualname__, _code_digest(fn.__code__),
                    _code_value(fn.__defaults__), _code_value(fn.__kwdefaults__),
                    _code_value(closure), constants))


@lru_cache(maxsize=1)
def _numeric_runtime():
    packages = []
    for name in ("numpy", "scipy"):
        try:
            packages.append((name, version(name)))
        except PackageNotFoundError:
            packages.append((name, None))
    return tuple(packages)


def _module_rules(modules):
    return {module.__name__: _module_digest(Path(module.__file__).read_bytes()) for module in modules}


def method_formulas(names):
    """Include the executed callable, scoring dispatch and the numerical helpers."""
    import inspect
    from loto_enterprise.benchmark import methods_common

    dispatcher = inspect.unwrap(registry.call_method)
    files = {Path(methods_common.__file__), Path(registry.__file__)}
    if inspect.isfunction(dispatcher):
        source = Path(inspect.getfile(dispatcher))
        if source.is_file():
            files.add(source)
    formulas = {}
    for name in sorted(set(names)):
        record = registry.METHODS.get(registry.resolve_method_name(name))
        if record is None:
            formulas[name] = None
            continue
        fn = inspect.unwrap(record[0])
        formulas[name] = [_function_digest(fn)]
        helpers = (fn.__globals__.get(key) for key in fn.__code__.co_names) if inspect.isfunction(fn) else ()
        for function in (fn, *helpers):
            if inspect.isfunction(function) and function.__module__.startswith("loto_enterprise."):
                files.add(Path(inspect.getfile(function)))
                if function is not fn:
                    formulas[name].append(_function_digest(function))
    sources = sorted((file.name, _module_digest(file.read_bytes())) for file in files)
    return _digest((formulas, _function_digest(dispatcher), sources, _numeric_runtime()))


def lr_semantics(lot, scheme, hit_target, *, layout=None):
    from covering import lr_any_prize_649, lr_any_prize_joker, lr_budget, lr_pool_budget, lr_pool_milp, lr_pool_ranking
    from covering import lr_catalog_cache, lr_portfolio, lr_schemes, lr_selection, probability
    from loto_enterprise.benchmark import decision, lr_budget_scoring, lr_hypotheses, restricted_decision, runner
    from loto_enterprise.core import dynamic_pools, dynamic_winner, lr_budget_forecast, lr_range_analysis, ranking
    from loto_enterprise.core import lr_played_portfolio, lr_ticket_experts, prize_categories

    catalog = {key: tuple(lr_hypotheses._methods(key)) for key in lot.bench_keys}
    selection_modules = (lr_hypotheses, decision, runner, restricted_decision, lr_pool_ranking,
                         dynamic_winner, dynamic_pools, ranking, lr_range_analysis, lr_schemes, lr_selection,
                         lr_played_portfolio, lr_ticket_experts, lr_budget_scoring, lr_budget_forecast,
                         lr_budget, lr_pool_budget, lr_pool_milp, lr_portfolio, lr_any_prize_649, lr_any_prize_joker,
                         prize_categories, probability, lr_catalog_cache)
    rules = _module_rules(selection_modules)
    executed = [_function_digest(f) for f in (
        lr_hypotheses.rank_lr_pool, lr_hypotheses.rank_by_score, lr_hypotheses._evaluate_fold,
        decision.decide_optimal_config_for_pool, lr_played_portfolio.grade_plan, lr_played_portfolio.grade_draw,
        lr_budget_scoring.evaluate_budget_recipes, lr_budget_scoring.restrict_to_pool,
        lr_budget_forecast.choose_dynamic_winner,
    )]
    return {
        "version": 1,
        "catalog": _digest([(k, registry.catalog_version_for_game(k), v) for k, v in catalog.items()]),
        "formulas": method_formulas({name for names in catalog.values() for name in names}
                                    | set(dynamic_winner.CANDIDATES) | set(lr_ticket_experts.DEFAULT_EXPERTS)
                                    | {"frequency", "random"}),
        "selection": _digest((rules, executed, layout_formulas(), tuple(lr_hypotheses.VALIDATION_PERCENTILES),
                              decision.game_hit_target(lot.bench_key, hit_target),
                              lr_pool_ranking.LR_MAX_CONSECUTIVE_RUN)),
        "geometry": _digest((lot.game_id, lot.bench_keys, asdict(lot.geo), lot.min_hit_target)),
        "scheme": _digest(((scheme.code, scheme.n, scheme.variants), [
            (s.code, s.n, s.variants) for s in lr_schemes.lr_schemes(scheme.game)
        ])),
        "layout": lr_played_portfolio.layout_identity(layout) if layout is not None else None,
    }


def layout_formulas():
    """Identity of the loaded optimizer used by the in-process layout cache."""
    from covering import lr_pool_budget, lr_pool_milp

    search = lr_pool_budget._PortfolioSearch
    executed = (
        lr_pool_budget._refine_layout, lr_pool_budget._default_search_seconds,
        search.optimize, search.search, search.grow, search.consider, search.solve,
        lr_pool_milp.solve_zone_coverage, lr_pool_milp._classes,
    )
    return _digest(([_function_digest(f) for f in executed], _numeric_runtime()))


def dynamic_formulas():
    """Bind a hot scoring cache to the formulas and the code that executes them."""
    from loto_enterprise.core import dynamic_pools, dynamic_winner, lr_budget_forecast, ranking

    formulas = method_formulas((*dynamic_winner.CANDIDATES, "frequency", "random"))
    rules = _module_rules((dynamic_pools, dynamic_winner, ranking))
    executed = [_function_digest(f) for f in (
        lr_budget_forecast._dynamic_scores, lr_budget_forecast.choose_dynamic_winner,
        dynamic_winner.choose_dynamic_winner,
    )]
    return _digest((formulas, rules, executed))
