"""Regresii din auditul 2026-10-07."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import app_nicegui  # importul rulează _sync_ui_namespace, ca aplicația
import ui_bench
import ui_hits
import ui_results

ROOT = Path(__file__).resolve().parent
UI_MODULES = ("ui_runtime.py", "ui_results.py", "ui_bench.py", "ui_hits.py", "app_nicegui.py")


def _private_definitions(path: Path) -> dict[str, str]:
    """Numele private definite (nu importate) la nivelul modulului, cu sursa lor."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            if name.startswith("_") and not name.startswith("__"):
                out[name] = ast.dump(node)
    return out


def test_ui_modules_do_not_define_one_private_name_two_ways():
    # _sync_ui_namespace copiază numele private în toate modulele UI; ultimul
    # definit câștigă. Două definiții diferite sub același nume schimbă tăcut
    # comportamentul modulului care pierde.
    seen: dict[str, tuple[str, str]] = {}
    clashes = []
    for name in UI_MODULES:
        for key, source in _private_definitions(ROOT / name).items():
            if key in seen and seen[key][1] != source:
                clashes.append((key, seen[key][0], name))
            seen.setdefault(key, (name, source))
    assert clashes == []


def test_full_ticket_chances_keep_small_percentages_after_namespace_sync():
    assert ui_results._pct is not ui_bench._rate_pct
    t = {
        "variants": [[1, 2, 3, 4, 5]],
        "chances": {"thresholds": [4, 5], "shown": {4: 0.003131, 5: 0.0000365}},
    }
    line = ui_results._full_ticket_chances(t, is_joker=False)[0]
    assert "4+ 0.313%" in line
    assert "5+ 0.00365%" in line


def _wf_entries(coverage: float, n: int = 3):
    return [
        SimpleNamespace(
            draw_index=i, wheel_coverage=coverage, hits=2, hits_union=3,
            joker_hit=None, ticket_context=None,
        )
        for i in range(n)
    ]


def test_incomplete_wheel_coverage_is_never_printed_as_100():
    # 6/49, pool 16, garanție 4, plafon 195 din 196: 99,95%.
    css, text = ui_hits._wf_coverage_note(_wf_entries(99.95))
    assert "minim 99.95%" in text and "100.0%" not in text
    summary = ui_results._wf_summary(_wf_entries(99.95))
    assert "min 99.95%" in summary and "100.0%" not in summary


def test_german_fetcher_asks_for_every_year_between_last_draw_and_today(monkeypatch):
    import datetime as dt
    import json

    import pytest

    import update_externe as ue

    asked: list[str] = []

    def fake_get(url, headers=None):
        asked.append(url)
        return json.dumps({"days": []}).encode()

    monkeypatch.setattr(ue, "_get", fake_get)
    with pytest.raises(ue.SourceError):
        ue.fetch_de_lotto(dt.date(2026, 9, 30), dt.date(2028, 1, 5))
    years = [ue._cet_midnight_ms(dt.date(y, 12, 31)) for y in (2026, 2027, 2028)]
    assert asked == [f"https://www.lotto.de/api/stats/entities.lotto/history/{ms}" for ms in years]


# --------------------------------------------------------------------------- #
# update_csv.py: gaură în istoric, a doua extragere a zilei, fișier blocat
# --------------------------------------------------------------------------- #
def _istoric(tmp_path, rows_649, rows_joker=(), rows_540=()):
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    (istoric / "loto_6_49.csv").write_text(
        "date,n1,n2,n3,n4,n5,n6\n" + "".join(r + "\n" for r in rows_649), encoding="utf-8"
    )
    (istoric / "joker.csv").write_text(
        "date,n1,n2,n3,n4,n5,joker\n" + "".join(r + "\n" for r in rows_joker),
        encoding="utf-8",
    )
    (istoric / "loto_5_40.csv").write_text(
        "date,n1,n2,n3,n4,n5,n6\n" + "".join(r + "\n" for r in rows_540), encoding="utf-8"
    )
    return istoric


def _pages(monkeypatch, istoric, by_game: dict[str, str]):
    import update_csv as uc

    url_to_game = {cfg["recent_url"]: key for key, cfg in uc.GAME_CONFIGS.items()}
    monkeypatch.setattr(uc, "_find_istoric_dir", lambda: istoric)
    monkeypatch.setattr(uc, "_get_page_text", lambda url: by_game.get(url_to_game[url], ""))
    return uc


def test_update_csv_refuses_a_page_that_no_longer_reaches_the_stored_history(
    tmp_path, monkeypatch, capsys
):
    istoric = _istoric(tmp_path, ["31-05-2026,1,2,3,4,5,6"])
    before = (istoric / "loto_6_49.csv").read_text(encoding="utf-8")
    page = "2026-09-27 7 8 9 10 11 12 2026-10-01 13 14 15 16 17 18 2026-10-04 1 9 17 25 33 41"
    uc = _pages(monkeypatch, istoric, {"loto_6_49": page})
    assert uc.update_all() == 0
    assert (istoric / "loto_6_49.csv").read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "NU scriu nimic" in out and "NEACTUALIZATE: Loto 6/49" in out


