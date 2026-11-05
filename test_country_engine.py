"""Motorul cu identitate de țară: `LotoEngine(geometrie, *, game_key, country)`.

- România fără identitate: exact comportamentul de dinainte (parametri, chei de
  bench, decizia din best_methods.json, cheile pool_history `6/49_12`).
- Alt joc (de_lotto): parametrii geometriei, decizia DOAR din fișierul țării,
  frequency marcat explicit ca fallback când țara n-are decizie, cheile
  pool_history/adaptive prefixate, niciodată cheia românească.
- Identitate explicită greșită: eroare, nu fallback pe România.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import loto_enterprise.core.method_selector as ms
from loto_engine import LotoEngine, game_params_for, resolve_engine_lottery
from loto_enterprise.core import adaptive_feedback as af
from loto_enterprise.core.lotteries import UnknownLotteryError

ROOT = Path(__file__).resolve().parent
DE_CSV = ROOT / "_ISTORIC/externe/germania_lotto_6aus49.csv"
RO_CSV = ROOT / "_ISTORIC/loto_6_49.csv"


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


def _csv(tmp_path: Path, source: Path, name: str, rows: int = 300) -> str:
    df = pd.read_csv(source).tail(rows).reset_index(drop=True)
    out = tmp_path / name
    df.to_csv(out, index=False)
    return str(out)


def _run(engine: LotoEngine, csv_path: str, **kwargs):
    assert engine.load_data(csv_path)
    params = dict(pool_size=12, guarantee=3, max_variants=5)
    params.update(kwargs)
    return engine.run_institutional_pipeline(**params)


# --------------------------------------------------------------------------- #
# Geometrie și identitate
# --------------------------------------------------------------------------- #


def test_params_come_from_the_geometry_table():
    assert game_params_for("6/45") == {"max_n": 45, "draw_n": 6, "play_n": 6}
    assert game_params_for("5/50") == {"max_n": 50, "draw_n": 5, "play_n": 5}
    assert game_params_for("6/49")["scheme"] == "2-2-2"  # câmpurile istorice RO rămân
    assert game_params_for("nimic") is None


def test_keyless_engine_is_the_legacy_romanian_engine():
    eng = LotoEngine("6/49")
    assert eng.lottery is None and eng.country is None and eng.game_key is None
    assert eng._bench_game_key() == "loto_6_49"
    assert eng._decision_config_path() is None
    assert eng._history_key_prefix() == ""
    assert "country" not in eng.audit
    # O etichetă necunoscută, fără identitate, rămâne pe fallback-ul de dinainte.
    legacy = LotoEngine("de_6aus49")
    assert legacy.params == LotoEngine("6/49").params
    assert legacy._bench_game_key() == "loto_6_49"


@pytest.mark.parametrize("geometry", ["6/45", "5/50"])
def test_keyless_geometry_without_a_romanian_game_fails(geometry):
    """6/45 fără țară ar folosi tăcut decizia românească 6/49: refuzat."""
    with pytest.raises(UnknownLotteryError):
        LotoEngine(geometry)


def test_explicit_foreign_identity():
    eng = LotoEngine("6/49", game_key="de_lotto", country="DE")
    assert eng.lottery.game_id == "de_lotto"
    assert (eng.country, eng.game_key) == ("DE", "de_lotto")
    assert eng.params == LotoEngine("6/49").params
    assert eng._bench_game_key() == "de_lotto"
    assert eng._bench_game_key(True) != "joker_urna2"
    assert eng._history_key_prefix() == "DE_de_lotto_"
    assert eng.audit["country"] == "DE" and eng.audit["bench_key"] == "de_lotto"
    assert Path(eng._decision_config_path()) == ms.decision_path_for("DE")
    # Țara singură ajunge dacă are un singur joc pe geometria cerută.
    assert LotoEngine("6/49", country="pl").game_key == "pl_lotto"
    assert LotoEngine("6/49", game_key="es_primitiva").country == "ES"


def test_explicit_romanian_identity_equals_the_legacy_engine():
    for geometry, key in (
        ("6/49", "loto_6_49"),
        ("5/40", "loto_5_40"),
        ("joker", "joker_urna1"),
    ):
        eng = LotoEngine(geometry, game_key=key, country="RO")
        legacy = LotoEngine(geometry)
        assert eng.params == legacy.params
        assert eng._bench_game_key() == legacy._bench_game_key()
        assert eng._bench_game_key(True) == legacy._bench_game_key(True)
        assert eng._decision_config_path() is None
        assert eng._history_key_prefix() == ""
        assert eng.audit.keys() == legacy.audit.keys()
    assert LotoEngine("joker", country="RO")._bench_game_key(True) == "joker_urna2"


@pytest.mark.parametrize(
    "geometry,kwargs",
    [
        ("6/49", {"game_key": "nu_exista"}),
        ("6/49", {"country": "XX"}),
        ("6/49", {"game_key": "de_lotto", "country": "PL"}),  # cheia altei țări
        ("5/40", {"game_key": "de_lotto"}),  # geometrie greșită
        ("5/40", {"country": "DE"}),  # țara n-are joc 5/40
        ("6/45", {"country": "RO"}),
        ("6/49", {"game_key": "joker_urna2"}),  # cheia Urnei 2 nu e identitate
        ("nimic", {"game_key": "de_lotto"}),
    ],
)
def test_explicit_wrong_identity_fails_loudly(geometry, kwargs):
    with pytest.raises(UnknownLotteryError):
        resolve_engine_lottery(geometry, **kwargs)
    with pytest.raises(UnknownLotteryError):
        LotoEngine(geometry, **kwargs)


# --------------------------------------------------------------------------- #
# Decizia: fișierul țării, frequency marcat explicit când lipsește
# --------------------------------------------------------------------------- #


def test_method_selector_decision_paths(decisions):
    assert ms.decision_path_for("RO") == decisions / "best_methods.json"
    assert ms.decision_path_for("de") == decisions / "decisions/DE/best_methods.json"
    with pytest.raises(UnknownLotteryError):
        ms.decision_path_for("XX")
    assert ms.has_decision("de_lotto", str(ms.decision_path_for("DE"))) is False


def test_foreign_game_without_decision_uses_flagged_frequency_not_the_romanian_winner(
    decisions, tmp_path
):
    _decision(decisions / "best_methods.json", "loto_6_49", "ewma_hl30", None)
    ro = LotoEngine("6/49")
    _run(ro, _csv(tmp_path, RO_CSV, "ro.csv"))
    assert ro.audit["bench_winner"]["loto_6_49"]["method"] == "ewma_hl30"

    de = LotoEngine("6/49", game_key="de_lotto", country="DE")
    _run(de, _csv(tmp_path, DE_CSV, "de.csv"))
    winner = de.audit["bench_winner"]
    assert set(winner) == {"de_lotto"}  # nicio cheie românească
    info = winner["de_lotto"]
    assert info["method"] == "frequency" and info["fallback"] is True
    assert info["no_decision"] is True
    assert info["reason"] == "fără decizie bench pentru Germania · Lotto 6aus49"
    assert info["family"] == "baseline"

    # Pool-ul e exact cel al frecvenței pe aceleași date.
    freq = LotoEngine("6/49")
    freq.use_bench_winner = False
    _run(freq, _csv(tmp_path, DE_CSV, "de2.csv"))
    assert de.hard_core == freq.hard_core


def test_foreign_game_uses_its_own_country_decision(decisions, tmp_path):
    _decision(decisions / "best_methods.json", "de_lotto", "gap_hazard", None)
    _decision(ms.decision_path_for("DE"), "de_lotto", "ewma_hl30", "DE")
    de = LotoEngine("6/49", game_key="de_lotto", country="DE")
    progress = []
    _run(
        de,
        _csv(tmp_path, DE_CSV, "de.csv"),
        progress_cb=lambda m, p: progress.append(m),
    )
    info = de.audit["bench_winner"]["de_lotto"]
    assert info["method"] == "ewma_hl30"
    assert "fallback" not in info
    assert any("Scoring: ewma_hl30" in m for m in progress)


@pytest.mark.parametrize("meta_country", [None, "RO", "PL"])
def test_country_decision_file_of_another_country_is_treated_as_missing(
    decisions, tmp_path, meta_country
):
    """O copie a deciziei românești (fără țară) sau decizia altei țări pusă în
    decisions/DE/ nu devine decizie germană."""
    _decision(ms.decision_path_for("DE"), "de_lotto", "ewma_hl30", meta_country)
    de = LotoEngine("6/49", game_key="de_lotto", country="DE")
    progress = []
    _run(
        de,
        _csv(tmp_path, DE_CSV, "de.csv"),
        progress_cb=lambda m, p: progress.append(m),
    )
    info = de.audit["bench_winner"]["de_lotto"]
    assert info["method"] == "frequency" and info["no_decision"] is True
    assert any(
        "frequency (fără bench pentru Germania · Lotto 6aus49)" in m for m in progress
    )


def test_romanian_decision_file_stamped_for_another_country_is_treated_as_missing(
    decisions,
):
    for stamp, expected in (
        ("DE", "frequency"),
        ("RO", "ewma_hl30"),
        (None, "ewma_hl30"),
    ):
        _decision(decisions / "best_methods.json", "loto_6_49", "ewma_hl30", stamp)
        ms._CONFIG = None  # rescris în aceeași milisecundă: mtime poate coincide
        assert ms.get_winner_name("loto_6_49", 12) == expected, stamp


def test_explicit_config_paths_are_not_country_checked(tmp_path):
    """Fișierele din afara căilor de țară (teste, `--out` explicit) se citesc ca înainte."""
    cfg = tmp_path / "other.json"
    _decision(cfg, "de_lotto", "ewma_hl30", "PL")
    assert ms.get_winner_name("de_lotto", 12, str(cfg)) == "ewma_hl30"
    rec = ms.recommend_optimal_config("de_lotto", 12, config_path=str(cfg))
    assert rec["scorer"] == "ewma_hl30" and rec["hit_target"] == 3


def test_urna2_contract_is_recognised_from_the_registry():
    assert ms._is_urna2_key("joker_urna2") is True
    assert ms._is_urna2_key("joker_urna1") is False
    assert ms._is_urna2_key("de_lotto") is False
    assert ms._is_urna2_key("nu_exista") is False


# --------------------------------------------------------------------------- #
# pool_history și starea adaptivă
# --------------------------------------------------------------------------- #


def test_pool_history_keys_romania_unchanged_foreign_prefixed(
    decisions, tmp_path, monkeypatch
):
    history_file = tmp_path / "pool_history.json"
    monkeypatch.setenv("LOTO_POOL_HISTORY_FILE", str(history_file))
    ro = LotoEngine("6/49")
    _run(ro, _csv(tmp_path, RO_CSV, "ro.csv"))
    before = json.loads(history_file.read_text(encoding="utf-8"))
    assert set(before) == {"6/49_12"}

    de = LotoEngine("6/49", game_key="de_lotto", country="DE")
    _run(de, _csv(tmp_path, DE_CSV, "de.csv"))
    after = json.loads(history_file.read_text(encoding="utf-8"))
    assert set(after) == {"6/49_12", "DE_de_lotto_6/49_12"}
    assert after["6/49_12"] == before["6/49_12"]
    assert after["DE_de_lotto_6/49_12"]["pool"] == de.hard_core
    # Primul pool german nu e comparat cu pool-ul românesc.
    assert de.audit["pool_variation"] == {}


def test_adaptive_state_keys_are_prefixed_only_for_foreign_games(tmp_path, monkeypatch):
    monkeypatch.setattr(af, "_STATE_FILE", tmp_path / "adaptive_state.json")
    assert af._state_key("6/49", 12) == "6/49_12"
    af.record_predicted_pool("6/49", 12, [1, 2, 3], data_rows=5)
    af.record_predicted_pool(
        "6/49", 12, [7, 8, 9], data_rows=6, key_prefix="DE_de_lotto_"
    )
    raw = json.loads((tmp_path / "adaptive_state.json").read_text(encoding="utf-8"))
    assert set(raw) == {"6/49_12", "DE_de_lotto_6/49_12"}
    assert af.load_adaptive_state("6/49", 12)["last_pool"] == [1, 2, 3]
    assert af.load_adaptive_state("6/49", 12, key_prefix="DE_de_lotto_")[
        "last_pool"
    ] == [7, 8, 9]
    summary = af.get_state_summary("6/45", 12, key_prefix="AT_x_")
    assert summary["baseline"] == pytest.approx(6 * 12 / 45)


def test_pipeline_adaptive_persistence_uses_the_foreign_prefix(
    decisions, tmp_path, monkeypatch
):
    state = tmp_path / "adaptive_state.json"
    monkeypatch.setattr(af, "_STATE_FILE", state)
    de = LotoEngine("6/49", game_key="de_lotto", country="DE")
    _run(de, _csv(tmp_path, DE_CSV, "de.csv"), enable_adaptive_persistence=True)
    assert set(json.loads(state.read_text(encoding="utf-8"))) == {"DE_de_lotto_6/49_12"}
