"""Audit read-only al outputului curent: decizie, randare, bilete, cache WF.

Rulează din rădăcina repo cu Python-ul aplicației. Nu generează joburi, nu
rescrie istoricul și nu trimite email. --report permite salvarea unei copii
a raportului randat cu codul curent, într-o cale explicită.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd

import app_nicegui as app_ui
from job_queue import DB_PATH
from loto_enterprise.benchmark import decision
from loto_enterprise.core.py314_io import pickle_load_path
from loto_enterprise.core import walk_forward_adapter as wf
from ui_shared import atomic_write_text, decode_queue_result
from wheeling_methods import compute_coverage_pct


class CaptureUI:
    """Capturează arborele randat de aceleași funcții care servesc NiceGUI."""
    def __init__(self):
        self.nodes = []
        self.stack = []

    def __getattr__(self, kind):
        def make(*args, **kwargs):
            node = {"kind": kind, "args": args, "kwargs": kwargs, "children": []}
            (self.stack[-1]["children"] if self.stack else self.nodes).append(node)
            owner = self

            class Element:
                def __enter__(self):
                    owner.stack.append(node)
                    return self

                def __exit__(self, *exc):
                    owner.stack.pop()

                def __getattr__(self, name):
                    return lambda *a, **kw: self

            return Element()
        return make

    def walk(self):
        def visit(nodes):
            for node in nodes:
                yield node
                yield from visit(node["children"])
        return visit(self.nodes)

    def text(self):
        return "\n".join(str(n["args"][0]) for n in self.walk() if n["args"])

    def ranking(self):
        result = []
        for node in self.walk():
            if node["kind"] != "row":
                continue
            labels = [n["args"][0] for n in node["children"]
                      if n["kind"] == "label" and n["args"]]
            if len(labels) >= 2 and (labels[0] == "🏆" or str(labels[0]).endswith(".")):
                result.append(str(labels[1]).removeprefix("🎯 "))
        return result


@contextmanager
def capture_ui():
    collector = CaptureUI()
    with patch.object(app_ui, "ui", collector):
        yield collector


def audit_rankings(df):
    tested = 0
    for target in (3, 4):
        with patch.object(decision, "BENCH_HIT_TARGET", target):
            for game, draw_n in app_ui._BENCH_DRAW_N.items():
                if game not in set(df["game"]):
                    continue
                pools = [1] if draw_n == 1 else range(draw_n, draw_n + 15)
                for pool in pools:
                    cfg = decision.decide_optimal_config_for_pool(df, game, pool, draw_n)
                    if "error" in cfg:
                        continue
                    app_ui._LB_ROWS_MEMO.clear()
                    with capture_ui() as ui:
                        app_ui._render_bench_leaderboard_slice(df, game, pool, game, top_n=20)
                    ranked = ui.ranking()
                    expected = cfg["ranked_methods"]
                    assert ranked[:len(expected)] == expected[:len(ranked)], (game, pool, target, ranked, expected)
                    for excluded in cfg.get("tiebreak_dependent", []):
                        assert excluded["method"] not in ranked, (game, pool, excluded)
                    if draw_n == 1:
                        assert "brut 4+" not in ui.text()
                    tested += 1
    return tested


def latest_bundle():
    # mode=ro evită inclusiv scrierile de inițializare ale helperului de queue.
    with sqlite3.connect(Path(DB_PATH).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id, completed_at, result_json FROM jobs WHERE status='COMPLETED' "
            "ORDER BY completed_at DESC,id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("Nu există job complet de verificat")
    return row, decode_queue_result(row["result_json"])


def audit_bundle(bundle):
    summary = []
    for fname, outs in bundle[0]:
        source = pd.read_csv(ROOT / "_ISTORIC" / fname)
        for game, raw in outs.items():
            data = app_ui._primary_pool_data(raw)
            pool = data["hard_core"]
            variants = data["variants"]
            audit = data.get("audit") or {}
            guarantee = int(audit.get("wheel_guarantee_used") or data["guarantee"])
            condition = int(audit.get("wheel_condition_used") or guarantee)
            wheel = [list(v[:5] if game == "joker" else v) for v in variants]
            assert len(pool) == len(set(pool)) == int(data["pool_size"])
            assert len(variants) == len({tuple(v) for v in variants})
            assert all(len(v) == len(set(v)) and set(v) <= set(pool) for v in wheel)
            cov = compute_coverage_pct(wheel, pool, guarantee, condition)
            assert abs(cov - float(data["context"]["coverage_pct"])) < 1e-8
            rp = audit.get("recent_penalty") or {}
            sig = wf._decision_sig(game, len(pool), audit.get("lookback_pct") or 100,
                                   rp.get("draws") or 0, rp.get("factor", 0.5))
            cache = wf._cache_path(game, wf._csv_hash(source, game), len(pool), 30, sig)
            item = {"game": game, "pool": len(pool), "variants": len(variants), "coverage": cov}
            if cache.exists():
                cached = pickle_load_path(cache)
                flat = cached["flat"]
                per = wf.per_draw_hit_summary(flat)
                assert all(0 <= r["best_ticket"] <= r["pool"] <= (6 if game == "6/49" else 5)
                           for r in per.values())
                assert len(per) == cached["n_test_draws"]
                # Recalculăm fiecare hit de bilet direct din extragerea CSV;
                # la cover complet, reuniunea numerelor de pe bilete e pool-ul.
                draw_n = 6 if game == "6/49" else 5
                actual = {i: set(int(row[f"n{j}"]) for j in range(1, draw_n + 1))
                          for i, row in source.iterrows()}
                unions = {}
                for p in flat:
                    nums = set(p.variant[:draw_n])
                    assert p.hits == len(nums & actual[p.draw_index]), (game, p.draw_index, p.hits)
                    unions.setdefault(p.draw_index, set()).update(nums)
                if all(p.wheel_coverage == 100.0 for p in flat):
                    assert all(len(nums & actual[i]) == per[i]["pool"] for i, nums in unions.items())
                app_ui.STATE["retro"][f"{fname}_{game}"] = flat
                app_ui.STATE["retro_meta"][f"{fname}_{game}"] = cached
                with capture_ui() as ui:
                    app_ui._render_hits_4plus(flat, game, cached, len(pool))
                tables = [n for n in ui.walk() if n["kind"] == "table"]
                assert len(tables[0]["kwargs"]["rows"]) == 2
                item["wf"] = app_ui._wf_summary(flat)
            else:
                item["wf"] = "Nu există cache pentru configurația și istoricul curente"
            with capture_ui() as ui:
                app_ui._render_pool_body(fname, game, data)
            assert "cover minim" not in ui.text()
            summary.append(item)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    import logging
    logging.getLogger().setLevel(logging.WARNING)
    df = pd.read_csv(ROOT / "bench_results" / "folds.csv")
    n = audit_rankings(df)
    row, bundle = latest_bundle()
    app_ui.STATE["results"] = bundle
    summary = audit_bundle(bundle)
    report = app_ui._build_report()
    assert "timesfm_predictions" not in report
    assert "pure_bench_mode" not in report
    if args.report:
        atomic_write_text(args.report, report)
    print(json.dumps({"rankings_verified": n, "job": row["id"],
                      "completed_at": row["completed_at"], "games": summary},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
