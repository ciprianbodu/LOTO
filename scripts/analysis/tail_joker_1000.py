"""Reference single pick for Joker Urna 2 in the 1,000-configuration replay."""

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analysis.legacy_methods_2026_11_20 import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.ranking import rank_by_score

JOKER_HISTORY = Path(__file__).resolve().parents[2] / "_ISTORIC" / "joker.csv"


def joker_matches(dates, cutoffs, final_indices) -> np.ndarray:
    """Score each requested draw against its day's frequency forecast for Urna 2."""
    frame = chronological_history(pd.read_csv(JOKER_HISTORY))
    source_dates = history_dates(frame)
    if source_dates is None or not np.array_equal(dates, source_dates.to_numpy()):
        raise ValueError("Joker dates must match the chronological main-ball history")
    cutoffs = np.asarray(cutoffs)
    if cutoffs.dtype.kind not in "iu" or not np.array_equal(
        cutoffs, training_cutoffs(frame)
    ):
        raise ValueError(
            "Joker training cutoffs must exclude every draw on the target day"
        )
    bonus, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=20)
    if not valid.all():
        raise ValueError("Joker bonus must be an integer between 1 and 20 on every row")

    indices = np.asarray(final_indices)
    if indices.ndim != 1 or (indices.size and indices.dtype.kind not in "iu"):
        raise ValueError("Expected a vector of chronological final draw indices")
    if not indices.size:
        return np.empty(0, dtype=bool)
    if (
        indices[0] < 0
        or indices[-1] >= len(frame)
        or np.any(indices[1:] <= indices[:-1])
    ):
        raise ValueError(
            "Final draw indices must be strictly increasing and within the history"
        )

    days, positions = np.unique(cutoffs[indices], return_inverse=True)
    predictions = []
    for end in days:
        scores, _ = call_method(
            "frequency", bonus[:end], 20, history_cutoffs=cutoffs[:end]
        )
        predictions.append(rank_by_score(scores, 1)[0])
    return np.asarray(predictions)[positions] == bonus[indices, 0]
