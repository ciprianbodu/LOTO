"""update_externe.py — adaugă extragerile noi în istoricele din _ISTORIC/externe/.

Pentru fiecare joc străin din registru (`loto_enterprise/core/lotteries.py`)
o singură sursă, oficială unde se poate (vezi `_ISTORIC/externe/README.md`):

1. descarcă extragerile recente (timeout ~15 s pe cerere);
2. validează fiecare extragere: dată, numere întregi distincte în interval,
   geometria jocului din registru (EuroMillions: 5 din 50 + 2 stele din 12);
3. verifică extragerile pe care CSV-ul le are deja în fereastra sursei: orice
   nepotrivire oprește jocul acela și nu scrie nimic;
4. adaugă numai extragerile mai noi (două extrageri în aceeași zi rămân în
   ordinea oficială), scriere atomică, terminații LF.

Rulare:
    python update_externe.py                 # toate jocurile străine
    python update_externe.py --dry-run       # arată ce ar adăuga, nu scrie
    python update_externe.py --game at_lotto --root CALE_COPIE

Exit code: 0 mereu (best-effort) — o eroare de rețea sau de format lasă
istoricul neschimbat și nu blochează ACTUALIZARI.bat.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Consola Windows e cp1252 by default -> diacriticele arunca UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TIMEOUT_S = 15
# Fereastra verificată față de CSV: extragerile sursei mai vechi de atât nu se
# compară (o sursă cu istoric complet nu blochează update-ul pe o dispută veche).
CHECK_DAYS = 60
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class SourceError(Exception):
    """Sursa a răspuns, dar conținutul nu e ce ne așteptăm."""


def _http_get(url: str, headers: dict | None = None) -> bytes:
    """Singurul punct de rețea (testele îl înlocuiesc)."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA_BROWSER})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def _get(url: str, headers: dict | None = None) -> bytes:
    return _http_get(url, headers)


# ---------------------------------------------------------------------------
# Tipul comun: o extragere = (data, numerele în ordinea rândului din CSV)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Draw:
    date: dt.date
    nums: tuple[int, ...]


def _dmy(s: str) -> dt.date:
    """'25.9.2026', '25. 9. 2026', '25-09-2026', '25/09/2026' -> date."""
    parts = [p for p in re.split(r"[.\-/\s]+", s.strip()) if p]
    if len(parts) != 3:
        raise SourceError(f"data necunoscuta {s!r}")
    d, m, y = (int(p) for p in parts)
    return dt.date(y, m, d)