def test_update_csv_adds_the_second_draw_of_a_day_already_stored(tmp_path, monkeypatch):
    istoric = _istoric(tmp_path, ["10-09-2026,1,2,3,4,5,6", "13-09-2026,7,8,9,10,11,12"])
    page = (
        "2026-09-10 1 2 3 4 5 6 2026-09-13 12 11 10 9 8 7 "  # aceeași, altă ordine
        "2026-09-13 20 21 22 23 24 25"  # a doua extragere a zilei
    )
    uc = _pages(monkeypatch, istoric, {"loto_6_49": page})
    assert uc.update_all() == 1
    lines = (istoric / "loto_6_49.csv").read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == ["13-09-2026,7,8,9,10,11,12", "13-09-2026,20,21,22,23,24,25"]


def test_update_csv_keeps_updating_other_games_when_one_file_is_locked(
    tmp_path, monkeypatch, capsys
):
    istoric = _istoric(
        tmp_path,
        ["01-10-2026,1,2,3,4,5,6"],
        ["01-10-2026,1,2,3,4,5,7"],
        ["01-10-2026,1,2,3,4,5,6"],
    )
    uc = _pages(
        monkeypatch,
        istoric,
        {
            "loto_6_49": "2026-10-01 1 2 3 4 5 6 2026-10-04 7 8 9 10 11 12",
            "joker": "2026-10-01 1 2 3 4 5 + 7 2026-10-04 6 7 8 9 10 + 3",
            "loto_5_40": "2026-10-01 1 2 3 4 5 6 2026-10-04 7 8 9 10 11 12",
        },
    )
    real = uc._append_rows_atomic

    def locked(csv_path, *a, **k):
        if csv_path.name == "loto_6_49.csv":
            raise PermissionError("fișier deschis în Excel")
        return real(csv_path, *a, **k)

    monkeypatch.setattr(uc, "_append_rows_atomic", locked)
    assert uc.update_all() == 2
    assert "04-10-2026,6,7,8,9,10,3" in (istoric / "joker.csv").read_text(encoding="utf-8")
    assert "04-10-2026" in (istoric / "loto_5_40.csv").read_text(encoding="utf-8")
    assert "NEACTUALIZATE: Loto 6/49" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Decizia: nume respins → frequency; auditul spune când rulează rezerva
# --------------------------------------------------------------------------- #
def _decision_file(monkeypatch, tmp_path, games: dict | None):
    import json

    import loto_enterprise.core.method_selector as ms

    path = tmp_path / "best_methods.json"
    if games is not None:
        path.write_text(json.dumps({"games": games}), encoding="utf-8")
    monkeypatch.setattr(ms, "_DEFAULT_CONFIG_PATH", path)
    monkeypatch.setattr(ms, "_CONFIG", None)
    monkeypatch.setattr(ms, "_CONFIG_PATH_USED", None)
    monkeypatch.setattr(ms, "_CONFIG_MTIME", -1.0)
    return ms


def _engine_649(n: int = 80):
    import numpy as np
    import pandas as pd

    from loto_engine import LotoEngine

    rng = np.random.default_rng(11)
    rows = []
    for i in range(n):
        nums = sorted(rng.choice(np.arange(1, 50), size=6, replace=False).tolist())
        rows.append({"date": f"{(i % 28) + 1:02d}-01-2026", **{f"n{j + 1}": v for j, v in enumerate(nums)}})
    eng = LotoEngine(game_type="6/49")
    eng.data = pd.DataFrame(rows)
    eng._build_draw_matrix()
    eng._winner_pool_hint = 12
    return eng


_LEGACY = {
    "winners_per_pool_best": {"k12": {"winner": "ewma_hl30"}},
    "winners_per_pool": {"k12": "ewma_hl30"},
    "overall_winner": "ewma_hl30",
}


def test_rejected_decision_scorer_falls_to_frequency_not_to_old_avg_hits_winner(
    monkeypatch, tmp_path
):
    ms = _decision_file(
        monkeypatch,
        tmp_path,
        {
            "loto_6_49": {
                "draw_n": 6,
                "auto_pilot_per_pool": {
                    "k12": {"scorer": "random", "ensemble": [{"method": "random", "weight": 1.0}]}
                },
                **_LEGACY,
            }
        },
    )
    assert ms.get_winner_name("loto_6_49", 12) == "frequency"
    assert [n for n, _fn, _w in ms.get_ensemble_for_game("loto_6_49", 12)] == ["frequency"]
    cfg = ms.recommend_optimal_config("loto_6_49", 12)
    assert cfg["scorer"] == "frequency" and cfg["fallback"] is True
    # Fără auto_pilot_per_pool, câmpurile vechi rămân rezerva documentată.
    ms = _decision_file(monkeypatch, tmp_path, {"loto_6_49": {"draw_n": 6, **_LEGACY}})
    assert ms.get_winner_name("loto_6_49", 12) == "ewma_hl30"


