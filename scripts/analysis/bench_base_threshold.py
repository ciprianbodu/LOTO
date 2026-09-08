"""Bench pe pragul de restrangere a bazei: tine „pragul optim" out-of-sample?

Intrebarea: daca un bench alege pragul T (joaca doar numere <= T) care da cea
mai buna rata 3+ pe istoric, pragul acela ramane bun pe extrageri pe care
bench-ul NU le-a vazut?

Protocol (acelasi ca la orice metoda din §12 P3 — ferestre externe, nu
selectie dupa rezultat):
  1. TRAIN = primele 70% din extrageri; TEST = ultimele 30% (niciodata vazute
     la selectie, exact ca walk-forward-ul aplicatiei).
  2. Pe TRAIN: pentru fiecare prag candidat, estimam rata 3+ prin Monte Carlo
     peste pool-uri aleatoare de marime K trase din [1..T]. Alegem argmax.
  3. Pe TEST: masuram pragul ales SI baseline-ul nerestrans (T = max_num).
     Diferenta dintre ele e castigul aparent al selectiei.
  4. Control prin permutare de etichete — partea decisiva. Aplicam pe TOT
     istoricul aceeasi permutare aleatoare a numerelor 1..max_num si reluam
     protocolul. Permutarea pastreaza fiecare proprietate reala a datelor
     (numar de extrageri, structura temporala, distributia frecventelor) si
     distruge exclusiv legatura dintre „numar mic" si „numar cald". Sub
     ipoteza nula, castigul observat trebuie sa cada in distributia obtinuta
     asa. Daca da, selectia pragului a ales zgomot.

Rulare:
    .venv\\Scripts\\python scripts/analysis/bench_base_threshold.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loto_enterprise.core.draw_validation import valid_draw_matrix  # noqa: E402

GAMES = {
    "6/49": ("_ISTORIC/loto_6_49.csv", ["n1", "n2", "n3", "n4", "n5", "n6"], 6, 49),
    "5/40": ("_ISTORIC/loto_5_40.csv", ["n1", "n2", "n3", "n4", "n5"], 5, 40),
}

K = 10  # marimea pool-ului jucat
TARGET = 3  # rata masurata: cel putin TARGET numere prinse
N_POOLS = 400  # pool-uri aleatoare per prag (Monte Carlo)
TRAIN_FRAC = 0.70
SEEDS = (1, 2, 3, 4, 5)
N_PERM = 40  # permutari de etichete pentru distributia nula


def _as_bool_matrix(draws: np.ndarray, max_num: int) -> np.ndarray:
    """(n_draws, max_num+1) bool: [d, n] = numarul n a iesit la extragerea d."""
    out = np.zeros((draws.shape[0], max_num + 1), dtype=bool)
    rows = np.repeat(np.arange(draws.shape[0]), draws.shape[1])
    out[rows, draws.ravel().astype(int)] = True
    return out


def _rate(bools: np.ndarray, pools: np.ndarray) -> float:
    """Rata medie de >=TARGET hituri, peste toate pool-urile si extragerile."""
    return float((bools[:, pools].sum(axis=2) >= TARGET).mean())


def _sample_pools(rng: np.random.Generator, threshold: int) -> np.ndarray:
    """N_POOLS pool-uri de cate K numere distincte, trase uniform din [1..T]."""
    base = np.arange(1, threshold + 1)
    return np.array([rng.choice(base, size=K, replace=False) for _ in range(N_POOLS)])


def bench_thresholds(bools: np.ndarray, max_num: int, seed: int) -> dict:
    """Alege pragul pe TRAIN, il masoara pe TEST. Intoarce si baseline-ul."""
    rng = np.random.default_rng(seed)
    cut = int(len(bools) * TRAIN_FRAC)
    train, test = bools[:cut], bools[cut:]
    train_rates, test_rates = {}, {}
    for t in range(max(K, 20), max_num + 1):
        pools = _sample_pools(rng, t)
        train_rates[t] = _rate(train, pools)
        test_rates[t] = _rate(test, pools)
    best = max(train_rates, key=train_rates.get)
    return {
        "best_threshold": best,
        "train_rate_best": train_rates[best],
        "train_rate_full": train_rates[max_num],
        "test_rate_best": test_rates[best],
        "test_rate_full": test_rates[max_num],
        "gain_pp": (test_rates[best] - test_rates[max_num]) * 100,
    }


def run_game(label: str, draws: np.ndarray, max_num: int) -> None:
    bools = _as_bool_matrix(draws, max_num)
    cut = int(len(bools) * TRAIN_FRAC)
    print(f"\n{'=' * 78}")
    print(f"{label}  —  train {cut} extrageri, test {len(bools) - cut}, pool K={K}")
    print("=" * 78)
    print(
        f"{'seed':>5} | {'prag ales':>9} | {'TRAIN ales':>10} {'TRAIN tot':>9} "
        f"| {'TEST ales':>9} {'TEST tot':>8} | {'castig TEST':>11}"
    )
    results = [bench_thresholds(bools, max_num, s) for s in SEEDS]
    for s, r in zip(SEEDS, results):
        print(
            f"{s:>5} | {r['best_threshold']:>9} | {r['train_rate_best'] * 100:>9.2f}% "
            f"{r['train_rate_full'] * 100:>8.2f}% | {r['test_rate_best'] * 100:>8.2f}% "
            f"{r['test_rate_full'] * 100:>7.2f}% | {r['gain_pp']:>+10.2f}pp"
        )
    observed = float(np.mean([r["gain_pp"] for r in results]))
    chosen = sorted({int(r["best_threshold"]) for r in results})
    print(f"\n  Castig mediu out-of-sample: {observed:+.2f}pp | praguri alese: {chosen}")

    null = np.array(
        [
            bench_thresholds(
                _as_bool_matrix(
                    np.concatenate(
                        (
                            [0],
                            np.random.default_rng(1000 + i).permutation(
                                np.arange(1, max_num + 1)
                            ),
                        )
                    )[draws.astype(int)],
                    max_num,
                ),
                max_num,
                seed=7,
            )["gain_pp"]
            for i in range(N_PERM)
        ]
    )
    p_value = float((null >= observed).mean())
    print(
        f"  Distributie nula ({N_PERM} permutari de etichete): medie {null.mean():+.2f}pp, "
        f"sd {null.std():.2f}, interval [{null.min():+.2f}, {null.max():+.2f}]"
    )
    print(f"  p = {p_value:.3f}  ->  {'SEMNAL' if p_value < 0.01 else 'ZGOMOT'}")


def main() -> int:
    for label, (path, cols, draw_n, max_num) in GAMES.items():
        df = pd.read_csv(ROOT / path)
        draws, _ = valid_draw_matrix(df, cols, draw_n=draw_n, max_num=max_num)
        run_game(label, draws, max_num)

    print(
        "\nCitire: pragul optim arata un castig pozitiv pe TEST, dar acelasi castig\n"
        "apare si cand numerele sunt reetichetate aleator, unde legatura dintre\n"
        "'numar mic' si 'numar cald' nu mai exista. Marimea castigului nu iese din\n"
        "distributia nula, deci selectia pragului nu a gasit semnal.\n"
        "Vezi CLAUDE.md §6 (restrict_base_max)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
