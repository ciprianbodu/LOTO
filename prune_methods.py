"""Elimină (legendează) cele mai slabe 50% metode, SEPARAT pe CPU și GPU,
pe baza ultimelor rezultate din benchmark (bench_results/folds.csv).

  python prune_methods.py            # DRY-RUN: arată ce ar dezactiva
  python prune_methods.py --apply    # scrie în disabled_methods.json (merge-only)

Metrica de performanță: rata 4+ (rate_4plus_kN) dacă există în folds.csv,
altfel avg_hits_topk. Comparăm metodele REALE (is_random=False) între ele,
în interiorul categoriei lor (CPU vs GPU), și dezactivăm jumătatea inferioară.

Baseline-urile (random, frequency) sunt PROTEJATE — decizia are nevoie de ele.
Merge-only: nu reactivează nimic. Metodele noi nu sunt afectate.

⚠️ NOTĂ STATISTICĂ: pe loterie aleatoare diferențele dintre metode sunt în mare
parte ZGOMOT. „Bottom 50%" reflectă acest bench; rulează tool-ul pe un bench
COMPLET (toate metodele), nu pe unul parțial/vechi.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROTECTED = {"random", "frequency"}


def _is_gpu(method: str, family: str = "") -> bool:
    """Aceeași clasificare ca runner-ul (_is_gpu_fam_global)."""
    m = (method or "").lower()
    f = (family or "").lower()
    return (m.startswith("torch_") or m.startswith("ens_torch") or m.endswith("_gpu")
            or f.startswith("nf-") or f.startswith("foundation") or f == "ssm"
            or f.startswith("torch"))


def _method_score(sub: pd.DataFrame) -> float:
    """Scor de performanță per metodă: max rata 4+ pe pool-uri, altfel avg_hits."""
    r4 = [c for c in sub.columns if c.startswith("rate_4plus")]
    if r4:
        vals = sub[r4].to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size:
            return float(np.nanmax(vals))
    if "avg_hits_topk" in sub.columns:
        return float(sub["avg_hits_topk"].mean())
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Legendează cele mai slabe 50% metode (CPU/GPU)")
    ap.add_argument("--folds", default="bench_results/folds.csv")
    ap.add_argument("--apply", action="store_true", help="scrie în disabled_methods.json")
    args = ap.parse_args()

    fp = Path(args.folds)
    if not fp.exists():
        print(f"[prune] lipsește {fp} — rulează un benchmark întâi.", file=sys.stderr)
        return 1
    df = pd.read_csv(fp)
    real = df[df["is_random"] == False] if "is_random" in df.columns else df  # noqa: E712

    # familie per metodă (din registry, dacă se poate importa)
    fam_map: dict = {}
    try:
        from loto_enterprise.benchmark.methods import method_meta
        for m in real["method"].unique():
            try:
                fam_map[m] = method_meta(m).get("family", "")
            except Exception:  # noqa: BLE001
                fam_map[m] = ""
    except Exception:  # noqa: BLE001
        pass

    rows = []
    for m, sub in real.groupby("method"):
        rows.append((m, _method_score(sub), _is_gpu(m, fam_map.get(m, ""))))
    perf = pd.DataFrame(rows, columns=["method", "score", "is_gpu"])

    to_disable = []
    for is_gpu, grp in perf.groupby("is_gpu"):
        cat = "GPU" if is_gpu else "CPU"
        cand = grp[~grp["method"].isin(PROTECTED)].sort_values("score")
        n_cut = len(cand) // 2  # jumătatea inferioară (floor)
        cut = cand.head(n_cut)["method"].tolist()
        print(f"\n=== {cat}: {len(grp)} metode, protejate {sorted(set(grp['method'])&PROTECTED)}, "
              f"dezactivez {n_cut} ===")
        for _, r in cand.iterrows():
            mark = "❌ OFF" if r["method"] in cut else "   keep"
            print(f"  {mark}  {r['method']:24s} scor={r['score']:.4f}")
        to_disable.extend(cut)

    print(f"\nTOTAL de legendat: {len(to_disable)} metode → {sorted(to_disable)}")
    if not to_disable:
        print("(nimic de dezactivat)")
        return 0

    if args.apply:
        from loto_enterprise.benchmark.disabled import add_disabled
        final = add_disabled(to_disable, reason=f"prune 50% din {fp.name}")
        print(f"\n✅ APLICAT. Blacklist permanent acum: {len(final)} metode.")
    else:
        print("\n(DRY-RUN — adaugă --apply ca să scrii în disabled_methods.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
