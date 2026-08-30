"""draw_shape_reversion: scoruri finite, nu degenerare, nu clonă de frequency.

Metoda e reumplere de FORMĂ (anvelopă/clase), nu tăiere de bază și nu recency.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.methods import METHODS, call_method, score_frequency
from loto_enterprise.benchmark.methods_math_extra import score_draw_shape_reversion
from loto_enterprise.core.ranking import is_consecutive_block, rank_by_score

CSV_649 = Path("_ISTORIC/loto_6_49.csv")
CSV_540 = Path("_ISTORIC/loto_5_40.csv")
CSV_JOKER = Path("_ISTORIC/joker.csv")


def _load(path: Path, cols: tuple[str, ...]) -> np.ndarray:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append([int(rec[c]) for c in cols])
    assert rows, f"{path} gol"
    return np.asarray(rows, dtype=np.int64)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    ra = ra.astype(np.float64)
    rb = rb.astype(np.float64)
    ra -= ra.mean()
    rb -= rb.mean()
    den = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    if den < 1e-12:
        return 0.0
    return float((ra * rb).sum() / den)


def test_registered_in_methods():
    assert "draw_shape_reversion" in METHODS
    fn, family, _train, _notes = METHODS["draw_shape_reversion"]
    assert family == "math-shape"
    assert fn is score_draw_shape_reversion


def test_short_history_returns_empty():
    tiny = np.arange(1, 7, dtype=np.int64).reshape(1, 6)
    assert score_draw_shape_reversion(tiny, 49) == {}
    short = np.tile(np.arange(1, 7, dtype=np.int64), (10, 1))
    assert score_draw_shape_reversion(short, 49) == {}


def test_finite_full_keys_not_degenerate_on_all_games():
    specs = (
        (CSV_649, ("n1", "n2", "n3", "n4", "n5", "n6"), 49, 12),
        (CSV_540, ("n1", "n2", "n3", "n4", "n5"), 40, 11),
        (CSV_JOKER, ("n1", "n2", "n3", "n4", "n5"), 45, 11),
    )
    for path, cols, max_num, pool_n in specs:
        draws = _load(path, cols)
        scores, _dt = call_method("draw_shape_reversion", draws, max_num)
        assert scores, f"{path}: scor gol"
        assert set(scores) == set(range(1, max_num + 1))
        assert all(np.isfinite(v) for v in scores.values()), path
        nuniq = len({round(v, 8) for v in scores.values()})
        assert nuniq > 8, f"{path}: prea puține nivele ({nuniq})"
        pool = sorted(rank_by_score(scores, pool_n))
        largest = list(range(max_num - pool_n + 1, max_num + 1))
        assert pool != largest, f"{path}: degenerare număr mare {pool}"
        assert not is_consecutive_block(pool, min_size=6), f"{path}: bloc consecutiv {pool}"
        freq = score_frequency(draws, max_num)
        a = np.array([scores[i] for i in range(1, max_num + 1)])
        b = np.array([freq[i] for i in range(1, max_num + 1)])
        r = abs(_spearman(a, b))
        assert r < 0.95, f"{path}: clonă frequency |Spearman|={r:.3f}"
        # Axe înrudite din curated (hot digit / paritate / benzi) — nu clone.
        for other in ("parity_balance", "649_mod10_hot", "cover_positional_bands"):
            oth, _ = call_method(other, draws, max_num)
            c = np.array([oth[i] for i in range(1, max_num + 1)])
            r2 = abs(_spearman(a, c))
            assert r2 < 0.95, f"{path}: clonă {other} |Spearman|={r2:.3f}"


def test_compressed_high_window_boosts_low_tail():
    """Cutie recentă sus → găurile de anvelopă jos se umplu (nu tăiere de bază)."""
    rng = np.random.default_rng(7)
    hist = [sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
            for _ in range(24)]
    hist += [sorted(rng.choice(np.arange(28, 46), size=6, replace=False).tolist())
             for _ in range(8)]
    scores = score_draw_shape_reversion(np.asarray(hist, dtype=np.int64), 49)
    assert scores
    low = float(np.mean([scores[i] for i in range(1, 8)]))
    high = float(np.mean([scores[i] for i in range(43, 50)]))
    assert low > high, f"low={low:.3f} high={high:.3f}"


def test_compressed_low_window_boosts_high_tail():
    rng = np.random.default_rng(11)
    hist = [sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
            for _ in range(24)]
    hist += [sorted(rng.choice(np.arange(4, 22), size=6, replace=False).tolist())
             for _ in range(8)]
    scores = score_draw_shape_reversion(np.asarray(hist, dtype=np.int64), 49)
    assert scores
    low = float(np.mean([scores[i] for i in range(1, 8)]))
    high = float(np.mean([scores[i] for i in range(43, 50)]))
    assert high > low, f"low={low:.3f} high={high:.3f}"
