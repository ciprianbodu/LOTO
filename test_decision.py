"""
Teste pentru `loto_enterprise/benchmark/decision.py` — robustețea statistică
introdusă pentru rate_4plus (limita Wilson), parte din optimizarea "hits+4":

  1. `_wilson_lower_bound` — sanitate + monotonicitate (mai multe dovezi la
     aceeași proporție => limită inferioară mai mare, mai apropiată de rata brută).
  2. Caz sintetic reprezentativ pentru bug-ul descoperit în date reale (vezi
     verificarea pe bench_results/folds.csv): o metodă cu rată brută mai mare
     doar datorită unei ferestre MICI/zgomotoase (10%) poate avea, agregat, mai
     PUȚINE evenimente 4+ totale decât o metodă cu rată brută mai mică dar mai
     multe evenimente pe volum mare de date. Media neponderată pe ferestre
     (vechiul comportament) alege greșit metoda cu zgomot; Wilson (pooled pe
     n_test real) alege corect metoda cu mai multe dovezi.
"""
from __future__ import annotations

import pandas as pd
import pytest

from loto_enterprise.benchmark import decision
from loto_enterprise.benchmark import methods as methods_mod


# Nume sintetice din fixture-uri. decide_optimal_config_for_pool păstrează
# doar metode din METHODS minus tombstone (folds vechi cu omnius etc.) —
# fără înregistrare, testele cadeau tăcut pe fallback-ul `frequency`.
_SYNTHETIC_METHODS = (
    "noisy_smallwindow",
    "robust_morevidence",
    "some_method",
    "weak_method",
    *(f"method_{i}" for i in range(6)),
)


@pytest.fixture(autouse=True)
def _register_synthetic_methods(monkeypatch):
    extra = {
        n: (lambda *_a, **_k: {}, "test", False, "synthetic")
        for n in _SYNTHETIC_METHODS
    }
    monkeypatch.setattr(methods_mod, "METHODS", {**methods_mod.METHODS, **extra})



def test_wilson_lower_bound_zero_events_is_zero():
    assert decision._wilson_lower_bound(0, 100) == 0.0
    assert decision._wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_more_evidence_same_rate_scores_higher():
    # Aceeași proporție (phat=0.1), dar cu de 10x mai multe date/evenimente —
    # limita inferioară trebuie să fie mai mare (mai multă încredere).
    few_evidence = decision._wilson_lower_bound(1, 10)
    more_evidence = decision._wilson_lower_bound(10, 100)
    assert more_evidence > few_evidence
    # Ambele sub rata brută (0.1) — Wilson e conservator prin construcție.
    assert few_evidence < 0.1
    assert more_evidence < 0.1


def test_wilson_lower_bound_monotonic_in_n_at_fixed_phat():
    vals = [decision._wilson_lower_bound(n * 0.2, n) for n in (10, 50, 100, 500, 2000)]
    assert vals == sorted(vals)


def _make_folds_row(game, method, pct, n_test, k_col, k_val, rate_col, rate_val,
                     is_random=False, failed=False):
    return {
        "game": game,
        "method": method,
        "percentile": pct,
        "n_test": n_test,
        "is_random": is_random,
        "failed": failed,
        "runtime_sec": 0.1,
        k_col: k_val,
        rate_col: rate_val,
    }


