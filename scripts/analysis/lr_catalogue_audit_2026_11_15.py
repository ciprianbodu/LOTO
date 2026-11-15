"""Independent audit of official LR layouts, costs and high-hit probabilities.

Read-only with respect to the project. No project modules, scorer, generic
wheel generator or covering probability helpers are imported. Each official
text layout is validated, all feasible pool intersections are enumerated and
weighted by the exact number of outside draws. Output directory is explicit.

Usage from the repository:
python scripts/analysis/lr_catalogue_audit_2026_11_15.py --out-dir scratch/lr_audit
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from math import comb
from pathlib import Path
import re
import time

import numpy as np

GEOMETRY = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}
FILE_GAMES = {"649": "6/49", "540": "5/40", "joker": "joker"}
BASELINES = (
    ("6/49", 48, "Cod confirmat"), ("5/40", 16, "Cod confirmat"),
    ("joker", 24, "Cod confirmat"), ("6/49", 59, "Maxim catalog LR"),
    ("5/40", 48, "Maxim catalog LR"), ("joker", 12, "Maxim catalog LR"),
)


def load_layouts(root):
    layouts = []
    for path in sorted((root / "covering_designs" / "lr").glob("LR_*.txt")):
        match = re.fullmatch(r"LR_(649|540|joker)_cod(\d+)\.txt", path.name)
        if match is None:
            continue
        game = FILE_GAMES[match[1]]
        pick, _, maximum = GEOMETRY[game]
        text = path.read_text(encoding="utf-8-sig")
        n = int(re.search(r"^#\s*numere:\s*(\d+)\s*$", text, re.M)[1])
        declared = int(re.search(r"^#\s*variante:\s*(\d+)\s*$", text, re.M)[1])
        rows = tuple(tuple(sorted(map(int, line.split()))) for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))
        if not pick < n <= maximum or len(rows) != declared or len(set(rows)) != declared:
            raise ValueError(f"Invalid official layout: {path}")
        if any(len(row) != pick or len(set(row)) != pick
               or any(x < 1 or x > n for x in row) for row in rows):
            raise ValueError(f"Invalid variant: {path}")
        if {x for row in rows for x in row} != set(range(1, n + 1)):
            raise ValueError(f"Unplayed position: {path}")
        layouts.append(dict(game=game, code=int(match[2]), pool=n, variants=rows,
                            file=str(path.relative_to(root)),
                            file_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if len({(r["game"], r["code"]) for r in layouts}) != len(layouts):
        raise ValueError("Duplicated official code")
    return layouts


def enumerate_layout(layout):
    game, n, rows = layout["game"], layout["pool"], layout["variants"]
    pick, drawn, maximum = GEOMETRY[game]
    tickets = np.array([sum(1 << (p - 1) for p in row) for row in rows], dtype=np.uint64)
    total = comb(maximum, drawn)
    tails = {t: 0 for t in range(1, pick + 1)}
    exact = {t: 0 for t in range(1, pick + 1)}
    conditional = {}
    histogram = [0] * (pick + 1)
    category_II_sixfold = 0
    pool_only_example = None
    for h in range(min(n, drawn) + 1):
        if drawn - h > maximum - n:
            continue
        subsets = list(combinations(range(n), h))
        masks = np.fromiter((sum(1 << p for p in row) for row in subsets),
                            dtype=np.uint64, count=comb(n, h))
        hits = np.bitwise_count(masks[:, None] & tickets[None, :])
        best = hits.max(axis=1)
        weight = comb(maximum - n, drawn - h)
        counts = np.bincount(best, minlength=pick + 1)
        for t, value in enumerate(counts):
            histogram[t] += int(value) * weight
        conditional[h] = {t: int(np.count_nonzero(best >= t)) for t in tails}
        for t in tails:
            tails[t] += conditional[h][t] * weight
            exact[t] += int(np.count_nonzero(np.any(hits == t, axis=1))) * weight
        if game == "5/40":
            q = np.count_nonzero(hits == 5, axis=1)
            category_II_sixfold += (5 * int(np.count_nonzero(q == 1))
                                    + 6 * int(np.count_nonzero(q >= 2))) * weight
        if h == 5 and pool_only_example is None and np.any(best < 5):
            index = int(np.flatnonzero(best < 5)[0])
            inside = [p + 1 for p in subsets[index]]
            pool_only_example = dict(pool=list(range(1, n + 1)),
                                     drawn=inside + list(range(n + 1, n + 1 + drawn - h)),
                                     pool_hits=h, best_lr_hits=int(best[index]))
    if sum(histogram) != total:
        raise AssertionError("Incomplete independent drawing count")
    probabilities = {t: tails[t] / total for t in tails}
    pool_probabilities = {
        t: sum(comb(n, h) * comb(maximum - n, drawn - h)
               for h in range(t, min(n, drawn) + 1)) / total
        for t in tails
    }
    result = {k: v for k, v in layout.items() if k != "variants"}
    result.update(variants=len(rows), denominator=total, histogram=histogram,
                  tail_counts=tails, exact_any_counts=exact,
                  probabilities=probabilities, pool_probabilities=pool_probabilities,
                  conditional_counts=conditional, pool_only_example=pool_only_example)
    if game == "joker":
        result["p5_plus_J"] = tails[5] / (20 * total)
    if game == "5/40":
        result["p_category_I"] = len(rows) / comb(40, 5)
        result["p_category_II_any"] = category_II_sixfold / (6 * total)
    return result


def compare(base, other):
    pick, drawn, _ = GEOMETRY[base["game"]]
    high = range(4, pick + 1)
    high_safe = all(other["tail_counts"][t] >= base["tail_counts"][t] for t in high)
    lost_sure = []
    conditional_safe = True
    for h in range(3, min(drawn, base["pool"], other["pool"]) + 1):
        for t in range(3, min(h, pick) + 1):
            a = Fraction(base["conditional_counts"][h][t], comb(base["pool"], h))
            b = Fraction(other["conditional_counts"][h][t], comb(other["pool"], h))
            if a == 1 and b < 1:
                lost_sure.append([h, t])
            conditional_safe &= b >= a
    exact_safe = base["game"] != "joker" or all(
        other["exact_any_counts"][t] >= base["exact_any_counts"][t] for t in range(1, 6)
    )
    return dict(base_code=base["code"], candidate_code=other["code"],
                base_variants=base["variants"], candidate_variants=other["variants"],
                preserves_high_tails=high_safe, preserves_sure_guarantees=not lost_sure,
                preserves_all_conditional_rates=bool(conditional_safe),
                preserves_joker_exact_categories=exact_safe,
                lost_sure_guarantees=lost_sure,
                dominates=high_safe and not lost_sure and exact_safe)


def partition_bounds(base, catalogue):
    """Every multiset of complete official codes at cost <= confirmed B.

    Pools and chosen numbers may overlap in any way. The union bound remains
    an upper bound for every such arrangement; exceeding it is impossible.
    Passing an upper bound is not evidence that a dominating union exists.
    """
    choices = sorted((r for r in catalogue if r["game"] == base["game"]
                      and r["variants"] <= base["variants"]),
                     key=lambda r: (r["variants"], r["code"]))
    pick, _, _ = GEOMETRY[base["game"]]
    partitions = []

    def visit(start, selected, cost):
        if selected:
            upper = {t: min(base["denominator"], sum(r["tail_counts"][t] for r in selected))
                     for t in range(4, pick + 1)}
            excluded = [t for t in upper if upper[t] < base["tail_counts"][t]]
            partitions.append(dict(codes=[r["code"] for r in selected], variants=cost,
                                   exact_budget=cost == base["variants"],
                                   is_baseline=len(selected) == 1 and selected[0]["code"] == base["code"],
                                   upper_tail_counts=upper,
                                   upper_probabilities={t: upper[t] / base["denominator"] for t in upper},
                                   excluded_at_thresholds=excluded,
                                   excluded_by_union_bound=bool(excluded)))
        for i in range(start, len(choices)):
            candidate = choices[i]
            new_cost = cost + candidate["variants"]
            if new_cost <= base["variants"]:
                visit(i, selected + [candidate], new_cost)

    visit(0, [], 0)
    return dict(game=base["game"], baseline_code=base["code"], budget=base["variants"],
                denominator=base["denominator"], partitions=partitions,
                all_alternatives_excluded=all(r["excluded_by_union_bound"]
                                              for r in partitions if not r["is_baseline"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    layouts = load_layouts(root)
    catalogue = [enumerate_layout(layout) for layout in layouts]
    comparisons = []
    same_size_pairs = []
    for base in catalogue:
        for other in catalogue:
            if base["game"] != other["game"] or base["code"] == other["code"]:
                continue
            if base["variants"] == other["variants"]:
                same_size_pairs.append([base["game"], base["code"], other["code"]])
            if other["variants"] <= base["variants"]:
                comparisons.append({"game": base["game"], **compare(base, other)})
    dominators = [row for row in comparisons if row["dominates"]]
    baseline_rows = []
    for game, code, kind in BASELINES:
        base = next(r for r in catalogue if r["game"] == game and r["code"] == code)
        eligible = [r for r in catalogue if r["game"] == game and r["variants"] <= base["variants"] and r["code"] != code]
        baseline_rows.append(dict(kind=kind, baseline=base,
                                  alternative_codes=[r["code"] for r in eligible],
                                  larger_pool_at_same_or_lower_cost=[r["code"] for r in eligible if r["pool"] > base["pool"]]))
    budget_unions = [partition_bounds(item["baseline"], catalogue)
                    for item in baseline_rows if item["kind"] == "Cod confirmat"]
    expected_budgets = {"6/49": (9, 12), "5/40": (8, 21), "joker": (10, 14)}
    for item in baseline_rows[:3]:
        base = item["baseline"]
        if (base["pool"], base["variants"]) != expected_budgets[base["game"]]:
            raise ValueError("The confirmed code no longer matches its recorded official geometry")
    expected_exact_partitions = {"6/49": {(48,)}, "5/40": {(16,)},
                                 "joker": {(24,), (34, 45)}}
    for union in budget_unions:
        exact = {tuple(sorted(part["codes"])) for part in union["partitions"]
                 if part["exact_budget"]}
        if exact != expected_exact_partitions[union["game"]] or not union["all_alternatives_excluded"]:
            raise ValueError("The catalogue changed; the dated conclusions require a new review")
    result = dict(source=str(root), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  official_scheme_count=len(catalogue), seconds=time.perf_counter() - started,
                  catalogue=catalogue, comparisons=comparisons,
                  same_size_pairs=same_size_pairs, dominators=dominators,
                  baselines=baseline_rows, confirmed_budget_partitions=budget_unions)
    (output / "lr_catalogue_audit_2026_11_15.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "AUDIT INDEPENDENT AL SCHEMELOR REDUSE OFICIALE LR — 15.11.2026",
        "",
        f"Au fost verificate {len(catalogue)} de scheme oficiale și {len(comparisons)} de comparații orientate la cost mai mic sau egal, în interiorul aceluiași joc.",
        "Numărul de variante, nu numărul de numere din pool, stabilește costul variantelor. Taxele pe bilet nu sunt incluse în această comparație.",
        f"Perechi de coduri distincte cu același număr de variante: {len(same_size_pairs)}. Alternative care păstrează toate pragurile înalte, garanțiile certe și categoriile exacte Joker: {len(dominators)}.",
        "Nu au fost modificate producția, setările, istoricele, scorerii, deciziile sau desfășurările oficiale.",
        "",
        "Codurile confirmate de utilizator și maximele LR, păstrate separat pentru context",
        "",
    ]
    for item in baseline_rows:
        b = item["baseline"]
        p = b["probabilities"]
        lines.append(f"{item['kind']}: {b['game']}, Cod {b['code']}, {b['pool']} numere, {b['variants']} variante.")
        lines.append(f"P(4+ pe cel puțin o variantă LR) = {100*p[4]:.9f}%; P(5+) = {100*p[5]:.9f}%" + (f"; P(6) = {100*p[6]:.9f}%." if b["game"] == "6/49" else "."))
        if b["game"] == "joker":
            lines.append(f"Cu o singură bilă Joker pe toate variantele, P(5+J) = {100*b['p5_plus_J']:.9f}%. Șase numere principale nu există la Joker.")
        lines.append(f"Alte coduri oficiale la cost cel mult egal: {item['alternative_codes']}. Coduri cu pool mai larg în acest buget: {item['larger_pool_at_same_or_lower_cost']}.")
        lines.append("")
    lines += [
        "Ce se poate și ce nu se poate îmbunătăți în acest catalog",
        "",
        "Bugetele reale confirmate de utilizator sunt 6/49 Cod 48: 9 numere și 12 variante; 5/40 Cod 16: 8 numere și 21 de variante; Joker Cod 24: 10 numere și 14 variante. Poolurile salvate anterior și numărul variantelor unor sisteme interne complete nu înlocuiesc această confirmare. Nu există alt cod oficial cu pool mai mare la același cost sau la cost mai mic.",
        "La 6/49, P(6) = B/C(49,6) pentru B variante distincte. La Joker, P(5) = B/C(45,5), iar P(5+J) = B/[20*C(45,5)] dacă toate variantele folosesc aceeași bilă Joker. Reducerea numărului de variante reduce aceste praguri. În fiecare joc, catalogul nu conține două coduri cu același B.",
        "La 5/40 se extrag șase numere, iar varianta are cinci; suprapunerea evenimentelor contează pentru P(5+). Verificarea exactă a tuturor codurilor confirmă că niciun cod la cost mai mic sau egal nu păstrează simultan pragurile și garanțiile codului de referință.",
        "",
        "Împărțirea bugetelor confirmate între mai multe coduri oficiale",
        "",
        "Sunt incluse repetările unui cod și toate sumele mai mici sau egale cu bugetul, fără a presupune că numerele alese pe bilete sunt aceleași. Pentru orice așezare relativă a numerelor, probabilitatea reuniunii evenimentelor nu poate depăși suma probabilităților individuale. Această limită poate exclude o îmbunătățire; depășirea ei față de reper nu dovedește existența unei îmbunătățiri.",
    ]
    for budget in budget_unions:
        lines.append(f"{budget['game']}, Cod {budget['baseline_code']}, buget {budget['budget']} variante:")
        for part in budget["partitions"]:
            codes = " + ".join(f"Cod {code}" for code in part["codes"])
            bounds = "; ".join(f"P({t}+) ≤ {100*p:.9f}%" for t, p in part["upper_probabilities"].items())
            verdict = "reperul confirmat" if part["is_baseline"] else (
                "îmbunătățire simultană exclusă la pragurile " + ", ".join(f"{t}+" for t in part["excluded_at_thresholds"])
                if part["excluded_by_union_bound"] else "limita nu exclude singură alternativa; nu dovedește superioritatea")
            lines.append(f"  {codes}: {part['variants']} variante; {bounds}; {verdict}.")
        lines.append("")
    lines += [
        "La 5/40, singura partiție exactă a bugetului de 21 de variante este Cod 16. Două scheme Cod 15 consumă numai 18 variante și ar putea avea o limită 4+ mai mare decât Cod 16, dar limita 5+ este 0,015840016%, sub 0,018236860% la Cod 16, astfel încât nu pot îmbunătăți simultan toate pragurile.",
        "La Joker, bugetul exact de 14 variante permite Cod 24 sau Cod 34 + Cod 45. Pentru ultima combinație, P(4+) ≤ 0,207324030%, sub 0,221156546% la Cod 24, indiferent de numerele alese. Toate celelalte partiții mai ieftine sunt excluse de aceeași limită.",
        "",
        "Pool hit și LR hit sunt măsuri diferite",
        "",
        "Pool hit numără numerele extrase care se află în pool. LR hit este numărul maxim de potriviri pe una dintre variantele desfășurării oficiale, cu pozițiile mapate în ordinea numerică crescătoare folosită de aplicație. Cinci numere în pool nu înseamnă automat cinci pe o variantă LR.",
    ]
    for item in baseline_rows[:3]:
        b = item["baseline"]
        lines.append(f"{b['game']}, Cod {b['code']}: P(5+ în pool) = {100*b['pool_probabilities'][5]:.9f}%, față de P(5+ pe LR) = {100*b['probabilities'][5]:.9f}%.")
        if b["pool_only_example"] is not None:
            ex = b["pool_only_example"]
            lines.append(f"Exemplu verificabil: pool {ex['pool']}; extrase {ex['drawn']}; {ex['pool_hits']} în pool, maximum {ex['best_lr_hits']} pe LR.")
    lines += [
        "",
        "Corecția de măsurare trebuie să raporteze separat ratele pe variantele LR, codul, numărul real de variante și categoriile de premiu, față de benchmarkul bazat pe pool sau de variantele generatorului intern. O reluare LR poate folosi poolurile istorice deja salvate, fără să refacă scorerii, dacă păstrează identitatea codului, hashul desfășurării și contextul fiecărei extrageri.",
        "Schimbarea obiectivului de evaluare din pool hit în LR hit nu demonstrează un avantaj predictiv și nu justifică promovarea automată a unui scorer. Studiile descrise în AGENTS.md, secțiunile LR din 04–05.11.2026, au evaluat deja variantele LR și nu au confirmat un avantaj robust pe istoricul reutilizat. Prezentul raport nu presupune ce numere au fost efectiv jucate la extragerea de azi.",
        "",
        "Metoda verificării",
        "",
        "Parser independent al celor 33 de fișiere oficiale; validare dimensiuni, poziții, duplicate și acoperirea pozițiilor. Pentru fiecare schemă au fost enumerate toate intersecțiile posibile ale extragerii cu poolul și ponderate exact cu C(N-n,d-h). Numărătorile întregi verifică toate pragurile și evenimentele «există o variantă cu exact h», separat la Joker. Garanțiile certe condiționate de h numere în pool au fost comparate prin fracții exacte. Niciun rezultat al unei extrageri observate nu intră în calcule.",
        f"Timp local: {result['seconds']:.3f} secunde. Script SHA256: {result['script_sha256']}.",
    ]
    report = "\n".join(lines) + "\n"
    (output / "lr_catalogue_audit_2026_11_15.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
