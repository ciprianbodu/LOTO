"""Walk-forward-ul verifica si numarul Joker (urna 2), nu doar urna 1."""

from __future__ import annotations

import pandas as pd

from loto_enterprise.core.backtesting import _joker_hit
from loto_enterprise.core.walk_forward_adapter import (
    WalkForwardResult,
    per_draw_hit_summary,
)


def _df(joker_values):
    rows = [
        {"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "joker": j} for j in joker_values
    ]
    return pd.DataFrame(rows)


def test_joker_hit_compares_ticket_joker_with_drawn_joker():
    df = _df([7, 12])
    lines = [[1, 2, 3, 4, 5, 12], [6, 7, 8, 9, 10, 12]]
    assert _joker_hit(df, 1, lines) is True
    assert _joker_hit(df, 0, lines) is False


def test_joker_hit_unknown_without_joker_on_ticket_or_in_csv():
    assert _joker_hit(_df([7]), 0, [[1, 2, 3, 4, 5]]) is None
    assert _joker_hit(pd.DataFrame({"n1": [1]}), 0, [[1, 2, 3, 4, 5, 7]]) is None
    assert _joker_hit(_df([0]), 0, [[1, 2, 3, 4, 5, 7]]) is None


def test_per_draw_summary_carries_joker_once_per_draw():
    flat = [
        WalkForwardResult(0, "d0", [1, 2, 3, 4, 5, 9], 2, 2, joker_hit=True),
        WalkForwardResult(0, "d0", [6, 7, 8, 9, 10, 9], 1, 2, joker_hit=True),
        WalkForwardResult(1, "d1", [1, 2, 3, 4, 5, 9], 0, 0, joker_hit=False),
        WalkForwardResult(2, "d2", [1, 2, 3, 4, 5, 6], 0, 0),
    ]
    per = per_draw_hit_summary(flat)
    assert [per[i].get("joker") for i in (0, 1, 2)] == [True, False, None]
    assert "joker" not in per[2]