def test_engine_audit_marks_the_reserve_when_the_decision_names_a_forbidden_method(
    monkeypatch, tmp_path
):
    from loto_enterprise.benchmark.methods import score_frequency

    _decision_file(
        monkeypatch,
        tmp_path,
        {
            "loto_6_49": {
                "draw_n": 6,
                "auto_pilot_per_pool": {
                    "k12": {"scorer": "random", "ensemble": [{"method": "random", "weight": 1.0}]}
                },
            }
        },
    )
    eng = _engine_649()
    scores = eng._get_timesfm_scores()
    assert scores == score_frequency(eng._draw_matrix.astype("int64"), 49)
    info = eng.audit["bench_winner"]["loto_6_49"]
    assert info["method"] == "frequency"
    assert info["fallback"] is True and info["attempted"] == "random"


def test_romanian_game_without_decision_is_audited_as_no_decision(monkeypatch, tmp_path):
    from loto_enterprise.benchmark.methods import score_frequency

    _decision_file(monkeypatch, tmp_path, None)  # best_methods.json lipsă
    eng = _engine_649()
    scores = eng._get_timesfm_scores()
    # Același pool ca înainte: aceeași funcție de frecvență pe aceleași extrageri.
    assert scores == score_frequency(eng._draw_matrix.astype("int64"), 49)
    info = eng.audit["bench_winner"]["loto_6_49"]
    assert info["method"] == "frequency"
    assert info["fallback"] is True and info["no_decision"] is True


def test_urn2_without_joker_values_is_not_audited_as_scored(monkeypatch, tmp_path):
    from test_engine_urna2_scoring import _engine, _joker_frame

    _decision_file(monkeypatch, tmp_path, None)
    eng = _engine(_joker_frame(with_joker=False))
    assert eng._get_timesfm_scores(is_joker_drum=True) == {}
    assert "joker_urna2" not in (eng.audit.get("bench_winner") or {})


def test_mail_names_the_rejected_method_instead_of_claiming_no_bench():
    from loto_enterprise.core.lotteries import ro_lottery_for_geometry

    spec = ro_lottery_for_geometry("6/49")
    data = {
        "pool_size": 12,
        "audit": {
            "bench_winner": {
                "loto_6_49": {"method": "frequency", "fallback": True, "attempted": "random"}
            }
        },
    }
    (line,) = app_nicegui._mail_method_lines(spec, data)
    assert "random" in line and "fără bench" not in line and "câștigătoarea" not in line


def test_decision_cache_never_pairs_one_path_with_another_files_content(
    monkeypatch, tmp_path
):
    import json
    import threading
    import time

    ms = _decision_file(monkeypatch, tmp_path, {"loto_6_49": {}})
    other = tmp_path / "decisions_de.json"
    other.write_text(json.dumps({"games": {"de_lotto": {}}}), encoding="utf-8")
    real_read = ms._read_config
    reading = threading.Event()

    def slow_read(path):
        if path == ms._DEFAULT_CONFIG_PATH and not reading.is_set():
            reading.set()
            time.sleep(0.2)  # citirea firului WF, cât firul UI cere altă țară
        return real_read(path)

    monkeypatch.setattr(ms, "_read_config", slow_read)
    first = threading.Thread(target=ms._load_config)
    first.start()
    assert reading.wait(5)
    second = threading.Thread(target=ms._load_config, args=(str(other),))
    second.start()
    second.join()
    first.join()
    assert list(ms._load_config(str(other))["games"]) == ["de_lotto"]
    assert list(ms._load_config()["games"]) == ["loto_6_49"]


# --------------------------------------------------------------------------- #
# Decizia: folduri incomplete, ordinea rândurilor
# --------------------------------------------------------------------------- #
def test_target_change_refuses_folds_that_do_not_cover_the_decision_bench(tmp_path):
    import json

    import pandas as pd
    import pytest

    from loto_enterprise.benchmark import decision as D

    folds = pd.read_csv(ROOT / "bench_results" / "folds.csv")
    meta = {
        "methods_tested_per_game": {
            g: sorted(set(folds.loc[folds["game"] == g, "method"])) for g in folds["game"].unique()
        },
        "percentiles": sorted(int(p) for p in folds["percentile"].unique()),
    }
    cfg = {"_meta": meta, "games": {"loto_6_49": {"draw_n": 6, "pick_n": 6}}}
    assert D.missing_decision_folds(cfg, folds) == []
    # Flush parțial: lipsește o fereastră a unei metode.
    cut = (folds["game"] == "loto_6_49") & (folds["method"] == "frequency") & (folds["percentile"] == 10)
    partial = tmp_path / "folds.csv"
    folds[~cut].to_csv(partial, index=False)
    bm = tmp_path / "best_methods.json"
    bm.write_text(json.dumps(cfg), encoding="utf-8")
    before = bm.read_bytes()
    with pytest.raises(D.IncompleteFoldsError):
        D.update_best_methods_with_auto_pilot(str(bm), str(partial), require_complete=True)
    assert bm.read_bytes() == before
    assert D.missing_decision_folds(cfg, folds[~cut]) == [("loto_6_49", "frequency", 10)]


