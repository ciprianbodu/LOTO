"""Official unit prizes for historical variants; UI callbacks never perform I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
import math
import threading
import time
from urllib.parse import urlsplit


logger = logging.getLogger("app_nicegui")
_BUSY_WAIT_SECONDS = 25.0
_BUSY_RETRY_SECONDS = 2.0

_GAMES = {
    "6/49": "loto_6_49",
    "loto_6_49": "loto_6_49",
    "5/40": "loto_5_40",
    "loto_5_40": "loto_5_40",
    "joker": "joker",
}


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _request(row: dict):
    when = _date(row.get("label"))
    try:
        source_numbers = tuple(row.get("drawn") or ())
        if any(
            isinstance(n, bool) or not math.isfinite(float(n))
            or not float(n).is_integer()
            for n in source_numbers
        ):
            return None
        numbers = tuple(sorted(int(n) for n in source_numbers))
        raw_variant = tuple(int(n) for n in row.get("best_variant_raw") or ())
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        when is None or len(numbers) not in {5, 6}
        or len(set(numbers)) != len(numbers) or not row.get("best_variant")
    ):
        return None
    # A true hit identifies the actual Joker. A false flag does not tell us
    # which of the other nineteen balls was drawn, so matching must stay unique.
    joker = (
        raw_variant[-1]
        if len(raw_variant) == 6 and row.get("best_variant_joker_hit") is True
        else None
    )
    return when.isoformat(), numbers, joker


@dataclass(frozen=True)
class PrizeView:
    state: str = "pending"
    draw: object | None = None


@dataclass(frozen=True)
class HitPrizeText:
    text: str
    source_url: str = ""
    detail: str = ""


def _official_url(value) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"loto.ro", "www.loto.ro"}
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    ):
        return str(value)
    return ""


def _money_number(bani: int) -> str:
    whole, cents = divmod(int(bani), 100)
    return f"{whole:,}".replace(",", ".") + f",{cents:02d}"


def hit_prize_text(game: str, row: dict, view: PrizeView) -> HitPrizeText:
    """Label for the displayed actual variant, never for the pool or an aggregate."""
    base = f"{int(row.get('best_ticket') or 0)} numere"
    canonical = _GAMES.get(str(game).strip().casefold())
    if canonical is None:
        return HitPrizeText(base)
    request = _request(row)
    if request is None:
        return HitPrizeText(base + " (premiu indisponibil: context incomplet)")
    if view.state in {"pending", "busy"}:
        return HitPrizeText(base + " (premiu oficial în curs de încărcare)")
    draw = view.draw
    if draw is None:
        return HitPrizeText(base + " (premiu oficial indisponibil)")
    # A stale result from another history or a same-day supplementary draw
    # must never acquire the current variant's cash label.
    try:
        identity_ok = (
            _GAMES.get(str(draw.game).strip().casefold()) == canonical
            and _date(draw.draw_date) == _date(row.get("label"))
            and set(int(n) for n in draw.numbers) == set(request[1])
            and (request[2] is None or draw.joker == request[2])
        )
    except (AttributeError, TypeError, ValueError):
        identity_ok = False
    if not identity_ok:
        return HitPrizeText(base + " (premiu oficial indisponibil)")

    from loto_enterprise.core.prize_categories import resolve_variant_category

    match = resolve_variant_category(
        game,
        row.get("best_variant_raw") or row.get("best_variant"),
        draw.numbers,
        official_joker=draw.joker,
        joker_hit=row.get("best_variant_joker_hit"),
        draw_date=draw.draw_date,
        available_categories=draw.categories,
        draw_order_known=getattr(draw, "order_known", True),
    )
    if match.category is None or match.main_hits != int(row.get("best_ticket") or 0):
        suffix = {
            "ambiguous_category": "categoria I/II neconfirmată",
            "missing_joker": "categoria Joker neconfirmată",
            "no_prize": "fără categorie de premiu",
        }.get(match.status, "categoria de premiu neconfirmată")
        return HitPrizeText(base + f" ({suffix})")
    category = next(
        (c for c in draw.categories if c.category == match.category), None
    )
    if category is None:
        return HitPrizeText(base + " (premiu oficial indisponibil)")
    source = _official_url(draw.source_url)
    category_text = f"cat. {match.category}"
    detail = (
        f"Loteria Română · {_date(draw.draw_date).strftime('%d-%m-%Y')} · "
        f"{category_text}. Valoarea publicată pentru o variantă câștigătoare."
        f" În pagina sursă, selectați luna {_date(draw.draw_date).strftime('%m/%Y')}."
    )
    if category.winners == 0:
        return HitPrizeText(
            base + f" ({category_text} — report)",
            source,
            detail + " Nu s-a plătit un câștig la această categorie.",
        )
    if category.amount_bani is None or category.amount_bani < 0:
        return HitPrizeText(base + f" ({category_text} — valoare indisponibilă)")
    amount = _money_number(category.amount_bani)
    currency = str(getattr(draw, "currency", "lei") or "lei").strip()
    if _date(draw.draw_date) < date(2005, 7, 1) and currency.casefold() == "lei":
        amount += ", valoare istorică publicată"
        detail += (
            " Unitatea monetară istorică nu este confirmată de sursă; "
            "suma nu este convertită în lei actuali."
        )
    else:
        amount += " " + currency
    return HitPrizeText(
        base + f" ({category_text} — {amount})", source, detail
    )


class PrizeHistoryLookup:
    """Bounded per-panel work queue; only its worker touches SQLite and HTTP.

    Call ``request`` only for currently displayed table pages. Each draw is
    attempted once per panel; the shared SQLite cache owns retry/refresh TTLs.
    ``view`` and ``version`` only read this object's in-memory snapshots.
    """

    def __init__(self, game: str, *, cache_factory=None):
        self.game = _GAMES.get(str(game).strip().casefold())
        self._cache_factory = cache_factory
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._active: set = set()
        self._views: dict = {}
        self._retry_at: dict = {}
        self._running = False
        self._closed = False
        self._version = 0
        self._thread = None
        self._stop = threading.Event()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def view(self, row: dict) -> PrizeView:
        key = _request(row)
        if key is None:
            return PrizeView("missing_context")
        with self._lock:
            return self._views.get(key, PrizeView())

    @property
    def retry_due(self) -> bool:
        """Memory-only signal to schedule a bounded retry of a foreign lease."""
        with self._lock:
            return any(at <= time.monotonic() for at in self._retry_at.values())

    def request(self, rows, *, retry_missing=False) -> None:
        if self.game is None:
            return
        with self._lock:
            if self._closed:
                return
            for row in rows:
                key = _request(row)
                busy_due = (
                    key in self._retry_at and self._retry_at[key] <= time.monotonic()
                )
                if (
                    (retry_missing or busy_due) and key in self._views
                    and self._views[key].draw is None
                ):
                    del self._views[key]
                    self._retry_at.pop(key, None)
                    self._version += 1
                if (
                    key is not None and key not in self._views
                    and key not in self._active
                ):
                    self._pending[key] = retry_missing or self._pending.get(key, False)
            if not self._pending or self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, name="official-prize-history", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._active.clear()

    def wait_idle(self, timeout=10) -> None:
        """Join only for offline tests; never called by UI callbacks."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _publish(self, keys, draws) -> None:
        with self._lock:
            for key, draw in zip(keys, draws):
                self._views[key] = PrizeView(
                    "ready" if draw is not None else "missing", draw
                )
                self._retry_at.pop(key, None)
                self._active.discard(key)
            self._version += 1

    def _month_draws(self, cache, year, month, keys, force):
        status = cache.ensure_month(
            self.game, year, month, **({"force": True} if force else {})
        )
        deadline = time.monotonic() + _BUSY_WAIT_SECONDS
        while getattr(status, "status", None) == "busy":
            found = cache.get_draws(self.game, keys)
            if all(draw is not None for draw in found):
                return found
            if self._stop.wait(0.25) or time.monotonic() >= deadline:
                return None
            # force is only for the initial explicit retry, never for polling
            # another process's live lease.
            status = cache.ensure_month(self.game, year, month)
        return cache.get_draws(self.game, keys)

    def _publish_busy(self, keys):
        with self._lock:
            for key in keys:
                self._views[key] = PrizeView("busy")
                self._retry_at[key] = time.monotonic() + _BUSY_RETRY_SECONDS
                self._active.discard(key)
            self._version += 1

    def _loop(self) -> None:
        try:
            if self._cache_factory is None:
                from loto_enterprise.core.official_prizes import PrizeCache

                cache = PrizeCache()
            else:
                cache = self._cache_factory()
            while True:
                with self._lock:
                    if self._closed or not self._pending:
                        self._running = False
                        return
                    force = dict(self._pending)
                    keys = list(force)
                    self._pending.clear()
                    self._active.update(keys)
                try:
                    cached = cache.get_draws(self.game, keys)
                    hits = [
                        (key, draw) for key, draw in zip(keys, cached)
                        if draw is not None
                    ]
                    if hits:
                        self._publish(
                            [key for key, _ in hits], [draw for _, draw in hits]
                        )
                    months: dict = {}
                    for key, draw in zip(keys, cached):
                        if draw is None:
                            when = _date(key[0])
                            months.setdefault((when.year, when.month), []).append(key)
                    for (year, month), missing in sorted(months.items(), reverse=True):
                        with self._lock:
                            if self._closed:
                                self._active.clear()
                                self._running = False
                                return
                        found = self._month_draws(
                            cache, year, month, missing,
                            any(force[key] for key in missing),
                        )
                        if found is None:
                            self._publish_busy(missing)
                        else:
                            self._publish(missing, found)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PREMII] Arhiva oficială indisponibilă: %s", exc)
                    with self._lock:
                        outstanding = [key for key in keys if key in self._active]
                    self._publish(outstanding, [None] * len(outstanding))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PREMII] Cache-ul oficial indisponibil: %s", exc)
            with self._lock:
                keys = list(self._pending) + list(self._active)
                self._pending.clear()
                self._active.clear()
                self._views.update((key, PrizeView("missing")) for key in keys)
                self._version += 1
                self._running = False

