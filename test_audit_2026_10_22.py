"""Constatările auditului pe module din 2026-10-22, fiecare cu testul care pica înainte.

Fiecare constatare a fost reprodusă de un auditor, apoi verificată separat de un
al doilea agent (reproducere) și de un arbitru pe contractele din AGENTS.md.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd
import pytest

import app_nicegui  # noqa: F401  (importul leagă numele UI, ca aplicația)
import cleanup_old_processes as cop
import covering.budget_designs as bd
import ui_bench
import ui_results
import update_csv as uc
import update_externe as ue
import worker
from covering.common import _sorted_pool
from covering.lr_schemes import lr_schemes, scheme_tickets
from loto_engine import LotoEngine
from loto_enterprise.benchmark import runner
from loto_enterprise.core.lotteries import GAMES_BY_ID
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.walk_forward_adapter import _penalty_sig

# Fixture-ul cozii izolate și configurația UI (aceeași cale ca jobul real).
from test_country_worker import _app_config, queue  # noqa: F401

ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Benchmark: scrierea finală a folds.csv
# --------------------------------------------------------------------------- #


def _tiny_game(tmp_path: Path, n_rows: int = 90) -> runner.GameDef:
    rows = []
    for i in range(n_rows):
        base = (i * 6) % 44
        rows.append({f"n{j + 1}": 1 + ((base + j) % 49) for j in range(6)})
    csv_path = tmp_path / "loto_6_49.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return runner.GameDef(
        key="loto_6_49",
        label="Loto 6/49",
        csv_path=str(csv_path),
        cols=[f"n{j}" for j in range(1, 7)],
        max_num=49,
        draw_n=6,
        pool_extra=0,
    )


def _refuse_folds_replace(monkeypatch, refusals: int | None):
    """os.replace pe folds.csv refuzat (alt proces ține fișierul deschis)."""
    real = runner.os.replace
    calls = {"folds": 0}

    def fake(src, dst):
        if Path(dst).name == "folds.csv":
            calls["folds"] += 1
            if refusals is None or calls["folds"] <= refusals:
                raise PermissionError(13, "fisier deschis de alt proces", str(dst))
        return real(src, dst)

    monkeypatch.setattr(runner.os, "replace", fake)
    monkeypatch.setattr(runner, "_FINAL_FLUSH_BACKOFF_SEC", 0.0)
    return calls


def test_a_final_folds_write_that_keeps_failing_stops_the_bench(tmp_path, monkeypatch):
    # Înainte: eșecul se loga la DEBUG, run_benchmark raporta succes, iar
    # decizia se construia din folds.csv-ul vechi de pe disc.
    out = tmp_path / "out"
    out.mkdir()
    stale = out / "folds.csv"
    stale.write_text("game,method,percentile\nSTALE,STALE,0\n", encoding="utf-8")
    _refuse_folds_replace(monkeypatch, refusals=None)
    with pytest.raises(PermissionError):
        runner.run_benchmark(
            games=[_tiny_game(tmp_path)],
            methods=["frequency"],
            percentiles=[100],
            use_cache=False,
            shuffled_control=False,
            out_dir=str(out),
        )
    assert "STALE" in stale.read_text(encoding="utf-8")
    assert not list(out.glob("*.tmp"))


def test_a_briefly_locked_folds_file_is_written_after_a_retry(tmp_path, monkeypatch):
    out = tmp_path / "out"
    calls = _refuse_folds_replace(monkeypatch, refusals=2)
    runner.run_benchmark(
        games=[_tiny_game(tmp_path)],
        methods=["frequency"],
        percentiles=[100],
        use_cache=False,
        shuffled_control=False,
        out_dir=str(out),
    )
    assert calls["folds"] == 3
    folds = pd.read_csv(out / "folds.csv")
    assert list(folds["method"]) == ["frequency"]


def test_the_5_40_console_line_names_the_base_pool():
    # 5/40: avg_hits_topk e măsurat pe k5 (biletul), dar consola scria „hits@k6”.
    source = (ROOT / "bench_all_methods.py").read_text(encoding="utf-8")
    assert "hits@k{game.draw_n}" not in source
    assert source.count("hits@k{game.base_k}") == 2
    assert (
        "render_regressive_table(console, folds_df, game.key, game.label, game.base_k)"
        in source
    )


def test_history_folders_differing_only_by_case_are_read_once(tmp_path, monkeypatch):
    # Pe Windows `_ISTORIC` și `_istoric` sunt același folder: a doua trecere
    # scria trei avertismente false „nume nerecunoscut sau joc deja luat”.
    (tmp_path / "_ISTORIC").mkdir()
    monkeypatch.chdir(tmp_path)
    dirs = runner._list_istoric_dirs()
    assert len(dirs) == 1
    if (tmp_path / "_istoric").exists():  # disc fără majuscule
        assert dirs == [Path("_ISTORIC")]


# --------------------------------------------------------------------------- #
# Covering: ordinea pe poziții și designurile cu buget
# --------------------------------------------------------------------------- #


def test_a_missing_score_goes_to_the_tail_of_the_design_positions():
    # Lipsa cheii valora 0.0: lângă un scor real negativ, 3 lua locul lui 2.
    assert _sorted_pool([1, 2, 3], {1: 0.5, 2: -0.3}) == [1, 2, 3]
    assert _sorted_pool([1, 2, 3], {1: 0.5, 2: -0.3, 3: float("nan")}) == [1, 2, 3]


def test_a_non_ascii_line_spoils_only_its_own_budget_section():
    valid = "B 2\n1 2 3 4 5\n2 3 4 5 6\n"
    nbsp = "B 3\n1 2 3 4 5\n1\u00a02 3 4 6\n2 3 4 5 6\n"
    alone = bd._parse(valid, 6, 5)
    # Înainte: amprenta ASCII ridica pentru tot fișierul, iar loader-ul îl
    # ignora întreg (toate bugetele cădeau pe căutarea din proces).
    both = bd._parse(valid + nbsp, 6, 5)
    assert set(both) == {2}
    assert both[2] == alone[2]  # amprenta secțiunii valide rămâne aceeași
    fullwidth = "B 3\n1 2 3 4 5\n\uff11 2 3 4 6\n2 3 4 5 6\n"
    assert set(bd._parse(valid + fullwidth, 6, 5)) == {2}


# --------------------------------------------------------------------------- #
# Date: rândul cu o celulă în plus, copia din sursa străină
# --------------------------------------------------------------------------- #


def test_a_joker_row_with_an_extra_cell_is_refused_not_rewritten():
    # Înainte: al șaselea număr devenea Joker-ul (7), iar Joker-ul real (12) se pierdea.
    page = "2026-10-15 1 2 3 4 5 + 6 2026-10-19 3 9 17 28 41 7 + 12 (adsbygoogle"
    rejected: list = []
    draws = uc._extract_draws(page, 5, True, None, 45, 20, rejected=rejected)
    assert [d["date"] for d in draws] == [dt.date(2026, 10, 15)]
    assert [r["date"] for r in rejected] == [dt.date(2026, 10, 19)]
    assert rejected[0]["reason"] == "rând necitibil"


def test_a_6_49_row_with_seven_cells_is_refused():
    page = "2026-10-15 1 2 3 4 5 6 2026-10-19 3 9 17 28 41 45 7 2026-10-22 7 8 9 10 11 12"
    rejected: list = []
    draws = uc._extract_draws(page, 6, False, None, 49, rejected=rejected)
    assert [d["date"] for d in draws] == [dt.date(2026, 10, 15), dt.date(2026, 10, 22)]
    assert [r["date"] for r in rejected] == [dt.date(2026, 10, 19)]


def test_site_rows_followed_by_the_next_date_or_text_still_parse():
    # Forma reală de pe loto49.ro: după ultimul număr vine data următoare sau text.
    page = (
        "2026-10-15 33 17 20 22 4 + 16 2026-10-18 16 19 26 45 30 + 13 "
        "(adsbygoogle = window.adsbygoogle || []).push({});"
    )
    rejected: list = []
    draws = uc._extract_draws(page, 5, True, None, 45, 20, rejected=rejected)
    assert [(d["main"], d["joker"]) for d in draws] == [
        ([33, 17, 20, 22, 4], 16),
        ([16, 19, 26, 45, 30], 13),
    ]
    assert rejected == []


TODAY = dt.date(2026, 10, 22)


def _have():
    return [
        ue.Draw(dt.date(2026, 10, 1), (1, 2, 3, 4, 5, 6)),
        ue.Draw(dt.date(2026, 10, 4), (7, 8, 9, 10, 11, 12)),
    ]


def test_a_foreign_source_repeating_the_last_draw_writes_nothing():
    # Înainte: copia se scria, iar PushHistory refuza apoi tot fișierul.
    lot = GAMES_BY_ID["at_lotto"]
    have = _have()
    copy = ue.Draw(dt.date(2026, 10, 8), (12, 11, 10, 9, 8, 7))
    with pytest.raises(ue.SourceError, match="copie"):
        ue.plan_update(lot, have, [*have, copy], TODAY)
    new = ue.Draw(dt.date(2026, 10, 8), (13, 14, 15, 16, 17, 18))
    again = ue.Draw(dt.date(2026, 10, 11), (18, 17, 16, 15, 14, 13))
    with pytest.raises(ue.SourceError, match="copie"):
        ue.plan_update(lot, have, [*have, new, again], TODAY)
    assert ue.plan_update(lot, have, [*have, new], TODAY) == ([new], 2)


# --------------------------------------------------------------------------- #
# Motor: factorul 0 al penalizării recente
# --------------------------------------------------------------------------- #


def test_factor_zero_puts_penalized_numbers_below_zero_scored_ones():
    # Înainte: 2 (penalizat, scor 0.8) ajungea 8e-10 > 0, deci peste 5 (scor 0).
    out, counts = LotoEngine.apply_recent_penalty(
        {1: 0.5, 2: 0.8, 5: 0.0}, [[1, 2]], 1, 0.0, 5
    )
    assert counts == {1: 1, 2: 1}
    assert rank_by_score(out, 1) == [5]
    assert rank_by_score(out, 3) == [5, 2, 1]  # ordinea scorului între penalizate
    # Negativ nepenalizat, apariții repetate: tot sub toate nepenalizatele.
    out, _ = LotoEngine.apply_recent_penalty(
        {1: 0.9, 2: 0.8, 3: 0.0, 4: -0.2}, [[1, 2], [1, 7]], 2, 0.0, 9
    )
    assert rank_by_score(out, 4) == [3, 4, 2, 1]


def test_factor_zero_orders_penalized_numbers_by_appearances_then_score():
    # Revizia diff-ului: un penalizat cu scor 0 și o apariție (0 * f = 0) ajungea
    # sub unul cu două apariții și scor pozitiv.
    out, counts = LotoEngine.apply_recent_penalty(
        {1: 0.0, 2: 0.9, 3: 0.5}, [[1, 2], [2, 4]], 2, 0.0, 4
    )
    assert counts == {1: 1, 2: 2, 4: 1}
    assert rank_by_score(out, 3) == [3, 1, 2]


def test_a_bench_without_folds_leaves_the_decision_alone(tmp_path, monkeypatch):
    # Revizia diff-ului: cu toate istoricele căzute la încărcare, decizia se
    # reconstruia din folds.csv-ul bench-ului anterior, ștampilată proaspătă.
    import bench_all_methods as bam

    out = tmp_path / "out"
    out.mkdir()
    (out / "folds.csv").write_text("game,method,percentile\nSTALE,STALE,0\n", encoding="utf-8")
    decision = tmp_path / "best_methods.json"
    decision.write_text('{"games": {}}', encoding="utf-8")
    bad = tmp_path / "ist"
    bad.mkdir()
    (bad / "loto_6_49.csv").write_text("foo,bar\n1,2\n", encoding="utf-8")
    written = []
    monkeypatch.setattr(bam, "write_bench_decision", lambda *a, **k: written.append(a) or True)
    monkeypatch.setattr(
        sys, "argv",
        ["bench_all_methods.py", "--istoric", str(bad), "--out", str(out),
         "--decision", str(decision), "--methods", "frequency", "--percentiles", "100",
         "--force-decision"],
    )
    assert bam.main() == 1
    assert written == []
    assert decision.read_text(encoding="utf-8") == '{"games": {}}'


@pytest.mark.parametrize("script", ["analiza_4plus.py", "analiza_escalada.py"])
def test_analysis_scripts_write_utf8_when_redirected(script, tmp_path):
    # Ieșirea redirecționată e cp1252 pe Windows: „Ă”/„→” ridicau UnicodeEncodeError.
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    args = [sys.executable, str(ROOT / script)]
    if script == "analiza_escalada.py":
        data = tmp_path / "ist"
        data.mkdir()
        for name in ("loto_6_49.csv", "loto_5_40.csv", "joker.csv"):
            rows = (ROOT / "_ISTORIC" / name).read_text(encoding="utf-8").splitlines()
            (data / name).write_text("\n".join(rows[:1] + rows[-400:]) + "\n", encoding="utf-8")
        args.append(str(data))
    proc = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, timeout=600)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
    assert "→".encode() in proc.stdout or "Ă".encode() in proc.stdout


def test_factor_zero_with_positive_scores_is_bit_identical():
    out, _ = LotoEngine.apply_recent_penalty({1: 0.5, 2: 0.8, 5: 0.3}, [[1, 2]], 1, 0.0, 5)
    assert out == {1: 0.5 * 1e-9, 2: 0.8 * 1e-9, 5: 0.3}


def test_only_factor_zero_walk_forward_keys_change():
    assert _penalty_sig(0, 0.0) == ""
    assert _penalty_sig(5, 0.5) == f"|rp5:{0.5.hex()}"
    assert _penalty_sig(5, 0.0) == f"|rp5:{0.0.hex()}|rpz1"
    source = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert 'PIPELINE_CACHE_VERSION = "v22"' in source


# --------------------------------------------------------------------------- #
# UI: legenda clasamentului live, cifrele comparației cu schema LR
# --------------------------------------------------------------------------- #


def test_live_bench_caption_names_the_thresholds_istoric_hits_shows(tmp_path, monkeypatch):
    from scripts.analysis.audit_output import capture_ui

    folds = tmp_path / "folds.csv"
    pd.DataFrame({"method": ["frequency"], "game": ["loto_6_49"]}).to_csv(folds, index=False)
    monkeypatch.setattr(ui_bench, "_country_folds_path", lambda *a, **k: folds)
    monkeypatch.setattr(ui_bench, "_render_bench_leaderboard_slice", lambda *a, **k: None)
    with capture_ui() as ui:
        ui_bench._render_bench_live_leaderboard(progress=0.5)
    caption = [line for line in ui.text().splitlines() if "Walk-forward:" in line]
    assert caption and "+4 și +5" in caption[0]
    assert "+3" not in caption[0]


def test_two_different_chances_never_print_alike_on_the_lr_comparison_line():
    # Joker pool 15: Cod 12 plus o variantă; la 5, 118 față de 119 variante
    # ieșeau amândouă „0.0097%”.
    pool = list(range(1, 16))
    scheme = next(s for s in lr_schemes("joker", 15) if s.code == 12)
    tickets = {tuple(sorted(v)) for v in scheme_tickets(scheme, pool)}
    extra = next(c for c in combinations(pool, 5) if c not in tickets)
    data = {
        "hard_core": pool,
        "variants": [list(v) for v in tickets] + [list(extra)],
        "guarantee": 4,
        "pool_size": 15,
    }
    line = next(
        line
        for line in ui_results._lr_scheme_lines("joker", data)
        if line.startswith("Cod 12") and "față de wheel-ul nostru" in line
    )
    pairs = re.findall(r"(\d[\d.]*%) față de (\d[\d.]*%)", line)
    assert pairs
    assert all(a != b for a, b in pairs), line


# --------------------------------------------------------------------------- #
# Worker: o eroare trecătoare a bazei nu mai pierde rezultatul
# --------------------------------------------------------------------------- #


def _job_stubs(monkeypatch):
    monkeypatch.setattr(worker, "update_job_progress", lambda *a, **k: False)
    monkeypatch.setattr(worker, "is_job_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(worker, "_run_pipeline_job", lambda job: "PAYLOAD-90-MIN")
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)


def test_a_transient_db_error_on_completion_is_retried(monkeypatch):
    _job_stubs(monkeypatch)
    calls = []

    def flaky(job_id, result_json, worker_token=None):
        calls.append(result_json)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return True

    monkeypatch.setattr(worker, "complete_job", flaky)
    worker._process_one_job({"id": 7, "task_type": "pipeline"})
    assert calls == ["PAYLOAD-90-MIN"] * 3


def test_a_result_the_db_keeps_refusing_is_saved_on_disk(tmp_path, monkeypatch):
    _job_stubs(monkeypatch)
    monkeypatch.setattr(worker.tempfile, "gettempdir", lambda: str(tmp_path))

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(worker, "complete_job", broken)
    with pytest.raises(sqlite3.OperationalError):
        worker._process_one_job({"id": 8, "task_type": "pipeline"})
    dumps = list(tmp_path.glob("loto_orphan_result_8_*.txt"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == "PAYLOAD-90-MIN"


def test_a_transient_db_error_on_progress_does_not_fail_the_job(queue, monkeypatch, caplog):  # noqa: F811
    real = worker.update_job_progress
    seen = {"raised": 0}

    def flaky_progress(job_id, pct, msg, **kw):
        # Numai progresul motorului (`[fișier][joc] mesaj`) cade, o singură dată.
        if msg.startswith("[") and "][" in msg and not seen["raised"]:
            seen["raised"] += 1
            raise sqlite3.OperationalError("database is locked")
        return real(job_id, pct, msg, **kw)

    monkeypatch.setattr(worker, "update_job_progress", flaky_progress)
    with caplog.at_level(logging.WARNING):
        data, status = queue(_app_config(monkeypatch, rows=200))
    assert seen["raised"] == 1
    assert data is not None and status["status"] == "RUNNING"
    assert "progresul n-a putut fi scris" in caplog.text


# --------------------------------------------------------------------------- #
# Curățarea proceselor vechi la START_8000
# --------------------------------------------------------------------------- #

VENV = r"D:\_BUILD\_LOTO\.venv"
PROJECT = r"G:\My Drive\_CODING\_LOTO"


def test_a_recycled_pid_does_not_adopt_an_older_process_tree():
    stale = cop.ProcView(
        pid=1234, name="python.exe", exe=VENV + r"\Scripts\python.exe",
        cmdline=("python.exe", "worker.py"), parent_pid=1, create_time=2000.0,
    )
    # Editorul a pornit înaintea worker-ului; ppid 1234 era un lansator mort.
    editor = cop.ProcView(pid=5678, name="Code.exe", parent_pid=1234, create_time=1000.0)
    renderer = cop.ProcView(pid=5700, name="Code.exe", parent_pid=5678, create_time=1001.0)
    pool_child = cop.ProcView(
        pid=6000, name="python.exe", exe=VENV + r"\Scripts\python.exe",
        parent_pid=1234, create_time=2001.0,
    )
    procs = [stale, editor, renderer, pool_child]
    picked = cop.select_stale_pids(
        procs, venv_dir=VENV, project_root=PROJECT, keep_pids={0}
    )
    assert picked == {1234, 6000}
    # Părintele mort (fără timp): orfanii lui rămân urmăriți, ca înainte.
    orphan = cop.ProcView(pid=7000, name="python.exe", parent_pid=999, create_time=5.0)
    assert cop.expand_descendants({999}, [orphan]) == {999, 7000}


def test_a_sibling_folder_with_the_same_prefix_is_not_the_project():
    sibling = cop.ProcView(
        pid=4321, name="python.exe", exe=r"C:\Python314\python.exe",
        cmdline=("python.exe", PROJECT + r"_old\worker.py"), cwd=PROJECT + "_old",
    )
    assert not cop.is_stale_proc(
        sibling, venv_dir=VENV, project_root=PROJECT, keep_pids={0}
    )
    ours = cop.ProcView(
        pid=4322, name="python.exe", exe=r"C:\Python314\python.exe",
        cmdline=("python.exe", PROJECT + r"\worker.py"), cwd=r"C:\Windows",
    )
    assert cop.is_stale_proc(ours, venv_dir=VENV, project_root=PROJECT, keep_pids={0})


# --------------------------------------------------------------------------- #
# Mai multe hituri 4+: designuri cu buget pentru biletul de 5 (5/40 și Joker)
# --------------------------------------------------------------------------- #

FIVE = {"5/40": (6, 40), "joker": (5, 45)}


@pytest.mark.parametrize("budget", [340, 370, 403])
@pytest.mark.parametrize("game", sorted(FIVE))
def test_stored_five_number_design_beats_the_engine(game, budget, monkeypatch):
    """La plafon, motorul adoptă secțiunea certificată: +1% la 4+ cel puțin,
    niciun prag 3..5 în scădere, aceleași 16 numere jucate."""
    from covering.anneal_hits import _certify
    from covering.dispatch import generate_wheel
    from covering.probability import wheel_hit_probabilities

    draw_n, max_num = FIVE[game]
    pool = list(range(1, 17))
    scores = {n: float(17 - n) for n in pool}
    assert bd.load_budget_design(16, 5, 4, budget) is not None
    monkeypatch.setattr(bd, "load_budget_design", lambda *a, **k: None)
    monkeypatch.setattr(bd, "_stored", lambda *a, **k: None)
    before, _ = generate_wheel("hitcover", pool, 5, 4, budget, scores, None, draw_n, max_num)
    monkeypatch.undo()
    after, _ = generate_wheel("hitcover", pool, 5, 4, budget, scores, None, draw_n, max_num)
    assert len(after) == budget == len({tuple(t) for t in after})
    assert {n for t in after for n in t} == set(pool)
    p0 = wheel_hit_probabilities(pool, before, draw_n, max_num)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, draw_n, max_num)["ticket"]
    assert p1[4] >= p0[4] * 1.01
    assert all(p1[t] >= p0[t] - 1e-15 for t in (3, 4, 5))
    assert _certify(pool, before, after, 4, draw_n, max_num)[0] == ""


def test_the_lr_scheme_size_keeps_its_scheme():
    # La 348 (5/40 Cod 37) motorul adoptă schema; designul H nu o înlocuiește.
    assert bd.load_budget_design(16, 5, 4, 348) is None
    assert bd.budget_design_signature(16, 5, 4, 348) is None
    assert bd.budget_design_signature(16, 5, 4, 370) is not None


def test_the_five_number_file_follows_the_delivery_rule():
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location(
        "gen5", ROOT / "scripts" / "analysis" / "gen_budget_designs_pick5.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    report = json.loads(
        (ROOT / "scripts" / "analysis" / "budget_designs_pick5_2026-10-22.json").read_text(
            encoding="utf-8"
        )
    )
    lr_sizes = gen.lr_scheme_sizes()
    kept = sorted(r["budget"] for r in report["rows"] if gen.keep_section(r, lr_sizes))
    assert kept == report["sections"]
    assert sorted(bd._designs(16, 5, 4)) == kept
    assert 348 not in kept
