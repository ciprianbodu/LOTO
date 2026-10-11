"""Testul pe extrageri viitoare pentru `ens_win100+winall+ewma0.01`, Joker Urna 1
(preînregistrat 2026-10-11).

Evaluează regula fixată în `preregistration_2026-10-11.json` pe extragerile Joker
cu data STRICT după data înregistrării. Pentru fiecare extragere nouă, pool-ul de
8 numere se calculează numai din extragerile cu dată anterioară (cele din aceeași
zi sunt excluse), cu copia înghețată `frozen_ens`. Reușita = cel puțin 3 dintre
cele 5 numere extrase din Urna 1 sunt în pool. Decizia vine dintr-un test
secvențial Wald (SPRT), cu pragurile din fișierul de înregistrare; nedecis la
plafonul de extrageri, pool-ul primește testul binomial exact final.

Mecanica SPRT, verdictul, amprentele și avertismentele de integritate sunt cele
ale testului din 2026-09-27 (`scripts/analysis/forward_test/forward_test.py`),
refolosite neschimbate.

Rulare din rădăcina proiectului:
    python scripts/analysis/forward_test_joker/forward_test_joker.py
Scriptul doar citește istoricul; nu scrie nimic în aplicație. Numele
fișierului nu se termină în `_test.py`, ca pytest să nu-l ia drept modul de
teste cu același nume ca evaluatorul din 2026-09-27.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DMD = ROOT / "scripts" / "analysis" / "forward_test"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ft = _load("forward_test_dmd", DMD / "forward_test.py")
frozen_ens = _load("frozen_ens", HERE / "frozen_ens.py")

REGISTRATION = HERE / "preregistration_2026-10-11.json"
FROZEN = HERE / "frozen_ens.py"
JOKER_MAX = 20


def load_history(csv_path: Path, max_num: int = 45, draw_n: int = 5):
    """Urna 1 în ordine cronologică (ordinea din fișier la aceeași dată).

    Rândul are data, cele `draw_n` numere ale Urnei 1 și numărul Joker (1..20),
    care nu intră în test. Un rând nevalid sau o extragere repetată oprește
    scriptul."""
    dates, rows, seen = [], [], set()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        day = ft._parse_date(parts[0])
        fields = [p for p in parts[1:] if p]
        if len(fields) != draw_n + 1:
            raise ValueError(
                f"{csv_path.name}, rândul {lineno}: extragere nevalidă, "
                f"{len(fields)} valori în loc de {draw_n + 1}: {line!r}"
            )
        try:
            nums = [int(x) for x in fields[:draw_n]]
            joker = int(fields[draw_n])
        except ValueError:
            raise ValueError(f"{csv_path.name}, rândul {lineno}: numere nevalide {line!r}")
        if (
            len(set(nums)) != draw_n
            or not all(1 <= n <= max_num for n in nums)
            or not 1 <= joker <= JOKER_MAX
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


def fingerprints() -> dict[str, str]:
    """Amprentele (LF) ale regulii, ale evaluatorului și ale înregistrării."""
    return {
        name: ft.file_hash(HERE / name)
        for name in (FROZEN.name, "forward_test_joker.py", REGISTRATION.name)
    }


def evaluate(reg: dict, dates, draws) -> dict:
    start = dt.date.fromisoformat(reg["start_after_date"])
    max_num = int(reg["max_num"])
    target = int(reg["target_hits"])
    cap = int(reg["max_forward_draws"])
    tests = {
        int(k): ft.Sprt(v["p0"], v["p1"], v["alpha"], v["beta"])
        for k, v in reg["sprt"].items()
    }
    ledger = []
    for i, day in enumerate(dates):
        if day <= start:
            continue
        if len(ledger) >= cap:
            break
        prior = sum(1 for d in dates if d < day)  # fără extrageri din aceeași zi
        rk = frozen_ens.ranking(dates[:prior], draws[:prior], max_num)
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
    final = ft.verdict(tests)
    if capped:
        next_pools = {}
        next_note = f"plafonul de {ft.count(cap, 'extrageri')} a fost atins: testul s-a încheiat"
    elif final != "în curs":
        next_pools = {}
        next_note = f"verdictul este final ({final}); extragerile următoare nu îl mai schimbă"
    elif last is None or last < start:
        next_pools = {}
        next_note = (
            f"prima extragere evaluată este prima cu data după {start.isoformat()}; "
            "pool-ul ei se poate calcula după ce istoricul ajunge la această dată"
        )
    else:
        nxt = frozen_ens.ranking(dates, draws, max_num)
        next_pools = {k: sorted(nxt[:k]) for k in tests}
        next_note = f"pentru următoarea extragere, cu data după {last.isoformat()}"
    return {
        "n": len(ledger),
        "capped": capped,
        "tests": tests,
        "verdict": final,
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
    ap.add_argument("--csv", default=str(ROOT / "_ISTORIC" / "joker.csv"))
    ap.add_argument("--json", action="store_true", help="rezultat complet în JSON")
    args = ap.parse_args(argv)

    reg = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    dates, draws = load_history(Path(args.csv), int(reg["max_num"]), int(reg["draw_n"]))
    warnings = ft.integrity_warnings(reg, dates, draws, FROZEN)
    res = evaluate(reg, dates, draws)
    if args.json:
        out = {
            "fingerprints": fingerprints(),
            "warnings": warnings,
            "n_forward_draws": res["n"],
            "capped": res["capped"],
            "verdict": res["verdict"],
            "tests": {
                k: {"hits": t.hits, "n": t.n, "llr": t.llr, "decision": t.decision,
                    "decided_at": t.decided_at, "hits_at_decision": t.hits_at_decision,
                    "final_p": t.final_p, "p0": t.p0, "p1": t.p1}
                for k, t in res["tests"].items()
            },
            "next_pools": res["next_pools"],
            "next_note": res["next_note"],
            "ledger": res["ledger"],
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"Test pe extrageri viitoare — {reg['method']} pe Joker Urna 1")
    print(f"Extrageri evaluate (după {reg['start_after_date']}): {res['n']}")
    for w in warnings:
        print(f"ATENȚIE: {w}")
    for k, t in res["tests"].items():
        lo, hi = ft.wilson(t.hits, t.n)
        rate = f"{100 * t.hits / t.n:.2f}%" if t.n else "—"
        print(
            f"Pool {k}: {t.hits}/{t.n} extrageri cu {reg['target_hits']}+ = {rate} "
            f"(interval 95%: {100 * lo:.1f}–{100 * hi:.1f}%) | aleator {100 * t.p0:.2f}%, "
            f"afirmat {100 * t.p1:.2f}% | decizie: {t.decision}"
            + (
                f" (după {ft.count(t.decided_at, 'extrageri')}, "
                f"{ft.count(t.hits_at_decision, 'reușite')} la decizie)"
                if t.decided_at
                else ""
            )
        )
    print(f"Verdict: {res['verdict']}")
    if res["next_pools"]:
        for k, pool in res["next_pools"].items():
            print(f"Pool {k} {res['next_note']}: {pool}")
    else:
        print(f"Notă: {res['next_note']}")
    print("Amprente SHA-256 (LF): " + ", ".join(f"{k} {v[:16]}" for k, v in fingerprints().items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
