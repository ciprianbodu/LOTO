"""Paritate bench vs producție (AGENTS.md §12 P1) + regresie fallback top-1.

Testul mare rulează FIECARE metodă activă (curated `active`) pe TOATE cele 4
geometrii de joc (loto_6_49, loto_5_40, joker_urna1, joker_urna2), pe mai multe
prefixe de istoric (tăiate la graniță de zi, ca în runner) și pe TOATE pool-urile
evaluate de bench, și assertă:

1. aceleași scoruri: apelul bench (`call_method`, cu `history_cutoffs` doar pentru
   metodele marcate `_uses_history_cutoffs`) == apelul producție
   (`fn(draws_2d.astype(int64), max_num)`, fără context — engine/scoring.py).
   Metodele cu cutoff-uri pot diferi prin contract (AGENTS.md §2,
   interval_extrema_k16); pentru ele se assertă doar paritatea de acceptare.
2. aceeași ACCEPTARE: `has_usable_score_variance` pe scorurile bench == pe cele
   de producție (aceeași funcție, aceleași scoruri) — un scorer plat/NaN/gol e
   respins identic în ambele lumi, nu cade pe fallback într-una și e acceptat în
   cealaltă.
3. același top-K: `runner._top_k(scores, k)` și `select_pool_from_scores(scores, k,
   blacklist=gol, max_consecutive_run=0)` dau ACEEAȘI MULȚIME de numere (engine-ul
   sortează numeric la ieșire, bench-ul păstrează ordinea rangului — doar
   apartenența e contractul, tie-break-ul o decide la granița k).
   Comparația se face cu limita de consecutive OPRITĂ (pornită, pool-ul jucat
   diferă de top-K prin construcție, §6); blacklist-ul e gol în producție
   (filtrele automate sunt dezactivate în pipeline.py).

Regresia: Urna 2 (top-1) pe un folds.csv VEchi, fără coloana `rate_1plus_k1`,
cade determinist pe `frequency` cu `low_confidence=True` și mismatch semnalizat
(`rate_col_used=None`, `rate_col_mismatch=True`) — niciodată pe `random`,
niciodată eroare, niciodată o altă metrică tăcută.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from loto_enterprise.benchmark import decision
from loto_enterprise.benchmark.curated import load_curated
from loto_enterprise.benchmark.methods import (
    METHODS,
    call_method,
    method_meta,
    resolve_method_name,
)
from loto_enterprise.benchmark.runner import _top_k, discover_games, load_draws
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import has_usable_score_variance

PREFIX_FRACTIONS = (0.6, 0.8, 1.0)


def _pool_sizes(game) -> list[int]:
    """Aceleași pool-uri pe care le evaluează bench-ul (runner._evaluate_fold)."""
    if game.is_single_pick:
        return [game.base_k]
    return [game.base_k + i for i in range(game.pool_extra + 1)]


def _prefix_ends(game, n_rows: int) -> list[int]:
    """Capete de prefix tăiate la graniță de zi, ca în runner (`history_cutoffs`)."""
    out: list[int] = []
    for frac in PREFIX_FRACTIONS:
        idx = min(n_rows - 1, int(frac * n_rows))
        pe = int(game.history_cutoffs[idx]) if game.history_cutoffs else idx + 1
        pe = max(1, min(n_rows, pe))
        if pe not in out:
            out.append(pe)
    return out


def test_active_methods_parity_bench_vs_production_all_games():
    active = load_curated()
    assert "frequency" in active and "random" in active
    games = discover_games()
    assert {g.key for g in games} == {
        "loto_6_49",
        "loto_5_40",
        "joker_urna1",
        "joker_urna2",
    }

    checked = 0
    for game in games:
        draws = load_draws(game)
        for pe in _prefix_ends(game, len(draws)):
            hist = draws[:pe]
            for name in active:
                rn = resolve_method_name(name)
                assert rn in METHODS, f"{name} rezolvă la {rn} care nu e în METHODS"
                meta = method_meta(rn)
                if not meta.get("available"):
                    # runner-ul sare metodele unavailable (SKIP); producția cade pe
                    # frequency — niciuna nu folosește metoda: paritate păstrată.
                    continue
                fn = METHODS[rn][0]

                # Apel producție: engine/scoring.py — fn(draws_2d int64, max_num).
                try:
                    raw = fn(hist.astype(np.int64), game.max_num)
                    prod_err = None
                except Exception as exc:  # noqa: BLE001
                    raw, prod_err = {}, exc

                # Apel bench: runner._evaluate_fold — call_method, cu history_cutoffs
                # doar pentru metodele marcate uses_history_cutoffs.
                ctx = (
                    {"history_cutoffs": game.history_cutoffs[:pe]}
                    if meta.get("uses_history_cutoffs")
                    else {}
                )
                try:
                    bench_scores, _dt = call_method(rn, hist, game.max_num, **ctx)
                    bench_err = None
                except Exception as exc:  # noqa: BLE001
                    bench_scores, bench_err = {}, exc

                assert (bench_err is None) == (prod_err is None), (
                    f"{game.key}/{rn} @ prefix {pe}: bench eroare={bench_err!r}, "
                    f"producție eroare={prod_err!r}"
                )
                if bench_err is not None:
                    continue

                # (2) aceeași acceptare
                assert has_usable_score_variance(bench_scores) == has_usable_score_variance(
                    raw
                ), f"{game.key}/{rn} @ prefix {pe}: acceptare diferă bench vs producție"

                # (1) metodele fără cutoff-uri: aceleași scoruri bit-cu-bit
                if not meta.get("uses_history_cutoffs"):
                    assert bench_scores == raw, (
                        f"{game.key}/{rn} @ prefix {pe}: scoruri diferă bench vs producție"
                    )

                # (3) regula top-K identică pe aceleași scoruri; producție cu
                # blacklist gol și limita de consecutive oprită (mcr=0).
                if has_usable_score_variance(raw):
                    for k in _pool_sizes(game):
                        bench_top = _top_k(raw, k)
                        prod_pool = select_pool_from_scores(
                            raw,
                            k,
                            set(),
                            None,
                            max_num=game.max_num,
                            max_consecutive_run=0,
                        )
                        # Paritate pe MULȚIME (tie-break-ul canonic decide
                        # apartenența la granița k); engine-ul sortează numeric
                        # pool-ul la ieșire, bench-ul păstrează ordinea rangului.
                        assert set(bench_top) == set(prod_pool), (
                            f"{game.key}/{rn} @ prefix {pe} k={k}: "
                            f"top-K bench {bench_top} != pool producție {prod_pool}"
                        )
                        assert prod_pool == sorted(bench_top)
                checked += 1

    # sanity: matricea a fost rulată cu adevărat (nu toate metodele sărite)
    assert checked >= 4 * len(active)


def test_flat_and_nan_scores_rejected_identically_bench_and_production():
    """Scorurile plate / NaN / goale sunt respinse de același predicat în ambele
    lumi, iar regula top-K pe scoruri valide dă același rezultat în ambele."""
    flat = {n: 1.0 for n in range(1, 50)}
    nan = {n: float("nan") for n in range(1, 50)}
    empty = {}
    for scores in (flat, nan, empty):
        assert not has_usable_score_variance(scores)
    good = {n: float(n) for n in range(1, 50)}
    assert has_usable_score_variance(good)
    for k in (6, 12, 16, 20):
        bench_top = _top_k(good, k)
        prod_pool = select_pool_from_scores(
            good, k, set(), None, max_num=49, max_consecutive_run=0
        )
        assert set(bench_top) == set(prod_pool)
        assert prod_pool == sorted(bench_top)


def _folds_row(game, method, pct, n_test, k_col, k_val, extra=None, is_random=False):
    row = {
        "game": game,
        "method": method,
        "percentile": pct,
        "n_test": n_test,
        "is_random": is_random,
        "failed": False,
        "runtime_sec": 0.1,
        k_col: k_val,
    }
    if extra:
        row.update(extra)
    return row


def test_urna2_top1_without_rate_1plus_k1_column_falls_back_to_frequency():
    """Regresie: folds.csv vechi (fără coloana `rate_1plus_k1`) pe Urna 2 k1 →
    fallback determinist pe `frequency`, low_confidence, mismatch semnalizat."""
    rows = []
    for pct, n_test in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        rows.append(
            _folds_row(
                "joker_urna2", "random", pct, n_test, "k1", 0.05,
                extra={"rate_4plus_k1": 0.05},
            )
        )
        rows.append(
            _folds_row(
                "joker_urna2", "some_method", pct, n_test, "k1", 0.09,
                extra={"rate_4plus_k1": 0.09},
            )
        )
    df = pd.DataFrame(rows)
    assert "rate_1plus_k1" not in df.columns  # premisa: cache vechi

    cfg = decision.decide_optimal_config_for_pool(
        df, game_key="joker_urna2", pool_size=1, draw_n=1
    )

    assert "error" not in cfg
    assert cfg["scorer"] == decision.SAFE_FALLBACK_SCORER == "frequency"
    assert cfg["scorer"] != "random"
    assert cfg["low_confidence"] is True
    assert cfg["rate_col_used"] is None
    assert cfg["rate_col_mismatch"] is True
    assert cfg["hit_target"] == 1
    assert cfg["target_label"] == "top-1 (1/1)"
    assert "FALLBACK" in cfg["rationale"]
