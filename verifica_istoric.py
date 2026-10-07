"""Verifica istoricele din _ISTORIC inainte de commit-ul automat al lansatorului.

`scripts/launcher_git.ps1 -Mode PushHistory` trimite aici CSV-urile care au
trecut de verificarea git (numai randuri adaugate). Fiecare trebuie sa fie
istoricul unui joc din registrul loteriilor, cu antetul geometriei lui, date
ZZ-LL-AAAA si extrageri valide dupa `valid_draw_matrix`, inclusiv a doua urna,
pe tot fisierul. Istoricele versionate trec toate; un fisier respins ramane
local, necomis.

Iesire: cate o linie pe stdout pentru fiecare argument, in aceeasi ordine,
`ok` sau motivul refuzului, numai ASCII. Cod 0 cand verificarea a rulat; orice
alt cod inseamna ca nu a rulat, iar lansatorul nu comite nimic.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys
from pathlib import Path

import pandas as pd

from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.lotteries import GAMES


def _shown(cells: list[str]) -> str:
    text = ",".join(cells)
    return text if len(text) <= 60 else text[:57] + "..."


def check_history(path: str, root: Path | None = None) -> str | None:
    """Motivul refuzului sau None. `path` e relativ la radacina, ca in git."""
    lottery = next((lot for lot in GAMES if lot.csv == path), None)
    if lottery is None:
        return "nu e istoricul unui joc din registrul loteriilor"
    geo = lottery.geo
    main = [f"n{i}" for i in range(1, geo.draw_n + 1)]
    second = list(geo.second.columns) if geo.second else []
    header = ["date", *main, *second]
    try:
        text = ((root or Path.cwd()) / path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return "nu e text UTF-8"
    except OSError as exc:
        return f"nu se poate citi: {exc.strerror or type(exc).__name__}"
    reader = csv.reader(io.StringIO(text, newline=""))
    rows: list[list[str]] = []
    lines: list[int] = []
    try:
        first = next(reader, None)
        if first != header:
            got = _shown(first) if first else "lipsa"
            return f"antet {got}, astept {','.join(header)}"
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                return (
                    f"randul {reader.line_num}: astept {len(header)} valori separate "
                    f"prin virgula, am gasit {len(row)}: {_shown(row)}"
                )
            try:
                dt.datetime.strptime(row[0], "%d-%m-%Y")
            except ValueError:
                return f"randul {reader.line_num}: data nu e ZZ-LL-AAAA: {_shown(row)}"
            rows.append(row)
            lines.append(reader.line_num)
    except csv.Error as exc:
        return f"CSV ilizibil la randul {reader.line_num}: {exc}"
    df = pd.DataFrame(rows, columns=header)
    _, valid = valid_draw_matrix(df, main, draw_n=geo.draw_n, max_num=geo.max_n)
    if geo.second:
        _, valid_second = valid_draw_matrix(
            df, second, draw_n=geo.second.draw_n, max_num=geo.second.max_n
        )
        valid &= valid_second
    if not valid.all():
        bad = int(valid.argmin())
        return (
            f"randul {lines[bad]}: numere invalide pentru {geo.name}: "
            f"{_shown(rows[bad])}"
        )
    return None


def main(argv: list[str]) -> int:
    for path in argv:
        reason = check_history(path) or "ok"
        # Citit de PowerShell cu pagina de cod a consolei: o linie, numai ASCII.
        reason = " ".join(reason.split())
        print(reason.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
