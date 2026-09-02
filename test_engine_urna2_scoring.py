"""Regresii pe scoring-ul Urnei 2 și pe auditul fallback-ului din engine."""
import numpy as np
import pandas as pd

from loto_engine import LotoEngine


def _joker_frame(n: int = 60, with_joker: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n):
        nums = sorted(rng.choice(np.arange(1, 46), size=5, replace=False).tolist())
        row = {"date": f"2026-01-{(i % 28) + 1:02d}"}
        row.update({f"n{j + 1}": v for j, v in enumerate(nums)})
        if with_joker:
            row["joker"] = int(rng.integers(1, 21))
        rows.append(row)
    return pd.DataFrame(rows)


def _engine(df: pd.DataFrame) -> LotoEngine:
    eng = LotoEngine(game_type="joker")
    eng.data = df
    eng._build_draw_matrix()
    eng._winner_pool_hint = 11
    return eng


def test_urna2_without_joker_column_yields_no_scores():
    """Fără coloana `joker`, Urna 2 nu se scorează pe numerele Urnei 1."""
    eng = _engine(_joker_frame(with_joker=False))
    assert eng._draw_matrix is not None and eng._draw_matrix.shape[1] == 5
    assert eng._frequency_fallback_scores(is_joker_drum=True) == {}
    assert eng._scores_via_bench_winner(is_joker_drum=True) == {}
    assert eng._get_timesfm_scores(is_joker_drum=True) == {}


def test_urna2_with_joker_column_scores_only_1_to_20():
    eng = _engine(_joker_frame(with_joker=True))
    scores = eng._frequency_fallback_scores(is_joker_drum=True)
    assert set(scores) == set(range(1, 21))


def test_unusable_bench_scores_audit_names_frequency_fallback(monkeypatch):
    """Scoruri plate de la câștigător → auditul spune `frequency`, nu metoda moartă."""
    eng = LotoEngine(game_type="6/49")
    rng = np.random.default_rng(3)
    rows = []
    for i in range(80):
        nums = sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
        row = {"date": f"2026-02-{(i % 28) + 1:02d}"}
        row.update({f"n{j + 1}": v for j, v in enumerate(nums)})
        rows.append(row)
    eng.data = pd.DataFrame(rows)
    eng._build_draw_matrix()
    eng._winner_pool_hint = 11
    eng.use_bench_winner = True

    def _flat(self, is_joker_drum=False):
        self.audit.setdefault("bench_winner", {})["loto_6_49"] = {
            "method": "zz_flat", "pool_hint": 11, "family": "x",
        }
        return {n: 0.5 for n in range(1, 50)}

    monkeypatch.setattr(LotoEngine, "_scores_via_bench_winner", _flat)
    scores = eng._get_timesfm_scores()
    assert set(scores) == set(range(1, 50))
    assert len(set(scores.values())) > 1
    info = eng.audit["bench_winner"]["loto_6_49"]
    assert info["method"] == "frequency"
    assert info["fallback"] is True
    assert info["attempted"] == "zz_flat"
    assert eng.audit["bench_winner_unusable_scores"] is True
