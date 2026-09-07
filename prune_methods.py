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
    """Scor de performanță per metodă: MEDIA pe (joc, fereastră) a ratei 4+ maxime
    pe pool-uri, altfel avg_hits.

    `sub` acoperă TOATE jocurile și TOATE ferestrele simultan (tool-ul nu
    filtrează pe `game`, spre deosebire de decision.py, care mereu izolează pe
    joc înainte de orice comparație). Un `nanmax` pe tot blocul 2D (cum era
    înainte) alegea o SINGURĂ celulă norocoasă — un joc, o fereastră, un pool —
    ca „scor" al metodei, nu o valoare reprezentativă; o metodă mediocră
    supraviețuia pe o celulă zgomotoasă, iar una decentă putea fi legendată
    PERMANENT (disabled_methods.json e merge-only, ireversibil) doar fiindcă
    celula ei cea mai bună nu ajungea la nivelul celulei norocoase a alteia.
    Maximul PE RAND (pe coloanele de pool-size, ``kN``) rămâne corect — pool-size
    e o alegere de design, nu zgomot, o metodă poate avea legitim un pool optim
    diferit — dar mediem acele maxime PE RÂNDURI (joc × fereastră), nu alegem
    cel mai norocos rând.
    """
    r4 = [c for c in sub.columns if c.startswith("rate_4plus")]
    if r4:
        row_maxes = []
        for row in sub[r4].to_numpy(dtype=float):
            finite = row[np.isfinite(row)]
            if finite.size:
                row_maxes.append(float(finite.max()))
        if row_maxes:
            return float(np.mean(row_maxes))
    if "avg_hits_topk" in sub.columns:
        return float(sub["avg_hits_topk"].mean())
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Legendează metodele slabe (CPU/GPU).\n"
                    "  --top N  : păstrează doar top-N per categorie, dezactivează restul.\n"
                    "  (fără --top): dezactivează jumătatea inferioară (comportament vechi).")
    ap.add_argument("--folds", default="bench_results/folds.csv")
    ap.add_argument("--apply", action="store_true", help="scrie în disabled_methods.json")
    ap.add_argument("--top", type=int, default=None,
                    help="păstrează top-N metode per categorie (CPU/GPU), dezactivează restul")
    ap.add_argument("--force-incomplete", action="store_true",
                    help="permite --apply chiar dacă folds.csv nu acoperă toate jocurile "
                         "(altfel un bench parțial/vechi ar lua o decizie IREVERSIBILĂ)")
    args = ap.parse_args()

    fp = Path(args.folds)
    if not fp.exists():
        print(f"[prune] lipsește {fp} — rulează un benchmark întâi.", file=sys.stderr)
        return 1
    df = pd.read_csv(fp)
    real = df[df["is_random"] == False] if "is_random" in df.columns else df  # noqa: E712

    # Gardă anti-decizie-parțială: disabled_methods.json e merge-only, ireversibil
    # (§4.3) — un folds.csv dintr-un Re-Bench --quick/--methods sau vechi/parțial
    # nu are voie să tombstoneze permanent o metodă fără avertisment explicit.
    if args.apply and "game" in real.columns:
        try:
            from loto_enterprise.benchmark.runner import discover_games
            expected_games = {g.key for g in discover_games()}
        except Exception:  # noqa: BLE001
            expected_games = set()
        seen_games = set(real["game"].unique())
        missing = expected_games - seen_games
        if missing and not args.force_incomplete:
            print(f"[prune] EROARE: {fp} nu acoperă toate jocurile — lipsesc {sorted(missing)}.",
                  file=sys.stderr)
            print("[prune] O decizie PERMANENTĂ (disabled_methods.json e ireversibil) pe date "
                  "parțiale ar putea legenda greșit o metodă care doar nu a fost testată pe acel "
                  "joc. Rulează un Re-Bench complet, sau adaugă --force-incomplete dacă e voit.",
                  file=sys.stderr)
            return 2

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
        cand = grp[~grp["method"].isin(PROTECTED)].sort_values("score", ascending=False)
        protected_in_grp = sorted(set(grp["method"]) & PROTECTED)

        if args.top is not None:
            # Păstrează top-N, dezactivează restul
            keep = set(cand.head(args.top)["method"].tolist())
            cut = cand[~cand["method"].isin(keep)]["method"].tolist()
            print(f"\n=== {cat}: {len(grp)} metode, protejate {protected_in_grp}, "
                  f"păstrez top-{args.top}, dezactivez {len(cut)} ===")
        else:
            # Comportament vechi: jumătatea inferioară
            cand = cand.sort_values("score")
            n_cut = len(cand) // 2
            cut = cand.head(n_cut)["method"].tolist()
            keep = set(cand["method"].tolist()) - set(cut)
            print(f"\n=== {cat}: {len(grp)} metode, protejate {protected_in_grp}, "
                  f"dezactivez {n_cut} (bottom 50%) ===")

        for _, r in cand.sort_values("score", ascending=False).iterrows():
            mark = "❌ OFF" if r["method"] in cut else "   keep"
            print(f"  {mark}  {r['method']:24s} scor={r['score']:.4f}")
        to_disable.extend(cut)

    print(f"\nTOTAL de legendat: {len(to_disable)} metode → {sorted(to_disable)}")
    if not to_disable:
        print("(nimic de dezactivat)")
        return 0

    if args.apply:
        from loto_enterprise.benchmark.disabled import add_disabled
        reason = (f"prune top-{args.top} din {fp.name}" if args.top
                  else f"prune 50% din {fp.name}")
        final = add_disabled(to_disable, reason=reason)
        print(f"\n✅ APLICAT. Blacklist permanent acum: {len(final)} metode.")
    else:
        print("\n(DRY-RUN — adaugă --apply ca să scrii în disabled_methods.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