def test_pooled_rate_does_not_depend_on_the_order_of_folds_rows():
    import pandas as pd

    from loto_enterprise.benchmark.decision import pooled_rate_and_neff

    # r·n = 0.1, 0.2, 0.3: (0.1+0.2)+0.3 ≠ 0.1+(0.2+0.3) în virgulă mobilă.
    rows = [{"rate_3plus_k12": r, "n_eval": 1, "n_test": 1} for r in (0.1, 0.2, 0.3)]
    forward = pooled_rate_and_neff(pd.DataFrame(rows), "rate_3plus_k12")
    backward = pooled_rate_and_neff(pd.DataFrame(rows[::-1]), "rate_3plus_k12")
    assert forward == backward


# --------------------------------------------------------------------------- #
# Istoric hits: reluarea „Bilet complet” se calculează o singură dată
# --------------------------------------------------------------------------- #
def test_full_ticket_replay_is_computed_once_per_request(monkeypatch):
    import time

    import loto_enterprise.core.ticket_replay as tr

    calls: list[str] = []

    def slow_replay(flat, game, tickets, guarantee, workers=1):
        calls.append(game)
        time.sleep(0.2)
        return {"best": {}, "uniform": None, "n_draws": 0, "missing": 0, "errors": {}}

    monkeypatch.setattr(tr, "replay_full_tickets", slow_replay)
    monkeypatch.setattr(ui_hits, "_REPLAY_DONE", {})
    monkeypatch.setattr(ui_hits, "_REPLAY_PENDING", {})
    monkeypatch.setattr(ui_hits, "_REPLAY_THREAD", {"running": False, "current": None})
    flats = {g: [object()] for g in ("6/49", "joker", "5/40")}
    filled: set[str] = set()
    deadline = time.monotonic() + 10
    while len(filled) < 3 and time.monotonic() < deadline:
        for g, flat in flats.items():  # temporizatorul de 1 s al fiecărui bloc
            if g not in filled and ui_hits._full_ticket_replay(flat, g, 3, 3) is not None:
                filled.add(g)
        time.sleep(0.05)
    assert filled == set(flats)
    assert sorted(calls) == sorted(flats)


# --------------------------------------------------------------------------- #
# Walk-forward: decizia rescrisă în timpul rulării nu ajunge în cache
# --------------------------------------------------------------------------- #
def test_walk_forward_does_not_cache_steps_scored_by_two_decisions(monkeypatch, tmp_path):
    import json

    import numpy as np
    import pandas as pd

    import loto_enterprise.core.walk_forward_adapter as wfa
    from loto_enterprise.core.backtesting import LotoBacktester

    ms = _decision_file(monkeypatch, tmp_path, {"loto_6_49": {}})
    monkeypatch.setattr(wfa, "CACHE_DIR", tmp_path / "wf")
    real = LotoBacktester.run_retroactive_backtest

    def rewrite_decision_midway(self, *a, **k):
        out = real(self, *a, **k)
        ms._DEFAULT_CONFIG_PATH.write_text(
            json.dumps({"games": {"loto_6_49": {"x": 1}}}), encoding="utf-8"
        )
        return out

    monkeypatch.setattr(LotoBacktester, "run_retroactive_backtest", rewrite_decision_midway)
    rng = np.random.default_rng(5)
    df = pd.DataFrame(
        [
            {"date": f"{(i % 28) + 1:02d}-{(i // 28) % 12 + 1:02d}-2025",
             **{f"n{j + 1}": v for j, v in enumerate(sorted(rng.choice(np.arange(1, 50), 6, replace=False).tolist()))}}
            for i in range(60)
        ]
    )
    flat, meta = wfa.run_honest_walk_forward(df, "6/49", 10, backtest_depth_percent=5.0, guarantee=3)
    assert flat and meta.get("decision_changed") is True
    assert not list((tmp_path / "wf").glob("*.pkl"))


# --------------------------------------------------------------------------- #
# Penalizarea recentă cu factorul 0 și blocul impus de interval
# --------------------------------------------------------------------------- #
def test_zero_penalty_factor_keeps_the_score_order_of_penalized_numbers():
    import numpy as np

    from loto_engine import LotoEngine
    from loto_enterprise.core.ranking import rank_by_score

    scores = {1: 0.9, 2: 0.8, 3: 0.7, 4: 0.3, 5: 0.2, 6: 0.1}
    out, penalized = LotoEngine.apply_recent_penalty(scores, np.array([[1, 2, 3]]), 1, 0.0, 6)
    assert set(penalized) == {1, 2, 3}
    # Sub toate nepenalizatele, dar în ordinea scorului, nu „numărul mare întâi".
    assert rank_by_score(out, 6) == [4, 5, 6, 1, 2, 3]


