"""Top-20 metode 6/49 — selectate din search_649 (500 metode, eval onest block=1 @ 30%).

Câștigător rafinat: 12% graph_649_katz_community + 88% gap_poisson → 10.92% rate_4plus @ k16 (+21.7%).
"""
from __future__ import annotations

from typing import Callable

from .methods_classical import score_autocorr, score_beta_binomial, score_gap_poisson, score_markov_2, score_prime_bias
from .methods_graph import (
    score_graph_649_katz_community,
    score_graph_community_strength,
    score_graph_degree,
    score_graph_katz_high,
    score_graph_pagerank,
    score_graph_second_order,
)
from .methods_search_649 import make_blend_scorer


def score_top649_00_katz12_gap88(draws_2d, max_num):
    return make_blend_scorer([(0.12, score_graph_649_katz_community), (0.88, score_gap_poisson)])(draws_2d, max_num)


def score_top649_01_katz15_gap85(draws_2d, max_num):
    return make_blend_scorer([(0.15, score_graph_649_katz_community), (0.85, score_gap_poisson)])(draws_2d, max_num)


def score_top649_02_katz85_pagerank15(draws_2d, max_num):
    return make_blend_scorer([(0.85, score_graph_649_katz_community), (0.15, score_graph_pagerank)])(draws_2d, max_num)


def score_top649_03_katz25_gap75(draws_2d, max_num):
    return make_blend_scorer([(0.25, score_graph_649_katz_community), (0.75, score_gap_poisson)])(draws_2d, max_num)


def score_top649_04_katz25_beta75(draws_2d, max_num):
    return make_blend_scorer([(0.25, score_graph_katz_high), (0.75, score_beta_binomial)])(draws_2d, max_num)


def score_top649_05_katz25_freq75(draws_2d, max_num):
    from .methods import score_frequency
    return make_blend_scorer([(0.25, score_graph_katz_high), (0.75, score_frequency)])(draws_2d, max_num)


def score_top649_06_katz85_katzhigh15(draws_2d, max_num):
    return make_blend_scorer([(0.85, score_graph_649_katz_community), (0.15, score_graph_katz_high)])(draws_2d, max_num)


def score_top649_07_katz75_prime25(draws_2d, max_num):
    return make_blend_scorer([(0.75, score_graph_649_katz_community), (0.25, score_prime_bias)])(draws_2d, max_num)


def score_top649_08_katz55_comm45(draws_2d, max_num):
    return make_blend_scorer([(0.55, score_graph_katz_high), (0.45, score_graph_community_strength)])(draws_2d, max_num)


def score_top649_09_katz65_comm35(draws_2d, max_num):
    return make_blend_scorer([(0.65, score_graph_katz_high), (0.35, score_graph_community_strength)])(draws_2d, max_num)


def score_top649_10_katz25_gap75_b(draws_2d, max_num):
    return make_blend_scorer([(0.25, score_graph_katz_high), (0.75, score_gap_poisson)])(draws_2d, max_num)


def score_top649_11_katz35_katzhigh65(draws_2d, max_num):
    return make_blend_scorer([(0.35, score_graph_649_katz_community), (0.65, score_graph_katz_high)])(draws_2d, max_num)


def score_top649_12_katz85_degree15(draws_2d, max_num):
    return make_blend_scorer([(0.85, score_graph_649_katz_community), (0.15, score_graph_degree)])(draws_2d, max_num)


def score_top649_13_katz65_second35(draws_2d, max_num):
    return make_blend_scorer([(0.65, score_graph_649_katz_community), (0.35, score_graph_second_order)])(draws_2d, max_num)


def score_top649_14_katz85_second15(draws_2d, max_num):
    return make_blend_scorer([(0.85, score_graph_649_katz_community), (0.15, score_graph_second_order)])(draws_2d, max_num)


def score_top649_15_katz85_markov15(draws_2d, max_num):
    return make_blend_scorer([(0.85, score_graph_katz_high), (0.15, score_markov_2)])(draws_2d, max_num)


def score_top649_16_autocorr(draws_2d, max_num):
    return score_autocorr(draws_2d, max_num)


