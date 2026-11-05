"""Known-outcome controls for the standalone retrospective diagnostic."""

import numpy as np
import pytest

from loto_enterprise.benchmark.runner import GameDef
from scripts.analysis import audit_method_windows as audit


def test_holm_correction_restores_order_and_is_monotone():
    assert audit.holm_adjust([0.04, 0.01, 0.03]) == pytest.approx([0.06, 0.03, 0.06])
    assert audit.holm_adjust([0.9, 0.8]) == [1.0, 1.0]
    assert audit.holm_adjust([]) == []


@pytest.mark.parametrize("skip_last", [False, True])
def test_windows_known_hits_exclude_same_day_and_account_for_skips(
    monkeypatch, skip_last
):
    draws = np.tile(np.arange(44, 50), (56, 1))
    game = GameDef("loto_6_49", "test", "unused.csv", [], 49, 6)
    game.history_cutoffs = tuple(i - i % 2 for i in range(56))
    lengths = []

    def scorer(history, max_num):
        lengths.append(len(history))
        return {
            n: (0.0 if skip_last and len(history) == 54 else n / max_num)
            for n in range(1, max_num + 1)
        }

    monkeypatch.setattr(audit, "METHODS", {"controlled": (scorer, "test", False, "")})
    monkeypatch.setattr(audit, "load_draws", lambda _game: draws)
    result = audit.audit_game(game, 2, 3)
    assert lengths == [50, 50, 52, 52, 54, 54]
    assert result["failures"] == []
    rows = [row for row in result["rows"] if row["pool"] == 6 and row["target"] == 4]
    assert len(rows) == 4
    assert [row["successes"] for row in rows] == (
        [2, 2, 0, 4] if skip_last else [2, 2, 2, 6]
    )
    assert [row["n_requested"] for row in rows] == [2, 2, 2, 6]
    assert rows[-1]["n_eval"] == (4 if skip_last else 6)
    assert rows[-1]["rate"] == 1.0
    if skip_last:
        assert rows[-1]["p_raw"] is None
        assert rows[2]["rate"] is None
    else:
        assert rows[-1]["p_raw"] == pytest.approx(rows[-1]["random_expected"] ** 6)