def test_a_pool_that_takes_the_whole_restricted_interval_is_not_blamed_on_the_scorer():
    from loto_enterprise.core.pool_selection import select_pool_from_scores

    scores = {n: float(n % 7) + n / 100 for n in range(1, 50)}
    excluded = set(range(1, 38))  # interval 38..49: exact 12 numere
    audit: dict = {}
    pool = select_pool_from_scores(scores, 12, excluded, audit=audit, max_num=49)
    assert pool == list(range(38, 50)) and audit["pool_is_consecutive_block"] is True
    assert "pool_consecutive_warning" not in audit
    restricted = {"restrict_base": {"min": 38, "max": 49, "excluded": sorted(excluded)}}
    assert ui_results._consecutive_pool_warning(pool, restricted) is None
    assert "POOL CONSECUTIV" in ui_results._consecutive_pool_warning(pool)


# --------------------------------------------------------------------------- #
# Bench: rulări care nu rescriu decizia; semnăturile datelor citite de bench
# --------------------------------------------------------------------------- #
def test_coarse_block_size_is_a_reduced_run_that_keeps_the_decision():
    from argparse import Namespace

    import bench_all_methods as bam

    pcts = [10, 30, 60, 100]
    assert bam.reduced_run_reason(Namespace(block_size=1, istoric=None), pcts, 3) is None
    assert bam.reduced_run_reason(Namespace(block_size=50, istoric=None), pcts, 3) == "--block-size 50"
    assert bam.reduced_run_reason(Namespace(block_size=1, istoric="x"), pcts, 3) == "--istoric explicit"
    assert bam.reduced_run_reason(Namespace(block_size=1, istoric=None), [10, 100], 3)


def test_a_draw_added_during_the_bench_keeps_the_decision_stale(tmp_path):
    import json

    from loto_enterprise.benchmark import freshness

    csv = tmp_path / "de.csv"
    csv.write_text(
        "date,n1,n2,n3,n4,n5,n6\n01-01-2026,1,2,3,4,5,6\n04-01-2026,7,8,9,10,11,12\n",
        encoding="utf-8",
    )
    maps = {"csv_map": {"de_lotto": [str(csv)]}, "cols_map": {"de_lotto": [f"n{i}" for i in range(1, 7)]}}
    dec = tmp_path / "best_methods.json"
    dec.write_text(json.dumps({"_meta": {"country": "DE"}, "games": {"de_lotto": {}}}), encoding="utf-8")
    before_bench = freshness.csv_signatures(**maps)
    with csv.open("a", encoding="utf-8") as fh:  # ACTUALIZARI.bat, în timpul bench-ului
        fh.write("08-01-2026,13,14,15,16,17,18\n")
    freshness.write_signatures_to_best_methods(str(dec), signatures=before_bench, **maps)
    report = freshness.check_freshness(str(dec), maps["csv_map"], maps["cols_map"], country="DE")
    assert report["de_lotto"].status != "fresh"


# --------------------------------------------------------------------------- #
# Covering: dispersie cu limită de consecutive, semnătura designurilor, reluare
# --------------------------------------------------------------------------- #
def _spread_ticket(lo, hi, pool_size, tickets, seed):
    import random

    from loto_enterprise.core.full_ticket import build_full_ticket
    from loto_enterprise.core.pool_selection import select_pool_from_scores

    rnd = random.Random(seed)
    scores = {n: rnd.random() for n in range(1, 50)}
    excluded = set(range(1, lo)) | set(range(hi + 1, 50))
    audit = {"restrict_base": {"min": lo, "max": hi, "excluded": sorted(excluded)}}
    pool = select_pool_from_scores(scores, pool_size, excluded, audit, max_num=49, max_consecutive_run=2)
    data = {"hard_core": pool, "guarantee": 3, "audit": audit, "hard_core_joker": []}
    t = build_full_ticket("6/49", data, tickets, spread=True, with_chances=False)
    return t, audit["consecutive_limit"]["applied"]


def test_spread_variants_keep_the_consecutive_limit_when_the_base_allows_it():
    from loto_enterprise.core.ranking import longest_consecutive_run

    # Baza 10..18 are 10 combinații conforme de 6 numere; 3 bilete = 9 variante.
    t, limit = _spread_ticket(10, 18, 6, 3, 180)
    assert len(t["variants"]) == 9 and limit == 2
    assert max(longest_consecutive_run(v) for v in t["variants"]) <= 2
    assert "Nicio variantă nu are mai mult de 2 numere consecutive" in t["note"]