def score_top649_17_katz65_katzhigh35(draws_2d, max_num):
    return make_blend_scorer([(0.65, score_graph_649_katz_community), (0.35, score_graph_katz_high)])(draws_2d, max_num)


def score_top649_18_katz75_katzhigh25(draws_2d, max_num):
    return make_blend_scorer([(0.75, score_graph_649_katz_community), (0.25, score_graph_katz_high)])(draws_2d, max_num)


def score_top649_19_katz75_second25(draws_2d, max_num):
    return make_blend_scorer([(0.75, score_graph_649_katz_community), (0.25, score_graph_second_order)])(draws_2d, max_num)


def score_top649_20_katz15_beta85(draws_2d, max_num):
    return make_blend_scorer([(0.15, score_graph_649_katz_community), (0.85, score_beta_binomial)])(draws_2d, max_num)


TOP649_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "649_katz12_gap88":      (score_top649_00_katz12_gap88,      "top649", False, "12% KatzCommunity + 88% gap_poisson (10.92% 4+, +21.7%)"),
    "649_katz15_gap85":      (score_top649_01_katz15_gap85,      "top649", False, "15% KatzCommunity + 85% gap_poisson (10.66% 4+)"),
    "649_katz85_pagerank15": (score_top649_02_katz85_pagerank15, "top649", False, "85% KatzCommunity + 15% PageRank"),
    "649_katz25_gap75":      (score_top649_03_katz25_gap75,      "top649", False, "25% KatzCommunity + 75% gap_poisson"),
    "649_katz25_beta75":     (score_top649_04_katz25_beta75,     "top649", False, "25% KatzHigh + 75% beta_binomial"),
    "649_katz25_freq75":     (score_top649_05_katz25_freq75,     "top649", False, "25% KatzHigh + 75% frequency"),
    "649_katz85_katzhigh15": (score_top649_06_katz85_katzhigh15, "top649", False, "85% KatzCommunity + 15% KatzHigh"),
    "649_katz75_prime25":    (score_top649_07_katz75_prime25,    "top649", False, "75% KatzCommunity + 25% prime_bias"),
    "649_katz55_comm45":     (score_top649_08_katz55_comm45,     "top649", False, "55% KatzHigh + 45% community"),
    "649_katz65_comm35":     (score_top649_09_katz65_comm35,     "top649", False, "65% KatzHigh + 35% community"),
    "649_katz25_gap75_b":    (score_top649_10_katz25_gap75_b,    "top649", False, "25% KatzHigh + 75% gap_poisson"),
    "649_katz35_katzhigh65": (score_top649_11_katz35_katzhigh65, "top649", False, "35% KatzCommunity + 65% KatzHigh"),
    "649_katz85_degree15":   (score_top649_12_katz85_degree15,   "top649", False, "85% KatzCommunity + 15% degree"),
    "649_katz65_second35":   (score_top649_13_katz65_second35,   "top649", False, "65% KatzCommunity + 35% second_order"),
    "649_katz85_second15":   (score_top649_14_katz85_second15,   "top649", False, "85% KatzCommunity + 15% second_order"),
    "649_katz85_markov15":   (score_top649_15_katz85_markov15,   "top649", False, "85% KatzHigh + 15% markov_2"),
    "649_top_autocorr":      (score_top649_16_autocorr,          "top649", False, "autocorr lag 1-5"),
    "649_katz65_katzhigh35": (score_top649_17_katz65_katzhigh35, "top649", False, "65% KatzCommunity + 35% KatzHigh"),
    "649_katz75_katzhigh25": (score_top649_18_katz75_katzhigh25, "top649", False, "75% KatzCommunity + 25% KatzHigh"),
    "649_katz75_second25":   (score_top649_19_katz75_second25,   "top649", False, "75% KatzCommunity + 25% second_order"),
    "649_katz15_beta85":     (score_top649_20_katz15_beta85,     "top649", False, "15% KatzCommunity + 85% beta_binomial"),
}

TOP649_WINNER = "649_katz12_gap88"