def _last_sunday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def _cet_midnight_ms(d: dt.date) -> int:
    """Miezul nopții Europe/Berlin (regula UE de ora de vară), în ms epoch.

    Fără zoneinfo: pe Windows baza de fusuri lipsește fără pachetul tzdata.
    """
    summer = _last_sunday(d.year, 3) < d <= _last_sunday(d.year, 10)
    offset = dt.timedelta(hours=2 if summer else 1)
    utc = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) - offset
    return int(utc.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Fetchere: fiecare întoarce extrageri CRONOLOGIC (în zi: ordinea oficială)
# ---------------------------------------------------------------------------


def fetch_de_lotto(last: dt.date, today: dt.date) -> list[Draw]:
    """lotto.de (DLTB): lista datelor pe an, apoi extragerea pe fiecare dată."""
    base = "https://www.lotto.de/api/stats/entities.lotto"
    hdr = {"User-Agent": UA_BROWSER, "Accept": "application/json"}
    start = last - dt.timedelta(days=14)
    dates: set[dt.date] = set()
    # Toți anii dintre capete: cu {start.year, today.year}, un istoric rămas în
    # urmă peste un an întreg sărea anii din mijloc și scria o gaură tăcută.
    for year in range(start.year, today.year + 1):
        data = json.loads(_get(f"{base}/history/{_cet_midnight_ms(dt.date(year, 12, 31))}", hdr))
        for day in data.get("days", []):
            d = dt.date.fromisoformat(day["date"])
            if start <= d <= today:
                dates.add(d)
    if not dates:
        raise SourceError("lista de date lotto.de e goala")
    out: list[Draw] = []
    for d in sorted(dates):
        entries = json.loads(_get(f"{base}/draws/{_cet_midnight_ms(d)}", hdr))
        for e in entries:
            name = str((e.get("gameType") or {}).get("name", "")).lower()
            if not name.startswith("lotto 6aus49"):
                continue
            coll = sorted(e.get("drawNumbersCollection") or [], key=lambda x: x["index"])
            out.append(Draw(d, tuple(int(x["drawNumber"]) for x in coll)))
    return out


def fetch_pl_lotto(last: dt.date, today: dt.date) -> list[Draw]:
    """wynikilotto.net.pl: fișier complet numerotat (lotto.pl blochează accesul)."""
    text = _get("https://www.wynikilotto.net.pl/download/lotto.csv").decode("utf-8-sig")
    out = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 8 and f[0].isdigit() and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", f[1]):
            out.append((int(f[0]), Draw(_dmy(f[1]), tuple(int(x) for x in f[2:8]))))
    out.sort(key=lambda x: x[0])  # numărul oficial al extragerii
    return [d for _, d in out]


def fetch_es_primitiva(last: dt.date, today: dt.date) -> list[Draw]:
    """lawebdelaprimitiva.com: export CSV al istoricului complet."""
    url = (
        "https://lawebdelaprimitiva.com/?tipo_loteria=PRI&action=descarga_historico"
        "&tipo_archivo=text/csv"
    )
    raw = _get(url)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*\w{3}-(\d{2}-\d{2}-\d{4})\s*;(.*)$", line)
        if m:
            f = [x.strip() for x in m.group(2).split(";")]
            out.append(Draw(_dmy(m.group(1)), tuple(int(x) for x in f[:6])))
    return out


def fetch_at_lotto(last: dt.date, today: dt.date) -> list[Draw]:
    """win2day.at (Österreichische Lotterien), API JSON public."""
    url = "https://lotterien.win2day.at/jam/drawgame/v1/public/drawResultInfo/lotto?limit=30"
    # WAF-ul win2day răspunde 400 la un User-Agent cu text în plus.
    data = json.loads(_get(url, {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}))
    if data.get("game") != "LOTTO":
        raise SourceError(f"joc neasteptat {data.get('game')!r}")
    out = []
    for d in data.get("drawResults", []):
        main = [x for x in d["results"] if x.get("resultType") == "LOTTO_NUMBER"]
        if len(main) != 1:
            raise SourceError(f"extragerea {d.get('drawNo')}: {len(main)} blocuri LOTTO_NUMBER")
        nums = tuple(int(v["number"]) for v in main[0]["resultValues"])
        out.append((dt.date.fromisoformat(d["drawDate"]), int(d["drawNo"]), nums))
    out.sort(key=lambda x: (x[0], x[1]))
    return [Draw(d, n) for d, _, n in out]


def fetch_be_lotto(last: dt.date, today: dt.date) -> list[Draw]:
    """Loterie Nationale (natlot.be), API public."""
    start = dt.datetime.combine(last - dt.timedelta(days=21), dt.time(), dt.timezone.utc)
    end = dt.datetime.combine(today + dt.timedelta(days=2), dt.time(), dt.timezone.utc)
    params = {
        "status": "PAYABLE",
        "game-names": "Lotto",
        "date-from": int(start.timestamp() * 1000),
        "date-to": int(end.timestamp() * 1000),
        "size": 250,
    }
    url = "https://apim.prd.natlot.be/api/v4/draw-games/draws?" + urllib.parse.urlencode(params)
    data = json.loads(_get(url, {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}))
    out = []
    for x in data.get("draws", []):
        if x.get("gameName") != "Lotto" or x.get("status") != "PAYABLE":
            continue
        normal = [r for r in x.get("results", []) if r.get("drawType") == "normal"]
        if len(normal) != 1:
            continue
        # Extragerea e seara (~20:00 la Bruxelles): data UTC = data locală.
        d = dt.datetime.fromtimestamp(x["drawTime"] / 1000, dt.timezone.utc).date()
        out.append((x["drawTime"], int(x["id"]), Draw(d, tuple(int(v) for v in normal[0]["primary"]))))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]


