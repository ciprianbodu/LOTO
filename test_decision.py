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
     (vechiul comportament) alege greșit metoda cu zgomot; Wilson pe rata
     pooled alege corect metoda cu mai multe dovezi.
  3. `pooled_rate_and_neff` — agregarea canonică: rata rămâne pooled (ponderată
     implicit pe recență), dar n-ul dat lui Wilson e cel EFECTIV (Kish), fiindcă
     ferestrele sim_depth sunt sufixe CUIBĂRITE; plus fallback-ul n_eval→n_test
     aplicat PE RÂND (folds mixte peste un bump de versiune).
"""
from __future__ import annotations

import pandas as pd
import pytest

from loto_enterprise.benchmark import decision

# decision.decide_optimal_config_for_pool filtrează candidații pe registry-ul
# METHODS (sare tombstone-urile/numele necunoscute din folds vechi). Numele
# SINTETICE de mai jos nu există în registry → fără înregistrare temporară,
# toate testele cădeau tăcut pe fallback-ul 'frequency' și nu mai verificau nimic.
_SYNTHETIC_METHODS = [
    "noisy_smallwindow", "robust_morevidence", "some_method", "weak_method",
    "avg_only_trap", "target_rate_winner", "missing_rate_method", "single_pick_winner",
    *[f"method_{i}" for i in range(6)],
]


@pytest.fixture(autouse=True)
def _register_synthetic_methods(monkeypatch):
    from loto_enterprise.benchmark import methods as _mm
    for _name in _SYNTHETIC_METHODS:
        if _name not in _mm.METHODS:
            monkeypatch.setitem(
                _mm.METHODS, _name,
                (lambda draws, max_num: {}, "test", False, "sintetic (doar teste)"),
            )


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


def test_consistency_gate_uses_same_target_rate_as_winner(monkeypatch):
    """Mai multe hituri MEDII nu califică o metodă care pierde la ținta 3+.

    Tiparul advers: `avg_only_trap` produce mai multe 2-hit-uri, deci bate
    random pe media k10 în toate ferestrele, dar pierde la 3+ în toate.
    `target_rate_winner` are media puțin mai mică decât random, însă îl bate
    exact pe rata 3+ cerută. Gate-ul trebuie să aleagă a doua metodă.
    """
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    rows = []
    for pct, n_test in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_make_folds_row(
            "target_gate", "random", pct, n_test, "k10", 1.0,
            "rate_3plus_k10", 0.10,
        ))
        rows.append(_make_folds_row(
            "target_gate", "avg_only_trap", pct, n_test, "k10", 1.4,
            "rate_3plus_k10", 0.09,
        ))
        rows.append(_make_folds_row(
            "target_gate", "target_rate_winner", pct, n_test, "k10", 0.9,
            "rate_3plus_k10", 0.12,
        ))

    cfg = decision.decide_optimal_config_for_pool(
        pd.DataFrame(rows), game_key="target_gate", pool_size=10, draw_n=6,
    )

    assert cfg["scorer"] == "target_rate_winner"
    assert cfg["qualifying_methods"] == 1
    assert "same 3+ target" in cfg["rationale"]


def test_single_pick_uses_top1_rate_not_the_global_3plus_target():
    """Urna 2 trebuie decisă pe 1/1 chiar dacă UI-ul are ținta globală +3/+4."""
    rows = []
    for pct, n_test in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_make_folds_row(
            "joker_urna2", "random", pct, n_test, "k1", 0.05,
            "rate_1plus_k1", 0.05,
        ))
        rows.append(_make_folds_row(
            "joker_urna2", "single_pick_winner", pct, n_test, "k1", 0.09,
            "rate_1plus_k1", 0.09,
        ))

    cfg = decision.decide_optimal_config_for_pool(
        pd.DataFrame(rows), game_key="joker_urna2", pool_size=1, draw_n=1,
    )

    assert cfg["scorer"] == "single_pick_winner"
    assert cfg["rate_col_used"] == "rate_1plus_k1"
    assert cfg["hit_target"] == 1
    assert cfg["target_label"] == "top-1 (1/1)"


def test_unsuffixed_rate_is_not_used_for_a_different_pool(monkeypatch):
    """`rate_3plus` este k=draw_n și nu poate decide o celulă k10."""
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    rows = []
    for pct, n_test in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_make_folds_row(
            "wrong_pool_rate", "random", pct, n_test, "k10", 1.0,
            "rate_3plus", 0.01,
        ))
        rows.append(_make_folds_row(
            "wrong_pool_rate", "some_method", pct, n_test, "k10", 1.5,
            "rate_3plus", 0.20,
        ))

    cfg = decision.decide_optimal_config_for_pool(
        pd.DataFrame(rows), game_key="wrong_pool_rate", pool_size=10, draw_n=6,
    )

    assert cfg["scorer"] == decision.SAFE_FALLBACK_SCORER
    assert cfg["low_confidence"] is True
    assert cfg["rate_col_used"] is None
    assert cfg["rate_col_mismatch"] is True


def test_method_without_common_rate_data_cannot_win(monkeypatch):
    """Un rând parțial cu avg_hits mare, dar rată T+ NaN, este sărit."""
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    rows = []
    for pct, n_test in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(_make_folds_row(
            "partial_rate", "random", pct, n_test, "k10", 1.0,
            "rate_3plus_k10", 0.10,
        ))
        rows.append(_make_folds_row(
            "partial_rate", "missing_rate_method", pct, n_test, "k10", 9.0,
            "rate_3plus_k10", float("nan"),
        ))

    cfg = decision.decide_optimal_config_for_pool(
        pd.DataFrame(rows), game_key="partial_rate", pool_size=10, draw_n=6,
    )

    assert cfg["scorer"] == decision.SAFE_FALLBACK_SCORER
    assert cfg["low_confidence"] is True


# ---------------------------------------------------------------------------
# pooled_rate_and_neff / pooled_wilson_distinct — ferestre CUIBARITE
# ---------------------------------------------------------------------------
def _neff(sizes):
    """n efectiv Kish pentru ferestre-sufix: (sum n)^2 / sum_ik min(n_i, n_k)."""
    tot = float(sum(sizes))
    return tot * tot / float(sum(min(a, b) for a in sizes for b in sizes))


def test_neff_is_kish_not_sum_and_not_max():
    """n-ul dat lui Wilson nu e nici suma (numara extrageri de mai multe ori),
    nici max (rata e o medie PONDERATA) — e n efectiv Kish, intre cele doua."""
    sizes = [258, 774, 1547, 2497]
    df = pd.DataFrame({"r": [0.04] * 4, "n_test": sizes})
    phat, n_eff = decision.pooled_rate_and_neff(df, "r")
    assert phat == pytest.approx(0.04)
    assert n_eff == pytest.approx(_neff(sizes), rel=1e-9)
    assert max(sizes) * 0.75 < n_eff < max(sizes)      # ~0.805 x n_max
    assert n_eff < sum(sizes)                          # strict sub varianta veche


def test_pooled_wilson_below_old_sum_based_bound():
    """Corectia trebuie sa fie CONSERVATOARE fata de formula veche (pooled pe suma)."""
    sizes = [100, 500]
    df = pd.DataFrame({"r": [0.04, 0.04], "n_test": sizes})
    new = decision.pooled_wilson_distinct(df, "r")
    old = decision._wilson_lower_bound(0.04 * sum(sizes), sum(sizes))
    assert new < old
    assert new == pytest.approx(
        decision._wilson_lower_bound(0.04 * _neff(sizes), _neff(sizes))
    )


def test_pooled_rate_stays_recency_weighted():
    """Rata ramane pooled pe TOATE ferestrele (ponderare implicita pe recenta)."""
    df = pd.DataFrame({"r": [0.10, 0.02], "n_test": [100, 400]})
    phat, _ = decision.pooled_rate_and_neff(df, "r")
    assert phat == pytest.approx((0.10 * 100 + 0.02 * 400) / 500)


def test_single_window_matches_plain_wilson():
    """Cu o singura fereastra, agregarea degenereaza la Wilson clasic."""
    df = pd.DataFrame({"r": [0.09], "n_test": [2497]})
    assert decision.pooled_wilson_distinct(df, "r") == pytest.approx(
        decision._wilson_lower_bound(0.09 * 2497, 2497)
    )


def test_n_eval_fallback_is_PER_ROW_not_per_frame():
    """Folds MIXT (unele randuri v13 cu n_eval, altele vechi fara): randurile vechi
    trebuie sa cada pe n_test, NU sa fie aruncate din agregare."""
    mixed = pd.DataFrame({
        "r": [0.10, 0.02],
        "n_test": [100, 400],
        "n_eval": [float("nan"), 400],
    })
    clean = pd.DataFrame({"r": [0.10, 0.02], "n_test": [100, 400]})
    assert decision.pooled_rate_and_neff(mixed, "r") == pytest.approx(
        decision.pooled_rate_and_neff(clean, "r")
    )


def test_n_eval_preferred_and_zero_rows_dropped():
    """n_eval e denominatorul cand e valid; o fereastra cu n_eval=0 si n_test=0
    (nimic evaluat) nu are ce contribui."""
    df = pd.DataFrame({"r": [0.5, 0.0], "n_test": [100, 0], "n_eval": [100, 0]})
    phat, n_eff = decision.pooled_rate_and_neff(df, "r")
    assert (phat, n_eff) == pytest.approx((0.5, 100.0))


def test_pooled_none_on_missing_or_empty():
    assert decision.pooled_rate_and_neff(pd.DataFrame({"x": [1]}), "r") is None
    assert decision.pooled_wilson_distinct(pd.DataFrame({"x": [1]}), "r") is None
    assert decision.pooled_rate_and_neff(pd.DataFrame({"r": [0.1], "n_test": [0]}), "r") is None


def test_clamp_bench_hit_target_only_3_or_4():
    assert decision.clamp_bench_hit_target(3) == 3
    assert decision.clamp_bench_hit_target(4) == 4
    assert decision.clamp_bench_hit_target("4") == 4
    assert decision.clamp_bench_hit_target(5) == 3
    assert decision.clamp_bench_hit_target(2) == 3
    assert decision.clamp_bench_hit_target("nope") == 3
    assert decision.clamp_bench_hit_target(None) == 3
    assert decision.clamp_bench_hit_target(5, default=4) == 4


# ---------------------------------------------------------------------------
# bench_cache: numele fisierelor poarta versiunea -> curatare selectiva posibila
# ---------------------------------------------------------------------------
def test_fold_cache_key_carries_version_prefix():
    from loto_enterprise.benchmark import bench_cache as bc
    key = bc._fold_key("deadbeef", "frequency", 30, "loto_6_49", False)
    assert key.startswith(bc.CACHE_VERSION + "_")


def test_purge_stale_fold_cache_keeps_current_version(tmp_path, monkeypatch):
    from loto_enterprise.benchmark import bench_cache as bc
    monkeypatch.setattr(bc, "CACHE_DIR", tmp_path)
    (tmp_path / f"{bc.CACHE_VERSION}_aaa.pkl").write_bytes(b"x")
    (tmp_path / "v11_bbb.pkl").write_bytes(b"y")
    (tmp_path / "legacy_no_prefix.pkl").write_bytes(b"z")  # dinainte de schema
    info = bc.purge_stale_fold_cache(dry_run=True)
    assert (info["kept"], info["stale"], info["deleted"]) == (1, 2, 0)
    assert len(list(tmp_path.glob("*.pkl"))) == 3  # dry-run nu sterge nimic
    info = bc.purge_stale_fold_cache(dry_run=False)
    assert (info["kept"], info["stale"], info["deleted"]) == (1, 2, 2)
    assert [f.name for f in tmp_path.glob("*.pkl")] == [f"{bc.CACHE_VERSION}_aaa.pkl"]
