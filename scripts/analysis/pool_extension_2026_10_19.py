"""Frozen retrospective extension: Romanian 5/40 pools9..15, Joker1 pools9..19.

Register before counting outcomes. Research only; no production configuration,
method registry or application state is changed. Cache/logs belong in TEMP.
"""

from __future__ import annotations

import os

for _key in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_key] = "1"

import argparse
import csv
import hashlib
import io
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import scipy
from scipy.stats import rankdata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from pool_extension_cache_2026_10_19 import load_data, load_scores
from pool_extension_candidates_2026_10_19 import candidate_specs, generate
from pool_extension_eval_2026_10_19 import evaluate_bank
from pool_extension_statistics_2026_10_19 import (
    holm_adjusted,
    hypergeom_tails,
    metrics,
    paired_test,
    replay_selector,
)

BASELINE = "markov_lag2"
EXPERTS = ("frequency", BASELINE, "ewma_hl30")
SEED = 2026101904
STEM = "pool_extension_2026-10-19"
LOCAL = Path(tempfile.gettempdir()) / "loto-pool-extension-20261019"
CACHE = LOCAL / "cache"
MANIFEST = HERE / f"{STEM}_protocol.json"
REPORT = HERE / f"{STEM}.json"
GAMES = {
    "loto_5_40": {
        "label": "Loto 5/40",
        "path": "_ISTORIC/loto_5_40.csv",
        "max_num": 40,
        "draw_n": 6,
        "play_n": 5,
        "ks": list(range(9, 16)),
    },
    "joker_urna1": {
        "label": "Joker Urna 1",
        "path": "_ISTORIC/joker.csv",
        "max_num": 45,
        "draw_n": 5,
        "play_n": 5,
        "ks": list(range(9, 20)),
    },
}
PROTECTED = (
    "best_methods.json",
    ".ui_state.json",
    "pool_history.json",
    "raport_complet.txt",
    "bench_results/folds.csv",
    "bench_results/report.json",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_bytes(
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )


def protected():
    return {p: sha(ROOT / p) if (ROOT / p).is_file() else None for p in PROTECTED}


def current_methods():
    from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
    from loto_enterprise.benchmark.methods import METHODS

    saved = json.loads((ROOT / "best_methods.json").read_bytes())["games"]
    result = {}
    for game, meta in GAMES.items():
        cells = saved[game]["auto_pilot_per_pool"]
        result[game] = {}
        for k in meta["ks"]:
            name = cells[f"k{k}"]["scorer"]
            if name not in METHODS or name in EXCLUDED_FROM_PRODUCTION:
                raise ValueError(
                    f"invalid current diagnostic method: {game}/k{k}/{name}"
                )
            result[game][f"k{k}"] = name
    return result


def specs(meta):
    controls = [
        {"name": BASELINE, "family": "reference", "complexity": 1},
        {"name": "frequency", "family": "control", "complexity": 1},
    ]
    pure = candidate_specs(meta["max_num"], meta["draw_n"])
    blends = [
        {
            "name": "mix_markov50__" + d["name"],
            "family": "markov_new_method_rank_blend",
            "complexity": d["complexity"] + 1,
            "parent_names": [BASELINE, d["name"]],
            "params": {
                "markov_weight": 0.5,
                "ties": "mean rank",
                "flat_fallback": "frequency",
            },
        }
        for d in pure
    ]
    return controls + pure + blends


def source_hashes():
    paths = list((ROOT / "loto_enterprise").rglob("*.py"))
    paths += [
        HERE / p
        for p in (
            "pool_extension_2026_10_19.py",
            "pool_extension_cache_2026_10_19.py",
            "pool_extension_candidates_2026_10_19.py",
            "pool_extension_eval_2026_10_19.py",
            "pool_extension_statistics_2026_10_19.py",
            "validate_pool_extension_2026_10_19.py",
            "validate_pool_screen_2026_10_19.py",
            "validate_pool_combinations_2026_10_19.py",
        )
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in sorted(paths)
    }


def protocol(current_baselines=None):
    return {
        "version": 1,
        "baseline": BASELINE,
        "seed": SEED,
        "games": GAMES,
        "candidate_specs": {g: specs(m) for g, m in GAMES.items()},
        "candidate_count_per_game": 32,
        "current_baselines": current_methods()
        if current_baselines is None
        else current_baselines,
        "current_baseline_scope": "DIAGNOSTIC ONLY: current per-pool method IDs selected on full reused history, evaluated with the same raw/consecutive2 pool construction and without recent penalties or base restriction; not a replay of historical application settings or production selection, not independent validation; no role in new selector",
        "selection": {
            "origins": [0.7, 0.8, 0.9],
            "lookback": 0.2,
            "score_start": "max(256, rows before floor(50% of all unique dates))",
            "boundaries": "exact fractions of unique dates in full CSV",
            "criterion": "mean4+ over requested pools; choose on past20%, hold fixed next10%",
            "gates": "no own fallback or consumed-parent invalidity in past window; raw boundary tie fraction<.5 at every k; explicit Markov reserve",
            "tie_break": "Markov first, then complexity, then ID",
        },
        "primary": "selector on80..100% vs Markov, consecutive limit2; two game hypotheses, Holm2",
        "diagnostic": "70..80%; current methods selected on reused full history",
        "secondary": "all32 fixed algorithm definitions per game on final20%, Holm32 within game and Holm64 across both games; raw uses SAME selector choices;3+,5+,mean hits,zero",
        "pool_selection": "rank_by_score canonical; apply_consecutive_limit(ranked,k,2) independently for EACH k; no prefix/nesting assumption",
        "geometry": "5/40 counts ALL SIX drawn numbers n1..n6, physical ticket has5; Joker Urna1 counts only n1..n5 from45, Urna2 not part of pool study",
        "inference": {
            "threshold": 4,
            "permutations": 99999,
            "bootstrap_samples": 2000,
            "unit": "whole day, common sign across all overlapping pools and all draws of that day",
            "assumption": "paired method-label exchangeability conditional on observed trajectories; does not calibrate whole adaptive search or serial dependence",
            "promotion": "none automatic, including a nominal/corrected retrospective signal",
        },
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "limitations": "Previously explored histories, no virgin holdout or prospective evidence. Existing6/49 studies remain frozen. Only these two Romanian games are evaluated; no additional countries or Joker Urna2 study.",
        "data_sha256": {g: sha(ROOT / m["path"]) for g, m in GAMES.items()},
        "source_sha256": source_hashes(),
        "environment": {
            "python": list(sys.version_info[:3]),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "sources": [
            "https://arxiv.org/abs/0710.3742",
            "https://ojs.aaai.org/index.php/AAAI/article/download/9602/9461",
        ],
        "source_scope": "BOCPD inspiration and calibration context; custom bilinear/interaction hypotheses, not a lottery-prediction guarantee or exact BBQ implementation",
    }


def verify_protocol():
    frozen = json.loads(MANIFEST.read_bytes())
    if {k: v for k, v in frozen.items() if k != "registered_at_utc"} != protocol(
        frozen["current_baselines"]
    ):
        raise RuntimeError("Frozen source/data/environment or diagnostic IDs changed")
    return frozen


def register():
    if MANIFEST.exists():
        verify_protocol()
        print("Protocol unchanged", flush=True)
    else:
        value = protocol()
        value["registered_at_utc"] = datetime.now(UTC).isoformat()
        write_json(MANIFEST, value)
        print("Extension protocol registered before outcome evaluation", flush=True)


def blend_scores(left, right, frequency):
    left, right, frequency = [
        np.asarray(a, dtype=np.float64) for a in (left, right, frequency)
    ]
    if (
        left.ndim != 2
        or left.shape[1] < 2
        or right.shape != left.shape
        or frequency.shape != left.shape
    ):
        raise ValueError("aligned [T,N] score matrices required")
    if not all(np.isfinite(a).all() for a in (left, right, frequency)):
        raise ValueError("nonfinite blend inputs")
    score = (
        rankdata(left, axis=1, method="average")
        + rankdata(right, axis=1, method="average")
        - 2
    ) / (2 * (left.shape[1] - 1))
    flat = np.ptp(score, axis=1) <= 1e-12
    score[flat] = frequency[flat]
    return score, flat


def all_scores(data, results, meta):
    values = {n: results[n][1] for n in EXPERTS}
    out = {n: (values[n], results[n][2].copy()) for n in (BASELINE, "frequency")}
    desc = {s["name"]: s for s in candidate_specs(meta["max_num"], meta["draw_n"])}
    seen = set()
    for name, _family, score, fallback in generate(
        data, values, max_num=meta["max_num"], draw_n=meta["draw_n"]
    ):
        if name not in desc or name in seen:
            raise RuntimeError("unknown/duplicate candidate")
        seen.add(name)
        invalid = np.asarray(fallback, dtype=bool).copy()
        for parent in desc[name]["parent_names"]:
            invalid |= results[parent][2]
        out[name] = (score, invalid)
        mixed, flat = blend_scores(values[BASELINE], score, values["frequency"])
        out["mix_markov50__" + name] = (mixed, invalid | results[BASELINE][2] | flat)
    if set(out) != {s["name"] for s in specs(meta)}:
        raise RuntimeError("incomplete bank")
    return out


def save_bank(path, **arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def paired(candidate, baseline, dates, meta, *, bootstrap=2000):
    return paired_test(
        candidate,
        baseline,
        dates,
        ks=meta["ks"],
        draw_n=meta["draw_n"],
        threshold=4,
        permutations=99999,
        seed=SEED,
        bootstrap_samples=bootstrap,
    )


def describe(hits, meta):
    return metrics(hits, ks=meta["ks"], draw_n=meta["draw_n"], threshold=4)


def compact_blocks(blocks):
    out = []
    for block in blocks:
        gates = block["gates"]
        out.append(
            {
                **{k: v for k, v in block.items() if k != "gates"},
                "eligible_count": sum(g["eligible"] for g in gates.values()),
                "chosen_gate": gates[block["chosen_id"]],
                "baseline_gate": gates[BASELINE],
                "excluded": {
                    n: {
                        "invalid_rows": g["invalid_rows_in_past_window"],
                        "max_tie_fraction": max(g["tie_fractions_in_past_window"]),
                    }
                    for n, g in gates.items()
                    if not g["eligible"]
                },
            }
        )
    return out


def game_bank(game, frozen, identity, workers):
    meta = GAMES[game]
    n, dn, ks = meta["max_num"], meta["draw_n"], meta["ks"]
    path = ROOT / meta["path"]
    full = load_data(path, max_num=n, draw_n=dn)
    dates = full["dates"]
    unique = np.unique(dates)
    start = max(256, int(np.searchsorted(dates, unique[len(unique) // 2])))
    current = frozen["current_baselines"][game]
    experts = list(EXPERTS) + sorted(set(current.values()) - set(EXPERTS))
    data, results = load_scores(
        path,
        experts,
        CACHE / game,
        identity,
        max_num=n,
        draw_n=dn,
        workers=workers,
        start=start,
    )
    targets = data["targets"]
    days = dates[targets]
    ids = [s["name"] for s in specs(meta)]
    print(
        f"{game}: {len(experts)} causal expert streams ready; generating32 new scores",
        flush=True,
    )
    generated = all_scores(data, results, meta)
    scores = np.stack([generated[name][0] for name in ids])
    invalid = np.stack([generated[name][1] for name in ids])
    reference_names = sorted(set(current.values()))
    extra_names = [name for name in reference_names if name not in ids]
    items = [(name, scores[i], invalid[i]) for i, name in enumerate(ids)]
    items += [
        ("current_method__" + name, results[name][1], results[name][2])
        for name in extra_names
    ]
    evaluated = {}
    for count, (name, hits, ties, _bad) in enumerate(
        evaluate_bank(
            items, data["draws"][targets], max_num=n, draw_n=dn, ks=ks, workers=workers
        ),
        1,
    ):
        evaluated[name] = (hits, ties)
        if count % 8 == 0 or count == len(items):
            print(f"{game}: pools {count}/{len(items)}", flush=True)
    hits = np.stack([evaluated[name][0] for name in ids])
    ties = np.stack([evaluated[name][1] for name in ids])
    valid = ~invalid
    refs = np.stack(
        [
            evaluated[name if name in ids else "current_method__" + name][0]
            for name in reference_names
        ]
    )
    refindex = {name: i for i, name in enumerate(reference_names)}
    current_hits = np.stack(
        [refs[refindex[current[f"k{k}"]], :, :, j] for j, k in enumerate(ks)], axis=-1
    )
    current_invalid = np.stack([results[current[f"k{k}"]][2] for k in ks], axis=-1)
    complexity = np.array([s["complexity"] for s in specs(meta)])
    replay = replay_selector(
        dates,
        targets,
        hits[:, :, 1],
        ids,
        ks=ks,
        draw_n=dn,
        complexity=complexity,
        eligibility_mask=valid,
        ties=ties,
        baseline_id=BASELINE,
    )
    selected = replay["selected_indices"]
    selector = hits[selected, np.arange(len(targets))]
    primary, diag = replay["primary_mask"], replay["diagnostic_mask"]
    baseline = hits[0]
    summary = {
        "geometry": meta,
        "input_rows": len(dates),
        "target_rows": len(targets),
        "start_history_rows": start,
        "blocks": compact_blocks(replay["blocks"]),
        "primary": {
            "selector": describe(selector[primary, 1], meta),
            "markov": describe(baseline[primary, 1], meta),
            "current": describe(current_hits[primary, 1], meta),
            "paired_vs_markov": paired(
                selector[primary, 1], baseline[primary, 1], days[primary], meta
            ),
            "paired_vs_current_diagnostic": paired(
                selector[primary, 1], current_hits[primary, 1], days[primary], meta
            ),
        },
        "diagnostic": {
            name: describe(h[diag, 1], meta)
            for name, h in (
                ("selector", selector),
                ("markov", baseline),
                ("current", current_hits),
            )
        },
        "raw": {
            name: describe(h[primary, 0], meta)
            for name, h in (
                ("selector", selector),
                ("markov", baseline),
                ("current", current_hits),
            )
        },
        "first_primary": str(days[primary][0]),
        "last_primary": str(days[primary][-1]),
        "current_baselines": current,
        "current_invalid_primary_by_k": {
            f"k{k}": int(current_invalid[primary, j].sum()) for j, k in enumerate(ks)
        },
        "invalid_primary_by_candidate": {
            name: int((~valid[i, primary]).sum())
            for i, name in enumerate(ids)
            if not valid[i, primary].all()
        },
        "invalid_target_by_candidate": {
            name: int((~valid[i]).sum())
            for i, name in enumerate(ids)
            if not valid[i].all()
        },
        "selected_primary_invalid_rows": int(
            (~valid[selected, np.arange(len(targets))][primary]).sum()
        ),
        "hypergeometric": {
            str(k): hypergeom_tails(k, max_num=n, draw_n=dn) for k in ks
        },
        "candidates": {},
    }
    for i, descriptor in enumerate(specs(meta)[2:], 2):
        test = paired(
            hits[i, primary, 1], baseline[primary, 1], days[primary], meta, bootstrap=0
        )
        summary["candidates"][descriptor["name"]] = {
            "spec": descriptor,
            "limited": describe(hits[i, primary, 1], meta),
            "raw": describe(hits[i, primary, 0], meta),
            "vs_markov": {
                k: test[k]
                for k in (
                    "mean_difference",
                    "p_value",
                    "exact_sign_flip_p_diagnostic",
                    "wins",
                    "losses",
                    "days",
                )
            },
            "invalid_primary_rows": int(invalid[i, primary].sum()),
        }
    for name, q in holm_adjusted(
        {
            name: row["vs_markov"]["p_value"]
            for name, row in summary["candidates"].items()
        }
    ).items():
        summary["candidates"][name]["holm_within_game"] = q
    save_bank(
        CACHE / f"{game}_bank.npz",
        identity=np.asarray(identity),
        candidate_ids=np.asarray(ids),
        full_dates=dates,
        targets=targets,
        draws=data["draws"][targets],
        complexity=complexity,
        hits=hits,
        ties=ties,
        valid=valid,
        candidate_scores=scores,
        expert_names=np.asarray(experts),
        expert_scores=np.stack([results[name][1] for name in experts]),
        expert_invalid=np.stack([results[name][2] for name in experts]),
        reference_names=np.asarray(reference_names),
        reference_hits=refs,
        current_hits=current_hits,
        current_invalid=current_invalid,
        selected_indices=selected,
        selector_hits=selector,
        primary_mask=primary,
        diagnostic_mask=diag,
    )
    return summary


def export_csv(report):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "game",
            "candidate",
            "family",
            "pool_size",
            "n",
            "candidate4",
            "markov4",
            "current4",
            "p_vs_markov",
            "holm32",
            "holm64",
        ]
    )
    for game, part in report["games"].items():
        for name, row in part["candidates"].items():
            for j, k in enumerate(part["geometry"]["ks"]):
                writer.writerow(
                    [
                        game,
                        name,
                        row["spec"]["family"],
                        k,
                        row["limited"]["n"],
                        row["limited"]["events4"][j],
                        part["primary"]["markov"]["events4"][j],
                        part["primary"]["current"]["events4"][j],
                        row["vs_markov"]["p_value"],
                        row["holm_within_game"],
                        row["holm_across_games"],
                    ]
                )
    (HERE / f"{STEM}_candidates.csv").write_bytes(stream.getvalue().encode("utf-8"))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "game",
            "date",
            "segment",
            "chosen",
            "pool_size",
            "selector_c2",
            "markov_c2",
            "current_c2",
            "selector_raw",
            "markov_raw",
            "current_raw",
        ]
    )
    for game, part in report["games"].items():
        with np.load(CACHE / f"{game}_bank.npz", allow_pickle=False) as data:
            for t in np.flatnonzero(data["primary_mask"] | data["diagnostic_mask"]):
                for j, k in enumerate(part["geometry"]["ks"]):
                    writer.writerow(
                        [
                            game,
                            data["full_dates"][data["targets"][t]],
                            "primary" if data["primary_mask"][t] else "diagnostic",
                            data["candidate_ids"][data["selected_indices"][t]],
                            k,
                            int(data["selector_hits"][t, 1, j]),
                            int(data["hits"][0, t, 1, j]),
                            int(data["current_hits"][t, 1, j]),
                            int(data["selector_hits"][t, 0, j]),
                            int(data["hits"][0, t, 0, j]),
                            int(data["current_hits"][t, 0, j]),
                        ]
                    )
    (HERE / f"{STEM}_trace.csv").write_bytes(stream.getvalue().encode("utf-8"))


def run(workers):
    frozen = verify_protocol()
    identity = sha(MANIFEST)
    before = protected()
    started = time.perf_counter()
    games = {game: game_bank(game, frozen, identity, workers) for game in GAMES}
    for game, q in holm_adjusted(
        {
            game: part["primary"]["paired_vs_markov"]["p_value"]
            for game, part in games.items()
        }
    ).items():
        games[game]["primary"]["holm_across_games"] = q
        games[game]["primary"]["selector_gate_passed"] = (
            q < 0.05
            and games[game]["primary"]["paired_vs_markov"]["mean_difference"] > 0
        )
    allp = {
        f"{game}:{name}": row["vs_markov"]["p_value"]
        for game, part in games.items()
        for name, row in part["candidates"].items()
    }
    for label, q in holm_adjusted(allp).items():
        game, name = label.split(":", 1)
        games[game]["candidates"][name]["holm_across_games"] = q
    verify_protocol()
    after = protected()
    if before != after:
        raise RuntimeError("Protected runtime files changed during research")
    report = {
        "protocol_sha256": identity,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": time.perf_counter() - started,
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
        "production_changed": False,
        "baseline": BASELINE,
        "candidate_count_per_game": 32,
        "candidate_game_tests": len(allp),
        "games": games,
        "protected_before": before,
        "protected_after": after,
        "conclusion": "Retrospective comparison; no automatic production promotion or proof of future advantage.",
    }
    write_json(REPORT, report)
    export_csv(report)
    print(
        json.dumps(
            {
                g: {
                    "selector4": p["primary"]["selector"]["primary"],
                    "markov4": p["primary"]["markov"]["primary"],
                    "current4_diagnostic": p["primary"]["current"]["primary"],
                    "p": p["primary"]["paired_vs_markov"]["p_value"],
                    "holm2": p["primary"]["holm_across_games"],
                    "gate": p["primary"]["selector_gate_passed"],
                }
                for g, p in games.items()
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.register:
        register()
    if args.run:
        run(max(1, min(args.workers, 4, os.cpu_count() or 1)))