def fetch_hu_hatos(last: dt.date, today: dt.date) -> list[Draw]:
    """Szerencsejáték Zrt.: fișierul oficial hatos.csv (cel mai nou primul)."""
    text = _get("https://bet.szerencsejatek.hu/cmsfiles/hatos.csv").decode("utf-8-sig")
    out = []
    for line in text.splitlines():
        f = line.split(";")
        if len(f) < 20:
            continue
        raw = f[3].strip()
        if not raw:  # rândurile vechi fără dată oficială nu intră în verificare
            continue
        d = dt.datetime.strptime(raw, "%Y.%m.%d.").date()
        out.append(Draw(d, tuple(int(x) for x in f[14:20])))
    out.reverse()
    return out


def fetch_cz_sportka(last: dt.date, today: dt.date) -> list[Draw]:
    """Allwyn (Sazka): istoricul oficial Sportka, 1. și 2. tah pe același rând."""
    url = "https://www.allwyn.cz/loterie/historie-cisel?game=sportka"
    hdr = {"User-Agent": UA_BROWSER, "Accept": "text/csv,*/*;q=0.8", "Accept-Language": "cs-CZ,cs;q=0.9"}
    body = ""
    for attempt in range(3):  # marginea Akamai răspunde uneori 403
        try:
            body = _get(url, hdr).decode("utf-8-sig")
            if body.startswith("datum;"):
                break
        except Exception:
            if attempt == 2:
                raise
        time.sleep(1 + attempt)
    if not body.startswith("datum;"):
        raise SourceError("raspunsul Allwyn nu e CSV-ul de istoric")
    out = []
    for r in list(csv.reader(io.StringIO(body), delimiter=";"))[1:]:
        if not r or not r[0].strip():
            continue
        d = _dmy(r[0])
        out.append(Draw(d, tuple(int(x) for x in r[4:10])))
        out.append(Draw(d, tuple(int(x) for x in r[11:17])))
    # Fișierul e cel mai nou primul; în zi, 1. tah înaintea lui 2. tah.
    days: dict[dt.date, list[Draw]] = {}
    for x in out:
        days.setdefault(x.date, []).append(x)
    return [x for d in sorted(days) for x in days[d]]


def fetch_sk_loto(last: dt.date, today: dt.date) -> list[Draw]:
    """TIPOS: arhivele oficiale loto1 (1. ťah) și loto2 (2. ťah)."""
    base = (
        "https://www.tipos.sk/loterie/ciselne-loterie/informacie-k-loteriam/"
        "archiv-vyzrebovanych-cisel?file="
    )
    per: list[dict[dt.date, tuple[int, ...]]] = []
    for name in ("loto1", "loto2"):
        raw = _get(base + name)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1250")
        rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        if not rows or "DATUM" not in rows[0]:
            raise SourceError(f"{name}: antet necunoscut")
        ix = {h: i for i, h in enumerate(rows[0])}
        m: dict[dt.date, tuple[int, ...]] = {}
        for r in rows[1:]:
            if len(r) <= ix["C_6"] or not r[ix["DATUM"]].strip():
                continue
            d = _dmy(r[ix["DATUM"]])
            if d in m:
                raise SourceError(f"{name}: data {d} repetata")
            m[d] = tuple(int(r[ix[f"C_{i}"]]) for i in range(1, 7))
        per.append(m)
    t1, t2 = per
    if set(t1) != set(t2):
        # Arhivele se regenerează separat: păstrăm numai zilele complete.
        common = set(t1) & set(t2)
    else:
        common = set(t1)
    return [Draw(d, t[d]) for d in sorted(common) for t in (t1, t2)]


