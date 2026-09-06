"""Audit reproductibil CPU, separat de registry, bench și starea aplicației.

Calendar (zi/lună/trimestru) și ablații ale penalizării existente, fără apeluri
la metode blacklistate. 50% istoric inițial, 20% selecție, 30% verificare.
La fiecare pas scorerul vede doar trecutul; calendarul datei țintă e cunoscut.
Holdout-ul este rezervat în acest experiment, nu istoric nemaivăzut de proiect.
Nu promovează automat metode și nu rescrie best_methods/folds/curated.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.stats import binomtest, hypergeom

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loto_engine import LotoEngine
from loto_enterprise.benchmark.methods import score_frequency
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance
from ui_shared import atomic_write_json, atomic_write_text
from wheeling_methods import compute_coverage_pct, generate_wheel

GAMES = {"6/49": ("loto_6_49.csv", 49, 6),
         "5/40": ("loto_5_40.csv", 40, 5), "joker": ("joker.csv", 45, 5)}
PENALTIES = {"penalty_1_050": (1, .5), "penalty_3_000": (3, 0.),
             "penalty_3_050": (3, .5), "penalty_3_080": (3, .8),
             "penalty_5_050": (5, .5)}


def calendar_key(date, kind):
    return {"weekday": date.weekday(), "month": date.month,
            "quarter": (date.month - 1) // 3}[kind]


def candidate_scores(history, dates, target_date, universe):
    """Primește explicit doar prefixul anterior țintei (ușor de verificat)."""
    base = score_frequency(history, universe)
    result = {"frequency": base}
    for name, (n, factor) in PENALTIES.items():
        scores, _ = LotoEngine.apply_recent_penalty(base, history, n, factor, universe)
        # Aceeași gardă anti-aplatizare ca în producție.
        result[name] = scores if has_usable_score_variance(scores) else dict(base)
    global_counts = np.bincount(history.ravel(), minlength=universe + 1)[1:]
    prior = global_counts / len(history)
    for kind in ("weekday", "month", "quarter"):
        wanted = calendar_key(target_date, kind)
        match = np.array([calendar_key(d, kind) == wanted for d in dates])
        counts = np.bincount(history[match].ravel(), minlength=universe + 1)[1:]
        # 50 pseudo-extrageri stabilite înainte de test; niciun tuning pe holdout.
        score = (counts + 50 * prior) / (int(match.sum()) + 50)
        result[f"calendar_{kind}"] = {i + 1: float(s) for i, s in enumerate(score)}
    return result


def holm(pvalues):
    """Corecție family-wise, inclusiv pentru ipoteze dependente."""
    ordered = sorted(pvalues, key=pvalues.get)
    out, previous = {}, 0.
    for i, key in enumerate(ordered):
        previous = max(previous, min(1., (len(ordered) - i) * pvalues[key]))
        out[key] = previous
    return out


def metrics(hits, base_hits, universe, draw_n, pool):
    h, b = np.asarray(hits), np.asarray(base_hits)
    result = {"n": len(h), "mean_hits": float(h.mean())}
    for target in (3, 4):
        observed = int((h >= target).sum())
        theory = float(hypergeom.sf(target - 1, universe, pool, draw_n))
        interval = binomtest(observed, len(h)).proportion_ci()
        result[f"hits_{target}plus"] = observed
        result[f"rate_{target}plus"] = observed / len(h)
        result[f"theory_{target}plus"] = theory
        result[f"ci95_{target}plus"] = [float(interval.low), float(interval.high)]
        result[f"p_{target}plus"] = float(binomtest(observed, len(h), theory, alternative="greater").pvalue)
        result[f"delta_{target}plus_pp"] = float(((h >= target).mean() - (b >= target).mean()) * 100)
        result[f"wins_{target}plus"] = int(((h >= target) & (b < target)).sum())
        result[f"losses_{target}plus"] = int(((h < target) & (b >= target)).sum())
    return result


def audit_game(game, pool):
    filename, universe, draw_n = GAMES[game]
    path = ROOT / "_ISTORIC" / filename
    df = pd.read_csv(path)
    dates = pd.to_datetime(df.date, format="%d-%m-%Y", errors="raise").tolist()
    if dates != sorted(dates):
        raise ValueError(f"{filename}: cronologie invalidă")
    draws = df[[f"n{i}" for i in range(1, draw_n + 1)]].to_numpy(dtype=int)
    if not all(len(set(d)) == draw_n and min(d) >= 1 and max(d) <= universe for d in draws):
        raise ValueError(f"{filename}: numere invalide")
    start, split = len(draws) // 2, len(draws) * 7 // 10
    hits, six_hits = {}, {}
    for i in range(start, len(draws)):
        scores = candidate_scores(draws[:i], dates[:i], dates[i], universe)
        for name, score in scores.items():
            pool_nums = set(rank_by_score(score, pool))
            hits.setdefault(name, []).append(len(pool_nums & set(draws[i])))
            if game == "5/40":
                # Supliment distinct: categoria III folosește 4 din TOATE cele 6.
                actual6 = set(int(df.iloc[i][f"n{j}"]) for j in range(1, 7))
                if len(actual6) != 6 or min(actual6) < 1 or max(actual6) > universe:
                    raise ValueError("5/40: al șaselea număr invalid")
                six_hits.setdefault(name, []).append(len(pool_nums & actual6))
    validation_len = split - start
    validation = {name: metrics(h[:validation_len], hits["frequency"][:validation_len], universe, draw_n, pool)
                  for name, h in hits.items()}
    # Include baseline-ul ca posibil câștigător; nu forțează o modificare.
    selected = max(sorted(hits), key=lambda name: (validation[name]["rate_3plus"],
                                                  validation[name]["mean_hits"]))
    holdout = {name: metrics(h[validation_len:], hits["frequency"][validation_len:], universe, draw_n, pool)
               for name, h in hits.items()}
    for value in holdout.values():
        value["half_rates_3plus"] = []
    for name, h in hits.items():
        h = np.asarray(h[validation_len:])
        holdout[name]["half_rates_3plus"] = [float((part >= 3).mean()) for part in np.array_split(h, 2)]
    result = {"rows": len(draws), "csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "pool": pool, "draw_n_scored": draw_n, "last_date": dates[-1].isoformat(),
              "validation_start": dates[start].isoformat(), "holdout_start": dates[split].isoformat(),
              "selected_on_validation": selected, "validation": validation, "holdout": holdout}
    if six_hits:
        result["holdout_all_six_540"] = {
            name: metrics(h[validation_len:], six_hits["frequency"][validation_len:], universe, 6, pool)
            for name, h in six_hits.items()}
    print(f"{game}: n={len(draws)}; ales înainte de holdout={selected}; "
          f"3+ holdout={holdout[selected]['rate_3plus']:.3%}", flush=True)
    return result


def target_matrix(blocks, v, condition, guarantee):
    targets = np.array(list(itertools.combinations(range(1, v + 1), condition)))
    # Matrice independentă de implementarea acoperirii din aplicație.
    return np.array([(np.isin(targets, block).sum(axis=1) >= guarantee) for block in blocks])


def audit_designs():
    result = []
    for path in sorted((ROOT / "covering_designs").glob("[CL]_*.txt")):
        parts = path.stem.split("_")
        v, pick = int(parts[1]), int(parts[2])
        condition, guarantee = ((int(parts[3]), int(parts[3])) if parts[0] == "C"
                                else (int(parts[3]), int(parts[4])))
        blocks = [list(map(int, line.split())) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        valid = bool(blocks) and all(len(b) == pick and len(set(b)) == pick and min(b) >= 1 and max(b) <= v for b in blocks)
        if not valid:
            raise ValueError(f"Design invalid: {path.name}")
        covered = target_matrix(blocks, v, condition, guarantee)
        counts = covered.sum(axis=0)
        full = bool((counts > 0).all())
        if not full or compute_coverage_pct(blocks, list(range(1, v + 1)), guarantee, condition) != 100.:
            raise ValueError(f"Design incomplet: {path.name}")
        redundant = []
        for i in range(len(blocks) - 1, -1, -1):
            if (counts[covered[i]] > 1).all():
                redundant.append(i)
                counts -= covered[i]
        # Limită inferioară de volum pentru L, Schonheim pentru C.
        per_block = sum(math.comb(pick, j) * math.comb(v - pick, condition - j)
                        for j in range(guarantee, min(pick, condition) + 1)
                        if 0 <= condition - j <= v - pick)
        lower = math.ceil(math.comb(v, condition) / per_block)
        if condition == guarantee:
            lower = 1
            for j in range(guarantee - 1, -1, -1):
                lower = math.ceil((v - j) * lower / (pick - j))
        result.append({"file": path.name, "tickets": len(blocks), "coverage": 100.,
                       "duplicates": len(blocks) - len({tuple(sorted(b)) for b in blocks}),
                       "removable": len(redundant), "lower_bound": lower,
                       "proven_optimal_by_bound": len(blocks) == lower,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    print(f"Designuri: {len(result)} validate, {sum(x['removable'] for x in result)} bilete eliminabile", flush=True)
    return result


def compare_wheels(pool):
    """Probabilități exacte pentru pool fix, inclusiv ținte sub condiția L.

    Fiecare h-submulțime din pool are C(N-pool, draw_n-h) extensii exterioare.
    Suma ponderată e P(max hit pe bilet >= t), nu P(pool >= t).
    """
    result = {}
    for game, (_, universe, draw_n) in GAMES.items():
        rows = []
        # 5/40: șase numere la categoria III; primele cinci rămân axa aplicației.
        for guarantee, condition in ((3, 3), (3, 4), (4, 4), (4, 5)):
            wheel, coverage = generate_wheel("lajolla", list(range(1, pool + 1)), draw_n,
                                             guarantee, condition=condition)
            row = {"guarantee": guarantee, "condition": condition, "tickets": len(wheel),
                   "coverage": coverage}
            for actual_n in ([draw_n, 6] if game == "5/40" else [draw_n]):
                probabilities = {3: 0., 4: 0.}
                for h in range(3, min(actual_n, pool) + 1):
                    for target in (3, 4):
                        covered = target_matrix(wheel, pool, h, target).any(axis=0)
                        probabilities[target] += int(covered.sum()) * math.comb(universe - pool, actual_n - h) / math.comb(universe, actual_n)
                row[f"draw_{actual_n}"] = {f"p_ticket_{t}plus": p for t, p in probabilities.items()}
            rows.append(row)
        result[game] = rows
    return result


def prune_redundant_designs(audit):
    """Elimină numai blocuri redundante, revalidând exact înainte de scriere."""
    changes = []
    for row in audit:
        if not row["removable"]:
            continue
        path = ROOT / "covering_designs" / row["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Design modificat după audit: {path.name}")
        parts = path.stem.split("_")
        v, pick, condition = map(int, parts[1:4])
        guarantee = condition if parts[0] == "C" else int(parts[4])
        lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        blocks = [list(map(int, line.split())) for line in lines]
        matrix = target_matrix(blocks, v, condition, guarantee)
        counts, keep = matrix.sum(axis=0), np.ones(len(blocks), dtype=bool)
        for i in range(len(blocks) - 1, -1, -1):
            if (counts[matrix[i]] > 1).all():
                keep[i] = False
                counts -= matrix[i]
        reduced = [b for b, k in zip(blocks, keep) if k]
        if not (counts > 0).all() or compute_coverage_pct(reduced, list(range(1, v + 1)), guarantee, condition) != 100.:
            raise ValueError(f"Reducere invalidă: {path.name}")
        atomic_write_text(path, "\n".join(line for line, k in zip(lines, keep) if k) + "\n")
        changes.append({"file": path.name, "before": len(blocks), "after": len(reduced),
                        "coverage_after": 100., "original_sha256": row["sha256"]})
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=int, default=11)
    parser.add_argument("--output", type=Path, default=ROOT / "bench_results/pattern_design_audit.json")
    parser.add_argument("--prune-redundant", action="store_true",
                        help="Scrie designurile cu blocurile redundante eliminate; păstrează garanția cerută.")
    args = parser.parse_args()
    if not 6 <= args.pool <= 16:
        parser.error("pool trebuie să fie în 6..16")
    result = {"protocol": "CPU; 50/20/30 cronologic; 8 ipoteze + frequency; fără promovare automată",
              "games": {game: audit_game(game, args.pool) for game in GAMES}}
    pvalues = {f"{game}/{name}": m["p_3plus"] for game, g in result["games"].items()
               for name, m in g["holdout"].items()}
    result["exploratory_holm_3plus"] = holm(pvalues)
    selected = {game: g["holdout"][g["selected_on_validation"]]["p_3plus"] for game, g in result["games"].items()}
    result["selected_holm_3plus"] = holm(selected)
    result["designs"] = audit_designs()
    if args.prune_redundant:
        result["design_changes"] = prune_redundant_designs(result["designs"])
    result["wheel_comparison"] = compare_wheels(args.pool)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output, result)
    print(f"Rezultat: {args.output}", flush=True)


if __name__ == "__main__":
    main()