def test_spread_note_does_not_claim_a_limit_that_cannot_fit():
    from loto_enterprise.core.ranking import longest_consecutive_run

    # Baza 10..16 are o singură combinație conformă pentru limita 3: 3 variante nu încap.
    t, limit = _spread_ticket(10, 16, 6, 1, 160)
    worst = max(longest_consecutive_run(v) for v in t["variants"])
    assert worst > limit
    assert "Nicio variantă" not in t["note"]
    assert f"cel mult {worst} pe o variantă" in t["note"]


def test_design_signature_follows_content_not_checkout_location(monkeypatch, tmp_path):
    import shutil

    import covering.designs as designs

    name = "C_12_6_4.txt"
    first, second = tmp_path / "a" / "covering_designs", tmp_path / "b" / "covering_designs"
    for target in (first, second):
        target.mkdir(parents=True)
        shutil.copy(ROOT / "covering_designs" / name, target / name)
    monkeypatch.setattr(designs, "_LAJOLLA_DIRS", [first])
    here = designs.covering_design_source_signature(12, 6, 4)
    monkeypatch.setattr(designs, "_LAJOLLA_DIRS", [second])
    assert designs.covering_design_source_signature(12, 6, 4) == here
    (second / name).write_text((second / name).read_text(encoding="utf-8") + "1 2 3 4 5 6\n", encoding="utf-8")
    assert designs.covering_design_source_signature(12, 6, 4) != here


def test_replay_skips_a_step_where_the_two_modes_have_different_variant_counts():
    from loto_enterprise.core.ticket_replay import replay_step

    # Pas din fallback-ul de frecvență: fără clasament, pool-ul de 6 nu se extinde.
    context = {
        "hard_core": [3, 9, 17, 25, 33, 41],
        "hard_core_joker": [],
        "audit": {"consecutive_limit": {"requested": 2, "applied": 2}},
        "actual": [3, 9, 17, 20, 30, 44],
    }
    assert replay_step("6/49", context, 1, 3) == {"error": "număr diferit de variante între moduri"}


def test_istoric_hits_uses_the_pool_size_actually_played():
    # Pool cerut 12, bază 10..20 (11 numere): fiecare pas WF joacă 11 numere.
    flat = [
        SimpleNamespace(draw_index=i, ticket_context={"hard_core": list(range(10, 21))})
        for i in range(4)
    ]
    assert ui_hits._wf_pool_size(flat, 12) == 11
    assert ui_hits._wf_pool_size(_wf_entries(100.0), 12) == 12  # cache vechi, fără context


def test_a_relative_worker_from_another_checkout_is_not_our_worker(monkeypatch, tmp_path):
    import subprocess
    import sys
    import time

    import ui_shared

    ours, other = tmp_path / "ours", tmp_path / "other"
    for folder in (ours, other):
        folder.mkdir()
        (folder / "worker.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(ui_shared, "WORKER_PATH", ours / "worker.py")
    # UI-ul rulează din checkout-ul lui: vechea comparație rezolva argumentul
    # relativ al ORICĂRUI proces față de acest director.
    monkeypatch.chdir(ours)
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "worker.py"], cwd=other))
        time.sleep(0.5)
        assert ui_shared.is_worker_running() is False
        procs.append(subprocess.Popen([sys.executable, "worker.py"], cwd=ours))
        deadline = time.monotonic() + 5
        while not ui_shared.is_worker_running() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ui_shared.is_worker_running() is True
    finally:
        for proc in procs:
            proc.kill()
            proc.wait(timeout=10)


def test_mail_best_draw_line_follows_the_calendar_not_the_row_order():
    import pandas as pd

    from loto_enterprise.core.lotteries import ro_lottery_for_geometry

    spec = ro_lottery_for_geometry("6/49")
    rows = [
        ("05-03-2020", [1, 2, 3, 4, 5, 6]),
        ("19-01-2017", [1, 2, 3, 4, 5, 7]),
        ("10-01-2017", [1, 2, 3, 4, 50, 8]),  # invalidă: 50 în afara jocului
    ]
    df = pd.DataFrame(
        [{"date": d, **{f"n{i + 1}": v for i, v in enumerate(nums)}} for d, nums in rows]
    )  # CSV încărcat de la cea mai nouă la cea mai veche
    line = app_nicegui._mail_best_draw_line(spec, df, [1, 2, 3, 4, 5, 6, 7])
    assert "ultima oară la 05-03-2020" in line
    assert "(2 extrageri" in line and "de 2 ori" in line


