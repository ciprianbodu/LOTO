"""ETA-ul Re-Bench din sidebar: `_target_bench_folds` + `_estimate_bench_eta`.

`_estimate_bench_eta` exista de mult, dar nu era apelata nicaieri — ETA-ul nu
ajungea niciodata pe ecran. `_target_bench_folds` calculeaza cate folduri va
rula CU ADEVARAT `run_rebench()` (fara `--methods`/`--quick`), replicand
poarta exacta din `bench_all_methods.py`: matricea restransa per joc
(`resolve_methods_per_game`) se aplica DOAR cu curarea activa; altfel CLI-ul
ruleaza toata lista pe fiecare joc, si estimarea trebuie sa reflecte asta —
nu doar sa arate un numar plauzibil.
"""

import app_nicegui as app
from loto_enterprise.benchmark import curated, runner


def test_target_bench_folds_matches_the_documented_matrix():
    """Cu curarea activa (starea reala a repo-ului), formula din CLAUDE.md §5:
    suma metodelor per joc dupa `resolve_methods_per_game` ori numarul de
    ferestre din `_PCTS`. Nu hardcodam 332 — CLAUDE.md spune explicit sa nu
    copiem cifre in cod — recalculam aceeasi formula independent."""
    from loto_enterprise.benchmark.curated import (
        apply_curation,
        resolve_methods_per_game,
    )
    from loto_enterprise.benchmark.disabled import load_disabled
    from loto_enterprise.benchmark.methods import list_methods, method_meta

    disabled = load_disabled()
    avail = [
        m
        for m in list_methods()
        if method_meta(m).get("available", True) and m not in disabled
    ]
    kept, info = apply_curation(avail)
    games = runner.discover_games()
    expected = 0
    if info.get("active"):
        per_game = resolve_methods_per_game(kept, (g.key for g in games))
        expected = (
            sum(len(v) for v in per_game.values())
            if per_game
            else len(kept) * len(games)
        )
    else:
        expected = len(kept) * len(games)
    n_windows = len([p for p in app._PCTS.split(",") if p.strip()])
    expected *= max(1, n_windows)
    assert app._target_bench_folds() == expected
    assert expected > 0


def test_target_bench_folds_is_larger_without_curation():
    """Poarta din `bench_all_methods.py`: matricea restransa per joc se aplica
    DOAR cu curarea activa. Fara ea, `run_rebench()` ruleaza toata lista de
    metode pe fiecare din cele 4 jocuri — un numar mult mai mare, nu acelasi."""
    original = curated.load_curated
    curated.load_curated = lambda: {}
    try:
        without = app._target_bench_folds()
    finally:
        curated.load_curated = original
    with_curation = app._target_bench_folds()
    assert without > with_curation > 0


def test_target_bench_folds_falls_back_to_zero_without_istoric():
    """`discover_games()` arunca fara CSV-uri — sidebar-ul nu are voie sa pice,
    doar sa piarda ETA-ul (label-ul din UI e afisat condiționat pe > 0)."""
    original = runner.discover_games

    def _raise(*args, **kwargs):
        raise FileNotFoundError("niciun istoric")

    runner.discover_games = _raise
    try:
        assert app._target_bench_folds() == 0
    finally:
        runner.discover_games = original


def test_estimate_bench_eta_uses_the_last_runs_average_runtime(tmp_path, monkeypatch):
    """Verificare directa a formulei: avg(runtime_sec) * target_folds * overhead."""
    import pandas as pd

    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    (tmp_path / "bench_results").mkdir()
    pd.DataFrame(
        {"runtime_sec": [1.0, 2.0, 3.0], "failed": [False, False, False]}
    ).to_csv(tmp_path / "bench_results" / "folds.csv", index=False)
    # avg=2.0, target=100, overhead implicit 1.25 -> 250s -> "~4 min"
    assert app._estimate_bench_eta(100) == "~4 min"


def test_estimate_bench_eta_falls_back_without_prior_bench(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    assert app._estimate_bench_eta(332) == "~5 min"
    assert app._estimate_bench_eta(1200) == "~50 min"


def test_sidebar_label_text_matches_what_the_button_block_renders():
    """Reproduce exact blocul din sidebar (fara sa randam tot `main_page`, care
    cere un client NiceGUI conectat) si verifica ca textul contine ETA-ul si
    numarul de folduri calculate de `_target_bench_folds` — nu doar ca cele
    doua functii exista si nu arunca."""
    from scripts.analysis.audit_output import capture_ui

    folds = app._target_bench_folds()
    assert folds > 0, "testul cere istoric + registry disponibile (rulare locala completa)"
    eta = app._estimate_bench_eta(folds)
    with capture_ui() as ui:
        app.ui.label(
            f"⏱ ETA estimat: {eta} pentru "
            f"{folds} folduri — calculat din durata ultimei rulări; "
            "prima estimare după o schimbare de matrice e optimistă."
        ).classes("text-caption text-grey")
    text = ui.text()
    assert "ETA estimat" in text
    assert eta in text
    assert str(folds) in text