def fetch_eu_euromillions(last: dt.date, today: dt.date) -> list[Draw]:
    """Française des Jeux: arhiva perioadei curente (zip), găsită pe pagina istoric."""
    page_url = "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/historique"
    page = _get(page_url).decode("utf-8", "replace")
    links = re.findall(r'<a class="block" download="[^"]*"[^>]*href="([^"]*)"', page)
    if not links:
        raise SourceError("pagina FDJ nu mai are legaturile de arhiva")
    data = _get(html.unescape(links[0]), {"User-Agent": UA_BROWSER, "Referer": page_url})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(members) != 1:
            raise SourceError(f"arhiva FDJ are {len(members)} fisiere CSV")
        raw = z.read(members[0])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    ix = {h: i for i, h in enumerate(rows[0])}
    need = ["date_de_tirage", *(f"boule_{i}" for i in range(1, 6)), "etoile_1", "etoile_2"]
    if any(k not in ix for k in need):
        raise SourceError("antet FDJ necunoscut")
    out = []
    for r in rows[1:]:
        if not r or not any(c.strip() for c in r):
            continue
        g = lambda k: r[ix[k]].strip()  # noqa: E731
        d = dt.datetime.strptime(g("date_de_tirage"), "%d/%m/%Y").date()
        mains = [int(g(f"boule_{i}")) for i in range(1, 6)]
        stars = sorted(int(g(k)) for k in ("etoile_1", "etoile_2"))
        out.append(Draw(d, tuple(mains + stars)))
    out.sort(key=lambda x: x.date)
    return out


_TOTO49_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TOTO49_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def parse_toto49_year(page: str) -> list[Draw]:
    """Tabelul anual toto49.com: tiraj, dată ZZ.LL.AAAA, 6 numere (1-ea tragere)."""
    out = []
    for tr in _TOTO49_ROW.findall(page):
        cells = [re.sub(r"<[^>]*>", " ", c) for c in _TOTO49_CELL.findall(tr)]
        m = re.match(r"\s*\d+\s+(\d\d\.\d\d\.\d{4})\s+(.*)", " ".join(cells), re.S)
        if not m:
            continue
        nums = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if len(nums) != 6:
            # Anii vechi au 2-3 trageri pe tiraj; sursa se folosește numai din 2020.
            raise SourceError(f"toto49: rand cu {len(nums)} numere")
        out.append(Draw(_dmy(m.group(1)), tuple(nums)))
    return out


def fetch_bg_toto2(last: dt.date, today: dt.date) -> list[Draw]:
    """toto49.com, arhiva anuală Toto 2 6/49 (neoficială; toto.bg e în spatele
    protecției anti-bot, iar tototiraj.bg nu mai e la zi)."""
    out: list[Draw] = []
    for year in range(last.year, today.year + 1):
        page = _get(f"https://www.toto49.com/arhiv/toto_49/{year}").decode("utf-8", "replace")
        rows = parse_toto49_year(page)
        if not rows and year < today.year:
            raise SourceError(f"toto49: anul {year} fara extrageri")
        out += rows
    out.sort(key=lambda x: x.date)
    return out


@dataclass(frozen=True)
class Source:
    fetch: Callable[[dt.date, dt.date], list[Draw]]
    label: str
    sort_main: bool  # CSV-ul ține numerele principale crescător


SOURCES: dict[str, Source] = {
    "de_lotto": Source(fetch_de_lotto, "lotto.de", False),
    "pl_lotto": Source(fetch_pl_lotto, "wynikilotto.net.pl", True),
    "es_primitiva": Source(fetch_es_primitiva, "lawebdelaprimitiva.com", True),
    "at_lotto": Source(fetch_at_lotto, "win2day.at", True),
    "be_lotto": Source(fetch_be_lotto, "loterie-nationale.be", True),
    "hu_hatos": Source(fetch_hu_hatos, "szerencsejatek.hu", True),
    "cz_sportka": Source(fetch_cz_sportka, "allwyn.cz", False),
    "sk_loto": Source(fetch_sk_loto, "tipos.sk", False),
    "eu_euromillions": Source(fetch_eu_euromillions, "fdj.fr", False),
    "bg_toto2": Source(fetch_bg_toto2, "toto49.com", True),
}


# ---------------------------------------------------------------------------
# Validare, verificare, scriere
# ---------------------------------------------------------------------------


def _layout(lottery) -> list[tuple[int, int]]:
    """(câte numere, max) pentru fiecare urnă din rândul CSV."""
    geo = lottery.geo
    parts = [(geo.draw_n, geo.max_n)]
    if geo.second is not None and not geo.second.modelled:
        parts.append((geo.second.draw_n, geo.second.max_n))
    elif geo.second is not None:
        raise SourceError(f"{lottery.game_id}: a doua urna modelata nu e suportata aici")
    return parts