# --------------------------------------------------------------------------- #
# Decizia: avantajul trebuie să treacă și corecția pentru candidați
# --------------------------------------------------------------------------- #
def test_holm_adjustment_and_binomial_excess():
    import pandas as pd

    from loto_enterprise.benchmark.decision import excess_p_value, holm_adjusted

    adj = holm_adjusted({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}
    frame = pd.DataFrame(
        [
            {"percentile": 10, "rate_3plus_k12": 0.9, "n_eval": 10, "n_test": 10},
            {"percentile": 100, "rate_3plus_k12": 0.2, "n_eval": 100, "n_test": 100},
        ]
    )
    # Fereastra completă decide: 20 de evenimente din 100 la p0 = 0,2.
    from scipy.stats import binom

    assert abs(excess_p_value(frame, "rate_3plus_k12", 0.2) - binom.sf(19, 100, 0.2)) < 1e-12
    assert excess_p_value(frame, "rate_3plus_k12", None) is None


def _gate_folds(rates: dict[str, float], p0: float, pool: int = 12) -> "pd.DataFrame":
    import pandas as pd

    sizes = {10: 78, 30: 233, 60: 466, 100: 777}
    rows = []
    for method, rate in {"random": p0, **rates}.items():
        for pct, n in sizes.items():
            rows.append({
                "game": "loto_6_49", "method": method, "percentile": pct,
                "is_random": False, "failed": False, "n_test": n, "n_eval": n,
                f"k{pool}": 1.5, "avg_hits_topk": 0.7, f"k{pool}_bl": 1.5,
                f"rate_3plus_k{pool}": rate, f"tiebreak_k{pool}": 0.1, "runtime_sec": 0.1,
            })
    return pd.DataFrame(rows)


def test_a_lucky_winner_among_many_candidates_is_flagged_but_still_chosen(monkeypatch):
    from loto_enterprise.benchmark import decision
    from loto_enterprise.benchmark.methods import METHODS

    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    names = sorted(set(METHODS) - set(decision.EXCLUDED_FROM_PRODUCTION) - {"random"})[:40]
    p0 = decision.expected_random_rate(49, 6, 12, 3)
    # +0,8..1,2 pp peste rata aleatoare, la fiecare dintre 40 de candidați.
    weak = {name: p0 + 0.008 + i * 1e-4 for i, name in enumerate(names)}
    cfg = decision.decide_optimal_config_for_pool(_gate_folds(weak, p0), "loto_6_49", 12, 6)
    assert cfg["scorer"] == names[-1] and cfg["low_confidence"] is False
    assert cfg["multiplicity"]["candidates"] == 40
    assert cfg["multiplicity"]["proven"] is False
    assert "avantaj nedemonstrat" in cfg["rationale"]
    assert ui_bench._multiplicity_note(cfg).startswith("⚠️ Avantaj nedemonstrat")

    strong = {**weak, names[0]: p0 + 0.08}
    cfg = decision.decide_optimal_config_for_pool(_gate_folds(strong, p0), "loto_6_49", 12, 6)
    assert cfg["scorer"] == names[0] and cfg["multiplicity"]["proven"] is True
    assert ui_bench._multiplicity_note(cfg) is None


# --------------------------------------------------------------------------- #
# Urna 2: metodele care repetă bila precedentă și duplicatul naive_bayes_last
# --------------------------------------------------------------------------- #
def test_urn2_decision_skips_methods_that_repeat_the_previous_ball(monkeypatch):
    import pandas as pd

    from loto_enterprise.benchmark import decision

    rows = []
    for method, rate in (("random", 0.05), ("markov_self_state", 0.30), ("ewma_hl30", 0.09)):
        for pct, n in {10: 66, 30: 197, 60: 394, 100: 657}.items():
            rows.append({
                "game": "joker_urna2", "method": method, "percentile": pct,
                "is_random": False, "failed": False, "n_test": n, "n_eval": n,
                "k1": rate, "k1_bl": rate, "avg_hits_topk": rate, "rate_1plus_k1": rate,
                "tiebreak_k1": 0.1, "runtime_sec": 0.1,
            })
    cfg = decision.decide_optimal_config_for_pool(pd.DataFrame(rows), "joker_urna2", 1, 1)
    assert cfg["scorer"] == "ewma_hl30"
    assert "markov_self_state" not in cfg["ranked_methods"]


def test_stale_urn2_decision_with_a_previous_ball_method_falls_to_frequency(
    monkeypatch, tmp_path
):
    entry = {"scorer": "markov_self_state", "ensemble": [{"method": "markov_self_state", "weight": 1.0}]}
    ms = _decision_file(
        monkeypatch,
        tmp_path,
        {
            "joker_urna2": {"draw_n": 1, "auto_pilot_per_pool": {"k1": entry}},
            "loto_6_49": {"draw_n": 6, "auto_pilot_per_pool": {"k12": entry}},
        },
    )
    assert ms.get_winner_name("joker_urna2", 1) == "frequency"
    assert ms.rejected_decision_scorer("joker_urna2", 1) == "markov_self_state"
    # Pe jocurile cu pool rămâne o metodă de recență obișnuită.
    assert ms.get_winner_name("loto_6_49", 12) == "markov_self_state"


def test_urn2_curation_drops_excluded_and_duplicate_methods():
    from loto_enterprise.benchmark.curated import load_per_game

    urn2 = set(load_per_game()["joker_urna2"])
    assert not urn2 & {"markov_self_state", "vlmm_self_k3", "naive_bayes_last"}
    assert {"markov_pairs", "frequency"} <= urn2


def _station_db(tmp_path, monkeypatch):
    """Coada stației, izolată: citirile și marcajul UI-ului pe aceeași bază."""
    import functools
    import json

    import job_queue as queue
    from ui_shared import pack_queue_result

    database = str(tmp_path / "station.db")
    jid = queue.submit_job("pipeline", json.dumps({"datasets": []}), db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(jid, pack_queue_result(([], 0)), db_path=database)
    for name in ("get_latest_completed_job", "mark_job_finalized", "get_job_status"):
        monkeypatch.setattr(
            app_nicegui, name, functools.partial(getattr(queue, name), db_path=database)
        )
    for name in ("_save_report_file",):
        monkeypatch.setattr(app_nicegui, name, lambda: None)
    for key in (
        "active_job_id",
        "results",
        "result_sources",
        "results_recovered",
        "legacy_finalized_job_id",
    ):
        monkeypatch.setitem(app_nicegui.STATE, key, None)
    return database, jid


def test_another_stations_marker_does_not_hide_this_stations_job(tmp_path, monkeypatch):
    """`.ui_state.json` din checkout se sincronizează între stații, iar id-urile
    pornesc de la 1 pe fiecare. Id-ul 1 salvat de altă stație nu mai ascunde
    jobul #1 nepreluat al acestei stații: marcajul stă pe rândul din baza ei."""
    import json

    import job_queue as queue

    database, jid = _station_db(tmp_path, monkeypatch)
    app_nicegui.UI_STATE_FILE.write_text(
        json.dumps({"last_finalized_job_id": jid + 1, "pool_size_val": 12}),
        encoding="utf-8",
    )
    app_nicegui._migrate_legacy_finalized_marker()
    saved = json.loads(app_nicegui.UI_STATE_FILE.read_text(encoding="utf-8"))
    assert "last_finalized_job_id" not in saved
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"] is None

    # A doua stație scrie din nou id-ul (versiune veche): nu mai contează.
    app_nicegui.UI_STATE_FILE.write_text(
        json.dumps({"last_finalized_job_id": jid}), encoding="utf-8"
    )
    app_nicegui._recover_completed_job(allow_finalize=False)
    assert app_nicegui.STATE["results"] == ([], 0)
    assert f"job #{jid}" in app_nicegui.STATE["results_recovered"]
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"]


def test_legacy_marker_of_the_latest_job_is_migrated_once(tmp_path, monkeypatch):
    """Marcajul vechi trece pe rândul jobului, iar cheia veche dispare. Jobul
    deja preluat se reafișează, fără finalizare (mail, oprire)."""
    import json

    import job_queue as queue

    database, jid = _station_db(tmp_path, monkeypatch)
    app_nicegui.UI_STATE_FILE.write_text(
        json.dumps({"last_finalized_job_id": jid}), encoding="utf-8"
    )
    app_nicegui._migrate_legacy_finalized_marker()
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"]
    saved = json.loads(app_nicegui.UI_STATE_FILE.read_text(encoding="utf-8"))
    assert "last_finalized_job_id" not in saved
    app_nicegui._recover_completed_job(allow_finalize=True)
    assert app_nicegui.STATE["active_job_id"] is None
    assert app_nicegui.STATE["results"] == ([], 0)
    assert f"job #{jid}" in app_nicegui.STATE["results_recovered"]


def test_settings_without_the_legacy_key_are_not_rewritten(tmp_path, monkeypatch):
    _station_db(tmp_path, monkeypatch)
    saves = []
    monkeypatch.setattr(app_nicegui, "_save_settings", lambda: saves.append(1))
    app_nicegui._migrate_legacy_finalized_marker()
    assert saves == []


def test_a_failed_migration_keeps_the_legacy_marker_and_its_rule(tmp_path, monkeypatch):
    """Baza blocată la migrare: cheia veche rămâne în fișier, iar recuperarea din
    aceeași pornire numai reafișează jobul deja preluat (fără mail sau oprire
    repetate)."""
    import json
    import sqlite3

    import job_queue as queue

    database, jid = _station_db(tmp_path, monkeypatch)
    app_nicegui.UI_STATE_FILE.write_text(
        json.dumps({"last_finalized_job_id": jid}), encoding="utf-8"
    )

    def _locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_nicegui, "mark_job_finalized", _locked)
    app_nicegui._migrate_legacy_finalized_marker()
    saved = json.loads(app_nicegui.UI_STATE_FILE.read_text(encoding="utf-8"))
    assert saved["last_finalized_job_id"] == jid

    app_nicegui._recover_completed_job(allow_finalize=True)
    assert app_nicegui.STATE["active_job_id"] is None
    assert app_nicegui.STATE["results"] == ([], 0)
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"] is None
