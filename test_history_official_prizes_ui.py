"""Unit prize labels for actual historical variants, with lazy background I/O."""

from dataclasses import replace
from datetime import date, timedelta
from threading import Event, get_ident
import time
from types import SimpleNamespace

import pytest

import app_nicegui  # noqa: F401
import ui_hits
import ui_prizes
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from scripts.analysis.audit_output import capture_ui
from ui_prizes import PrizeHistoryLookup, PrizeView, hit_prize_text


def category(cat="III", label="4/6", winners=212, amount=101535):
    return PrizeCategory(cat, label, winners, amount, 123456789, 999999999)


def draw(game="loto_6_49", *, numbers=(1, 2, 3, 4, 40, 41), cats=None, joker=None):
    return PrizeDraw(
        game, "2026-10-29", numbers, joker, tuple(cats or [category()]),
        "https://www.loto.ro/loto-new/rezultate_extragere.html",
    )


def record(
    *, numbers=(1, 2, 3, 4, 40, 41), variant=(1, 2, 3, 4, 10, 11),
    hits=4, joker_hit=None,
):
    return {
        "label": "29-10-2026", "drawn": list(numbers), "pool": 6,
        "best_ticket": hits,
        "best_variant": sorted(variant[:5] if joker_hit is not None else variant),
        "best_variant_raw": list(variant), "best_variant_joker_hit": joker_hit,
    }


def test_label_uses_actual_variant_and_unit_amount_not_pool_fund_or_report():
    result = hit_prize_text("6/49", record(), PrizeView("ready", draw()))
    assert result.text == "4 numere (cat. III — 1.015,35 lei)"
    assert "999" not in result.text and "123" not in result.text
    assert result.source_url.startswith("https://www.loto.ro/")
    assert "29-10-2026" in result.detail
    assert "În pagina sursă, selectați luna 10/2026." in result.detail


def test_report_has_no_cash_amount_even_if_source_publishes_fund_and_rollover():
    actual = (1, 2, 3, 4, 5, 6)
    item = record(numbers=actual, variant=actual, hits=6)
    official = draw(numbers=actual, cats=[category("I", "6/6", 0, None)])
    result = hit_prize_text("6/49", item, PrizeView("ready", official))
    assert result.text == "6 numere (cat. I — report)"
    assert "lei" not in result.text
    assert "Nu s-a plătit" in result.detail


def test_pre_2005_ambiguous_currency_keeps_the_published_value_without_conversion():
    old = replace(
        draw(cats=[category("II", "5/6", 1, 20337879400)]),
        draw_date="2004-01-11",
    )
    row = {
        **record(variant=(1, 2, 3, 4, 40, 11), hits=5),
        "label": "11-01-2004",
    }
    result = hit_prize_text("6/49", row, PrizeView("ready", old))
    assert "203.378.794,00, valoare istorică publicată" in result.text
    assert "lei" not in result.text and "RON" not in result.text
    assert "nu este confirmată" in result.detail
    explicit = replace(old, currency="ROL")
    assert "203.378.794,00 ROL" in hit_prize_text(
        "6/49", row, PrizeView("ready", explicit)
    ).text


@pytest.mark.parametrize(
    "variant,expected", [((9, 1, 2, 3, 4), "I"), ((1, 2, 3, 4, 5), "II")]
)
def test_five_of_forty_uses_official_extraction_order(variant, expected):
    actual = (9, 1, 2, 3, 4, 5)
    official = draw(
        "loto_5_40", numbers=actual,
        cats=[category("I", "5/6*", 1, 10000), category("II", "5/6", 2, 20000)],
    )
    result = hit_prize_text(
        "5/40", record(numbers=actual, variant=variant, hits=5),
        PrizeView("ready", official),
    )
    assert f"cat. {expected} —" in result.text
    assert ("100,00" if expected == "I" else "200,00") in result.text
    unknown = replace(official, order_known=False)
    assert "categoria I/II neconfirmată" in hit_prize_text(
        "5/40", record(numbers=actual, variant=variant, hits=5),
        PrizeView("ready", unknown),
    ).text


