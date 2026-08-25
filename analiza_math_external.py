"""Testare EXTERNĂ: 5 metode matematice CPU nefolosite, per joc.

Walk-forward onest pe ultimele 30% din CSV (aceeași fereastră ca UI WF),
pool=11, rank_by_score. Compară rata 3+ / 4+ cu hipergeometric (random).
Decorelare: |Spearman| < 0.95 vs per_game existent și între candidați.

NU e predicție — loteria e aleatoare. Un candidat intra în curated DOAR dacă
bate P(≥3) SAU P(≥4) hipergeometric (cel puțin +1 eveniment) ȘI nu e clonă.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from loto_enterprise.benchmark.curated import load_curated, load_per_game
from loto_enterprise.benchmark.methods import METHODS, call_method, method_meta
from loto_enterprise.core.method_selector import MAX_MEMBER_CORR, _pair_corr
from loto_enterprise.core.ranking import (
    is_consecutive_block,
    longest_consecutive_run,
    rank_by_score,
)

POOL = 11
WF_PCT = 30
BLOCK = 8  # re-score every 8 test draws (expanding history)
MAX_ADD = 5
MIN_UNIQ_SCORES = 5
# pool 11 cu o rundă de 8+ numere consecutive = aceeași degenerare
# geometrică ca sum_affinity (bloc pe axa 1…N), nu semnal.
MAX_CONSEC_RUN = 7
# 4+ e rar: +1 eveniment vs hipergeometric e zgomot. Cerem +2 dacă
# metoda NU bate și 3+ (ținta bench).
MIN_EXTRA_4PLUS_IF_NO_3 = 2

GAMES = {
    "loto_6_49": {
        "csv": Path("_ISTORIC/loto_6_49.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5", "n6"),
        "max_num": 49,
        "draw_n": 6,
        "candidates": [
            "pca_resid_surprise",
            "cusum_appearance",
            "circular_kernel",
            "649_wilson_lb",
            "649_spectral_cooc",
        ],
    },
    "loto_5_40": {
        "csv": Path("_ISTORIC/loto_5_40.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5"),
        "max_num": 40,
        "draw_n": 5,
        "candidates": [
            "mi_lag_bag",
            "nmf_cooc",
            "bayes_poisson",
            "649_mom_10_40",
            "fourier",
        ],
    },
    "joker_urna1": {
        "csv": Path("_ISTORIC/joker.csv"),
        "cols": ("n1", "n2", "n3", "n4", "n5"),
        "max_num": 45,
        "draw_n": 5,
        "candidates": [
            "pair_affinity",
            "649_beta_mean",
            "649_hazard_overdue",
            "649_sum_reversion",
            "neg_binomial",
        ],
    },
}


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
    game_key, method, csv_s, cols, max_num, draw_n = args
    draws = _load_csv(Path(csv_s), tuple(cols), max_num)
    n = len(draws)
    n_test = max(1, int(round(n * WF_PCT / 100.0)))
    start = n - n_test
    train = draws[:start]
    test = draws[start:]

    n3 = n4 = n_eval = 0
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
        top = set(rank_by_score(scores, POOL))
        for j in range(pos, end):
            actual = set(int(x) for x in test[j])
            h = len(top & actual)
            hit_sum += h
            n_eval += 1
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
        pool_now = rank_by_score(train_scores, POOL)
        consec = longest_consecutive_run(pool_now)
        block = is_consecutive_block(pool_now, min_size=6)

    p3 = hyper_p_ge(3, max_num, draw_n, POOL)
    p4 = hyper_p_ge(4, max_num, draw_n, POOL)
    rate3 = n3 / n_eval
    rate4 = n4 / n_eval
    exp3 = p3 * n_eval
    exp4 = p4 * n_eval
    beat3 = n3 >= math.floor(exp3) + 1
    extra4 = n4 - exp4
    beat4 = n4 >= math.floor(exp4) + 1
    # „bun la 4+" fără 3+ cere un plus mai gros (+2 evenimente), altfel
    # un singur 4 norocos trece poarta.
    beat4_strong = n4 >= math.floor(exp4) + MIN_EXTRA_4PLUS_IF_NO_3
    beats = bool(beat3 or (beat4_strong and extra4 >= MIN_EXTRA_4PLUS_IF_NO_3))
    meta = method_meta(method)
    return {
        "game": game_key,
        "method": method,
        "ok": True,
        "family": meta.get("family", ""),
        "notes": meta.get("notes", ""),
        "n_train": int(len(train)),
        "n_test": int(n_eval),
        "n3": int(n3),
        "n4": int(n4),
        "rate3": rate3,
        "rate4": rate4,
        "avg_hits": hit_sum / n_eval,
        "p3": p3,
        "p4": p4,
        "exp3": exp3,
        "exp4": exp4,
        "beat3": beat3,
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
    active = set(load_curated())
    per_game = load_per_game()
    jobs = []
    for gk, spec in GAMES.items():
        already = set(per_game.get(gk) or []) | active
        for m in spec["candidates"]:
            if m not in METHODS:
                print(f"SKIP {gk}/{m}: nu e în METHODS")
                continue
            if m in already and m in (per_game.get(gk) or []):
                print(f"SKIP {gk}/{m}: deja în per_game")
                continue
            jobs.append((
                gk, m, str(spec["csv"]), spec["cols"],
                spec["max_num"], spec["draw_n"],
            ))

    # scoruri de referință ale metodelor deja din per_game (pe train = primele 70%)
    ref_scores: dict[str, dict[str, dict]] = {gk: {} for gk in GAMES}
    for gk, spec in GAMES.items():
        draws = _load_csv(spec["csv"], spec["cols"], spec["max_num"])
        n_test = max(1, int(round(len(draws) * WF_PCT / 100.0)))
        train = draws[: len(draws) - n_test]
        for m in per_game.get(gk) or []:
            if m not in METHODS:
                continue
            try:
                sc, _ = call_method(m, train, spec["max_num"])
            except Exception as exc:  # noqa: BLE001
                print(f"ref {gk}/{m} fail: {exc}")
                continue
            ref_scores[gk][m] = sc

    n_cpu = max(1, int((os.cpu_count() or 2) * 0.8))
    print(f"jobs={len(jobs)} workers={n_cpu} pool={POOL} wf={WF_PCT}% block={BLOCK}")
    results: list[dict] = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=n_cpu) as ex:
        futs = [ex.submit(_eval_one, job) for job in jobs]
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            if not rec.get("ok"):
                print(f"FAIL {rec.get('game')}/{rec.get('method')}: {rec.get('error')}")
            else:
                flag = "YES" if rec["beats"] else "no "
                print(
                    f"{flag} {rec['game']:13s} {rec['method']:24s} "
                    f"3+ {rec['rate3']*100:5.2f}% (rnd {rec['p3']*100:5.2f}%, "
                    f"{rec['n3']}/{rec['n_test']} vs ~{rec['exp3']:.1f})  "
                    f"4+ {rec['rate4']*100:5.2f}% (rnd {rec['p4']*100:5.2f}%)  "
                    f"{rec['runtime_sec']:.1f}s"
                )
    print(f"eval {time.perf_counter() - t0:.1f}s")

    selected: dict[str, list[dict]] = {gk: [] for gk in GAMES}
    for gk in GAMES:
        rows = [r for r in results if r.get("ok") and r["game"] == gk]
        for r in rows:
            sc = r.get("train_scores") or {}
            worst, vs = _max_abs_spearman(sc, list(ref_scores[gk].items()))
            r["spearman_vs_existing"] = worst
            r["spearman_vs_name"] = vs
            r["clone_existing"] = abs(worst) >= MAX_MEMBER_CORR
            r["degenerate"] = (
                (not r.get("finite"))
                or r.get("nuniq", 0) < MIN_UNIQ_SCORES
                or r.get("consecutive_block")
                or int(r.get("consec_run") or 0) >= MAX_CONSEC_RUN
            )
            r["eligible"] = (
                r["beats"] and not r["clone_existing"] and not r["degenerate"]
            )

        # greedy: sort by 3+ then 4+, skip clones of already picked
        cand = [r for r in rows if r["eligible"]]
        cand.sort(key=lambda x: (x["rate3"], x["rate4"], x["avg_hits"]), reverse=True)
        picked: list[dict] = []
        picked_scores: list[tuple[str, dict]] = []
        for r in cand:
            if len(picked) >= MAX_ADD:
                break
            w, vs = _max_abs_spearman(r.get("train_scores") or {}, picked_scores)
            if abs(w) >= MAX_MEMBER_CORR:
                r["clone_new"] = vs
                r["eligible"] = False
                continue
            r["clone_new"] = ""
            picked.append(r)
            picked_scores.append((r["method"], r.get("train_scores") or {}))
        selected[gk] = picked

    # strip bulky scores from dump
    dump_rows = []
    for r in results:
        d = {k: v for k, v in r.items() if k != "train_scores"}
        dump_rows.append(d)
    out = {
        "pool": POOL,
        "wf_pct": WF_PCT,
        "block": BLOCK,
        "max_member_corr": MAX_MEMBER_CORR,
        "results": dump_rows,
        "selected": {
            gk: [p["method"] for p in picked] for gk, picked in selected.items()
        },
        "selected_detail": {
            gk: [
                {
                    "method": p["method"],
                    "family": p["family"],
                    "rate3": p["rate3"],
                    "rate4": p["rate4"],
                    "n3": p["n3"],
                    "n4": p["n4"],
                    "p3": p["p3"],
                    "p4": p["p4"],
                    "spearman_vs_existing": p.get("spearman_vs_existing"),
                    "spearman_vs_name": p.get("spearman_vs_name"),
                }
                for p in picked
            ]
            for gk, picked in selected.items()
        },
    }
    Path("/tmp/analiza_math_external.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print("wrote /tmp/analiza_math_external.json")
    print("\n=== SELECTED (beat 3+ or 4+, |Spearman|<0.95, not degenerate) ===")
    for gk, picked in selected.items():
        print(f"  {gk}: {len(picked)}/{MAX_ADD} → {[p['method'] for p in picked]}")
        for p in picked:
            print(
                f"      {p['method']}  3+={p['rate3']*100:.2f}%  4+={p['rate4']*100:.2f}%  "
                f"|r|={abs(p.get('spearman_vs_existing') or 0):.3f} vs {p.get('spearman_vs_name')}"
            )


if __name__ == "__main__":
    main()
