"""Exact, bounded-memory evaluation of the new 6/49 combination bank.

No production logic is changed. Top-N always uses rank_by_score, and the
constrained pool is obtained from the actual application's helper.
For the FULL 49-ball universe, pools9..16 are nested prefixes of constrained16:
any maximal set without triples has >=ceil(49/3)=17 members. Indeed, each
excluded ball in a maximal set requires at least two selected neighbours
within distance2; each selected ball has at most four such neighbours, so
2*(49-s)<=4*s. Thus any feasible set of <=16 can be extended to16 and the
helper's completion check cannot reject a locally feasible choice. This
Earlier locally rejected balls stay infeasible after adding more balls; by
induction they are never required by a completion. This argument is intentionally
NOT generalized to restricted universes or K>16.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance

KS = tuple(range(9, 17))


def evaluate_scores(scores, draws):
    """Return hits[T,2,8] (raw, consecutive2) and raw boundary ties[T,8]."""
    scores = np.asarray(scores, dtype=np.float64)
    draws = np.asarray(draws)
    if scores.ndim != 2 or scores.shape[1] != 49:
        raise ValueError("scores must have shape [T,49]")
    if draws.shape != (len(scores), 6):
        raise ValueError("draws must have shape [T,6]")
    if not np.isfinite(draws).all() or not ((draws >= 1) & (draws <= 49)).all():
        raise ValueError("draw labels outside1..49")
    if not np.equal(draws, np.floor(draws)).all() or any(
        len(set(r)) != 6 for r in draws
    ):
        raise ValueError("draws must contain six distinct integers")
    hits = np.zeros((len(scores), 2, len(KS)), dtype=np.int8)
    ties = np.zeros((len(scores), len(KS)), dtype=bool)
    for t, (row, draw) in enumerate(zip(scores, draws, strict=True)):
        mapping = dict(enumerate(map(float, row), start=1))
        if not has_usable_score_variance(mapping):
            raise ValueError(f"unusable scores at row{t}")
        ranked = rank_by_score(mapping, 49)
        constrained = apply_consecutive_limit(ranked, 16, 2)
        indicator = np.zeros(50, dtype=np.int8)
        indicator[draw.astype(np.int64)] = 1
        hits[t, 0] = np.cumsum(indicator[ranked[:16]])[8:16]
        hits[t, 1] = np.cumsum(indicator[constrained])[8:16]
        for j, k in enumerate(KS):
            ties[t, j] = np.isclose(
                row[ranked[k - 1] - 1], row[ranked[k] - 1], rtol=0, atol=1e-12
            )
    return hits, ties


def _evaluate_one(item):
    name, scores, invalid, draws = item
    hits, ties = evaluate_scores(scores, draws)
    return name, hits, ties, invalid


def evaluate_bank(items, draws, *, workers=4):
    """Stream candidate matrices with <=2*workers pending large inputs.

    items yields(name,scores[T,49],invalid[T]); no outcomes enter the bank.
    This iterator counts hits only after each matrix has already been emitted.
    """
    iterator = iter(items)
    pending = set()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < 2 * workers:
                try:
                    name, scores, invalid = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending.add(pool.submit(_evaluate_one, (name, scores, invalid, draws)))
            if pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