@pytest.fixture
def noisy_vs_robust_folds() -> pd.DataFrame:
    """Reproduce, sintetic, exact tiparul găsit în bench_results/folds.csv real
    (ex. loto_6_49 k10: prime_bias vs ml_ridge_cv) — o metodă câștigă pe medie
    brută doar din fereastra mică (10%), dar are mai PUȚINE evenimente 4+
    agregate decât concurenta ei."""
    game = "test_game"
    pool = 10
    k_col = f"k{pool}"
    rate_col = f"rate_4plus_k{pool}"
    windows = [(10, 25), (30, 75), (60, 150), (100, 250)]  # (pct, n_test)

    rows = []
    # random baseline: bate niciodată metodele de mai jos (avg_hits mic constant)
    for pct, n_test in windows:
        rows.append(_make_folds_row(game, "random", pct, n_test, k_col, 1.0, rate_col, 0.01))

    # Metoda "noisy": rată brută mare doar în fereastra mică (2/25=0.08), apoi
    # scade constant — 8 evenimente 4+ în total din 500 extrageri testate.
    noisy_events = {10: 2, 30: 2, 60: 2, 100: 2}  # total 8 / 500
    for pct, n_test in windows:
        rate = noisy_events[pct] / n_test
        rows.append(_make_folds_row(game, "noisy_smallwindow", pct, n_test, k_col, 1.5, rate_col, rate))

    # Metoda "robust": rată brută mică în fereastra mică (0/25), dar mult mai
    # multe evenimente agregate — 19 evenimente 4+ în total din 500 extrageri.
    robust_events = {10: 0, 30: 3, 60: 6, 100: 10}  # total 19 / 500
    for pct, n_test in windows:
        rate = robust_events[pct] / n_test
        rows.append(_make_folds_row(game, "robust_morevidence", pct, n_test, k_col, 1.4, rate_col, rate))

    return pd.DataFrame(rows)


def test_raw_mean_would_pick_the_noisy_method(noisy_vs_robust_folds):
    """Sanity check pe fixture: confirmă că media BRUTĂ (comportamentul vechi)
    ar alege metoda cu zgomot — altfel testul de mai jos nu ar demonstra nimic."""
    df = noisy_vs_robust_folds
    rate_col = "rate_4plus_k10"
    noisy_mean = df[df["method"] == "noisy_smallwindow"][rate_col].mean()
    robust_mean = df[df["method"] == "robust_morevidence"][rate_col].mean()
    assert noisy_mean > robust_mean


def test_decide_optimal_config_picks_method_with_more_pooled_evidence(noisy_vs_robust_folds):
    """Testul central: decizia NOUĂ trebuie să aleagă 'robust_morevidence'
    (mai multe evenimente 4+ agregate), NU 'noisy_smallwindow' (rată brută mai
    mare doar din fereastra mică/zgomotoasă)."""
    cfg = decision.decide_optimal_config_for_pool(
        noisy_vs_robust_folds, game_key="test_game", pool_size=10, draw_n=6,
    )
    assert cfg.get("scorer") == "robust_morevidence"


def test_decide_optimal_config_returns_valid_structure_on_simple_case():
    """Caz simplu, non-adversarial: structura returnată rămâne validă."""
    game = "simple_game"
    pool = 6
    k_col = f"k{pool}"
    rate_col = f"rate_4plus_k{pool}"
    rows = []
    for pct, n_test in [(10, 30), (100, 300)]:
        rows.append(_make_folds_row(game, "random", pct, n_test, k_col, 1.0, rate_col, 0.02))
        rows.append(_make_folds_row(game, "some_method", pct, n_test, k_col, 1.3, rate_col, 0.03))
    df = pd.DataFrame(rows)

    cfg = decision.decide_optimal_config_for_pool(df, game_key=game, pool_size=pool, draw_n=6)

    assert cfg.get("scorer") == "some_method"
    assert "sim_depth_pct" in cfg
    assert "use_blacklist" in cfg
    assert "rationale" in cfg


# ---------------------------------------------------------------------------
# _build_ensemble_weights / ensemble field (variance-reduction)
# ---------------------------------------------------------------------------
def test_build_ensemble_weights_single_entry_gets_full_weight():
    out = decision._build_ensemble_weights([("only_method", 0.05)])
    assert out == [{"method": "only_method", "weight": 1.0}]


def test_build_ensemble_weights_proportional_to_confidence():
    out = decision._build_ensemble_weights([("a", 0.06), ("b", 0.03), ("c", 0.01)])
    by_method = {e["method"]: e["weight"] for e in out}
    # proporțional cu confidence: a:b:c = 6:3:1 -> suma = 10
    assert by_method["a"] == pytest.approx(0.6, abs=1e-3)
    assert by_method["b"] == pytest.approx(0.3, abs=1e-3)
    assert by_method["c"] == pytest.approx(0.1, abs=1e-3)
    assert sum(by_method.values()) == pytest.approx(1.0, abs=1e-3)


