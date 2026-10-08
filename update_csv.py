"""update_csv.py — Detectează și adaugă extrageri noi în CSV-urile din _ISTORIC/.

Verifică DOAR prima URL (extrageri recente) pentru fiecare joc — rapid, fără să
rescrie tot istoricul. Adaugă la final rândurile care lipsesc, scriere atomică.
Nu scrie nimic pentru un joc (EROARE în ieșire) când pagina nu are extrageri,
are un rând invalid la/după ultima extragere stocată, diferă de CSV în ultimele
CHECK_DAYS zile sau aduce un rând care copiază numerele rândului anterior.

Rulare:
    python update_csv.py               # verifică toate jocurile
    python update_csv.py --verbose     # afișează detalii extra

Exit code: 0 mereu (best-effort) — eroare de rețea / parsing nu blochează pornirea.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
from datetime import date, timedelta

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

_VERBOSE = "--verbose" in sys.argv

# URL-ul RECENT (primul) e de ajuns pentru update — extrage ultimele luni.
GAME_CONFIGS = {
    "loto_6_49": {
        "display_name": "Loto 6/49",
        "recent_url": "https://www.loto49.ro/arhiva-loto49.php",
        "num_main": 6,
        "has_joker": False,
        "csv_name": "loto_6_49.csv",
        "max_num": 49,
    },
    "joker": {
        "display_name": "Joker",
        "recent_url": "https://www.loto49.ro/arhiva-joker.php",
        "num_main": 5,
        "has_joker": True,
        "csv_name": "joker.csv",
        "max_num": 45,
        "joker_max": 20,
    },
    "loto_5_40": {
        "display_name": "Loto 5/40",
        "recent_url": "https://www.loto49.ro/arhiva-superloto.php",
        "num_main": 6,
        "has_joker": False,
        "csv_name": "loto_5_40.csv",
        "max_num": 40,
    },
}

TIMEOUT_S = 12  # secunde per request — nu blocăm pornirea dacă site-ul e lent
# Fereastra în care extragerile deja stocate se compară cu site-ul (ca în
# update_externe.py): pagina 5/40 merge până în 1995, iar o dispută veche
# (24-10-2024, corectat local, greșit pe site) nu trebuie să blocheze update-ul.
CHECK_DAYS = 60

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


def _extract_draws(
    text: str,
    num_main: int,
    has_joker: bool,
    after: date | None,
    max_num: int,
    joker_max: int | None = None,
    rejected: list | None = None,
):
    """Extrage extrageri din textul paginii. Returnează doar cele > after.

    `rejected`, dacă e dat, primește rândurile potrivite de model, dar
    respinse ({"date", "text", "reason"}; data e None când nu se poate citi),
    ca `update_all` să nu sară tăcut peste o extragere greșită de pe site.

    Contractul comun (draw_validation.py) respinge orice extragere cu valori
    in afara intervalului jocului — inainte doar duplicatele intra-extragere
    erau verificate aici, nu si intervalul. Un fragment HTML deformat (potrivit
    peste un rand vecin, separator lipsa) putea produce un numar in afara
    intervalului, scris tacut in _ISTORIC/, respins abia mai tarziu, silentios,
    la citirea prin draw_validation.py (randul pur si simplu dispare din
    istoricul folosit de engine/benchmark, fara nicio urma aici)."""
    # Fiecare număr se termină înaintea unei cifre sau a unei cratime: o celulă
    # lipsă nu mai împrumută „20” din anul datei următoare, iar „490” nu mai
    # trece drept „49” urmat de „0”.
    if has_joker:
        pattern = re.compile(
            r"\b(\d{4}-\d{1,2}-\d{1,2})\b"
            r"((?:\s+\d{1,2}(?![\d-])){" + str(num_main) + r"})"
            r"\s*\+?\s*(\d{1,2})(?![\d-])"
        )
    else:
        pattern = re.compile(
            r"\b(\d{4}-\d{1,2}-\d{1,2})\b"
            r"((?:\s+\d{1,2}(?![\d-])){" + str(num_main) + r"})"
        )

    today = date.today()
    results = []
    seen = set()

    def _reject(d, m, reason):
        if rejected is not None:
            text = " ".join(m.group(0).split())
            rejected.append({"date": d, "text": text, "reason": reason})

    matched = set()
    for m in pattern.finditer(text):
        matched.add(m.start(1))
        d = _parse_site_date(m.group(1))
        if not d or d > today:
            _reject(d, m, "dată în viitor" if d else "dată invalidă")
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
            _reject(d, m, "numere repetate")
            continue
        if any(not (1 <= n <= max_num) for n in nums):
            # Numar in afara intervalului jocului: acelasi fragment HTML
            # deformat suspectat mai sus, doar ca deraparea a produs un numar
            # valid ca sir de cifre dar imposibil pentru geometria jocului.
            _reject(d, m, f"număr în afara 1..{max_num}")
            continue
        joker_num = int(m.group(3)) if has_joker else None
        if has_joker and joker_num is not None and joker_max is not None:
            if not (1 <= joker_num <= joker_max):
                _reject(d, m, f"Joker în afara 1..{joker_max}")
                continue

        key = (d, tuple(nums), joker_num)
        if key in seen:
            continue
        seen.add(key)
        results.append({"date": d, "main": nums, "joker": joker_num})

    if rejected is not None:
        # O dată fără rând citibil după ea (cifră în plus, literă, celulă lipsă)
        # e tot o extragere sărită: o raportăm, nu o pierdem tăcut.
        for m in re.finditer(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", text):
            if m.start(1) in matched:
                continue
            d = _parse_site_date(m.group(1))
            if after and d and d <= after:
                continue
            snippet = " ".join(text[m.start(1) : m.start(1) + 48].split())
            rejected.append({"date": d, "text": snippet, "reason": "rând necitibil"})

    results.sort(key=lambda r: r["date"])
    return results


def _draw_key(row: list) -> tuple:
    """Identitatea unei extrageri: data, numerele principale ca mulțime, Joker.

    Ordinea numerelor nu contează: același rând scris în altă ordine (import
    vechi, sursă diferită) rămâne aceeași extragere, nu una nouă.
    """
    cells = [str(x).strip() for x in row]
    try:
        nums = tuple(sorted(int(x) for x in cells[1:]))
    except ValueError:
        return tuple(cells)
    return (cells[0], nums)


def _draw_key_with_joker(row: list, has_joker: bool) -> tuple:
    if not has_joker or len(row) < 2:
        return _draw_key(row)
    return (*_draw_key(row[:-1]), str(row[-1]).strip())


def _stored_rows(csv_path: Path) -> list[list[str]]:
    """Rândurile de date ale CSV-ului (fără antet și rânduri goale), în ordinea din fișier."""
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return [r for r in rows[1:] if r]


def _site_row(rec: dict, has_joker: bool) -> list:
    row = [rec["date"].strftime("%d-%m-%Y")] + list(rec["main"])
    if has_joker:
        row.append(rec["joker"])
    return [str(x) for x in row]


def _canonical_key(day: date, key: tuple) -> tuple:
    """Cheia cu data ZZ-LL-AAAA și Joker-ul ca număr: „4-10-2026” sau „09”
    scrise de mână sunt aceeași extragere ca „04-10-2026” / „9” de pe site."""
    rest = list(key[1:])
    if len(rest) == 2:
        try:
            rest[1] = str(int(rest[1]))
        except ValueError:
            pass
    return (f"{day:%d-%m-%Y}", *rest)


def _row_identity(row: list, has_joker: bool) -> tuple:
    """Identitatea unui rând (stocat sau de pe site), cu data și Joker-ul canonice."""
    key = _draw_key_with_joker(row, has_joker)
    day = _csv_date_to_date(str(row[0]).strip()) if row else None
    return _canonical_key(day, key) if day is not None else key


def _shown_key(key: tuple) -> str:
    """Extragerea din cheie, lizibilă: '1 2 3 4 5 6' sau '1 2 3 4 5 + 7'."""
    if len(key) > 1 and isinstance(key[1], tuple):
        nums = " ".join(str(n) for n in key[1])
        return f"{nums} + {key[2]}" if len(key) > 2 else nums
    return ",".join(str(x) for x in key)  # rând stocat necitibil


def _main_numbers(row: list, has_joker: bool) -> tuple | None:
    try:
        return tuple(sorted(int(x) for x in (row[1:-1] if has_joker else row[1:])))
    except ValueError:
        return None


def _site_refusal(
    stored: list[list[str]],
    all_draws: list[dict],
    rejected: list[dict],
    new_draws: list[dict],
    last: date | None,
    has_joker: bool,
) -> str | None:
    """Motivul pentru care NU scriem nimic pentru joc, sau None.

    Toate cazurile lasă altfel istoricul greșit fără urmă: rândurile noi se
    adaugă numai după ultima dată stocată, deci o extragere sărită nu mai vine
    niciodată, iar PushHistory publică fișierul (numai rânduri adăugate)."""
    # 1. Un rând de pe site respins (numere repetate, în afara intervalului, dată
    # imposibilă) la/după ultima extragere stocată: extragerile de după el ar
    # intra, iar el n-ar mai fi adăugat nici după corectura site-ului.
    for r in rejected:
        if r["date"] is None or last is None or r["date"] >= last:
            return f"rândul de pe site „{r['text']}” e invalid ({r['reason']})"
    # 2. O pagină fără nicio extragere (altă structură, pagină de protecție
    # servită cu 200) nu înseamnă „la zi”.
    if not all_draws:
        return "pagina nu conține nicio extragere (structura site-ului s-a schimbat?)"
    # 3. Zilele comune site–CSV din ultimele CHECK_DAYS zile trebuie să coincidă.
    # În ziua ultimei extrageri stocate, site-ul poate avea în plus a doua
    # extragere a zilei; un rând stocat care nu mai e pe site (corectat pe site
    # sau local) ar intra altfel drept a doua extragere a aceleiași zile.
    if last is not None:
        start = max(last - timedelta(days=CHECK_DAYS), all_draws[0]["date"])
        end = min(last, all_draws[-1]["date"])
        site_days: dict[date, set] = {}
        for d in all_draws:
            key = _draw_key_with_joker(_site_row(d, has_joker), has_joker)
            site_days.setdefault(d["date"], set()).add(_canonical_key(d["date"], key))
        stored_days: dict[date, set] = {}
        for row in stored:
            day = _csv_date_to_date(row[0])
            if day is not None:
                key = _draw_key_with_joker(row, has_joker)
                stored_days.setdefault(day, set()).add(_canonical_key(day, key))
        for day in sorted(set(site_days) | set(stored_days)):
            if not start <= day <= end:
                continue
            got, mine = site_days.get(day, set()), stored_days.get(day, set())
            same = (mine <= got) if day == last else (mine == got)
            if same:
                continue
            only_csv = "; ".join(map(_shown_key, sorted(mine - got, key=str))) or "-"
            only_site = "; ".join(map(_shown_key, sorted(got - mine, key=str))) or "-"
            return (
                f"extragerile din {day:%d-%m-%Y} diferă: CSV {only_csv}, site "
                f"{only_site}. Verifică manual pe loto.ro; dacă CSV-ul are extragerea "
                f"corectă, adaugă manual extragerile noi până când ziua iese din "
                f"verificarea ultimelor {CHECK_DAYS} de zile"
            )
    # 4. AGENTS.md §4.1: un rând nou cu aceleași numere principale ca rândul
    # anterior (ultimul stocat sau rândul nou dinaintea lui) e o copie.
    prev_main = _main_numbers(stored[-1], has_joker) if stored else None
    prev_date = stored[-1][0] if stored else ""
    for d in new_draws:
        main = tuple(sorted(d["main"]))
        if main == prev_main:
            return (
                f"extragerea din {d['date']:%d-%m-%Y} repetă numerele rândului din "
                f"{prev_date} (copie, nu extragere). Verifică manual pe loto.ro"
            )
        prev_main, prev_date = main, f"{d['date']:%d-%m-%Y}"
    return None


def _append_rows_atomic(
    csv_path: Path, new_rows: list, has_joker: bool, num_main: int
) -> int:
    """Citește CSV existent, adaugă rândurile noi, rescrie atomic (tmp + rename).
    Întoarce numărul de rânduri EFECTIV scrise (poate fi mai mic decât
    len(new_rows) dacă aceeași extragere — aceeași dată ȘI aceleași numere,
    în orice ordine — e deja în CSV; o a doua extragere în aceeași zi, cu
    numere diferite, se adaugă)."""
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
        header = ["date"] + [f"n{i + 1}" for i in range(num_main)]
        if has_joker:
            header.append("joker")

    # Plasă de siguranță INDEPENDENTĂ de `_last_date_in_csv`: nu re-adăugăm
    # o extragere deja stocată (dată + numere, în orice ordine). Zilele cu două
    # extrageri diferite rămân permise — istoricul le stochează deja, WF le
    # tratează separat.
    seen = {_row_identity(row, has_joker) for row in existing if row}
    to_add = []
    for rec in new_rows:
        row = _site_row(rec, has_joker)
        key = _row_identity(row, has_joker)
        if key not in seen:
            seen.add(key)
            to_add.append(row)
    if not to_add:
        return 0

    # Scriere atomică: scrie în tmp, rename
    dir_ = csv_path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
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
    istoric_dir = _find_istoric_dir()
    if not istoric_dir:
        print("[UPDATE-CSV] Folderul _ISTORIC/ nu există — skip.")
        return 0

    total_added = 0
    not_updated: list[str] = []
    today = date.today()
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
            # START_8000.bat :push_istoric ar face auto-commit + push pe
            # origin/main la urmatoarea pornire, fara niciun avertisment.
            print(
                f"  {cfg['display_name']:<12}: CSV EXISTA dar fara nicio data valida — "
                "pare trunchiat/corupt. SAR peste (nu tratez ca prima rulare)."
            )
            continue

        # Fetch MEREU site-ul ca să raportăm ultima extragere reală (best-effort).
        site_last = None
        new_draws = []
        all_draws = []
        rejected: list[dict] = []
        stored: list[list[str]] = []
        try:
            text = _get_page_text(cfg["recent_url"])
            all_draws = _extract_draws(
                text,
                cfg["num_main"],
                cfg["has_joker"],
                after=None,
                max_num=cfg["max_num"],
                joker_max=cfg.get("joker_max"),
                rejected=rejected,
            )
            if all_draws:
                site_last = all_draws[-1]["date"]
            # Ziua ultimei extrageri stocate rămâne candidată: a doua extragere
            # a zilei poate apărea pe site după ce prima a fost salvată. Cele
            # deja stocate cad la comparația pe (dată, numere).
            stored = _stored_rows(csv_path)
            known = {_row_identity(r, cfg["has_joker"]) for r in stored}
            for d in all_draws:
                key = _row_identity(_site_row(d, cfg["has_joker"]), cfg["has_joker"])
                if (last is None or d["date"] >= last) and key not in known:
                    known.add(key)
                    new_draws.append(d)
        except Exception as exc:
            print(
                f"  {cfg['display_name']:<12}: CSV={last_str} | site=EROARE ({type(exc).__name__}) — continuă cu datele existente."
            )
            not_updated.append(cfg["display_name"])
            continue

        site_str = site_last.strftime("%d-%m-%Y") if site_last else "N/A"
        gap = (today - site_last).days if site_last else None
        gap_str = f"{gap} zile în urmă" if gap is not None else "?"

        if last is not None and all_draws and all_draws[0]["date"] > last:
            # Pagina recentă nu mai ajunge până la ultima extragere stocată:
            # extragerile dintre ele nu se văd. Adăugate, ar lăsa o gaură pe care
            # rulările următoare n-o mai completează (adaugă numai după ultima
            # dată), iar PushHistory ar publica fișierul.
            print(
                f"  {cfg['display_name']:<12}: CSV={last_str} | site={site_str} — pagina "
                f"începe la {all_draws[0]['date']:%d-%m-%Y}, după ultima extragere "
                "stocată. NU scriu nimic: ar rămâne o gaură în istoric. Completează "
                "manual extragerile lipsă."
            )
            not_updated.append(cfg["display_name"])
            continue

        refusal = _site_refusal(
            stored, all_draws, rejected, new_draws, last, cfg["has_joker"]
        )
        if refusal:
            # „EROARE” în text: ACTUALIZARI.bat afișează atunci avertismentul.
            print(
                f"  {cfg['display_name']:<12}: CSV={last_str} | site={site_str} | "
                f"EROARE verificare: {refusal}. NU scriu nimic."
            )
            not_updated.append(cfg["display_name"])
            continue

        if new_draws:
            try:
                written = _append_rows_atomic(
                    csv_path, new_draws, cfg["has_joker"], cfg["num_main"]
                )
            except OSError as exc:
                # Fișier deschis în alt program (Excel pe Windows) sau disc plin:
                # celelalte jocuri continuă, iar scriptul iese tot cu 0.
                print(
                    f"  {cfg['display_name']:<12}: CSV={last_str} -> site={site_str} | "
                    f"NU am putut scrie CSV-ul ({type(exc).__name__}: {exc})."
                )
                not_updated.append(cfg["display_name"])
                continue
            dates_str = ", ".join(r["date"].strftime("%d-%m-%Y") for r in new_draws)
            print(
                f"  {cfg['display_name']:<12}: CSV={last_str} -> site={site_str} (azi: {gap_str}) | +{written} extrageri noi: {dates_str}"
            )
            total_added += written
        else:
            print(
                f"  {cfg['display_name']:<12}: CSV={last_str} | site={site_str} (azi: {gap_str}) | la zi."
            )

    if not_updated:
        print(
            f"[UPDATE-CSV] Total adăugate: {total_added}. NEACTUALIZATE: "
            f"{', '.join(not_updated)} (vezi motivul de mai sus)."
        )
    elif total_added > 0:
        print(
            f"[UPDATE-CSV] Total adăugate: {total_added} extrageri noi. CSV-urile din _ISTORIC/ sunt la zi."
        )
    else:
        print(
            "[UPDATE-CSV] Toate jocurile sunt la zi (nicio extragere nouă pe loto49.ro)."
        )

    return total_added


if __name__ == "__main__":
    update_all()
    sys.exit(0)  # mereu 0 — best-effort, nu blochează nimic
