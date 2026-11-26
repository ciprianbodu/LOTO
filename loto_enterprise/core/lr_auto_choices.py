"""Choose the three LR switches on paid portfolios before the final walk-forward window."""
from itertools import product
from math import isfinite
from pathlib import Path

import pandas as pd

from covering.lr_budget import budget_in_bani
from covering.lr_pool_ranking import LR_RESTRICTED_RANGE
from loto_enterprise.benchmark.lr_budget_scoring import MIN_PORTFOLIO_DAYS
from loto_enterprise.benchmark.lr_hypotheses import _cancellation
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import _forecast_engine
from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS
from loto_enterprise.core.lr_walk_forward import _holdout, validate_lr_walk_forward


# Use at most the previous twelve complete draw days for the eight-way comparison.
COMPARISON_DAYS = MIN_PORTFOLIO_DAYS


def _selection_history(game, history, depth):
    if history is None:
        raise ValueError("Selecția automată cere istoricul jocului.")
    if not isfinite(depth) or not 0 < depth <= 100:
        raise ValueError("Adâncimea validării trebuie să fie în intervalul (0, 100].")
    lot = require_lottery(game, "RO")
    source = (Path(lot.csv).name, history) if isinstance(history, pd.DataFrame) else history
    engine = _forecast_engine(lot, source, 0)
    frame = engine.data
    dates = history_dates(frame)
    if dates is None:
        raise ValueError("Selecția automată cere data fiecărei extrageri.")
    bounds = training_cutoffs(frame)
    stop = _holdout(bounds, depth)
    starts = list(dict.fromkeys(bounds[:stop]))
    if len(starts) < 2:
        raise ValueError("Istoric insuficient înaintea validării pentru a compara bifele automat. "
                         "Alegeți o fereastră de validare mai scurtă sau modul manual.")

    start = starts[max(1, len(starts) - COMPARISON_DAYS)]
    # _holdout converts percentage to a whole row count. A half-row margin keeps the
    # intended integer stable under float division; start and stop are day boundaries.
    inner_depth = 100 * (stop - start + 0.5) / stop
    labels = dates.iloc[start:stop].dt.strftime("%Y-%m-%d").tolist()
    return (source[0], frame.iloc[:stop]), inner_depth, labels, dates.iloc[stop].date().isoformat()


def _paid_result(report, labels, budget_bani):
    if report.get("profile_reference_mismatch"):
        raise ValueError("Referința inversă validată diferă de referința folosită la selecție.")
    if (report.get("portfolio_refinement") or {}).get("complete") is not True:
        raise ValueError("Portofoliul de bilete a rămas provizoriu.")
    draws = report["draws"]
    if [draw["date"] for draw in draws] != labels:
        raise ValueError("Zilele sau extragerile comparate diferă.")
    for draw in draws:
        if draw.get("error"):
            raise ValueError(draw["error"])
        if type(draw.get("objective_hit")) is not bool or not draw.get("paid_variants"):
            raise ValueError("Obiectivul nu poate fi evaluat pe toate biletele.")
        if not 0 < draw["price_bani"] <= budget_bani:
            raise ValueError("Costul biletului este incompatibil cu bugetul.")
    return sum(draw["objective_hit"] for draw in draws)


def select_lr_auto_choices(game_id, history, options, *, depth, replay_policy="fixed", cancel_path=None,
                           progress_cb=None):
    """Compare on common earlier draws; ties prefer direct, unrestricted, standard selection."""
    check = _cancellation(cancel_path)
    check(force=True)
    source, inner_depth, labels, validation_start = _selection_history(game_id, history, float(depth))
    shared = {k: options[k] for k in ("budget_lei", "objective", "scheme", "dynamic",
                                      "lookback", "hit_target", "joker_size")}
    cap = budget_in_bani(shared["budget_lei"])
    candidates = []
    winner = None
    if progress_cb:
        progress_cb(0, 8)
    for inverse, restricted, fixed_share in product((False, True), repeat=3):
        check(force=True)
        candidate = dict(inverse=inverse, restricted=restricted, fixed_share=fixed_share)
        try:
            report = validate_lr_walk_forward(
                game_id, source, **shared, inverse=inverse, restricted=restricted, depth=inner_depth,
                selection_policy=ONLINE_TICKET_EXPERTS if fixed_share else None,
                replay_policy=replay_policy, cancel_path=cancel_path,
            )
            hits = _paid_result(report, labels, cap)
            candidate.update(hits=hits, evaluated=len(labels))
            if winner is None or hits > winner["hits"]:
                winner = candidate
        except ValueError as exc:
            candidate["error"] = str(exc)
        candidates.append(candidate)
        if progress_cb:
            progress_cb(len(candidates), 8)
    check(force=True)
    if winner is None:
        reasons = " · ".join(dict.fromkeys(c["error"] for c in candidates))
        raise ValueError(f"Nicio combinație a bifelor nu a putut fi evaluată integral la {game_id}. {reasons}")

    return {**winner, "first_date": labels[0], "last_date": labels[-1], "days": len(set(labels)),
            "restricted_range": list(LR_RESTRICTED_RANGE), "validation_start": validation_start,
            "training_rows": len(source[1]), "candidates": candidates}