@pytest.mark.parametrize("chosen,flag,expected", [(7, True, "III"), (8, False, "IV")])
def test_joker_prize_checks_urn_two_of_the_displayed_variant(chosen, flag, expected):
    actual = (1, 2, 3, 4, 33)
    official = draw(
        "joker", numbers=actual, joker=7,
        cats=[category("III", "4/5+J", 1, 10000), category("IV", "4/5", 2, 20000)],
    )
    item = record(numbers=actual, variant=(1, 2, 3, 4, 10, chosen), joker_hit=flag)
    assert f"cat. {expected} —" in hit_prize_text(
        "joker", item, PrizeView("ready", official)
    ).text
    item["best_variant_joker_hit"] = not flag
    assert "cat. " not in hit_prize_text(
        "joker", item, PrizeView("ready", official)
    ).text


@pytest.mark.parametrize("other", [
    replace(draw(), draw_date="2026-10-25"),
    replace(draw(), numbers=(1, 2, 3, 4, 40, 42)),
    replace(draw(), game="loto_5_40"),
])
def test_stale_or_same_day_different_draw_never_gets_a_cash_label(other):
    assert hit_prize_text("6/49", record(), PrizeView("ready", other)).text == (
        "4 numere (premiu oficial indisponibil)"
    )


def test_unknown_statuses_and_foreign_games_are_explicit():
    assert "în curs de încărcare" in hit_prize_text("6/49", record(), PrizeView()).text
    assert "oficial indisponibil" in hit_prize_text(
        "6/49", record(), PrizeView("missing")
    ).text
    assert hit_prize_text("bg_toto2", record(), PrizeView()).text == "4 numere"
    missing = record()
    missing["best_variant"] = []
    assert "context incomplet" in hit_prize_text("6/49", missing, PrizeView()).text


def test_inconsistent_hit_count_and_malformed_draw_cannot_get_a_cash_label():
    inconsistent = {**record(), "best_ticket": 5}
    assert "lei" not in hit_prize_text(
        "6/49", inconsistent, PrizeView("ready", draw())
    ).text
    malformed = {**record(), "drawn": [1.5, 2, 3, 4, 40, 41]}
    assert "context incomplet" in hit_prize_text(
        "6/49", malformed, PrizeView("ready", draw())
    ).text


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "https://evil.example/", "https://loto.ro:bad/"]
)
def test_source_link_only_accepts_the_official_https_host(url):
    result = hit_prize_text(
        "6/49", record(), PrizeView("ready", replace(draw(), source_url=url))
    )
    assert result.source_url == ""
    assert "1.015,35 lei" in result.text


def test_lookup_is_lazy_threaded_and_deduplicates_same_draw_requests():
    entered, release = Event(), Event()
    events = []
    official = draw()

    class Cache:
        present = False

        def get_draws(self, game, keys):
            events.append(("read", get_ident(), tuple(keys)))
            return [official if self.present else None for _ in keys]

        def ensure_month(self, game, year, month):
            events.append(("fetch", get_ident(), year, month))
            entered.set()
            assert release.wait(3)
            self.present = True

    cache = Cache()
    lookup = PrizeHistoryLookup("6/49", cache_factory=lambda: cache)
    assert events == []
    assert lookup.view(record()).state == "pending"
    lookup.request([record(), record()])
    assert entered.wait(3)
    lookup.request([record()])
    assert lookup.view(record()).state == "pending"
    release.set()
    lookup.wait_idle()
    assert lookup.view(record()).draw is official
    assert sum(event[0] == "fetch" for event in events) == 1
    assert all(event[1] != get_ident() for event in events)
    before = list(events)
    lookup.request([record()])
    lookup.wait_idle()
    assert events == before


def test_positive_database_cache_never_requests_network():
    official = draw()

    class Cache:
        def get_draws(self, game, keys):
            return [official for _ in keys]

        def ensure_month(self, *args):
            raise AssertionError("A stored draw must not request the archive")

    lookup = PrizeHistoryLookup("6/49", cache_factory=Cache)
    lookup.request([record()])
    lookup.wait_idle()
    assert lookup.view(record()).draw is official


def test_fetch_failure_keeps_cached_success_and_stops_retries_for_this_panel():
    good = record()
    missing = {**record(), "label": "25-10-2026"}

    class Cache:
        fetches = 0

        def get_draws(self, game, keys):
            return [draw() if key[0] == "2026-10-29" else None for key in keys]

        def ensure_month(self, *args):
            self.fetches += 1
            raise OSError("offline")

    cache = Cache()
    lookup = PrizeHistoryLookup("6/49", cache_factory=lambda: cache)
    lookup.request([good, missing])
    lookup.wait_idle()
    assert lookup.view(good).state == "ready"
    assert lookup.view(missing).state == "missing"
    lookup.request([missing])
    lookup.wait_idle()
    assert cache.fetches == 1


