"""Walk-forward for revived CPU + cover_* scorers. Same gates as analiza_math_external."""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analiza_math_external import (  # noqa: E402
    BLOCK,
    GAMES,
    MAX_CONSEC_RUN,
    MAX_MEMBER_CORR,
    MIN_UNIQ_SCORES,
    MIN_UNIQ_SCORES_SINGLE_PICK,
    TOP_N,
    WF_PCT,
    _eval_one,
    _max_abs_spearman,
)
from loto_enterprise.benchmark.methods import METHODS  # noqa: E402
from loto_enterprise.benchmark.methods_coverage import COVERAGE_METHODS  # noqa: E402
from loto_enterprise.benchmark.methods_revived import REVIVED_METHODS  # noqa: E402
from loto_enterprise.core.method_selector import _pair_corr  # noqa: E402

SLOW_PREFIX = ("ml_",)
SLOW_EXACT = frozenset(
    {
        "arima_auto",
        "arima_sm",
        "ets_auto",
        "ces_auto",
        "theta_auto",
        "adida",
        "imapa",
        "tsb",
        "croston_opt",
        "holt_winters",
        "stl",
        "hmm_gaussian",
        "ml_gaussian_process",
    }
)

OUT = ROOT / "bench_results" / "revived_cover_external.json"


def candidates() -> list[str]:
    names = set(REVIVED_METHODS) | set(COVERAGE_METHODS)
    out = []
    for n in sorted(names):
        if n in SLOW_EXACT or n.startswith(SLOW_PREFIX):
            continue
        if n not in METHODS:
            continue
        out.append(n)
    return out


def main() -> None:
    cand = candidates()
    jobs = []
    for gk, spec in GAMES.items():
        for m in cand:
            jobs.append(
                (
                    gk,
                    m,
                    str(ROOT / spec["csv"]),
                    spec["cols"],
                    spec["max_num"],
                    spec["draw_n"],
                    spec["pool"],
                )
            )
    n_cpu = max(1, min(8, int((os.cpu_count() or 2) * 0.8)))
    print(f"candidates={cand}", flush=True)
    print(f"jobs={len(jobs)} workers={n_cpu} wf={WF_PCT}% block={BLOCK}", flush=True)
    results: list[dict] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=n_cpu) as ex:
        futs = [ex.submit(_eval_one, job) for job in jobs]
        done = 0
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            done += 1
            if not rec.get("ok"):
                print(
                    f"[{done}/{len(jobs)}] FAIL {rec.get('game')}/{rec.get('method')}: {rec.get('error')}",
                    flush=True,
                )
                continue
            target = int(rec["hit_target"])
            flag = "YES" if rec["beats"] else "no "
            print(
                f"[{done}/{len(jobs)}] {flag} {rec['game']:13s} {rec['method']:28s} "
                f"{target}+ {rec[f'rate{target}'] * 100:5.2f}% "
                f"(rnd {rec[f'p{target}'] * 100:5.2f}%)  "
                f"4+ {rec['rate4'] * 100:5.2f}%  {rec['runtime_sec']:.1f}s",
                flush=True,
            )
    print(f"eval {time.perf_counter() - t0:.1f}s", flush=True)

    selected: dict[str, list[str]] = {}
    eligible: dict[str, list[dict]] = {}
    for gk in GAMES:
        rows = [r for r in results if r.get("ok") and r["game"] == gk]
        for r in rows:
            min_uniq = (
                MIN_UNIQ_SCORES_SINGLE_PICK
                if int(r.get("hit_target") or 3) == 1
                else MIN_UNIQ_SCORES
            )
            r["degenerate"] = (
                (not r.get("finite"))
                or r.get("nuniq", 0) < min_uniq
                or r.get("consecutive_block")
                or int(r.get("consec_run") or 0) >= MAX_CONSEC_RUN
            )
            r["eligible"] = bool(r["beat_target"] and not r["degenerate"])
        cand_rows = [r for r in rows if r["eligible"]]
        cand_rows.sort(
            key=lambda x: (
                -x[f"rate{int(x['hit_target'])}"],
                -x["rate4"],
                -x["avg_hits"],
                x["method"],
            )
        )
        picked: list[dict] = []
        picked_scores: list[tuple[str, dict]] = []
        for r in cand_rows:
            if len(picked) >= TOP_N:
                break
            w, vs = _max_abs_spearman(r.get("train_scores") or {}, picked_scores)
            r["spearman_vs_picked"] = w
            r["spearman_vs_name"] = vs
            if abs(w) >= MAX_MEMBER_CORR:
                r["clone_new"] = vs
                continue
            r["clone_new"] = ""
            picked.append(r)
            picked_scores.append((r["method"], r.get("train_scores") or {}))
        selected[gk] = [p["method"] for p in picked]
        eligible[gk] = [
            {
                "method": p["method"],
                "rate_target": p[f"rate{int(p['hit_target'])}"],
                "rate3": p["rate3"],
                "rate4": p["rate4"],
                "p3": p["p3"],
                "p1": p["p1"],
                "wilson_lb3": p["wilson_lb3"],
                "wilson_lb1": p["wilson_lb1"],
                "nuniq": p["nuniq"],
                "consec_run": p["consec_run"],
                "spearman_vs_picked": p.get("spearman_vs_picked"),
            }
            for p in picked
        ]
        print(f"{gk} eligible unique: {selected[gk]}", flush=True)

    dump = []
    for r in results:
        dump.append({k: v for k, v in r.items() if k != "train_scores"})
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "wf_pct": WF_PCT,
                "block": BLOCK,
                "candidates": cand,
                "selected": selected,
                "eligible_detail": eligible,
                "results": dump,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
