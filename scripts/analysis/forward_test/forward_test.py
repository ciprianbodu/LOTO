"""Testul pe extrageri viitoare pentru `dmd_forecast` (preînregistrat 2026-09-27).

Evaluează regula fixată în `preregistration_2026-09-27.json` pe extragerile 6/49
cu data STRICT după data înregistrării. Pentru fiecare extragere nouă, pool-ul se
calculează numai din extragerile cu dată anterioară (cele din aceeași zi sunt
excluse), cu copia înghețată `frozen_dmd`. Decizia vine dintr-un test secvențial
Wald (SPRT) pe fiecare pool, cu pragurile scrise în fișierul de înregistrare.

Rulare din rădăcina proiectului:
    python scripts/analysis/forward_test/forward_test.py
Scriptul doar citește istoricul; nu scrie nimic în aplicație.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

import frozen_dmd  # noqa: E402

REGISTRATION = HERE / "preregistration_2026-09-27.json"


def _parse_date(text: str) -> dt.date:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"dată necunoscută în istoric: {text!r}")


def load_history(csv_path: Path) -> tuple[list[dt.date], np.ndarray]:
    """Istoricul 6/49 în ordine cronologică (ordinea din fișier la aceeași dată)."""
    dates, rows = [], []
    for line in csv_path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        dates.append(_parse_date(parts[0]))
        rows.append([int(x) for x in parts[1:7]])
    order = sorted(range(len(dates)), key=lambda i: (dates[i], i))
    return [dates[i] for i in order], np.asarray([rows[i] for i in order], dtype=int)


def history_hash(dates, draws, count: int) -> str:
    h = hashlib.sha256()
    for d, r in zip(dates[:count], draws[:count]):
        h.update(f"{d.isoformat()}|{','.join(map(str, sorted(r)))}\n".encode())
    return h.hexdigest()


def file_hash(path: Path) -> str:
    """SHA-256 pe conținut cu terminații LF: un checkout Windows (autocrlf) dă CRLF."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class Sprt:
    """Test secvențial Wald pentru o proporție: H0 p=p0 contra H1 p=p1."""

    def __init__(self, p0: float, p1: float, alpha: float, beta: float):
        self.p0, self.p1 = p0, p1
        self.upper = math.log((1 - beta) / alpha)
        self.lower = math.log(beta / (1 - alpha))
        self.win = math.log(p1 / p0)
        self.lose = math.log((1 - p1) / (1 - p0))
        self.llr = 0.0
        self.n = self.hits = 0
        self.decided_at: int | None = None
        self.decision = "continuă"

    def add(self, success: bool) -> None:
        self.n += 1
        self.hits += int(success)
        if self.decided_at is not None:
            return
        self.llr += self.win if success else self.lose
        if self.llr >= self.upper:
            self.decision, self.decided_at = "avantaj confirmat", self.n
        elif self.llr <= self.lower:
            self.decision, self.decided_at = "fără avantaj (respins)", self.n


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def evaluate(reg: dict, dates, draws) -> dict:
    start = dt.date.fromisoformat(reg["start_after_date"])
    max_num = int(reg["max_num"])
    target = int(reg["target_hits"])
    tests = {
        int(k): Sprt(v["p0"], v["p1"], v["alpha"], v["beta"])
        for k, v in reg["sprt"].items()
    }
    ledger = []
    for i, day in enumerate(dates):
        if day <= start:
            continue
        prior = sum(1 for d in dates if d < day)  # fără extrageri din aceeași zi
        rk = frozen_dmd.ranking(draws[:prior], max_num)
        drawn = set(int(x) for x in draws[i])
        row = {"date": day.isoformat(), "drawn": sorted(drawn)}
        for k, test in tests.items():
            pool = sorted(rk[:k])
            hits = len(drawn & set(pool))
            test.add(hits >= target)
            row[f"pool{k}"] = pool
            row[f"hits{k}"] = hits
        ledger.append(row)
        if len(ledger) >= int(reg["max_forward_draws"]):
            break
    nxt = frozen_dmd.ranking(draws, max_num)
    return {
        "n": len(ledger),
        "tests": tests,
        "ledger": ledger,
        "next_pools": {k: sorted(nxt[:k]) for k in tests},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default=str(ROOT / "_ISTORIC" / "loto_6_49.csv"))
    ap.add_argument("--json", action="store_true", help="rezultat complet în JSON")
    args = ap.parse_args(argv)

    reg = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    dates, draws = load_history(Path(args.csv))
    warnings = []
    if file_hash(HERE / "frozen_dmd.py") != reg["frozen_method_sha256"]:
        warnings.append("copia înghețată a metodei a fost modificată după înregistrare")
    if history_hash(dates, draws, reg["history_rows_at_registration"]) != reg["history_sha256"]:
        warnings.append("istoricul de dinaintea înregistrării s-a schimbat (corecturi în CSV)")

    res = evaluate(reg, dates, draws)
    if args.json:
        out = {
            "warnings": warnings,
            "n_forward_draws": res["n"],
            "tests": {
                k: {"hits": t.hits, "n": t.n, "llr": t.llr, "decision": t.decision,
                    "decided_at": t.decided_at, "p0": t.p0, "p1": t.p1}
                for k, t in res["tests"].items()
            },
            "next_pools": res["next_pools"],
            "ledger": res["ledger"],
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"Test pe extrageri viitoare — {reg['method']} pe Loto 6/49")
    print(f"Extrageri evaluate (după {reg['start_after_date']}): {res['n']}")
    for w in warnings:
        print(f"ATENȚIE: {w}")
    for k, t in res["tests"].items():
        lo, hi = wilson(t.hits, t.n)
        rate = f"{100 * t.hits / t.n:.2f}%" if t.n else "—"
        print(
            f"Pool {k}: {t.hits}/{t.n} extrageri cu {reg['target_hits']}+ = {rate} "
            f"(interval 95%: {100 * lo:.1f}–{100 * hi:.1f}%) | aleator {100 * t.p0:.2f}%, "
            f"afirmat {100 * t.p1:.2f}% | decizie: {t.decision}"
            + (f" (după {t.decided_at} extrageri)" if t.decided_at else "")
        )
    for k, pool in res["next_pools"].items():
        print(f"Pool {k} pentru următoarea extragere: {pool}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
