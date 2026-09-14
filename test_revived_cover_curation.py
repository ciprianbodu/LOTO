"""Curation after cover/revived external eval: names exist and stay distinct."""

from loto_enterprise.benchmark.curated import load_curated, load_per_game
from loto_enterprise.benchmark.methods import METHODS


def test_cover_winners_are_registered_and_curated():
    pg = load_per_game()
    active = set(load_curated())
    expected = {
        "loto_6_49": {
            "cover_diversity_mmr",
            "cover_adaptive_blend",
            "modular",
            "compression",
            "markov_3",
        },
        "loto_5_40": {"weighted_recent", "ssa"},
        "joker_urna1": {
            "modular",
            "weighted_recent",
            "markov_2",
            "cover_complement",
            "markov_3",
        },
    }
    for game, names in expected.items():
        assert names <= set(pg[game]), (game, names - set(pg[game]))
        assert names <= active
        assert names <= set(METHODS)
    assert "random" in active and "frequency" in active
    for lst in pg.values():
        assert len(lst) <= 20
