"""Bench per țară (`bench_all_methods.py --country CC`).

România rulează exact ca înainte; o altă țară ia jocurile din registru, scrie
numai sub căile ei și nu atinge niciodată folds.csv / report.json /
best_methods.json românești.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import bench_all_methods as bam
from loto_enterprise.benchmark import decision, freshness, runner
from loto_enterprise.core.lotteries import (
    PROJECT_ROOT,
    UnknownLotteryError,
    bench_out_dir_for,
    decision_path_for,
    require_lottery,
)

ROOT = Path(__file__).resolve().parent
RO_FILES = [
    ROOT / "bench_results" / "folds.csv",
    ROOT / "bench_results" / "report.json",
    ROOT / "best_methods.json",
]


def _digest(paths):
    out = {}
    for p in paths:
        out[str(p)] = (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        )
    return out


def _trimmed_de_csv(tmp_path: Path, rows: int = 160) -> Path:
    src = PROJECT_ROOT / require_lottery("de_lotto").csv
    ist = tmp_path / "ist"
    ist.mkdir()
    df = pd.read_csv(src, dtype=str)
    dst = ist / src.name
    df.tail(rows).to_csv(dst, index=False)
    return ist


# ---------------------------------------------------------------- căi ----------


def test_ro_paths_are_the_legacy_ones():
    assert bam.resolve_bench_paths("RO", None, None) == (
        "RO",
        "bench_results",
        "best_methods.json",
    )
    assert bam.resolve_bench_paths(None, None, None)[1:] == (
        "bench_results",
        "best_methods.json",
    )


def test_foreign_default_paths_are_country_paths():
    cc, out, dec = bam.resolve_bench_paths("de", None, None)
    assert cc == "DE"
    assert Path(out) == bench_out_dir_for("DE")
    assert Path(out).parts[-3:] == ("bench_results", "countries", "DE")
    assert Path(dec) == decision_path_for("DE")
    assert Path(dec).parts[-3:] == ("decisions", "DE", "best_methods.json")


@pytest.mark.parametrize(
    "out,dec",
    [
        ("bench_results", None),
        (str(ROOT / "bench_results"), None),
        (None, "best_methods.json"),
        (None, str(ROOT / "best_methods.json")),
    ],
)
def test_foreign_run_refuses_romanian_paths(out, dec):
    with pytest.raises(ValueError):
        bam.resolve_bench_paths("DE", out, dec)


def test_unknown_country_fails_loudly():
    with pytest.raises(UnknownLotteryError):
        bam.resolve_bench_paths("XX", None, None)


def test_decisions_dir_is_gitignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "decisions/" in lines
    # bench_results/countries/ cade sub `bench_results/*` (fără excepție).
    assert "bench_results/*" in lines
    assert not any(ln.startswith("!bench_results/countries") for ln in lines)


def _run_cli(args, tmp_path, timeout=600):
    env = dict(os.environ)
    env["LOTO_RUNTIME_DIR"] = str(tmp_path / "rt")
    env["LOTO_BENCH_CACHE_DIR"] = str(tmp_path / "bc")
    env["LOTO_POOL_HISTORY_FILE"] = str(tmp_path / "ph.json")
    return subprocess.run(
        [sys.executable, str(ROOT / "bench_all_methods.py"), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--country", "DE", "--out", "bench_results"],
        ["--country", "DE", "--decision", "best_methods.json"],
        ["--country", "XX"],
    ],
)
def test_cli_refuses_before_running(args, tmp_path):
    before = _digest(RO_FILES)
    proc = _run_cli([*args, "--quick", "--no-rich"], tmp_path, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _digest(RO_FILES) == before


# ------------------------------------------------------- rulare străină ------


def test_foreign_bench_writes_only_country_paths(tmp_path):
    ist = _trimmed_de_csv(tmp_path)
    out = tmp_path / "out"
    dec = tmp_path / "dec" / "best_methods.json"
    before = _digest(RO_FILES)
    proc = _run_cli(
        [
            "--country",
            "DE",
            "--istoric",
            str(ist),
            "--methods",
            "random,frequency",
            "--percentiles",
            "10,50,100",
            "--force-decision",
            "--no-shuffled-control",
            "--no-rich",
            "--out",
            str(out),
            "--decision",
            str(dec),
        ],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert _digest(RO_FILES) == before
    assert (out / "folds.csv").exists() and (out / "report.json").exists()

    folds = pd.read_csv(out / "folds.csv")
    assert set(folds["game"]) == {"de_lotto"}
    assert {"random", "frequency"} <= set(folds["method"])

    cfg = json.loads(dec.read_text(encoding="utf-8"))
    assert cfg["_meta"]["country"] == "DE"
    assert list(cfg["games"]) == ["de_lotto"]
    assert cfg["games"]["de_lotto"]["auto_pilot_per_pool"]
    sigs = cfg["_meta"]["csv_signatures"]
    assert list(sigs) == ["de_lotto"]
    assert Path(sigs["de_lotto"]["csv_path"]).parent == ist
    assert sigs["de_lotto"]["rows"] == 160

    # Prospețimea țării, pe aceleași CSV-uri: proaspătă.
    csv_map = {"de_lotto": [str(ist / Path(require_lottery("de_lotto").csv).name)]}
    cols_map = {"de_lotto": [f"n{i}" for i in range(1, 7)]}
    rep = freshness.check_freshness(str(dec), csv_map, cols_map, country="DE")
    assert rep["de_lotto"].status == "fresh"
    # ... și devine veche când istoricul țării se schimbă.
    csv = Path(csv_map["de_lotto"][0])
    df = pd.read_csv(csv, dtype=str)
    df.iloc[:-1].to_csv(csv, index=False)
    rep = freshness.check_freshness(str(dec), csv_map, cols_map, country="DE")
    assert rep["de_lotto"].status != "fresh"


# ----------------------------------------------------------- runner ----------


def test_registry_games_come_from_registry():
    games = runner.registry_games("DE")
    assert [g.key for g in games] == ["de_lotto"]
    g = games[0]
    assert g.cols == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert (g.max_num, g.draw_n, g.base_k, g.pool_extra) == (49, 6, 6, 14)
    assert Path(g.csv_path) == PROJECT_ROOT / require_lottery("de_lotto").csv
    assert g.label == "Germania · Lotto 6aus49"


def test_registry_games_unknown_country_and_missing_csv(tmp_path):
    with pytest.raises(UnknownLotteryError):
        runner.registry_games("XX")
    with pytest.raises(FileNotFoundError):
        runner.registry_games("DE", str(tmp_path))


def _write_csv(path: Path, n_cols: int = 6):
    cols = ",".join(f"n{i}" for i in range(1, n_cols + 1))
    path.write_text(f"{cols}\n" + ",".join(str(i) for i in range(1, n_cols + 1)) + "\n")


def test_discover_games_prefers_exact_ro_names_and_warns(tmp_path, caplog):
    # „a_loto_649_strain.csv” e sortat înaintea lui „loto_6_49.csv”.
    _write_csv(tmp_path / "a_loto_649_strain.csv")
    _write_csv(tmp_path / "loto_6_49.csv")
    _write_csv(tmp_path / "necunoscut.csv")
    with caplog.at_level(logging.WARNING, logger=runner.logger.name):
        games = runner.discover_games(str(tmp_path))
    assert [g.key for g in games] == ["loto_6_49"]
    assert Path(games[0].csv_path).name == "loto_6_49.csv"
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "a_loto_649_strain.csv" in warned
    assert "necunoscut.csv" in warned


def test_discover_games_ro_repository_unchanged():
    games = runner.discover_games(str(ROOT / "_ISTORIC"))
    assert [(g.key, Path(g.csv_path).name) for g in games] == [
        ("loto_6_49", "loto_6_49.csv"),
        ("loto_5_40", "loto_5_40.csv"),
        ("joker_urna1", "joker.csv"),
        ("joker_urna2", "joker.csv"),
    ]


def test_stall_timeout_scales_only_for_long_histories():
    assert runner.default_stall_timeout(0) == 900.0
    assert runner.default_stall_timeout(2588) == 900.0  # cel mai lung istoric RO
    assert runner.default_stall_timeout(6000) == pytest.approx(3600.0)
    assert runner.default_stall_timeout(10**6) == 6 * 3600.0


# ---------------------------------------------------------- freshness --------


def test_country_freshness_inputs():
    path, csv_map, cols_map = freshness.country_freshness_inputs("RO")
    assert path == "best_methods.json"
    assert csv_map is freshness.GAMES_CSV_MAP and cols_map is None
    path, csv_map, cols_map = freshness.country_freshness_inputs("DE")
    assert Path(path) == decision_path_for("DE")
    assert list(csv_map) == ["de_lotto"]
    assert csv_map["de_lotto"] == [str(PROJECT_ROOT / require_lottery("de_lotto").csv)]
    assert cols_map["de_lotto"] == [f"n{i}" for i in range(1, 7)]


def test_foreign_freshness_rejects_decision_of_other_country(tmp_path):
    _, csv_map, cols_map = freshness.country_freshness_inputs("DE")
    bm = tmp_path / "best_methods.json"
    bm.write_text(json.dumps({"_meta": {"country": "PL"}, "games": {}}))
    freshness.write_signatures_to_best_methods(str(bm), csv_map, cols_map)
    rep = freshness.check_freshness(str(bm), csv_map, cols_map, country="DE")
    assert rep["de_lotto"].status == "missing"
    assert rep["de_lotto"].recommendation == "full_rebench"
    # Același fișier cu țara corectă: proaspăt.
    cfg = json.loads(bm.read_text())
    cfg["_meta"]["country"] = "DE"
    bm.write_text(json.dumps(cfg))
    rep = freshness.check_freshness(str(bm), csv_map, cols_map, country="DE")
    assert rep["de_lotto"].status == "fresh"


def test_ro_freshness_default_keys_unchanged(tmp_path):
    rep = freshness.check_freshness(str(tmp_path / "absent.json"))
    assert list(rep) == ["loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2"]


def test_auto_pilot_writes_the_given_path(tmp_path):
    folds = pd.DataFrame(
        [
            {
                "game": "de_lotto",
                "method": m,
                "percentile": p,
                "is_random": False,
                "n_test": 50,
                "n_eval": 50,
                "rate_3plus_k6": r,
                "k6": 0.7,
                "runtime_sec": 0.1,
                "failed": False,
            }
            for m, r in (("frequency", 0.02), ("random", 0.018))
            for p in (10, 50, 100)
        ]
    )
    fp = tmp_path / "folds.csv"
    folds.to_csv(fp, index=False)
    bm = tmp_path / "decisions" / "DE" / "best_methods.json"
    bm.parent.mkdir(parents=True)
    bm.write_text(
        json.dumps(
            {"_meta": {"country": "DE"}, "games": {"de_lotto": {"draw_n": 6}}}
        )
    )
    before = _digest(RO_FILES)
    decision.update_best_methods_with_auto_pilot(
        best_methods_path=str(bm), folds_csv_path=str(fp)
    )
    cfg = json.loads(bm.read_text())
    assert "k6" in cfg["games"]["de_lotto"]["auto_pilot_per_pool"]
    assert _digest(RO_FILES) == before
