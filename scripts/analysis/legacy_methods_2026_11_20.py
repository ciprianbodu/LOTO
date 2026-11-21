"""Registry used to reproduce studies predating the compact scoring catalog."""

from pathlib import Path

from loto_enterprise.benchmark.methods import BASELINES, evaluate_scorer
from scripts.analysis.legacy_scoring.methods_bayes import BAYES_METHODS
from scripts.analysis.legacy_scoring.methods_evalue import EVALUE_METHODS
from scripts.analysis.legacy_scoring.methods_experimental import EXPERIMENTAL_METHODS
from scripts.analysis.legacy_scoring.methods_learning import LEARNING_METHODS
from scripts.analysis.legacy_scoring.methods_recency import RECENCY_METHODS
from scripts.analysis.legacy_scoring.methods_relational import RELATIONAL_METHODS
from scripts.analysis.legacy_scoring.methods_selfcalib import SELFCALIB_METHODS
from scripts.analysis.legacy_scoring.methods_wave2 import WAVE2_METHODS

# The September window audit and early October replay used the original 52.
SEPTEMBER_METHODS = {
    **BASELINES,
    **RECENCY_METHODS,
    **RELATIONAL_METHODS,
    **LEARNING_METHODS,
    **WAVE2_METHODS,
}
# Expert pool signed in the October 19 selection and combination protocols.
OCTOBER_19_EXPERTS = tuple(sorted(SEPTEMBER_METHODS.keys() - {
    "random", "haar_multiscale", "neighbor_adjacent", "repeat_last_draw", "rwr_last_draw",
}))
METHODS = {
    **SEPTEMBER_METHODS,
    **EXPERIMENTAL_METHODS,
    **BAYES_METHODS,
    **EVALUE_METHODS,
    **SELFCALIB_METHODS,
}
METHOD_LOAD_ERRORS = {}


def archived_scoring_sources(root: Path) -> list[Path]:
    """Sources that dated scorer caches must hash."""
    analysis = root / "scripts" / "analysis"
    return [
        analysis / "legacy_methods_2026_11_20.py",
        *sorted((analysis / "legacy_scoring").glob("methods*.py")),
        root / "loto_enterprise/benchmark/methods_markov.py",
    ]


def eligible_methods_for_game(game_key):
    excluded = {"random"}
    if game_key in ("loto_5_40", "joker_urna1", "joker_urna2"):
        excluded.add("interval_extrema_k16")
    if game_key == "joker_urna2":
        excluded.update(("naive_bayes_last", "markov_self_state", "vlmm_self_k3", "hedge_experts_mixture"))
    return [name for name in METHODS if name not in excluded]


def call_method(name, draws_2d, max_num, *, history_cutoffs=None):
    if name == "alternating_parity":
        name = "season_period2"
    return evaluate_scorer(METHODS[name][0], draws_2d, max_num, history_cutoffs)
