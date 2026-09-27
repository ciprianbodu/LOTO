"""Testul pe extrageri viitoare pentru `dmd_forecast` (preînregistrat 2026-09-27).

Evaluează regula fixată în `preregistration_2026-09-27.json` pe extragerile 6/49
cu data STRICT după data înregistrării. Pentru fiecare extragere nouă, pool-ul se
calculează numai din extragerile cu dată anterioară (cele din aceeași zi sunt
excluse), cu copia înghețată `frozen_dmd`. Decizia vine dintr-un test secvențial
Wald (SPRT) pe fiecare pool, cu pragurile scrise în fișierul de înregistrare; un
pool nedecis la plafonul de extrageri primește testul binomial exact final.

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
CONFIRMED = "avantaj confirmat"
REJECTED = "fără avantaj (respins)"


def _parse_date(text: str) -> dt.date:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"dată necunoscută în istoric: {text!r}")


def load_history(
    csv_path: Path, max_num: int = 49, draw_n: int = 6
) -> tuple[list[dt.date], np.ndarray]:
    """Istoricul în ordine cronologică (ordinea din fișier la aceeași dată).

    Un rând nevalid sau o extragere repetată oprește scriptul: un rezultat
    calculat pe un fișier stricat ar număra în test extrageri care nu au avut loc.
    """
    dates, rows, seen = [], [], set()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        day = _parse_date(parts[0])
        try:
            nums = [int(x) for x in parts[1 : 1 + draw_n]]
        except ValueError:
            raise ValueError(f"{csv_path.name}, rândul {lineno}: numere nevalide {line!r}")
        if (
            len(nums) != draw_n
            or len(set(nums)) != draw_n
            or not all(1 <= n <= max_num for n in nums)
        ):
            raise ValueError(f"{csv_path.name}, rândul {lineno}: extragere nevalidă {line!r}")
        key = (day, tuple(sorted(nums)))
        if key in seen:
            raise ValueError(f"{csv_path.name}, rândul {lineno}: extragere repetată {line!r}")
        seen.add(key)
        dates.append(day)
        rows.append(nums)
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
        self.p0, self.p1, self.alpha = p0, p1, alpha
        self.upper = math.log((1 - beta) / alpha)
        self.lower = math.log(beta / (1 - alpha))
        self.win = math.log(p1 / p0)
        self.lose = math.log((1 - p1) / (1 - p0))
        self.llr = 0.0
        self.n = self.hits = 0
        self.decided_at: int | None = None
        self.decision = "continuă"
        self.final_p: float | None = None

    def add(self, success: bool) -> None:
        self.n += 1
        self.hits += int(success)
        if self.decided_at is not None:
            return
        self.llr += self.win if success else self.lose
        if self.llr >= self.upper:
            self.decision, self.decided_at = CONFIRMED, self.n
        elif self.llr <= self.lower:
            self.decision, self.decided_at = REJECTED, self.n

    def final_test(self) -> None:
        """Pool nedecis la plafon: test binomial exact unilateral, cu același α."""
        if self.decided_at is not None:
            return
        from scipy.stats import binomtest

        self.final_p = float(binomtest(self.hits, self.n, self.p0, alternative="greater").pvalue)
        self.decided_at = self.n
        if self.final_p < self.alpha:
            self.decision = f"{CONFIRMED} (test final, p={self.final_p:.4f})"
        else:
            self.decision = f"fără avantaj (test final, p={self.final_p:.4f})"


def verdict(tests: dict[int, Sprt]) -> str:
    """Regula preînregistrată: confirmată dacă cel puțin un pool e confirmat,
    respinsă dacă toate pool-urile sunt respinse."""
    decisions = [t.decision for t in tests.values()]
    if any(d.startswith(CONFIRMED) for d in decisions):
        return "metodă confirmată"
    if all(d.startswith("fără avantaj") for d in decisions):
        return "metodă respinsă"
    return "în curs"


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def integrity_warnings(reg: dict, dates, draws, method_file: Path) -> list[str]:
    """Ce s-a schimbat față de înregistrare: regula, istoricul vechi, rânduri târzii."""
    warnings = []
    if file_hash(method_file) != reg["frozen_method_sha256"]:
        warnings.append("copia înghețată a metodei a fost modificată după înregistrare")
    n_reg = int(reg["history_rows_at_registration"])
    last_reg = dt.date.fromisoformat(reg["history_last_date_at_registration"])
    start = dt.date.fromisoformat(reg["start_after_date"])
    upto = sum(1 for d in dates if d <= last_reg)
    if upto != n_reg:
        warnings.append(
            f"istoricul de până la {last_reg.isoformat()} are {upto} extrageri, "
            f"nu {n_reg} ca la înregistrare"
        )
    elif history_hash(dates, draws, n_reg) != reg["history_sha256"]:
        warnings.append("istoricul de dinaintea înregistrării s-a schimbat (corecturi în CSV)")
    late = sorted({d.isoformat() for d in dates if last_reg < d < start})
    if late:
        warnings.append(
            "extrageri cu data între ultima extragere înregistrată și data de start, "
            f"când nu era programată nicio extragere: {', '.join(late)}"
        )
    return warnings


def evaluate(reg: dict, dates, draws) -> dict:
    start = dt.date.fromisoformat(reg["start_after_date"])
    max_num = int(reg["max_num"])
    target = int(reg["target_hits"])
    cap = int(reg["max_forward_draws"])
    tests = {
        int(k): Sprt(v["p0"], v["p1"], v["alpha"], v["beta"])
        for k, v in reg["sprt"].items()
    }
    ledger = []
    for i, day in enumerate(dates):
        if day <= start:
            continue
        if len(ledger) >= cap:
            break
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
    capped = len(ledger) >= cap
    if capped:
        for test in tests.values():
            test.final_test()
    last = dates[-1] if dates else None
    if capped:
        next_pools, next_note = {}, f"plafonul de {cap} extrageri a fost atins: testul s-a încheiat"
    elif last is None or last < start:
        next_pools = {}
        next_note = (
            f"prima extragere evaluată este prima cu data după {start.isoformat()}; "
            "pool-ul ei se poate calcula după ce istoricul ajunge la această dată"
        )
    else:
        nxt = frozen_dmd.ranking(draws, max_num)
        next_pools = {k: sorted(nxt[:k]) for k in tests}
        next_note = f"pentru următoarea extragere, cu data după {last.isoformat()}"
    return {
        "n": len(ledger),
        "capped": capped,
        "tests": tests,
        "verdict": verdict(tests),
        "ledger": ledger,
        "next_pools": next_pools,
        "next_note": next_note,
    }


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default=str(ROOT / "_ISTORIC" / "loto_6_49.csv"))
    ap.add_argument("--json", action="store_true", help="rezultat complet în JSON")
    args = ap.parse_args(argv)

    reg = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    dates, draws = load_history(Path(args.csv), int(reg["max_num"]), int(reg["draw_n"]))
    warnings = integrity_warnings(reg, dates, draws, HERE / "frozen_dmd.py")
    res = evaluate(reg, dates, draws)
    if args.json:
        out = {
            "warnings": warnings,
            "n_forward_draws": res["n"],
            "capped": res["capped"],
            "verdict": res["verdict"],
            "tests": {
                k: {"hits": t.hits, "n": t.n, "llr": t.llr, "decision": t.decision,
                    "decided_at": t.decided_at, "final_p": t.final_p, "p0": t.p0, "p1": t.p1}
                for k, t in res["tests"].items()
            },
            "next_pools": res["next_pools"],
            "next_note": res["next_note"],
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
    print(f"Verdict: {res['verdict']}")
    if res["next_pools"]:
        for k, pool in res["next_pools"].items():
            print(f"Pool {k} {res['next_note']}: {pool}")
    else:
        print(f"Notă: {res['next_note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
