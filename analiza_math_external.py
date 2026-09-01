"""Testare EXTERNĂ: până la 100 metode matematice CPU per joc/urnă.

Walk-forward onest pe ultimele 30% din CSV (aceeași fereastră ca UI WF),
pool=11 pentru urnele principale și top-1 pentru Joker Urna 2, rank_by_score.
Compară rata țintă cu baseline-ul hipergeometric (random). Top 20 per joc:
numai metode cu rata țintă strict peste baseline, apoi selecție greedy după
rată, |Spearman| < 0.95 între aleși, fără degenerare (bloc consecutiv /
<5 nivele de scor). Dacă nu există 20 metode distincte care trec pragul,
raportează numărul real; nu completează lista cu metode slabe sau clone.

Setul e format din toate metodele matematice numpy/CPU din METHODS, plus
`ml_knn_5`, singura metodă ML care a trecut în pre-screening poarta oficială
Urna 2 pe toate cele 111 metode. Exclude croston (statsforecast) și `random`
(nu e candidat de producție).

NU e predicție — loteria e aleatoare.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Înainte de numpy: altfel OpenBLAS rămâne multi-thread și fork-ul se agață.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

from loto_enterprise.benchmark.curated import load_per_game
from loto_enterprise.benchmark.methods import METHODS, call_method, method_meta
from loto_enterprise.core.method_selector import MAX_MEMBER_CORR, _pair_corr
from loto_enterprise.core.ranking import (
    is_consecutive_block,
    longest_consecutive_run,
    rank_by_score,
)

WF_PCT = 30
BLOCK = 8  # re-score every 8 test draws (expanding history)
TOP_N = 20
MAX_TEST_PER_GAME = 100
MIN_UNIQ_SCORES = 5
MIN_UNIQ_SCORES_SINGLE_PICK = 2
MAX_CONSEC_RUN = 7
MIN_EXTRA_4PLUS_IF_NO_3 = 2
OUTPUT_PATH = Path("bench_results") / "math_external_latest.json"

SKIP_EXACT = frozenset({"random", "croston_classic", "croston_sba"})

GAMES = {
    "loto_6_49": {
        "csv": Path("_ISTORIC/loto_6_49.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5", "n6"),
        "max_num": 49,
        "draw_n": 6,
        "pool": 11,
    },
    "loto_5_40": {
        "csv": Path("_ISTORIC/loto_5_40.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5"),
        "max_num": 40,
        "draw_n": 5,
        "pool": 11,
    },
    "joker_urna1": {
        "csv": Path("_ISTORIC/joker.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5"),
        "max_num": 45,
        "draw_n": 5,
        "pool": 11,
    },
    "joker_urna2": {
        "csv": Path("_ISTORIC/joker.csv"),
        "cols": ("joker",),
        "max_num": 20,
        "draw_n": 1,
        "pool": 1,
    },
}


def _cpu_math_candidates() -> list[str]:
    """Metode matematice CPU + KNN validat, max MAX_TEST_PER_GAME."""
    out: list[str] = []
    for name in METHODS:
        if name in SKIP_EXACT or (name.startswith("ml_") and name != "ml_knn_5"):
            continue
        meta = method_meta(name)
        if not meta.get("available", True):
            continue
        out.append(name)
    # Preferă metodele care NU sunt deja în vreun per_game, apoi restul
    # (ca să umplem cota de 100 cu semnal nou dacă registry-ul crește).
    pg_all: set[str] = set()
    for lst in load_per_game().values():
        pg_all.update(lst)
    out.sort(key=lambda n: (n in pg_all, n))
    return out[:MAX_TEST_PER_GAME]


def _load_csv(path: Path, cols: tuple[str, ...], max_num: int) -> np.ndarray:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            row = [int(rec[c]) for c in cols]
            if all(1 <= v <= max_num for v in row):
                rows.append(row)
    if not rows:
        raise RuntimeError(f"{path} gol / necitibil")
    return np.asarray(rows, dtype=np.int64)


def hyper_p_ge(k: int, universe: int, draw_n: int, pool: int) -> float:
    total = math.comb(universe, draw_n)
    if total == 0:
        return 0.0
    acc = 0
    for j in range(k, draw_n + 1):
        if j > pool or (draw_n - j) > (universe - pool):
            continue
        acc += math.comb(pool, j) * math.comb(universe - pool, draw_n - j)
    return acc / total


def _eval_one(args: tuple) -> dict:
    """Worker: walk-forward + scor pe train + degenerare. Picklabil."""
    game_key, method, csv_s, cols, max_num, draw_n, pool_size = args
    draws = _load_csv(Path(csv_s), tuple(cols), max_num)
    n = len(draws)
    n_test = max(1, int(round(n * WF_PCT / 100.0)))
    start = n - n_test
    train = draws[:start]
    test = draws[start:]

    n1 = n3 = n4 = n_eval = 0
    hit_sum = 0
    t0 = time.perf_counter()
    pos = 0
    n_test_len = len(test)
    empty = 0
    blocks = 0
    while pos < n_test_len:
        end = min(pos + BLOCK, n_test_len)
        history = draws[: start + pos]
        try:
            scores, _dt = call_method(method, history, max_num)
        except Exception as exc:  # noqa: BLE001
            return {
                "game": game_key, "method": method, "ok": False,
                "error": f"call_method: {exc}",
            }
        blocks += 1
        if not scores:
            empty += 1
            pos = end
            continue
        top = set(rank_by_score(scores, pool_size))
        for j in range(pos, end):
            actual = set(int(x) for x in test[j])
            h = len(top & actual)
            hit_sum += h
            n_eval += 1
            if h >= 1:
                n1 += 1
            if h >= 3:
                n3 += 1
            if h >= 4:
                n4 += 1
        pos = end
    runtime = time.perf_counter() - t0
    if n_eval == 0:
        return {
            "game": game_key, "method": method, "ok": False,
            "error": f"empty scores on all {blocks} blocks",
        }

    try:
        train_scores, _ = call_method(method, train, max_num)
    except Exception as exc:  # noqa: BLE001
        train_scores = {}
        train_err = str(exc)
    else:
        train_err = ""

    uniq = 0
    consec = 0
    block = False
    finite = False
    pool_now: list[int] = []
    if train_scores:
        vals = [float(v) for v in train_scores.values()]
        finite = all(math.isfinite(v) for v in vals)
        uniq = len({round(v, 8) for v in vals})
        pool_now = rank_by_score(train_scores, pool_size)
        consec = longest_consecutive_run(pool_now)
        block = is_consecutive_block(pool_now, min_size=6)

    p1 = hyper_p_ge(1, max_num, draw_n, pool_size)
    p3 = hyper_p_ge(3, max_num, draw_n, pool_size)
    p4 = hyper_p_ge(4, max_num, draw_n, pool_size)
    rate1 = n1 / n_eval
    rate3 = n3 / n_eval
    rate4 = n4 / n_eval
    exp1 = p1 * n_eval
    exp3 = p3 * n_eval
    exp4 = p4 * n_eval
    beat1 = n1 >= math.floor(exp1) + 1
    beat3 = n3 >= math.floor(exp3) + 1
    extra4 = n4 - exp4
    beat4 = n4 >= math.floor(exp4) + 1
    # „bun la 4+" fără 3+ cere un plus mai gros (+2 evenimente), altfel
    # un singur 4 norocos trece poarta.
    beat4_strong = n4 >= math.floor(exp4) + MIN_EXTRA_4PLUS_IF_NO_3
    hit_target = 1 if draw_n == 1 else 3
    beat_target = beat1 if hit_target == 1 else beat3
    beats = bool(beat_target)
    meta = method_meta(method)
    return {
        "game": game_key,
        "method": method,
        "ok": True,
        "family": meta.get("family", ""),
        "notes": meta.get("notes", ""),
        "n_train": int(len(train)),
        "n_test": int(n_eval),
        "pool": int(pool_size),
        "hit_target": hit_target,
        "n1": int(n1),
        "n3": int(n3),
        "n4": int(n4),
        "rate1": rate1,
        "rate3": rate3,
        "rate4": rate4,
        "avg_hits": hit_sum / n_eval,
        "p1": p1,
        "p3": p3,
        "p4": p4,
        "exp1": exp1,
        "exp3": exp3,
        "exp4": exp4,
        "beat1": beat1,
        "beat3": beat3,
        "beat_target": beat_target,
        "beat4": beat4,
        "beat4_strong": beat4_strong,
        "beats": beats,
        "runtime_sec": runtime,
        "empty_blocks": empty,
        "blocks": blocks,
        "finite": finite,
        "nuniq": uniq,
        "consec_run": consec,
        "consecutive_block": block,
        "train_err": train_err,
        "train_scores": {int(k): float(v) for k, v in (train_scores or {}).items()},
        "pool_sample": [int(x) for x in pool_now],
    }


def _max_abs_spearman(cand_scores: dict, others: list[tuple[str, dict]]) -> tuple[float, str]:
    worst = 0.0
    vs = ""
    for name, sc in others:
        if not sc:
            continue
        r = _pair_corr(cand_scores, sc)
        if r is None:
            continue
        if abs(r) >= abs(worst):
            worst = float(r)
            vs = name
    return worst, vs


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True, errors="replace") if hasattr(sys.stdout, "reconfigure") else None
    candidates = _cpu_math_candidates()
    print(f"cpu_math_candidates={len(candidates)} cap={MAX_TEST_PER_GAME}")
    jobs = []
    for gk, spec in GAMES.items():
        for m in candidates:
            jobs.append((
                gk, m, str(spec["csv"]), spec["cols"],
                spec["max_num"], spec["draw_n"], spec["pool"],
            ))

    n_cpu = max(1, int((os.cpu_count() or 2) * 0.8))
    pools = ",".join(f"{g}={s['pool']}" for g, s in GAMES.items())
    print(f"jobs={len(jobs)} workers={n_cpu} pools={pools} wf={WF_PCT}% block={BLOCK} top={TOP_N}")
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
                print(f"[{done}/{len(jobs)}] FAIL {rec.get('game')}/{rec.get('method')}: {rec.get('error')}", flush=True)
            else:
                flag = "YES" if rec["beats"] else "no "
                target = int(rec["hit_target"])
                print(
                    f"[{done}/{len(jobs)}] {flag} {rec['game']:13s} {rec['method']:28s} "
                    f"{target}+ {rec[f'rate{target}']*100:5.2f}% "
                    f"(rnd {rec[f'p{target}']*100:5.2f}%)  "
                    f"4+ {rec['rate4']*100:5.2f}%  {rec['runtime_sec']:.1f}s",
                    flush=True,
                )
    print(f"eval {time.perf_counter() - t0:.1f}s", flush=True)

    selected: dict[str, list[dict]] = {gk: [] for gk in GAMES}
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
            # Cerința de producție este 3+ peste random. Un 4+ norocos nu mai
            # poate compensa o rată 3+ sub baseline în lista per_game.
            r["eligible"] = bool(r["beat_target"] and not r["degenerate"])

        cand = [r for r in rows if r["eligible"]]
        # Departajare complet deterministă: execuția paralelă nu trebuie să
        # decidă care dintre metodele cu rate identice intră prima în greedy.
        cand.sort(
            key=lambda x: (
                -x[f"rate{int(x['hit_target'])}"],
                -x["rate4"],
                -x["avg_hits"],
                x["method"],
            )
        )
        picked: list[dict] = []
        picked_scores: list[tuple[str, dict]] = []
        for r in cand:
            if len(picked) >= TOP_N:
                r["clone_new"] = ""
                r["over_top_n"] = True
                continue
            w, vs = _max_abs_spearman(r.get("train_scores") or {}, picked_scores)
            r["spearman_vs_picked"] = w
            r["spearman_vs_name"] = vs
            if abs(w) >= MAX_MEMBER_CORR:
                r["clone_new"] = vs
                r["eligible"] = False
                continue
            r["clone_new"] = ""
            picked.append(r)
            picked_scores.append((r["method"], r.get("train_scores") or {}))
        selected[gk] = picked

    dump_rows = []
    for r in results:
        d = {k: v for k, v in r.items() if k != "train_scores"}
        dump_rows.append(d)
    out = {
        "pool_by_game": {gk: int(spec["pool"]) for gk, spec in GAMES.items()},
        "wf_pct": WF_PCT,
        "block": BLOCK,
        "top_n": TOP_N,
        "max_member_corr": MAX_MEMBER_CORR,
        "n_candidates": len(candidates),
        "candidates": candidates,
        "results": dump_rows,
        "selected": {
            gk: [p["method"] for p in picked] for gk, picked in selected.items()
        },
        "selected_detail": {
            gk: [
                {
                    "method": p["method"],
                    "family": p["family"],
                    "hit_target": p["hit_target"],
                    "rate1": p["rate1"],
                    "rate3": p["rate3"],
                    "rate4": p["rate4"],
                    "n3": p["n3"],
                    "n4": p["n4"],
                    "p1": p["p1"],
                    "p3": p["p3"],
                    "p4": p["p4"],
                    "spearman_vs_picked": p.get("spearman_vs_picked"),
                    "spearman_vs_name": p.get("spearman_vs_name"),
                }
                for p in picked
            ]
            for gk, picked in selected.items()
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"wrote {OUTPUT_PATH}", flush=True)
    print(f"\n=== TOP {TOP_N} (beat target random, |Spearman|<{MAX_MEMBER_CORR}, not degenerate) ===", flush=True)
    for gk, picked in selected.items():
        print(f"  {gk}: {len(picked)}/{TOP_N} -> {[p['method'] for p in picked]}", flush=True)
        for p in picked:
            target = int(p["hit_target"])
            print(
                f"      {p['method']:28s}  {target}+={p[f'rate{target}']*100:.2f}%  "
                f"4+={p['rate4']*100:.2f}%  "
                f"|r|={abs(p.get('spearman_vs_picked') or 0):.3f} vs {p.get('spearman_vs_name') or '-'}",
                flush=True,
            )


if __name__ == "__main__":
    main()
