"""Walk-forward cu identitate de țară (etapa B2).

- România (fără identitate sau explicită): semnătura deciziei, numele fișierului
  de cache și meta-ul rămân cele de dinainte.
- Alt joc (de_lotto): decizia din `decisions/DE/best_methods.json`, semnătură
  proprie (țară, cheie, hash-ul fișierului de decizie, și pe ramura de eroare),
  identitatea ajunge PE NUME la motorul fiecărui pas: în proces, secvențial și
  în procesele paralele reale (AGENTS.md §4.4).
- Identitate necunoscută: eroare, nu fallback pe România.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

import loto_enterprise.core.backtesting as bt
import loto_enterprise.core.method_selector as ms
import loto_enterprise.core.walk_forward_adapter as wf
from loto_engine import LotoEngine
from loto_enterprise.core.lotteries import UnknownLotteryError

ROOT = Path(__file__).resolve().parent
DE_CSV = ROOT / "_ISTORIC/externe/germania_lotto_6aus49.csv"


@pytest.fixture
def decisions(tmp_path, monkeypatch):
    """Decizii izolate: best_methods.json românesc și decisions/<CC>/ în tmp."""
    ro = tmp_path / "best_methods.json"
    monkeypatch.setattr(ms, "_DEFAULT_CONFIG_PATH", ro)
    monkeypatch.setattr(ms, "_DECISIONS_ROOT", tmp_path)
    monkeypatch.setattr(ms, "_CONFIG", None)
    monkeypatch.setattr(LotoEngine, "use_bench_winner", True)
    return tmp_path


def _decision(path: Path, game_key: str, scorer: str, country: str | None, pool=12):
    meta = {"note": "test"}
    if country is not None:
        meta["country"] = country
    entry = {
        "scorer": scorer,
        "ensemble": [{"method": scorer, "weight": 1.0}],
        "sim_depth_pct": 40,
        "hit_target": 3,
    }
    cfg = {
        "_meta": meta,
        "games": {game_key: {"auto_pilot_per_pool": {f"k{pool}": entry}}},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")


def _de_tail(n: int) -> pd.DataFrame:
    return pd.read_csv(DE_CSV).tail(n).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Semnătura deciziei și cheia de cache
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "game_type, key",
    [("6/49", "loto_6_49"), ("5/40", "loto_5_40"), ("joker", "joker_urna1")],
)
def test_romanian_signature_and_cache_path_unchanged(decisions, game_type, key):
    _decision(decisions / "best_methods.json", key, "ewma_hl30", None)
    kw = dict(recent_penalty_draws=2, max_consecutive_run=2)
    keyless = wf._decision_sig(game_type, 12, **kw)
    assert wf._decision_sig(game_type, 12, **kw, country="RO") == keyless
    assert (
        wf._decision_sig(game_type, 12, **kw, game_key=key, country="RO") == keyless
    )
    path = wf._cache_path(game_type, "abc", 12, "0x1p+0", keyless)
    assert path.name == (
        f"walk_forward_{wf.CACHE_VERSION}_{game_type.replace('/', '_')}"
        f"_abc_pool12_d0x1p+0_{keyless}.pkl"
    )
    assert wf._cache_path(game_type, "abc", 12, "0x1p+0", keyless, country="RO") == path


def test_foreign_signature_is_separate_and_follows_the_country_file(decisions):
    ro_file = decisions / "best_methods.json"
    de_file = ms.decision_path_for("DE")
    _decision(ro_file, "loto_6_49", "ewma_hl30", None)
    ro_sig = wf._decision_sig("6/49", 12)
    absent = wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE")
    assert absent != ro_sig
    # Fără identitate de joc explicită, țara singură ajunge (un singur 6/49 în DE).
    assert wf._decision_sig("6/49", 12, country="DE") == absent

    _decision(de_file, "de_lotto", "gap_hazard", "DE")
    with_de = wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE")
    assert with_de not in (absent, ro_sig)
    # Decizia românească nu atinge cheia străină …
    _decision(ro_file, "loto_6_49", "gap_hazard", None)
    assert wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE") == with_de
    # … iar un alt conținut al fișierului țării o schimbă.
    _decision(de_file, "de_lotto", "ewma_hl30", "DE")
    assert wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE") != with_de

    path = wf._cache_path(
        "6/49", "abc", 12, "0x1p+0", with_de, game_key="de_lotto", country="DE"
    )
    assert path.name.startswith(f"walk_forward_{wf.CACHE_VERSION}_DE_de_lotto_abc_")


def test_foreign_suffix_also_on_the_error_branch(decisions, monkeypatch):
    def _broken(*_a, **_k):
        raise RuntimeError("fără decizie")

    monkeypatch.setattr(ms, "recommend_optimal_config", _broken)
    ro = wf._decision_sig("6/49", 12)
    de = wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE")
    pl = wf._decision_sig("6/49", 12, game_key="pl_lotto", country="PL")
    assert ro.startswith("nd") and de.startswith("nd") and pl.startswith("nd")
    assert len({ro, de, pl}) == 3


def test_foreign_signature_reads_only_the_country_decision(decisions, monkeypatch):
    seen = []
    real = ms.recommend_optimal_config

    def _spy(game_key, pool_size, config_path=None):  # noqa: D401
        seen.append((game_key, config_path))
        return real(game_key, pool_size, config_path=config_path)

    monkeypatch.setattr(ms, "recommend_optimal_config", _spy)
    wf._decision_sig("6/49", 12, game_key="de_lotto", country="DE")
    assert seen == [("de_lotto", str(ms.decision_path_for("DE")))]
    seen.clear()
    wf._decision_sig("6/49", 12)
    assert seen == [("loto_6_49", None)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"country": "XX"},
        {"game_key": "nu_exista"},
        {"game_key": "de_lotto", "country": "PL"},
        {"game_key": "loto_5_40", "country": "RO"},  # geometrie nepotrivită
    ],
)
def test_unknown_identity_fails_loudly(decisions, kwargs):
    with pytest.raises(UnknownLotteryError):
        wf._decision_sig("6/49", 12, **kwargs)
    with pytest.raises(UnknownLotteryError):
        wf.run_honest_walk_forward(_de_tail(30), "6/49", 12, **kwargs)
    with pytest.raises(UnknownLotteryError):
        bt.LotoBacktester(_de_tail(30), "6/49", **kwargs)


def test_identity_is_keyword_only():
    import inspect

    for fn in (wf.run_honest_walk_forward, wf._decision_sig, wf._cache_path):
        params = inspect.signature(fn).parameters
        assert params["game_key"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["country"].kind is inspect.Parameter.KEYWORD_ONLY


def test_new_geometries_in_the_wf_tables():
    assert wf._WF_PICK["6/45"] == 6 and wf._WF_PICK["5/50"] == 5
    assert wf._MAX_NUM["6/45"] == 45 and wf._MAX_NUM["5/50"] == 50
    assert bt._GAME_PICK_N["6/45"] == 6 and bt._GAME_PICK_N["5/50"] == 5
    from loto_enterprise.core import adaptive_feedback as af

    assert af._GAME_GEOMETRY["6/45"] == (6, 45)
    assert af._GAME_GEOMETRY["5/50"] == (5, 50)
    df = pd.DataFrame(
        {"date": ["01-01-2026"], **{f"n{i}": [i] for i in range(1, 6)}, "s1": [1]}
    )
    changed = df.copy()
    changed["s1"] = [2]
    # Stelele nu sunt modelate: nu intră în hash-ul istoricului 5/50.
    assert wf._csv_hash(df, "5/50") == wf._csv_hash(changed, "5/50")


def test_backtester_params_for_new_geometries():
    df = pd.DataFrame({f"n{i}": [i, i + 6] for i in range(1, 7)})
    params = bt.LotoBacktester.__new__(bt.LotoBacktester)._get_game_params("6/45")
    assert params["max_n"] == 45 and params["draw_n"] == 6
    params = bt.LotoBacktester.__new__(bt.LotoBacktester)._get_game_params("5/50")
    assert params["max_n"] == 50 and params["draw_n"] == 5
    assert df is not None


# --------------------------------------------------------------------------- #
# Identitatea până la motorul fiecărui pas
# --------------------------------------------------------------------------- #


def test_romanian_backtester_keeps_keyless_step_engines():
    df = pd.read_csv(ROOT / "_ISTORIC/loto_6_49.csv").tail(20).reset_index(drop=True)
    for kwargs in ({}, {"country": "RO"}, {"game_key": "loto_6_49"}):
        b = bt.LotoBacktester(df, "6/49", **kwargs)
        assert b.game_key is None and b.country is None


def test_worker_step_takes_identity_from_shared_by_name(decisions, monkeypatch):
    _decision(ms.decision_path_for("DE"), "de_lotto", "ewma_hl30", "DE")
    b = bt.LotoBacktester(_de_tail(30), "6/49", game_key="de_lotto", country="DE")
    monkeypatch.setattr(
        bt,
        "_WF_SHARED",
        {
            "df": b.df,
            "draws": [list(d) for d in b.draws],
            "dates": b.dates,
            "game_type": "6/49",
            "game_key": "de_lotto",
            "country": "DE",
        },
    )
    step = bt._wf_worker_step(
        {
            "sim_idx": 25,
            "pool_size": 12,
            "guarantee": 3,
            "max_variants": 2,
            "lookback_percent": 100.0,
            "recent_penalty_draws": 0,
            "recent_penalty_factor": 0.5,
            "restrict_base_max": 0,
            "restrict_base_min": 0,
            "wheel_condition": 3,
            "max_consecutive_run": 0,
        }
    )
    assert step is not None
    assert (step.game_key, step.country, step.bench_method) == (
        "de_lotto",
        "DE",
        "ewma_hl30",
    )


def test_wf_worker_init_stores_identity():
    import pickle

    saved = dict(bt._WF_SHARED)
    try:
        df = _de_tail(5)
        bt._wf_worker_init(
            pickle.dumps(df), "6/49", [(1, 2, 3, 4, 5, 6)], ["x"],
            {"game_key": "de_lotto", "country": "DE"},
        )
        assert bt._WF_SHARED["game_key"] == "de_lotto"
        assert bt._WF_SHARED["country"] == "DE"
        bt._wf_worker_init(pickle.dumps(df), "6/49", [(1, 2, 3, 4, 5, 6)], ["x"])
        assert bt._WF_SHARED["game_key"] is None and bt._WF_SHARED["country"] is None
    finally:
        bt._WF_SHARED.clear()
        bt._WF_SHARED.update(saved)


@pytest.mark.parametrize("use_feedback", [False, True])
def test_foreign_wf_uses_the_foreign_decision_in_process(
    decisions, monkeypatch, use_feedback
):
    """Calea în proces (sondă + secvențial) și calea secvențială cu stare."""
    _decision(decisions / "best_methods.json", "loto_6_49", "gap_hazard", None)
    _decision(ms.decision_path_for("DE"), "de_lotto", "ewma_hl30", "DE")
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 1e9)
    b = bt.LotoBacktester(_de_tail(40), "6/49", game_key="de_lotto", country="DE")
    preds = b.run_retroactive_backtest(
        backtest_depth_percent=15.0,
        pool_size=12,
        guarantee=3,
        max_variants=2,
        use_feedback=use_feedback,
        enable_hard_inversion=False,
    )
    assert preds
    assert {(p.game_key, p.country, p.bench_method) for p in preds} == {
        ("de_lotto", "DE", "ewma_hl30")
    }


def test_run_honest_walk_forward_foreign_meta_and_cache(
    decisions, monkeypatch, tmp_path
):
    _decision(ms.decision_path_for("DE"), "de_lotto", "ewma_hl30", "DE")
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path / "wf")
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 1e9)
    seen = []
    real = bt._retroactive_step_stateless

    def _spy(*a, **k):
        seen.append((k.get("game_key"), k.get("country")))
        return real(*a, **k)

    monkeypatch.setattr(bt, "_retroactive_step_stateless", _spy)
    flat, meta = wf.run_honest_walk_forward(
        _de_tail(40), "6/49", 12, backtest_depth_percent=15.0,
        guarantee=3, max_variants=2, game_key="de_lotto", country="DE",
    )
    assert flat and meta["n_predictions"] > 0
    assert (meta["game_key"], meta["country"], meta["game_id"]) == (
        "de_lotto", "DE", "de_lotto",
    )
    assert "DE_de_lotto" in Path(meta["cache_file"]).name
    assert seen and set(seen) == {("de_lotto", "DE")}
    assert meta["decision_sig"] == wf._decision_sig(
        "6/49", 12, guarantee=3, max_variants=2, game_key="de_lotto", country="DE"
    )
    # A doua rulare citește cache-ul propriu al jocului străin.
    _flat2, meta2 = wf.run_honest_walk_forward(
        _de_tail(40), "6/49", 12, backtest_depth_percent=15.0,
        guarantee=3, max_variants=2, game_key="de_lotto", country="DE",
    )
    assert meta2["from_cache"] is True


def test_run_honest_walk_forward_romanian_meta_unchanged(decisions, monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path / "wf")
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 1e9)
    df = pd.read_csv(ROOT / "_ISTORIC/loto_6_49.csv").tail(30).reset_index(drop=True)
    _f, meta = wf.run_honest_walk_forward(
        df, "6/49", 12, backtest_depth_percent=10.0, guarantee=3, max_variants=2
    )
    _f2, meta_ro = wf.run_honest_walk_forward(
        df, "6/49", 12, backtest_depth_percent=10.0, guarantee=3, max_variants=2,
        country="RO", use_cache=False,
    )
    for m in (meta, meta_ro):
        assert not {"game_key", "country", "game_id"} & set(m)
    assert meta["cache_file"] == meta_ro["cache_file"]
    assert Path(meta["cache_file"]).name.startswith(f"walk_forward_{wf.CACHE_VERSION}_6_49_")


def test_parallel_branch_carries_the_foreign_identity(monkeypatch, caplog):
    """Ramura paralelă REALĂ (_stateless, procese): fără „WF rapid indisponibil",
    predicții nenule, iar identitatea străină a ajuns la motorul fiecărui pas.

    Procesele copil nu văd monkeypatch-urile (forkserver/spawn), deci citesc
    decizia din `decisions/DE/` reală: fără ea, pasul trebuie să cadă pe
    frequency marcat, niciodată pe câștigătorul românesc."""
    df = _de_tail(40)
    monkeypatch.setattr(bt, "_wf_max_workers", lambda: 2)
    monkeypatch.setattr(bt, "_WF_SERIAL_MAX_MS", 0.0)
    monkeypatch.setattr(bt, "_WF_PROBE_STEPS", 1)
    b = bt.LotoBacktester(df, "6/49", game_key="de_lotto", country="DE")
    with caplog.at_level(logging.WARNING, logger=bt.logger.name):
        preds = b.run_retroactive_backtest(
            backtest_depth_percent=15.0,
            pool_size=12,
            guarantee=3,
            max_variants=2,
            use_feedback=False,
            enable_hard_inversion=False,
        )
    assert not [r for r in caplog.records if "WF rapid indisponibil" in r.getMessage()]
    assert len(preds) > 1, "ramura paralelă nu a produs pași dincolo de sondă"
    assert {(p.game_key, p.country) for p in preds} == {("de_lotto", "DE")}
    if not ms.decision_path_for("DE").exists():
        assert {p.bench_method for p in preds} == {"frequency"}


# --------------------------------------------------------------------------- #
# Opțiunile WF din ecoul rezultatului
# --------------------------------------------------------------------------- #


def test_wf_generation_options_identity_only_for_foreign_results():
    import ui_runtime

    base = {"pool_size": 12, "guarantee": 3}
    ro = ui_runtime._wf_generation_options(dict(base))
    ro_echo = ui_runtime._wf_generation_options(
        dict(base, country="RO", game_id="6/49", bench_key="loto_6_49")
    )
    assert ro == ro_echo
    assert "game_key" not in ro and "country" not in ro
    de = ui_runtime._wf_generation_options(
        dict(base, country="DE", game_id="de_lotto", bench_key="de_lotto")
    )
    assert de["game_key"] == "de_lotto" and de["country"] == "DE"
    assert {k: v for k, v in de.items() if k not in ("game_key", "country")} == ro