def test_build_ensemble_weights_all_zero_confidence_splits_equally():
    """Evenimente insuficiente pt toate metodele (confidence=0) — ponderi
    egale, nu excludere completă (floor 1e-6 în implementare)."""
    out = decision._build_ensemble_weights([("a", 0.0), ("b", 0.0)])
    by_method = {e["method"]: e["weight"] for e in out}
    assert by_method["a"] == pytest.approx(0.5, abs=1e-3)
    assert by_method["b"] == pytest.approx(0.5, abs=1e-3)


def test_build_ensemble_weights_empty_input():
    assert decision._build_ensemble_weights([]) == []


def test_decide_optimal_config_includes_ensemble_field(noisy_vs_robust_folds):
    """Decizia trebuie să includă un câmp 'ensemble' (top metode calificate +
    ponderi), consumat de method_selector.get_ensemble_for_game."""
    cfg = decision.decide_optimal_config_for_pool(
        noisy_vs_robust_folds, game_key="test_game", pool_size=10, draw_n=6,
    )
    assert "ensemble" in cfg
    assert isinstance(cfg["ensemble"], list)
    assert cfg["ensemble"], "ensemble nu trebuie să fie gol când există metode calificate"
    total_weight = sum(e["weight"] for e in cfg["ensemble"])
    assert total_weight == pytest.approx(1.0, abs=1e-3)
    # câștigătorul unic (scorer) trebuie să fie primul/dominant în ensemble
    assert cfg["ensemble"][0]["method"] == cfg["scorer"]


def test_decide_optimal_config_ensemble_capped_at_max_methods():
    """Cu mai multe metode calificate decât ENSEMBLE_MAX_METHODS, ensemble-ul
    nu trebuie să depășească limita."""
    game = "many_methods_game"
    pool = 6
    k_col = f"k{pool}"
    rate_col = f"rate_4plus_k{pool}"
    rows = []
    windows = [(10, 50), (100, 500)]
    for pct, n_test in windows:
        rows.append(_make_folds_row(game, "random", pct, n_test, k_col, 1.0, rate_col, 0.01))
        for i in range(6):  # mai multe metode calificate decât ENSEMBLE_MAX_METHODS (3)
            rate = 0.02 + i * 0.001
            rows.append(_make_folds_row(game, f"method_{i}", pct, n_test, k_col, 1.2 + i * 0.01,
                                         rate_col, rate))
    df = pd.DataFrame(rows)

    cfg = decision.decide_optimal_config_for_pool(df, game_key=game, pool_size=pool, draw_n=6)

    assert len(cfg["ensemble"]) <= decision.ENSEMBLE_MAX_METHODS


def test_decide_optimal_config_fallback_branch_has_single_member_ensemble():
    """Pe ramura FALLBACK (nicio metodă bate random ≥60%), ensemble-ul trebuie
    să rămână conservator — un singur membru, nu combinăm metode neconfirmate."""
    game = "fallback_game"
    pool = 6
    k_col = f"k{pool}"
    rate_col = f"rate_4plus_k{pool}"
    rows = []
    for pct, n_test in [(10, 30), (100, 300)]:
        # metoda e mai SLABĂ decât random -> nu se califică (nu bate random)
        rows.append(_make_folds_row(game, "random", pct, n_test, k_col, 2.0, rate_col, 0.05))
        rows.append(_make_folds_row(game, "weak_method", pct, n_test, k_col, 1.0, rate_col, 0.02))
    df = pd.DataFrame(rows)

    cfg = decision.decide_optimal_config_for_pool(df, game_key=game, pool_size=pool, draw_n=6)

    assert cfg["rationale"].startswith("FALLBACK")
    assert cfg["ensemble"] == [{"method": cfg["scorer"], "weight": 1.0}]


def test_clamp_bench_hit_target_only_3_or_4():
    assert decision.clamp_bench_hit_target(3) == 3
    assert decision.clamp_bench_hit_target(4) == 4
    assert decision.clamp_bench_hit_target("4") == 4
    assert decision.clamp_bench_hit_target(5) == 3
    assert decision.clamp_bench_hit_target(2) == 3
    assert decision.clamp_bench_hit_target("nope") == 3
    assert decision.clamp_bench_hit_target(None) == 3
    assert decision.clamp_bench_hit_target(5, default=4) == 4
