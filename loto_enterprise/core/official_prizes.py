"""Official Romanian draw prizes, with a persistent, month-based SQLite cache.

Network access is explicit: reads never download anything unless ``fetch=True``.
Call ``ensure_month`` from a background worker, not from the NiceGUI event loop.
Amounts are the published values in hundredths of a leu, without redenomination
or tax calculations. A REPORT is never exposed as a prize paid to a winner.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Iterable
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid


_BASE = "https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/"
ARCHIVE_URLS = {
    "loto_6_49": _BASE + "649_si_noroc/rezultate_extragere.html",
    "loto_5_40": _BASE + "540_si_super_noroc/rezultate_extrageri.html",
    "joker": _BASE + "joker_si_noroc_plus/rezultate_extrageri.html",
}
_ALIASES = {
    "6/49": "loto_6_49",
    "649": "loto_6_49",
    "5/40": "loto_5_40",
    "540": "loto_5_40",
    "joker_urna1": "joker",
}
_HEADINGS = {"loto_6_49": "6/49", "loto_5_40": "5/40", "joker": "joker"}
_CATEGORY_ORDER = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
_MAX_RESPONSE_BYTES = 2_000_000
_PARSER_CACHE_REVISION = 2
_HTTP_SLOTS = threading.BoundedSemaphore(2)


class ArchiveError(ValueError):
    """An unavailable or invalid archive response, never an invented payout."""

    def __init__(self, message: str, *, retry_seconds: int | None = None):
        super().__init__(message)
        self.retry_seconds = retry_seconds


@dataclass(frozen=True)
class PrizeCategory:
    category: str
    matches_label: str
    winners: int
    amount_bani: int | None
    report_bani: int | None
    raw_value_bani: int | None
    note: str | None = None  # Optional official footnote; old cache rows omit it.


@dataclass(frozen=True)
class PrizeDraw:
    game: str
    draw_date: str
    numbers: tuple[int, ...]
    joker: int | None
    categories: tuple[PrizeCategory, ...]
    source_url: str
    currency: str = "lei"
    order_known: bool = True
    supplementary: bool | None = field(default=None, compare=False)
    official_id: str | None = field(default=None, compare=False)
    draw_order: int | None = field(default=None, compare=False)
    # Only a result edition explicitly numbered by the source has this field.
    result_version: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class MonthStatus:
    status: str
    draws_count: int
    error: str | None = None


def normalize_game(game: str) -> str:
    game = str(game).strip().lower()
    game = _ALIASES.get(game, game)
    if game not in ARCHIVE_URLS:
        raise ValueError(f"Unsupported official prize archive game: {game}")
    return game


def _date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value).strip().split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid draw date: {value}")


def _plain(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    ).casefold()


def _money(value: str) -> int | None:
    text = re.sub(r"\s+", "", value).strip()
    if text in {"", "-", "–", "—"}:
        return None
    if re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?", text):
        normalized = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{1,2}", text):
        # Some official tables mix RO and English amounts in the same row.
        # Require an explicit decimal point with 1-2 digits; a grouping-only
        # dot remains Romanian, while ambiguous comma-only groups are refused.
        normalized = text.replace(",", "")
    else:
        raise ArchiveError(f"Invalid published prize value: {value!r}")
    return int(Decimal(normalized) * 100)


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Node | str] = []

    def nodes(self, tag=None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.nodes(tag)

    def text(self):
        return " ".join(
            child.text() if isinstance(child, _Node) else child
            for child in self.children
        ).strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()


class _Document(HTMLParser):
    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _selected_month(document: _Node) -> tuple[int, int] | None:
    selected = {}
    for select in document.nodes("select"):
        name = select.attrs.get("name")
        if name in {"select-year", "select-month"}:
            values = [
                n.attrs.get("value") for n in select.nodes("option")
                if "selected" in n.attrs
            ]
            if len(values) == 1 and str(values[0]).isdigit():
                selected[name] = int(values[0])
    if len(selected) == 2:
        return selected["select-year"], selected["select-month"]
    return None


def _draw_attribute(block, heading, names):
    values = {node.attrs[name].strip() for node in (block, heading) for name in names if name in node.attrs}
    if len(values) > 1 or "" in values:
        raise ArchiveError("Conflicting official draw identity attributes")
    return next(iter(values), None)


def parse_month_html(
    game: str, html: str, year: int, month: int, *, draw_dates: set[str] | None = None,
    preserve_event_identity: bool = False,
) -> list[PrizeDraw]:
    """Parse official tables, with main draws before supplements.

    Event consumers need every observation, including two distinct events with
    identical results. Content-keyed historical and payout consumers use the
    existing projection by drawn numbers.
    """
    game = normalize_game(game)
    document = _Document(html).root
    if _selected_month(document) != (year, month):
        raise ArchiveError("Official archive did not confirm the requested month")
    by_date: dict[str, dict[tuple[tuple[int, ...], int | None], tuple[PrizeDraw, bool]]] = {}
    observed: dict[str, list[PrizeDraw]] = {}
    for block in document.nodes("div"):
        if not block.has_class("rezultate-extrageri-content"):
            continue
        headings = [n for n in block.nodes("div") if n.has_class("button-open-details")]
        if not headings:
            continue
        heading = " ".join(headings[0].text().split())
        pattern = (
            r"detalii castiguri\s+(?P<supplementary>extrageri speciale\s+)?"
            rf"la\s+{re.escape(_HEADINGS[game])}"
            r"\s+din\s+(?P<date>\d{2}[.\-/]\d{2}[.\-/]\d{4})"
        )
        match = re.search(pattern, _plain(heading))
        if not match:
            continue  # The companion Noroc game has a separate prize table.
        draw_date = _date(match["date"])
        supplementary = match["supplementary"] is not None
        if (draw_date.year, draw_date.month) != (year, month):
            raise ArchiveError("Official archive returned a draw from another month")
        if draw_dates is not None and draw_date.isoformat() not in draw_dates:
            continue
        number_blocks = [n for n in block.nodes("div") if n.has_class("numere-extrase")]
        if len(number_blocks) != 1:
            raise ArchiveError("Official drawn numbers are missing or ambiguous")
        numbers = []
        joker = None
        for image in number_blocks[0].nodes("img"):
            path = unquote(urlparse(image.attrs.get("src", "")).path)
            ball = re.search(r"/images/bile/(?:(joker)/)?(\d+) *\.png$", path)
            if not ball:
                raise ArchiveError("Unrecognized official draw ball image")
            if ball.group(1):
                if joker is not None:
                    raise ArchiveError("More than one Joker ball")
                joker = int(ball.group(2))
            else:
                numbers.append(int(ball.group(2)))
        expected_n, maximum = (5, 45) if game == "joker" else (6, 40 if game == "loto_5_40" else 49)
        if len(numbers) != expected_n or len(set(numbers)) != expected_n:
            raise ArchiveError("Invalid official drawn number count")
        if any(not 1 <= n <= maximum for n in numbers):
            raise ArchiveError("Official drawn number outside the game universe")
        if (game == "joker" and (joker is None or not 1 <= joker <= 20)) or (game != "joker" and joker is not None):
            raise ArchiveError("Invalid official Joker ball")
        tables = [n for n in block.nodes("table") if n.has_class("results-table")]
        if len(tables) != 1:
            raise ArchiveError("Official prize table is missing or ambiguous")
        rows = list(tables[0].nodes("tr"))
        notes = [" ".join(row.text().split()) for row in rows
                 if row.has_class("total") and re.match(r"note\s*:\s*\*", _plain(row.text()))]
        starred = [row for row in rows if any(
            re.sub(r"\s+", "", cell.text()).upper() == "REPORT*"
            for cell in list(row.nodes("td"))[1:2]
        )]
        # Map a unique star to a unique official note only. Other table notes
        # must never be attributed to an unrelated category.
        report_note = notes[0] if len(notes) == len(starred) == 1 else None
        categories = []
        for row in rows:
            cells = [n.text() for n in row.nodes("td")]
            if not cells or row.has_class("total"):
                continue
            category = re.fullmatch(r"\s*([IVX]+)\s*\(([^)]+)\)\s*", cells[0])
            if len(cells) != 4 or not category or category.group(1) not in _CATEGORY_ORDER:
                raise ArchiveError("Unrecognized official prize category row")
            winner_text = re.sub(r"\s+", "", cells[1]).upper()
            if winner_text in {"REPORT", "REPORT*"}:
                winners = 0
            elif re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+)", winner_text):
                winners = int(winner_text.replace(".", ""))
            else:
                raise ArchiveError("Unrecognized official winner count")
            raw = _money(cells[2])
            if winners > 0 and raw is None:
                raise ArchiveError("Paid category has no published prize value")
            categories.append(PrizeCategory(
                category.group(1), category.group(2), winners,
                raw if winners > 0 else None, _money(cells[3]), raw,
                report_note if winner_text == "REPORT*" else None,
            ))
        names = [c.category for c in categories]
        expected_names = list(_CATEGORY_ORDER[:8 if game == "joker" else 3])
        if game == "loto_6_49" and names == ["I", "II", "III", "IV"]:
            expected_names.append("IV")
        if names != expected_names:
            raise ArchiveError("Official prize categories are incomplete or duplicated")
        # Presentation IDs, element order and URL fragments do not identify an
        # event. Only explicitly named draw attributes carry this meaning.
        official_id = _draw_attribute(block, headings[0], ("data-draw-id", "data-extragere-id"))
        ordinal = _draw_attribute(block, headings[0], ("data-draw-order", "data-ordine-extragere"))
        if ordinal is not None and not re.fullmatch(r"[1-9][0-9]{0,2}", ordinal):
            raise ArchiveError("Invalid official draw order")
        version = _draw_attribute(block, headings[0], ("data-result-version", "data-versiune-rezultat"))
        if version is not None and not re.fullmatch(r"[1-9][0-9]{0,17}", version):
            raise ArchiveError("Invalid official result version")
        draw = PrizeDraw(
            game, draw_date.isoformat(), tuple(numbers), joker,
            tuple(categories), ARCHIVE_URLS[game], supplementary=supplementary,
            official_id=official_id, draw_order=int(ordinal) if ordinal is not None else None,
            result_version=int(version) if version is not None else None,
        )
        if preserve_event_identity:
            observed.setdefault(draw.draw_date, []).append(draw)
            continue
        same_day = by_date.setdefault(draw.draw_date, {})
        key = (_number_key(draw.numbers, game), draw.joker)
        previous = same_day.get(key)
        if previous is not None and previous[0] != draw:
            raise ArchiveError("Conflicting official tables for the same draw")
        if previous is None or not supplementary:
            same_day[key] = draw, supplementary

    # The archive can display the supplementary table first. Keep date and
    # same-kind table order, but emit the main draw before its supplements.
    if preserve_event_identity:
        return [draw for same_day in observed.values() for draw in sorted(
            same_day, key=lambda item: (item.supplementary, item.draw_order or 0),
        )]
    return [
        draw
        for same_day in by_date.values()
        for draw, _ in sorted(same_day.values(), key=lambda item: item[1])
    ]


def _fetch_month_html(game: str, year: int, month: int, timeout: float = 10) -> str:
    # This POST only submits the archive's read-only year/month filter. GET
    # parameters are ignored by the official server and return the current month.
    request = Request(
        ARCHIVE_URLS[game],
        data=urlencode({"select-year": year, "select-month": month}).encode("ascii"),
        headers={"User-Agent": "LOTO official-prize-cache/1.0", "Accept": "text/html"},
    )
    with _HTTP_SLOTS:
        for attempt in range(2):
            try:
                with urlopen(request, timeout=timeout) as response:
                    if urlparse(response.url).hostname not in {"www.loto.ro", "loto.ro"}:
                        raise ArchiveError("Official archive redirected outside loto.ro")
                    data = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(data) > _MAX_RESPONSE_BYTES:
                        raise ArchiveError("Official archive response is too large")
                    return data.decode(response.headers.get_content_charset() or "utf-8")
            except HTTPError as exc:
                if exc.code == 429:
                    retry = exc.headers.get("Retry-After", "")
                    seconds = min(int(retry), 86400) if retry.isdigit() else 3600
                    raise ArchiveError("Official archive rate limit", retry_seconds=seconds) from exc
                if exc.code < 500 or attempt:
                    raise ArchiveError(f"Official archive HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt:
                    raise ArchiveError(f"Official archive unavailable: {type(exc).__name__}") from exc
    raise ArchiveError("Official archive unavailable")


def _number_key(numbers, game=None) -> str:
    original = tuple(numbers)
    values = tuple(int(n) for n in original)
    if any(isinstance(n, bool) or converted != n for n, converted in zip(original, values)):
        raise ValueError("Draw numbers must be integers")
    if len(values) != len(set(values)):
        raise ValueError("Draw numbers must be distinct")
    if game == "loto_5_40" and len(values) == 6:
        return ",".join(map(str, sorted(values[:5]))) + "|" + str(values[-1])
    return ",".join(map(str, sorted(values)))


class PrizeCache:
    """Persistent prizes; independent connections and fetch leases across workers."""

    def __init__(
        self, db_path: str | Path | None = None, *,
        fetcher: Callable[[str, int, int], str] | None = None,
        now: Callable[[], float] = time.time,
    ):
        if db_path is None:
            from runtime_paths import RUNTIME_ROOT

            db_path = RUNTIME_ROOT / "official_prizes.db"
        self.db_path = Path(db_path)
        self._fetcher = fetcher or _fetch_month_html
        self._now = now
        self._schema_lock = threading.Lock()
        self._initialized = False

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if not self._initialized:
                with self._schema_lock:
                    if not self._initialized:
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.executescript("""
                            CREATE TABLE IF NOT EXISTS prize_draws (
                                game TEXT NOT NULL, draw_date TEXT NOT NULL,
                                number_key TEXT NOT NULL, joker INTEGER NOT NULL,
                                payload TEXT NOT NULL, fetched_at REAL NOT NULL,
                                source_sha256 TEXT NOT NULL,
                                PRIMARY KEY (game, draw_date, number_key, joker)
                            );
                            CREATE TABLE IF NOT EXISTS prize_months (
                                game TEXT NOT NULL, year INTEGER NOT NULL,
                                month INTEGER NOT NULL, status TEXT NOT NULL,
                                retry_after REAL NOT NULL, checked_at REAL NOT NULL,
                                failures INTEGER NOT NULL DEFAULT 0,
                                draws_count INTEGER NOT NULL DEFAULT 0,
                                error TEXT, lease_token TEXT,
                                PRIMARY KEY (game, year, month)
                            );
                        """)
                        if connection.execute("PRAGMA user_version").fetchone()[0] < _PARSER_CACHE_REVISION:
                            connection.execute("BEGIN IMMEDIATE")
                            if connection.execute("PRAGMA user_version").fetchone()[0] < _PARSER_CACHE_REVISION:
                                # Older parsers ignored the special-draw heading. Make a missing
                                # draw eligible for refresh while retaining every verified payout.
                                legacy = connection.execute(
                                    "SELECT rowid,payload FROM prize_draws WHERE game='loto_5_40' "
                                    "AND instr(number_key,'|')=0"
                                ).fetchall()
                                connection.executemany(
                                    "UPDATE prize_draws SET number_key=? WHERE rowid=?",
                                    [(_number_key(json.loads(payload)["numbers"], "loto_5_40"), rowid)
                                     for rowid, payload in legacy],
                                )
                                connection.execute("UPDATE prize_months SET retry_after=0 WHERE status='complete'")
                                connection.execute(f"PRAGMA user_version = {_PARSER_CACHE_REVISION}")
                            connection.commit()
                        self._initialized = True
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _decode(payload: str) -> PrizeDraw:
        row = json.loads(payload)
        row["numbers"] = tuple(row["numbers"])
        row["categories"] = tuple(PrizeCategory(**c) for c in row["categories"])
        return PrizeDraw(**row)

    def get_draws(self, game: str, draws: Iterable[tuple]) -> list[PrizeDraw | None]:
        """Aligned batch reads, using one connection and no network requests.

        A request is ``(date, main_numbers, actual_joker_or_None)``. Unknown
        Joker is accepted only when exactly one official draw matches the main
        number set on that date. An ascending legacy 5/40 row has no usable
        sixth-ball position and likewise requires a unique official set.
        """
        game = normalize_game(game)
        requests = []
        for draw_date, numbers, joker in draws:
            if joker is not None and (isinstance(joker, bool) or int(joker) != joker):
                raise ValueError("Joker number must be an integer")
            numbers = tuple(numbers)
            key = _number_key(numbers, game)
            legacy_540 = game == "loto_5_40" and joker in (None, 0) and numbers == tuple(sorted(numbers))
            requests.append((_date(draw_date).isoformat(), key, joker, _number_key(numbers) if legacy_540 else None))
        if not requests:
            return []
        results = []
        legacy_by_date = {}
        with closing(self._connect()) as connection:
            for draw_date, key, joker, unordered in requests:
                if unordered is not None:
                    if draw_date not in legacy_by_date:
                        rows = connection.execute(
                            "SELECT payload FROM prize_draws WHERE game=? AND draw_date=? AND joker=0",
                            (game, draw_date),
                        ).fetchall()
                        matching = {}
                        for row in rows:
                            official = self._decode(row[0])
                            matching.setdefault(_number_key(official.numbers), []).append(official)
                        legacy_by_date[draw_date] = matching
                    draws_on_set = legacy_by_date[draw_date].get(unordered, ())
                    results.append(draws_on_set[0] if len(draws_on_set) == 1 else None)
                    continue
                if joker is None:
                    rows = connection.execute(
                        "SELECT payload FROM prize_draws WHERE game=? AND draw_date=? AND number_key=?",
                        (game, draw_date, key),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT payload FROM prize_draws WHERE game=? AND draw_date=? AND number_key=? AND joker=?",
                        (game, draw_date, key, int(joker)),
                    ).fetchall()
                results.append(self._decode(rows[0][0]) if len(rows) == 1 else None)
        return results

    def get_draw(self, game, draw_date, numbers, joker=None, *, fetch=False):
        numbers = tuple(numbers)
        found = self.get_draws(game, [(draw_date, numbers, joker)])[0]
        if found is None and fetch:
            parsed_date = _date(draw_date)
            self.ensure_month(game, parsed_date.year, parsed_date.month)
            found = self.get_draws(game, [(draw_date, numbers, joker)])[0]
        return found

    def ensure_month(self, game: str, year: int, month: int, *, force=False) -> MonthStatus:
        """Download a missing/stale month once, with a cross-process fetch lease.

        Past successful months are stable until explicitly forced. The current
        month can refresh after 15 minutes; missing months and failures back off.
        Existing positive rows are kept when a refresh fails or becomes empty.
        """
        game = normalize_game(game)
        year, month = int(year), int(month)
        if not 1998 <= year <= 2100 or not 1 <= month <= 12:
            raise ValueError("Invalid official archive month")
        now = self._now()
        token = uuid.uuid4().hex
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM prize_months WHERE game=? AND year=? AND month=?",
                (game, year, month),
            ).fetchone()
            if old and old["retry_after"] > now and (not force or old["status"] == "fetching"):
                connection.rollback()
                status = {"complete": "cached", "fetching": "busy"}.get(old["status"], old["status"])
                return MonthStatus(status, old["draws_count"], old["error"])
            failures = old["failures"] if old else 0
            previous_count = old["draws_count"] if old else 0
            connection.execute("""
                INSERT INTO prize_months
                (game,year,month,status,retry_after,checked_at,failures,draws_count,error,lease_token)
                VALUES (?,?,?,'fetching',?,?,?,?,NULL,?)
                ON CONFLICT(game,year,month) DO UPDATE SET
                    status='fetching',retry_after=excluded.retry_after,
                    checked_at=excluded.checked_at,lease_token=excluded.lease_token
                """, (game, year, month, now + 120, now, failures, previous_count, token))
            connection.commit()
        finally:
            connection.close()
        try:
            html = self._fetcher(game, year, month)
            draws = parse_month_html(game, html, year, month)
            if len(draws) < previous_count:
                raise ArchiveError("Incomplete archive would remove verified prize draws")
            digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
            today = datetime.fromtimestamp(now, timezone.utc).date()
            past = (year, month) < (today.year, today.month)
            state = "complete" if draws else "missing"
            retry_after = now + (100 * 366 * 86400 if draws and past else 900 if draws else 86400)
            start = date(year, month, 1).isoformat()
            end = date(year + (month == 12), 1 if month == 12 else month + 1, 1).isoformat()
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                lease = connection.execute(
                    "SELECT lease_token FROM prize_months WHERE game=? AND year=? AND month=?",
                    (game, year, month),
                ).fetchone()
                if lease is None or lease[0] != token:
                    connection.rollback()
                    return MonthStatus("busy", previous_count)
                connection.execute(
                    "DELETE FROM prize_draws WHERE game=? AND draw_date>=? AND draw_date<?",
                    (game, start, end),
                )
                connection.executemany(
                    "INSERT INTO prize_draws VALUES (?,?,?,?,?,?,?)",
                    [(game, draw.draw_date, _number_key(draw.numbers, game), draw.joker or 0,
                      json.dumps(asdict(draw), ensure_ascii=False), now, digest)
                     for draw in draws],
                )
                connection.execute("""
                    UPDATE prize_months SET status=?,retry_after=?,checked_at=?,
                    failures=0,draws_count=?,error=NULL,lease_token=NULL
                    WHERE game=? AND year=? AND month=? AND lease_token=?
                    """, (state, retry_after, now, len(draws), game, year, month, token))
                connection.commit()
            finally:
                connection.close()
            return MonthStatus("fetched" if draws else "missing", len(draws))
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:250]}"
            delay = getattr(exc, "retry_seconds", None) or min(21600, 60 * 2 ** min(failures, 8))
            connection = self._connect()
            try:
                connection.execute("""
                    UPDATE prize_months SET status='error',retry_after=?,checked_at=?,
                    failures=?,error=?,lease_token=NULL
                    WHERE game=? AND year=? AND month=? AND lease_token=?
                    """, (now + delay, now, failures + 1, error, game, year, month, token))
                connection.commit()
            finally:
                connection.close()
            return MonthStatus("error", previous_count, error)
