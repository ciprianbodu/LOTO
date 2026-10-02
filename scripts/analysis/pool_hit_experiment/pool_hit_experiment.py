"""Experiment preinregistrat 2026-10-02: reguli de selectie pentru mai multe
hituri IN POOL. Protocolul este in PREREGISTRATION_2026-10-02.md, parametrii si
amprentele datelor in preregistration_2026-10-02.json.

Nu atinge productia: citeste registry-ul METHODS, nu scrie nicio stare.

Moduri:
    python pool_hit_experiment.py hashes      # amprentele prefixelor (pentru JSON)
    python pool_hit_experiment.py run [--jobs 4]  # evaluarea completa -> results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
REG_PATH = HERE / "preregistration_2026-10-02.json"

# --- parametri fixati inainte de evaluare (copiati si in JSON) -------------
WARMUP = 200  # extrageri anterioare (cu data strict mai mica) cerute de o tinta
META_WINDOW = 300  # ferestre trecute pentru selectorul meta
META_TOP = 5  # C4: media rangurilor celor mai bune 5 metode recente
DEV_FRACTION = 0.70  # primele 70% din randurile romanesti = dezvoltare

DATASETS = {
    # nume: (cale, max_num, draw_n, coloane, rol)
    "ro_649": ("_ISTORIC/loto_6_49.csv", 49, 6, [f"n{i}" for i in range(1, 7)], "ro"),
    "ro_540": ("_ISTORIC/loto_5_40.csv", 40, 6, [f"n{i}" for i in range(1, 7)], "ro"),
    "ro_joker": ("_ISTORIC/joker.csv", 45, 5, [f"n{i}" for i in range(1, 6)], "ro"),
    "at_645": ("_ISTORIC/externe/austria_lotto_6aus45.csv", 45, 6, None, "ext"),
    "be_645": ("_ISTORIC/externe/belgia_lotto_6din45.csv", 45, 6, None, "ext"),
    "hu_645": ("_ISTORIC/externe/ungaria_hatoslotto_6din45.csv", 45, 6, None, "ext"),
    "cz_649": ("_ISTORIC/externe/cehia_sportka_6din49.csv", 49, 6, None, "ext"),
    "sk_649": ("_ISTORIC/externe/slovacia_loto_6din49.csv", 49, 6, None, "ext"),
    "bg_649": ("_ISTORIC/externe/bulgaria_toto2_6din49.csv", 49, 6, None, "ext"),
}
# Celula de test: (nume, seturi de date, pool, prag)
TEST_CELLS = [
    ("ext_645_k6", ["at_645", "be_645", "hu_645"], 6, 3),
    ("ext_645_k12", ["at_645", "be_645", "hu_645"], 12, 3),
    ("ext_649_k6", ["cz_649", "sk_649", "bg_649"], 6, 3),
    ("ext_649_k12", ["cz_649", "sk_649", "bg_649"], 12, 3),
    ("ro_649_k6", ["ro_649"], 6, 3),
    ("ro_649_k12", ["ro_649"], 12, 3),
    ("ro_joker_k11", ["ro_joker"], 11, 3),
    ("ro_540_k11", ["ro_540"], 11, 4),
]
CANDIDATES = ("C1_meta_best", "C2_borda_all", "C3_dev_best", "C4_meta_top5_borda")
POOLS = (6, 11, 12)
# referinta de dezvoltare pentru C3 pe fiecare celula
DEV_SOURCE = {
    "ext_645": "ro_649",
    "ext_649": "ro_649",
    "ro_649": "ro_649",
    "ro_joker": "ro_joker",
    "ro_540": "ro_540",
}
ALPHA = 0.05


def _lines(path: Path) -> list[str]:
    return path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8").splitlines()


def prefix_hash(path: Path, n_rows: int) -> str:
    """SHA-256 al antetului + primelor n_rows randuri, cu LF."""
    lines = _lines(path)[: n_rows + 1]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def load(name: str, n_rows: int | None = None):
    import pandas as pd

    rel, max_num, draw_n, cols, _ = DATASETS[name]
    df = pd.read_csv(ROOT / rel, dtype=str)
    if n_rows is not None:
        df = df.iloc[:n_rows]
    cols = cols or [f"n{i}" for i in range(1, draw_n + 1)]
    dates = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="raise")
    draws = df[cols].astype(int).to_numpy()
    for row in draws:
        if len(set(row)) != draw_n or row.min() < 1 or row.max() > max_num:
            raise ValueError(f"{name}: extragere invalida {row}")
    order = np.argsort(dates.to_numpy(), kind="stable")
    return dates.to_numpy()[order], draws[order], max_num, draw_n


def production_methods() -> list[str]:
    from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
    from loto_enterprise.benchmark.methods import METHODS

    return sorted(m for m in METHODS if m not in EXCLUDED_FROM_PRODUCTION)


def ranking(scores: dict, max_num: int) -> np.ndarray:
    """Ordinea canonica a aplicatiei (core.ranking.rank_by_score)."""
    from loto_enterprise.core.ranking import rank_by_score

    top = [int(n) for n in rank_by_score(scores, max_num) if 1 <= int(n) <= max_num]
    if len(top) < max_num // 2:
        raise ValueError("scor inutilizabil")
    seen = set(top)
    # numerele fara scor finit vin la coada, mai mare intai (regula canonica)
    top += [n for n in range(max_num, 0, -1) if n not in seen]
    return np.asarray(top, dtype=np.int16)


def _evaluate_dataset(name: str, n_rows: int) -> dict:
    """Pentru fiecare tinta eligibila: clasamentul complet al fiecarei metode."""
    warnings.filterwarnings("ignore")
    from loto_enterprise.benchmark.methods import METHODS

    dates, draws, max_num, _ = load(name, n_rows)
    methods = production_methods()
    prior = np.searchsorted(dates, dates, side="left")  # exclude toata ziua tintei
    targets = [i for i in range(len(draws)) if prior[i] >= WARMUP]
    ranks = np.zeros((len(targets), len(methods), max_num), dtype=np.int16)
    freq = METHODS["frequency"][0]
    for t, i in enumerate(targets):
        hist = draws[: prior[i]]
        for m, meth in enumerate(methods):
            try:
                sc = METHODS[meth][0](hist, max_num)
                ranks[t, m] = ranking(sc, max_num)
            except Exception:
                # scor inutilizabil -> fallback-ul de productie
                ranks[t, m] = ranking(freq(hist, max_num), max_num)
    return {
        "name": name,
        "methods": methods,
        "targets": targets,
        "prior": prior[targets],
        "dates": dates[targets],
        "draws": draws[targets],
        "ranks": ranks,
        "max_num": max_num,
        "n_rows": len(draws),
    }


def hits_matrix(ev: dict, k: int) -> np.ndarray:
    """hits[t, m] = |pool_k(metoda m, tinta t) ∩ extragere t|."""
    T, M, _ = ev["ranks"].shape
    out = np.zeros((T, M), dtype=np.int8)
    for t in range(T):
        drawn = set(int(x) for x in ev["draws"][t])
        top = ev["ranks"][t, :, :k]
        out[t] = [len(drawn.intersection(map(int, row))) for row in top]
    return out


def borda(rank_rows: np.ndarray, max_num: int) -> np.ndarray:
    """Media pozitiilor; egalitate -> numarul mai mare (regula canonica)."""
    pos = np.zeros(max_num + 1)
    for row in rank_rows:
        pos[row] += np.arange(len(row))
    nums = np.arange(1, max_num + 1)
    key = sorted(nums, key=lambda n: (pos[n], -n))
    return np.asarray(key, dtype=np.int16)


def candidate_hits(ev: dict, k: int, dev_best: str) -> dict[str, np.ndarray]:
    """Hiturile fiecarei reguli candidate pe fiecare tinta."""
    H = hits_matrix(ev, k).astype(float)
    T, M = H.shape
    names = ev["methods"]
    dates = ev["dates"]
    out = {c: np.zeros(T, dtype=np.int8) for c in CANDIDATES}
    fi = names.index("frequency")
    di = names.index(dev_best)
    drawn = [set(map(int, d)) for d in ev["draws"]]
    for t in range(T):
        # trecut vizibil: tinte anterioare cu data strict mai mica
        past = np.searchsorted(dates, dates[t], side="left")
        lo = max(0, past - META_WINDOW)
        if past > lo:
            mean = H[lo:past].mean(axis=0)
            # egalitate -> ordinea alfabetica (index mai mic)
            best = int(np.argmax(mean))
            top5 = np.argsort(-mean, kind="stable")[:META_TOP]
        else:
            best = fi
            top5 = np.array([fi])
        out["C1_meta_best"][t] = int(H[t, best])
        out["C3_dev_best"][t] = int(H[t, di])
        b_all = borda(ev["ranks"][t], ev["max_num"])[:k]
        out["C2_borda_all"][t] = len(drawn[t].intersection(map(int, b_all)))
        b5 = borda(ev["ranks"][t, top5], ev["max_num"])[:k]
        out["C4_meta_top5_borda"][t] = len(drawn[t].intersection(map(int, b5)))
    return out


def baseline(max_num: int, draw_n: int, k: int, thr: int) -> float:
    from scipy.stats import hypergeom

    return float(hypergeom(max_num, draw_n, k).sf(thr - 1))


def wilson(x: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = x / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj, run = {}, 0.0
    for i, (key, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        adj[key] = run
    return adj


def run(jobs: int) -> dict:
    from scipy.stats import binomtest

    reg = json.loads(REG_PATH.read_text(encoding="utf-8"))
    for name, info in reg["datasets"].items():
        h = prefix_hash(ROOT / DATASETS[name][0], info["rows"])
        if h != info["prefix_sha256"]:
            raise SystemExit(f"{name}: amprenta difera de preinregistrare")
    # punct de reluare per set de date (numai calcul; nu schimba regulile)
    import pickle

    ck = HERE / ".checkpoint"
    ck.mkdir(exist_ok=True)
    evs = {}
    for n in DATASETS:
        f = ck / f"{n}_{reg['datasets'][n]['prefix_sha256'][:16]}.pkl"
        if f.exists():
            evs[n] = pickle.loads(f.read_bytes())
    todo = [n for n in DATASETS if n not in evs]
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_evaluate_dataset, n, reg["datasets"][n]["rows"]): n for n in todo}
        for fu in as_completed(futs):
            n = futs[fu]
            evs[n] = fu.result()
            f = ck / f"{n}_{reg['datasets'][n]['prefix_sha256'][:16]}.pkl"
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(evs[n]))
            tmp.replace(f)
            print(f"checkpoint {n}", flush=True)
    evs = {n: evs[n] for n in DATASETS}

    # --- dezvoltare: cea mai buna metoda pe primele 70% (numai tinte dev) ---
    dev = {}
    for game in ("ro_649", "ro_540", "ro_joker"):
        ev = evs[game]
        cut = int(DEV_FRACTION * ev["n_rows"])
        is_dev = np.asarray(ev["targets"]) < cut
        dev[game] = {"cut_row": cut, "n_dev_targets": int(is_dev.sum()), "by_pool": {}}
        for k, thr in ((6, 3), (11, 3 if game != "ro_540" else 4), (12, 3)):
            H = hits_matrix(ev, k)[is_dev]
            rate = (H >= thr).mean(axis=0)
            mean = H.mean(axis=0)
            order = sorted(
                range(len(ev["methods"])), key=lambda m: (-rate[m], -mean[m], ev["methods"][m])
            )
            dev[game]["by_pool"][k] = {
                "threshold": thr,
                "best": ev["methods"][order[0]],
                "table": [
                    (ev["methods"][m], float(rate[m]), float(mean[m])) for m in order
                ],
            }
        # performanta candidatilor pe dev (informativ)
    results, pvals = {}, {}
    for cell, sets, k, thr in TEST_CELLS:
        src = DEV_SOURCE[cell.rsplit("_k", 1)[0]]
        dev_best = dev[src]["by_pool"][k]["best"]
        x = {c: 0 for c in CANDIDATES}
        n = 0
        max_num, draw_n = DATASETS[sets[0]][1], DATASETS[sets[0]][2]
        per_set = {}
        dev_info = {c: [0, 0] for c in CANDIDATES}
        for s in sets:
            ev = evs[s]
            ch = candidate_hits(ev, k, dev_best)
            if DATASETS[s][4] == "ro":
                mask = np.asarray(ev["targets"]) >= dev[s]["cut_row"]
            else:
                mask = np.ones(len(ev["targets"]), dtype=bool)
            per_set[s] = {"n": int(mask.sum())}
            for c in CANDIDATES:
                succ = int((ch[c][mask] >= thr).sum())
                x[c] += succ
                per_set[s][c] = succ
                dev_info[c][0] += int((ch[c][~mask] >= thr).sum())
                dev_info[c][1] += int((~mask).sum())
            n += int(mask.sum())
        p0 = baseline(max_num, draw_n, k, thr)
        results[cell] = {"n": n, "baseline": p0, "dev_best": dev_best, "per_set": per_set}
        for c in CANDIDATES:
            p = binomtest(x[c], n, p0, alternative="greater").pvalue
            pvals[(c, cell)] = p
            results[cell][c] = {
                "x": x[c],
                "rate": x[c] / n,
                "wilson95": wilson(x[c], n),
                "p_raw": p,
                "dev_x": dev_info[c][0],
                "dev_n": dev_info[c][1],
            }
    adj = holm(pvals)
    for (c, cell), p in adj.items():
        results[cell][c]["p_holm"] = p
        results[cell][c]["survives"] = p < ALPHA
    return {"dev": dev, "test": results, "n_tests": len(pvals)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["hashes", "run"])
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    if a.mode == "hashes":
        out = {}
        for name, (rel, *_rest) in DATASETS.items():
            n = len(_lines(ROOT / rel)) - 1
            out[name] = {"file": rel, "rows": n, "prefix_sha256": prefix_hash(ROOT / rel, n)}
        print(json.dumps(out, indent=2))
        return
    res = run(a.jobs)
    (HERE / "results_2026-10-02.json").write_text(
        json.dumps(res, indent=1, default=float), encoding="utf-8"
    )
    for cell, r in res["test"].items():
        for c in CANDIDATES:
            v = r[c]
            print(
                f"{cell:14s} {c:20s} n={r['n']:5d} {v['rate']:.4f} vs {r['baseline']:.4f}"
                f" p={v['p_raw']:.4f} holm={v['p_holm']:.4f}"
            )


if __name__ == "__main__":
    main()