def _header(lottery) -> list[str]:
    geo = lottery.geo
    cols = ["date"] + [f"n{i}" for i in range(1, geo.draw_n + 1)]
    if geo.second is not None:
        cols += list(geo.second.columns)
    return cols


def validate_draw(lottery, draw: Draw, today: dt.date) -> str | None:
    """Motivul respingerii sau None."""
    if not isinstance(draw.date, dt.date) or draw.date > today + dt.timedelta(days=1):
        return f"data invalida {draw.date}"
    nums = list(draw.nums)
    pos = 0
    for count, max_n in _layout(lottery):
        part = nums[pos : pos + count]
        pos += count
        if len(part) != count or any(type(x) is not int for x in part):
            return f"{draw.date}: numar gresit de valori {draw.nums}"
        if len(set(part)) != count or not all(1 <= x <= max_n for x in part):
            return f"{draw.date}: valori invalide {draw.nums}"
    if pos != len(nums):
        return f"{draw.date}: prea multe valori {draw.nums}"
    return None


def _key(lottery, nums) -> tuple:
    """Comparație independentă de ordinea din urnă (unele surse sortează)."""
    out, pos = [], 0
    for count, _ in _layout(lottery):
        out.append(tuple(sorted(nums[pos : pos + count])))
        pos += count
    return tuple(out)


def _row_nums(lottery, source: Source, nums: tuple[int, ...]) -> list[int]:
    n = lottery.geo.draw_n
    main = sorted(nums[:n]) if source.sort_main else list(nums[:n])
    return main + list(nums[n:])


def read_csv(lottery, path: Path) -> list[Draw]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != _header(lottery):
        raise SourceError(f"antet CSV neasteptat: {rows[0] if rows else 'gol'}")
    out = []
    for r in rows[1:]:
        if not r:
            continue
        out.append(Draw(dt.datetime.strptime(r[0], "%d-%m-%Y").date(), tuple(int(x) for x in r[1:])))
    if not out:
        raise SourceError("CSV fara extrageri")
    return out


def plan_update(lottery, have: list[Draw], src: list[Draw], today: dt.date) -> tuple[list[Draw], int]:
    """(extrageri noi, câte au fost verificate). SourceError la orice problemă."""
    if not src:
        raise SourceError("sursa nu a intors nicio extragere")
    for d in src:
        why = validate_draw(lottery, d, today)
        if why:
            raise SourceError(f"extragere invalida din sursa: {why}")
    for a, b in zip(src, src[1:]):
        if b.date < a.date:
            raise SourceError("sursa nu e in ordine cronologica")
    by_src: dict[dt.date, list[tuple]] = {}
    raw_src: dict[dt.date, list[Draw]] = {}
    for d in src:
        k = _key(lottery, d.nums)
        if k in by_src.get(d.date, []):
            raise SourceError(f"sursa repeta extragerea {d.date} {d.nums}")
        by_src.setdefault(d.date, []).append(k)
        raw_src.setdefault(d.date, []).append(d)
    stored: dict[dt.date, list[tuple]] = {}
    for d in have:
        stored.setdefault(d.date, []).append(_key(lottery, d.nums))
    last = have[-1].date
    check_from = max(have[0].date, last - dt.timedelta(days=CHECK_DAYS))
    new: list[Draw] = []
    checked = 0
    for day in sorted(by_src):
        got, mine = by_src[day], stored.get(day, [])
        if day < check_from:
            continue
        if day < last:
            if sorted(got) != sorted(mine):
                raise SourceError(f"nepotrivire {day:%d-%m-%Y}: sursa {got} fata de CSV {mine}")
            checked += len(got)
        elif day == last:
            # Rândurile zilei din CSV trebuie să fie începutul listei oficiale;
            # se adaugă numai o a doua extragere a aceleiași zile.
            if got[: len(mine)] != mine:
                raise SourceError(f"nepotrivire {day:%d-%m-%Y}: sursa {got} fata de CSV {mine}")
            checked += len(mine)
            new += raw_src[day][len(mine) :]
        else:
            new += raw_src[day]
    if checked == 0:
        # Fără suprapunere nu putem dovedi că sursa e aceeași cu istoricul.
        raise SourceError(f"nicio extragere comuna cu CSV-ul (ultima din CSV: {last:%d-%m-%Y})")
    return new, checked