def test_manual_retry_forces_only_missing_month_and_preserves_positive_cache():
    good = record()
    missing = {**record(), "label": "25-10-2026"}

    class Cache:
        forced = []
        filled = False

        def get_draws(self, game, keys):
            return [
                replace(draw(), draw_date=key[0])
                if self.filled or key[0] == "2026-10-29" else None
                for key in keys
            ]

        def ensure_month(self, *args, force=False):
            self.forced.append(force)
            self.filled = force

    cache = Cache()
    lookup = PrizeHistoryLookup("6/49", cache_factory=lambda: cache)
    lookup.request([good, missing])
    lookup.wait_idle()
    assert lookup.view(missing).state == "missing"
    good_draw = lookup.view(good).draw
    lookup.request([good, missing], retry_missing=True)
    lookup.wait_idle()
    assert cache.forced == [False, True]
    assert lookup.view(good).draw is good_draw
    assert lookup.view(missing).state == "ready"


def test_two_panels_wait_for_one_sqlite_lease(tmp_path, monkeypatch):
    import loto_enterprise.core.official_prizes as official

    entered, release = Event(), Event()
    calls = []

    def fetcher(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return "offline test fixture"

    monkeypatch.setattr(official, "parse_month_html", lambda *args: [draw()])
    path = tmp_path / "shared-prizes.db"
    first = PrizeHistoryLookup(
        "6/49", cache_factory=lambda: official.PrizeCache(path, fetcher=fetcher)
    )
    second = PrizeHistoryLookup(
        "6/49", cache_factory=lambda: official.PrizeCache(path, fetcher=fetcher)
    )
    first.request([record()])
    assert entered.wait(3)
    second.request([record()])
    time.sleep(0.1)
    assert second.view(record()).state == "pending"
    release.set()
    first.wait_idle()
    second.wait_idle()
    assert first.view(record()).state == second.view(record()).state == "ready"
    assert len(calls) == 1


def test_long_busy_lease_stays_pending_and_can_be_retried(monkeypatch):
    monkeypatch.setattr(ui_prizes, "_BUSY_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(ui_prizes, "_BUSY_RETRY_SECONDS", 0.01)

    class Cache:
        filled = False

        def get_draws(self, game, keys):
            return [draw() if self.filled else None for _ in keys]

        def ensure_month(self, *args):
            return SimpleNamespace(status="busy")

    cache = Cache()
    lookup = PrizeHistoryLookup("6/49", cache_factory=lambda: cache)
    lookup.request([record()])
    lookup.wait_idle()
    assert lookup.view(record()).state == "busy"
    assert "în curs de încărcare" in hit_prize_text(
        "6/49", record(), lookup.view(record())
    ).text
    time.sleep(0.02)
    assert lookup.retry_due
    cache.filled = True
    lookup.request([record()])
    lookup.wait_idle()
    assert lookup.view(record()).state == "ready"


def _entries(n=1, game="6/49"):
    result = []
    for i in range(n):
        when = date(2026, 10, 29) - timedelta(days=i)
        result.append(WalkForwardResult(
            draw_index=i, draw_date=when.isoformat(), variant=[1, 2, 3, 4, 10, 11],
            hits=4, hits_union=6, ticket_context={
                "hard_core": list(range(1, 17)), "actual": [1, 2, 3, 4, 40, 41],
            },
        ))
    return result


def test_ui_expansion_loads_only_visible_pages_and_keeps_pool_unpriced(monkeypatch):
    instances = []

    class Lookup:
        version = 0
        retry_due = False

        def __init__(self, game):
            self.requests = []
            self.ready = False
            instances.append(self)

        def view(self, row):
            official = replace(draw(), draw_date=row["label"])
            return PrizeView("ready", official) if self.ready else PrizeView()

        def request(self, rows):
            self.requests.append(list(rows))
            self.ready = True
            self.version += 1

    monkeypatch.setattr(ui_prizes, "PrizeHistoryLookup", Lookup)
    with capture_ui() as captured:
        ui_hits._render_hits_4plus(_entries(32), "6/49", {"pool_size": 16})
    lookup, = instances
    assert lookup.requests == []  # No SQLite or HTTP during render.
    tables = [node for node in captured.walk() if node["kind"] == "table"]
    pool = next(
        node for node in tables
        if "pool_nums" in {c["name"] for c in node["kwargs"]["columns"]}
    )
    variants = next(
        node for node in tables
        if "ticket" in {c["name"] for c in node["kwargs"]["columns"]}
    )
    assert all(
        "lei" not in row["hits"] and "cat." not in row["hits"]
        for row in pool["kwargs"]["rows"]
    )
    hits_column = next(
        c for c in variants["kwargs"]["columns"] if c["name"] == "hits"
    )
    assert "text-no-wrap" not in hits_column.get("classes", "")
    expansion = next(
        node for node in captured.walk()
        if node["kind"] == "expansion" and "Variante WF" in node["args"][0]
    )
    expansion["kwargs"]["on_value_change"](SimpleNamespace(value=True))
    assert len(lookup.requests) == 1 and len(lookup.requests[0]) == 15
    assert all(
        "cat. III — 1.015,35 lei" in row["hits"]
        for row in variants["kwargs"]["rows"]
    )
    variants["kwargs"]["on_pagination_change"](
        SimpleNamespace(value={"page": 2, "rowsPerPage": 15})
    )
    assert len(lookup.requests[-1]) == 15
    assert lookup.requests[-1][0]["label"] != lookup.requests[0][0]["label"]


def test_per_draw_stats_keeps_the_raw_first_best_variant_and_its_joker_flag():
    rows = _entries(game="joker")
    rows[0].variant = [1, 2, 3, 4, 10, 7]
    rows[0].joker_hit = True
    rows[0].ticket_context["actual"] = [1, 2, 3, 4, 33]
    later = replace(rows[0], variant=[1, 2, 3, 4, 11, 8], joker_hit=False)
    stats = ui_hits._wf_per_draw_stats([rows[0], later], "joker")
    assert stats[0]["best_variant"] == [1, 2, 3, 4, 10]
    assert stats[0]["best_variant_raw"] == [1, 2, 3, 4, 10, 7]
    assert stats[0]["best_variant_joker_hit"] is True


def test_completed_lookup_rebinds_real_nicegui_rows_and_keeps_the_filter(monkeypatch):
    """NiceGUI copies row dicts; updating source dicts plus update() is insufficient."""
    from nicegui import ui as nicegui_ui
    from scripts.analysis.audit_output import CaptureUI

    instances = []

    class Lookup:
        version = 0
        retry_due = False
        ready = False

        def __init__(self, game):
            instances.append(self)

        def view(self, row):
            if not self.ready:
                return PrizeView()
            official = replace(
                draw(cats=[category(), category("II", "5/6", 2, 20000)]),
                draw_date=row["label"],
            )
            return PrizeView("ready", official)

        def request(self, _rows):
            pass

    class RealTablesUI(CaptureUI):
        def __init__(self):
            super().__init__()
            self.tables = []

        def table(self, **kwargs):
            table = nicegui_ui.table(**kwargs)
            self.tables.append(table)
            return table

    captured = RealTablesUI()
    monkeypatch.setattr(ui_prizes, "PrizeHistoryLookup", Lookup)
    monkeypatch.setattr(ui_hits, "ui", captured)
    rows = _entries(2)
    rows[1].hits = 5
    rows[1].variant = [1, 2, 3, 4, 40, 11]
    try:
        ui_hits._render_hits_4plus(rows, "6/49", {"pool_size": 16})
        tables = [
            table for table in captured.tables
            if "ticket" in {column["name"] for column in table.columns}
        ]
        assert len(tables) == 2
        assert all(
            "în curs de încărcare" in row["hits"]
            for table in tables for row in table.rows
        )
        expansion = next(
            node for node in captured.walk()
            if node["kind"] == "expansion" and "Variante WF" in node["args"][0]
        )
        expansion["kwargs"]["on_value_change"](SimpleNamespace(value=True))
        toggle = next(
            node for node in expansion["children"]
            if node["kind"] == "row"
            and any(child["kind"] == "toggle" for child in node["children"])
        )
        selector = next(child for child in toggle["children"] if child["kind"] == "toggle")
        selector["kwargs"]["on_change"](SimpleNamespace(value=5))
        assert len(tables[0].rows) == 1
        before_pagination = [dict(table.pagination) for table in tables]
        instances[0].ready = True
        instances[0].version += 1
        timer = next(node for node in captured.walk() if node["kind"] == "timer")
        timer["args"][1]()
        for table in tables:
            assert [row["hits"] for row in table.rows] == [
                "5 numere (cat. II — 200,00 lei)"
            ]
        assert [dict(table.pagination) for table in tables] == before_pagination
    finally:
        for table in captured.tables:
            table.delete()
