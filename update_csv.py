"""update_csv.py — Detectează și adaugă extrageri noi în CSV-urile din _ISTORIC/.

Verifică DOAR prima URL (extrageri recente) pentru fiecare joc — rapid, fără să
rescrie tot istoricul. Adaugă la final rândurile care lipsesc, scriere atomică.

Rulare:
    python update_csv.py               # verifică toate jocurile
    python update_csv.py --force       # verifică chiar dacă CSV pare la zi
    python update_csv.py --verbose     # afișează detalii extra

Exit code: 0 mereu (best-effort) — eroare de rețea / parsing nu blochează pornirea.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
from datetime import date, datetime

# Consola Windows e cp1252 by default -> diacriticele (ă, î) arunca UnicodeEncodeError.
# Reconfiguram stdout/stderr pe UTF-8 cu fallback 'replace' ca sa nu mai crape.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_FORCE   = "--force"   in sys.argv
_VERBOSE = "--verbose" in sys.argv

# URL-ul RECENT (primul) e de ajuns pentru update — extrage ultimele luni.
GAME_CONFIGS = {
    "loto_6_49": {
        "display_name": "Loto 6/49",
        "recent_url": "https://www.loto49.ro/arhiva-loto49.php",
        "num_main": 6,
        "has_joker": False,
        "csv_name": "loto_6_49.csv",
    },
    "joker": {
        "display_name": "Joker",
        "recent_url": "https://www.loto49.ro/arhiva-joker.php",
        "num_main": 5,
        "has_joker": True,
        "csv_name": "joker.csv",
    },
    "loto_5_40": {
        "display_name": "Loto 5/40",
        "recent_url": "https://www.loto49.ro/arhiva-superloto.php",
        "num_main": 6,
        "has_joker": False,
        "csv_name": "loto_5_40.csv",
    },
}

TIMEOUT_S = 12  # secunde per request — nu blocăm pornirea dacă site-ul e lent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_istoric_dir() -> Path | None:
    root = Path(__file__).parent
    for name in ("_ISTORIC", "_istoric", "ISTORIC", "istoric"):
        p = root / name
        if p.is_dir():
            return p
    return None


def _parse_site_date(s: str) -> date | None:
    """Parsează 'yyyy-mm-dd' sau 'yyyy-m-d' din textul site-ului."""
    try:
        parts = s.strip().split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _csv_date_to_date(s: str) -> date | None:
    """Parsează 'dd-mm-yyyy' (formatul scris în CSV de noi)."""
    try:
        parts = s.strip().split("-")
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except Exception:
        return None


def _last_date_in_csv(csv_path: Path) -> date | None:
    """Cea mai RECENTĂ dată din CSV (maxim peste toate rândurile), NU data
    ultimului rând citit — fișierele sunt de obicei ordonate crescător, dar
    nimic din acest script nu impune sau verifică invariantul. Cu "ultimul
    rând citit" ca proxy pentru "cea mai recentă", o corecție manuală, o
    îmbinare care reordonează liniile, sau o adăugare din altă parte care nu
    respectă ordinea ar face `update_all()` fie să reintroducă extrageri deja
    prezente ca duplicate (nedetectate — validarea nu verifică duplicate
    între rânduri), fie să sară tăcut extrageri noi reale. None dacă fișierul
    e gol/lipsă/fără niciun rând cu dată parsabilă."""
    if not csv_path.exists():
        return None
    max_date = None
    try:
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = _csv_date_to_date(row.get("date", ""))
                if d and (max_date is None or d > max_date):
                    max_date = d
    except Exception:
        return None
    return max_date


def _get_page_text(url: str) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        raw = resp.read()
    # BeautifulSoup opțional — dacă nu e instalat, facem strip HTML de bază
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(raw, "html.parser").get_text(" ")
    except ImportError:
        # fallback: strip taguri HTML cu regex
        return re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))


def _extract_draws(text: str, num_main: int, has_joker: bool, after: date | None):
    """Extrage extrageri din textul paginii. Returnează doar cele > after."""
    if has_joker:
        pattern = re.compile(
            r'\b(\d{4}-\d{1,2}-\d{1,2})\b'
            r'((?:\s+\d{1,2}){' + str(num_main) + r'})'
            r'\s*\+?\s*(\d{1,2})'
        )
    else:
        pattern = re.compile(
            r'\b(\d{4}-\d{1,2}-\d{1,2})\b'
            r'((?:\s+\d{1,2}){' + str(num_main) + r'})'
        )

    today = date.today()
    results = []
    seen = set()
    for m in pattern.finditer(text):
        d = _parse_site_date(m.group(1))
        if not d or d > today:
            continue
        if after and d <= after:
            continue
        nums = [int(x) for x in m.group(2).split()]
        if len(set(nums)) != num_main:
            # Numere duplicate INTR-O SINGURA extragere: fragment HTML deformat
            # (potriveste peste un rand invecinat, sau un separator lipsa).
            # Contractul comun de validare (draw_validation.py) respinge exact
            # asta pentru orice alt consumator — scraper-ul nu are voie sa scrie
            # in _ISTORIC/ ceva ce engine/benchmark ar respinge oricum la citire,
            # doar tacut, mai tarziu.
            continue
        joker_num = int(m.group(3)) if has_joker else None

        key = (d, tuple(nums), joker_num)
        if key in seen:
            continue
        seen.add(key)
        results.append({"date": d, "main": nums, "joker": joker_num})

    results.sort(key=lambda r: r["date"])
    return results


def _append_rows_atomic(csv_path: Path, new_rows: list, has_joker: bool, num_main: int) -> int:
    """Citește CSV existent, adaugă rândurile noi, rescrie atomic (tmp + rename).
    Întoarce numărul de rânduri EFECTIV scrise (poate fi mai mic decât
    len(new_rows) dacă unele aveau deja o dată prezentă în CSV — vezi garda
    `existing_dates` de mai jos)."""
    # Citește rândurile existente
    existing: list[list[str]] = []
    header: list[str] | None = None
    if csv_path.exists():
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if rows:
            header = rows[0]
            existing = rows[1:]

    if header is None:
        header = ["date"] + [f"n{i+1}" for i in range(num_main)]
        if has_joker:
            header.append("joker")

    # Construiește rândurile noi
    to_add = []
    for rec in new_rows:
        row = [rec["date"].strftime("%d-%m-%Y")] + rec["main"]
        if has_joker:
            row.append(rec["joker"])
        to_add.append([str(x) for x in row])

    # Plasă de siguranță INDEPENDENTĂ de `_last_date_in_csv`: chiar dacă acel
    # calcul ar greși cumva (fișier reordonat, corectat manual), niciun rând
    # cu o dată deja prezentă în CSV nu se mai adaugă a doua oară.
    existing_dates = {row[0] for row in existing if row}
    to_add = [row for row in to_add if row[0] not in existing_dates]
    if not to_add:
        return 0

    # Scriere atomică: scrie în tmp, rename
    dir_ = csv_path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(existing)
            writer.writerows(to_add)
        os.replace(tmp_path, csv_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise
    return len(to_add)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def update_all() -> int:
    """Verifică și actualizează toate jocurile. Returnează numărul total de rânduri adăugate."""
    from datetime import timedelta
    istoric_dir = _find_istoric_dir()
    if not istoric_dir:
        print("[UPDATE-CSV] Folderul _ISTORIC/ nu există — skip.")
        return 0

    total_added = 0
    today = date.today()
    yesterday = today - timedelta(days=1)
    print(f"[UPDATE-CSV] Data curentă: {today.strftime('%d-%m-%Y')}")

    for game_key, cfg in GAME_CONFIGS.items():
        csv_path = istoric_dir / cfg["csv_name"]
        last = _last_date_in_csv(csv_path)
        last_str = last.strftime("%d-%m-%Y") if last else "N/A"

        if csv_path.exists() and last is None:
            # Fisierul EXISTA dar n-are niciun rand cu data parsabila — trunchiat
            # sau corupt, nu "prima rulare vreodata" (fisierele din _ISTORIC/ sunt
            # versionate cu mii de randuri deja). A trata asta ca "totul de pe
            # site e nou" ar rescrie CSV-ul cu doar cateva luni de istoric — si
            # `loto_git_sync.bat push_istoric` ar face auto-commit + push pe
            # origin/main la urmatoarea pornire, fara niciun avertisment.
            print(f"  {cfg['display_name']:<12}: CSV EXISTA dar fara nicio data valida — "
                  "pare trunchiat/corupt. SAR peste (nu tratez ca prima rulare).")
            continue

        # Fetch MEREU site-ul ca să raportăm ultima extragere reală (best-effort).
        site_last = None
        new_draws = []
        try:
            text = _get_page_text(cfg["recent_url"])
            all_draws = _extract_draws(text, cfg["num_main"], cfg["has_joker"], after=None)
            if all_draws:
                site_last = all_draws[-1]["date"]
            # Doar extragerile mai noi decât CSV-ul nostru
            new_draws = [d for d in all_draws if (last is None or d["date"] > last)]
        except Exception as exc:
            print(f"  {cfg['display_name']:<12}: CSV={last_str} | site=EROARE ({type(exc).__name__}) — continuă cu datele existente.")
            continue

        site_str = site_last.strftime("%d-%m-%Y") if site_last else "N/A"
        gap = (today - site_last).days if site_last else None
        gap_str = f"{gap} zile în urmă" if gap is not None else "?"

        if new_draws:
            written = _append_rows_atomic(csv_path, new_draws, cfg["has_joker"], cfg["num_main"])
            dates_str = ", ".join(r["date"].strftime("%d-%m-%Y") for r in new_draws)
            print(f"  {cfg['display_name']:<12}: CSV={last_str} -> site={site_str} (azi: {gap_str}) | +{written} extrageri noi: {dates_str}")
            total_added += written
        else:
            print(f"  {cfg['display_name']:<12}: CSV={last_str} | site={site_str} (azi: {gap_str}) | la zi.")

    if total_added > 0:
        print(f"[UPDATE-CSV] Total adăugate: {total_added} extrageri noi. CSV-urile din _ISTORIC/ sunt la zi.")
    else:
        print("[UPDATE-CSV] Toate jocurile sunt la zi (nicio extragere nouă pe loto49.ro).")

    return total_added


if __name__ == "__main__":
    update_all()
    sys.exit(0)  # mereu 0 — best-effort, nu blochează nimic