def write_atomic(lottery, source: Source, path: Path, new: list[Draw]) -> None:
    """Rescrie fișierul întreg (tmp + os.replace), LF, cu rândurile noi la final."""
    original = path.read_bytes()
    if not original.endswith(b"\n"):
        original += b"\n"
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for d in new:
        w.writerow([d.date.strftime("%d-%m-%Y"), *_row_nums(lottery, source, d.nums)])
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(original + buf.getvalue().encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_game(lottery, root: Path, today: dt.date, dry_run: bool = False) -> tuple[int, str]:
    """(rânduri adăugate, linia de stare). Nu aruncă niciodată."""
    name = lottery.name if lottery.name == lottery.country_name else f"{lottery.country_name} {lottery.name}"
    source = SOURCES.get(lottery.game_id)
    if source is None:
        return 0, f"  {name:<28}: fara sursa de actualizare — sar peste."
    path = root / lottery.csv
    try:
        have = read_csv(lottery, path)
    except Exception as exc:
        return 0, f"  {name:<28}: CSV ilizibil ({exc}) — SAR peste, nu scriu nimic."
    last = have[-1].date
    last_str = last.strftime("%d-%m-%Y")
    try:
        src = source.fetch(last, today)
    except Exception as exc:
        return 0, (
            f"  {name:<28}: CSV={last_str} | {source.label}=EROARE ({type(exc).__name__})"
            " — continua cu datele existente."
        )
    try:
        new, checked = plan_update(lottery, have, src, today)
    except Exception as exc:
        return 0, (
            f"  {name:<28}: CSV={last_str} | {source.label}: EROARE verificare ({exc})"
            " — nu scriu nimic."
        )
    site_str = src[-1].date.strftime("%d-%m-%Y")
    if not new:
        return 0, f"  {name:<28}: CSV={last_str} | {source.label}={site_str} | la zi ({checked} verificate)."
    dates = ", ".join(d.date.strftime("%d-%m-%Y") for d in new)
    if dry_run:
        return 0, f"  {name:<28}: CSV={last_str} -> {source.label}={site_str} | {len(new)} extrageri noi (dry-run, nescrise): {dates}"
    try:
        write_atomic(lottery, source, path, new)
    except Exception as exc:
        return 0, f"  {name:<28}: EROARE la scriere ({type(exc).__name__}: {exc}) — fisierul ramane neschimbat."
    return len(new), f"  {name:<28}: CSV={last_str} -> {source.label}={site_str} | +{len(new)} extrageri noi: {dates}"


def update_all(root: Path = ROOT, games: list[str] | None = None, dry_run: bool = False, today: dt.date | None = None) -> int:
    from loto_enterprise.core.lotteries import GAMES

    today = today or dt.date.today()
    print(f"[UPDATE-EXTERNE] Istorice externe (_ISTORIC/externe), data curenta: {today:%d-%m-%Y}")
    total = 0
    for lot in GAMES:
        if lot.is_romanian or (games and lot.game_id not in games):
            continue
        try:
            added, line = update_game(lot, root, today, dry_run)
        except Exception as exc:  # plasă finală: nu oprim niciodată lansatorul
            added, line = 0, f"  {lot.game_id}: EROARE neasteptata ({type(exc).__name__}) — sar peste."
        print(line, flush=True)
        total += added
    if total:
        print(f"[UPDATE-EXTERNE] Total adaugate: {total} extrageri noi in _ISTORIC/externe/.")
    else:
        print("[UPDATE-EXTERNE] Nicio extragere noua scrisa in _ISTORIC/externe/.")
    return total


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--game", action="append", help="game_id din registru (repetabil)")
    ap.add_argument("--root", default=str(ROOT), help="radacina care contine _ISTORIC/")
    try:
        a = ap.parse_args(argv)
        update_all(Path(a.root), a.game, a.dry_run)
    except SystemExit:
        pass
    except Exception as exc:
        print(f"[UPDATE-EXTERNE] EROARE neasteptata ({type(exc).__name__}: {exc}) — istoricele raman neschimbate.")
    return 0


if __name__ == "__main__":
    main()
    sys.exit(0)  # mereu 0 — best-effort
