"""Auditul global din 2026-10-22, grupul „bench”: constatările C5, C6 și C8.

Fiecare test pica pe codul de dinainte (55ea7f6) și trece după corectură.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app_nicegui as app
from loto_engine import LotoEngine
from loto_enterprise.benchmark import runner

ROOT = Path(__file__).resolve().parent


@pytest.fixture
def ui_state(monkeypatch):
    """SETTINGS/STATE izolate; România implicit (ca în test_country_ui)."""
    monkeypatch.setattr(app, "SETTINGS", dict(app.SETTINGS))
    import ui_runtime

    monkeypatch.setattr(ui_runtime, "SETTINGS", app.SETTINGS)
    state = {**app.STATE, "datasets": [], "dataset_game": {}}
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(ui_runtime, "STATE", state)
    app._sync_ui_namespace()
    app.SETTINGS["country_val"] = "RO"
    app.SETTINGS["games_val"] = []
    app.SETTINGS["restrict_base_enabled_val"] = False
    yield state
    app._sync_ui_namespace()


# --------------------------------------------------------------------------- #
# C5: panoul de bench al unei țări străine numără `random` o singură dată.
# --------------------------------------------------------------------------- #
def test_country_bench_target_counts_random_once(ui_state, monkeypatch):
    from loto_enterprise.benchmark.curated import apply_curation
    from loto_enterprise.benchmark.methods import list_methods, method_meta

    avail = [m for m in list_methods() if method_meta(m).get("available", True)]
    kept, _ = apply_curation(avail)
    assert "random" in kept  # REQUIRED_METHODS: runner-ul îl rulează din listă
    monkeypatch.setattr(app, "_avg_fold_runtime", lambda *a, **k: 1.0)
    texts = app._country_bench_texts("AT")
    n_win = len([p for p in app._PCTS.split(",") if p.strip()])
    # Austria: un joc, o cheie de bench; lista rulată = `kept` (cu random inclus).
    assert texts["curation"] == (
        f"Austria: {len(kept) - 1} metode × 1 joc, plus baseline-ul random."
    )
    assert f"pentru {app.count(len(kept) * n_win, 'folduri', 'un fold')} " in texts["eta"]


# --------------------------------------------------------------------------- #
# C6: un istoric căzut la încărcare nu rescrie decizia celorlalte jocuri.
# --------------------------------------------------------------------------- #
def _tiny_game(tmp_path: Path, key: str = "loto_6_49", n_rows: int = 90) -> runner.GameDef:
    rows = []
    for i in range(n_rows):
        base = (i * 6) % 44
        rows.append({f"n{j + 1}": 1 + ((base + j) % 49) for j in range(6)})
    csv_path = tmp_path / f"{key}.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return runner.GameDef(
        key=key,
        label=key,
        csv_path=str(csv_path),
        cols=[f"n{j}" for j in range(1, 7)],
        max_num=49,
        draw_n=6,
        pool_extra=0,
    )


def _run(games, out: Path) -> dict:
    return runner.run_benchmark(
        games=games,
        methods=["frequency"],
        percentiles=[100],
        use_cache=False,
        shuffled_control=False,
        out_dir=str(out),
    )


def test_the_report_names_the_games_whose_history_did_not_load(tmp_path):
    good = _tiny_game(tmp_path)
    lost = runner.GameDef(
        key="loto_5_40",
        label="Loto 5/40",
        csv_path=str(tmp_path / "lipsa" / "loto_5_40.csv"),
        cols=[f"n{j}" for j in range(1, 7)],
        max_num=40,
        draw_n=6,
        pool_extra=0,
    )
    out = tmp_path / "out"
    report = _run([good, lost], out)
    assert report.get("games_load_failed") == ["loto_5_40"]
    on_disk = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert on_disk["games_load_failed"] == ["loto_5_40"]

    # Calea normală: cheia lipsește, report.json rămâne ca înainte.
    out_ok = tmp_path / "out_ok"
    report = _run([good], out_ok)
    assert "games_load_failed" not in report
    assert "games_load_failed" not in (out_ok / "report.json").read_text(encoding="utf-8")


def test_a_bench_with_one_unreadable_history_leaves_the_decision_alone(
    tmp_path, monkeypatch
):
    # Joker.csv cu o dată goală (sau blocat de sincronizarea Drive): 6/49 și 5/40
    # produc folduri, iar decizia Joker se înlocuia cu celule {"error"},
    # ștampilată la zi, cu ieșirea 0.
    import bench_all_methods as bam

    ist = tmp_path / "ist"
    ist.mkdir()
    for name in ("loto_6_49.csv", "loto_5_40.csv", "joker.csv"):
        rows = (ROOT / "_ISTORIC" / name).read_text(encoding="utf-8").splitlines()
        tail = rows[:1] + rows[-200:]
        if name == "joker.csv":
            tail[100] = "," + tail[100].split(",", 1)[1]
        (ist / name).write_text("\n".join(tail) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    decision = tmp_path / "best_methods.json"
    sentinel = '{"games": {"joker_urna1": {"sentinel": true}}}'
    decision.write_text(sentinel, encoding="utf-8")
    monkeypatch.setenv("LOTO_BENCH_CACHE_DIR", str(tmp_path / "cache"))

    written = []
    real_write = bam.write_bench_decision

    def spy(*a, **k):
        written.append(a)
        return real_write(*a, **k)

    monkeypatch.setattr(bam, "write_bench_decision", spy)
    monkeypatch.setattr(
        sys,
        "argv",
        ["bench_all_methods.py", "--istoric", str(ist), "--out", str(out),
         "--decision", str(decision), "--methods", "frequency", "--percentiles", "100",
         "--no-shuffled-control", "--no-cache", "--no-rich", "--force-decision"],
    )
    assert bam.main() == 1
    assert written == []
    assert decision.read_text(encoding="utf-8") == sentinel
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["games_load_failed"] == ["joker_urna1", "joker_urna2"]
    assert int(report["n_folds_total"]) > 0  # celelalte jocuri au rulat


# --------------------------------------------------------------------------- #
# C8: rezerva motorului la generare nu e o mutare a deciziei. WF-ul rămâne sărit
# (pașii ar fi scorați de metoda deciziei, nu de frequency, care a dat pool-ul),
# dar panoul și raportul spun motivul adevărat.
# --------------------------------------------------------------------------- #
@pytest.fixture
def decision_now(monkeypatch):
    """Metoda (și funcția ei) pe care decizia de acum o dă fiecărei chei."""
    import loto_enterprise.core.method_selector as ms

    chosen: dict[str, tuple] = {}
    monkeypatch.setattr(ms, "has_decision", lambda key, cfg=None: key in chosen)
    monkeypatch.setattr(
        ms,
        "get_ensemble_for_game",
        lambda key, pool_size=None, config_path=None, max_methods=None: [
            (chosen[key][0], chosen[key][1], 1.0)
        ],
    )
    monkeypatch.setattr(ms, "rejected_decision_scorer", lambda *a, **k: None)
    return chosen


def _fallback(**extra):
    info = {
        "method": "frequency",
        "fallback": True,
        "reason": "bench-winner scoring empty",
        "pool_hint": 16,
    }
    info.update(extra)
    return {"pool_size": 16, "pool_size_requested": 16,
            "audit": {"bench_winner": {"loto_6_49": info}}}


_RESERVE_MARK = {"scorer_fallback": True, "scorer": "markov_lag2"}
_RESERVE_PANEL = (
    "⚠️ Walk-forward sărit: la generare scorerul deciziei (markov_lag2) a eșuat, iar "
    "pool-ul vine din rezerva frequency; pașii WF ar fi scorați de metoda deciziei, "
    "nu de cea care a produs pool-ul afișat. Generează din nou."
)
_RESERVE_REPORT = (
    "  Walk-forward: sărit — la generare scorerul deciziei (markov_lag2) a eșuat, iar "
    "pool-ul vine din rezerva frequency; pașii WF ar fi scorați de metoda deciziei, "
    "nu de cea care a produs pool-ul afișat."
)
_MOVED_PANEL = "⚠️ Walk-forward sărit: decizia de bench s-a schimbat după generare"
_MOVED_REPORT = "  Walk-forward: sărit — decizia de bench s-a schimbat după generare."


def test_a_scorer_fallback_at_generation_skips_wf_for_its_true_reason(decision_now):
    decision_now["loto_6_49"] = ("markov_lag2", None)
    # Scorerul deciziei a eșuat la generare; decizia e aceeași. Pașii WF ar fi
    # scorați tot de markov_lag2, nu de frequency: jocul rămâne sărit...
    for data in (
        _fallback(),
        _fallback(attempted="markov_lag2", reason="bench-winner scoring unusable"),
    ):
        assert app._decision_moved_since_generation("6/49", data)
        assert not app._result_scorers_match_decision("6/49", data)
        # ...cu motivul adevărat, nu „decizia s-a schimbat”.
        assert app._wf_skip_mark("6/49", data) == _RESERVE_MARK
    # Rezerva numește altă metodă încercată: o mutare reală se vede în continuare.
    moved_attempt = _fallback(attempted="ewma_hl30")
    # Fără decizie la generare, iar acum există una: decizia s-a mutat.
    no_decision = _fallback(no_decision=True)
    # Rezultat normal al altei metode: tot mutare.
    moved = {"pool_size": 16, "audit": {"bench_winner": {"loto_6_49": {"method": "ewma_hl30"}}}}
    for data in (moved_attempt, no_decision, moved):
        assert app._decision_moved_since_generation("6/49", data)
        assert app._wf_skip_mark("6/49", data) == {"decision_moved": True}
    # Rezerva unui scorer respins (decizia de acum dă chiar frequency): WF rulează.
    decision_now["loto_6_49"] = ("frequency", None)
    assert not app._decision_moved_since_generation("6/49", _fallback(attempted="markov_lag2"))


class _InlineThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def wf_job(monkeypatch, decision_now):
    """Un rezultat 6/49 gata de WF; `run_honest_walk_forward` numără apelurile."""
    from loto_enterprise.core import walk_forward_adapter as wfa

    frame = pd.DataFrame({"date": ["24-09-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    calls: list = []

    def setup(data: dict) -> list:
        data = {"hard_core": list(range(1, 17)), "guarantee": 4, **data}
        for key, value in {
            "results": ([("loto_6_49.csv", {"6/49": data})], 1),
            "result_sources": {"loto_6_49.csv": frame},
            "retro": {},
            "retro_meta": {},
            "wf_seq": 0,
            "wf_user_cancel": False,
            "job_start_time": None,
        }.items():
            monkeypatch.setitem(app.STATE, key, value)
        return calls

    def run_wf(**kwargs):
        calls.append(kwargs)
        return [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1}

    for name in ("_save_report_file", "_finalize_pipeline"):
        monkeypatch.setattr(app, name, lambda: None)
    monkeypatch.setattr(app.threading, "Thread", _InlineThread)
    monkeypatch.setattr(app, "_echo_mismatch", lambda *_: False)
    monkeypatch.setattr(wfa, "run_honest_walk_forward", run_wf)
    return setup


def _hits_panel_text(monkeypatch, tmp_path) -> str:
    """Textul secțiunii „Analiză & Clasament” pentru rezultatul din STATE."""
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    (tmp_path / "bench_results").mkdir(exist_ok=True)
    (tmp_path / "bench_results" / "folds.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(ui_hits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ui_hits, "_render_bench_leaderboard", lambda *a, **k: None)
    monkeypatch.setattr(ui_hits, "_render_last_csv_draw", lambda *a, **k: None)
    with capture_ui() as ui:
        ui_hits._render_analysis_menu(app.STATE["results"][0])
    return ui.text()


def test_the_live_wf_skips_the_engine_reserve_and_says_why(
    wf_job, decision_now, monkeypatch, tmp_path
):
    decision_now["loto_6_49"] = ("markov_lag2", None)
    calls = wf_job(_fallback(attempted="markov_lag2"))
    app._start_walk_forward()
    assert calls == []  # pașii nu sunt scorați de markov_lag2 sub pool-ul frequency
    assert "loto_6_49.csv_6/49" not in app.STATE["retro"]
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == _RESERVE_MARK
    report = app._build_report()
    assert _RESERVE_REPORT in report
    assert "decizia de bench s-a schimbat" not in report
    panel = _hits_panel_text(monkeypatch, tmp_path)
    assert _RESERVE_PANEL in panel
    assert "decizia de bench s-a schimbat" not in panel


def test_the_live_wf_keeps_the_moved_decision_text(wf_job, decision_now, monkeypatch, tmp_path):
    # Re-Bench după generare: decizia alege acum altă metodă decât cea încercată.
    decision_now["loto_6_49"] = ("ewma_hl30", None)
    calls = wf_job(_fallback(attempted="markov_lag2"))
    app._start_walk_forward()
    assert calls == []
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"decision_moved": True}
    report = app._build_report()
    assert _MOVED_REPORT in report
    assert "rezerva frequency; pașii WF" not in report
    panel = _hits_panel_text(monkeypatch, tmp_path)
    assert _MOVED_PANEL in panel
    assert "rezerva frequency; pașii WF" not in panel


def test_recovery_marks_the_engine_reserve_with_its_true_reason(wf_job, decision_now):
    decision_now["loto_6_49"] = ("markov_lag2", None)
    calls = wf_job(_fallback())
    # Cache-ul de sub decizia de acum ar fi pașii lui markov_lag2: nu se afișează.
    assert app._load_cached_walk_forward() == 0
    assert calls == []
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == _RESERVE_MARK
    assert _RESERVE_REPORT in app._build_report()

    # Rezerva numește scorerul încercat, iar decizia alege acum altul: mutare.
    decision_now["loto_6_49"] = ("ewma_hl30", None)
    wf_job(_fallback(attempted="markov_lag2"))
    assert app._load_cached_walk_forward() == 0
    assert calls == []
    assert app.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"decision_moved": True}
    assert _MOVED_REPORT in app._build_report()


def _engine_649() -> LotoEngine:
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
    eng._winner_pool_hint = 16
    eng.use_bench_winner = True
    return eng


@pytest.mark.parametrize("failure", ["raise", "empty"])
def test_the_engine_fallback_names_the_scorer_that_failed(decision_now, failure):
    def broken(draws, max_num):
        if failure == "raise":
            raise FloatingPointError("eroare numerica")
        return {}

    decision_now["loto_6_49"] = ("markov_lag2", broken)
    eng = _engine_649()
    scores = eng._get_timesfm_scores()
    assert set(scores) == set(range(1, 50))
    assert eng.audit["bench_winner"]["loto_6_49"] == {
        "method": "frequency",
        "fallback": True,
        "reason": "bench-winner scoring empty",
        "pool_hint": 16,
        "family": "baseline",
        "attempted": "markov_lag2",
    }
    data = {"pool_size": 16, "pool_size_requested": 16, "audit": eng.audit}
    # Aceeași decizie: WF-ul e sărit cu motivul rezervei, nu „decizie schimbată”.
    assert app._decision_moved_since_generation("6/49", data)
    assert app._wf_skip_mark("6/49", data) == _RESERVE_MARK
    # Re-Bench care mută decizia după generare: mutarea se vede.
    decision_now["loto_6_49"] = ("ewma_hl30", broken)
    assert app._decision_moved_since_generation("6/49", data)
    assert app._wf_skip_mark("6/49", data) == {"decision_moved": True}
    # Starea nu rămâne pe motor pentru generarea următoare.
    assert getattr(eng, "_bench_winner_empty_attempt", None) is None


def test_a_frequency_decision_that_yields_nothing_keeps_its_old_audit(decision_now):
    # Rezerva unui scorer respins/indisponibil e chiar `frequency`: nu se numește
    # pe sine, auditul rămâne cel de dinainte.
    decision_now["loto_6_49"] = ("frequency", lambda draws, max_num: {})
    eng = _engine_649()
    eng._get_timesfm_scores()
    assert eng.audit["bench_winner"]["loto_6_49"] == {
        "method": "frequency",
        "fallback": True,
        "reason": "bench-winner scoring empty",
        "pool_hint": 16,
        "family": "baseline",
    }


def test_scripts_imported_by_tests_keep_the_console_lenient():
    """`reconfigure(encoding=...)` fără `errors` pune handler-ul „strict”. Importat
    la colectare (test_audit_2026_10_22 importă bench_all_methods), făcea strict
    fișierul de captură al pytest: un copil care scria octeți ne-UTF-8 pe fd 2
    (WF paralel) dădea UnicodeDecodeError și toate testele de după cădeau."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent
    bad = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\.reconfigure\(([^)]*)\)", text):
            if "encoding" in m.group(1) and "errors" not in m.group(1):
                bad.append(f"{path.name}: {m.group(0)}")
    assert not bad, bad
