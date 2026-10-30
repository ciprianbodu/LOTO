"""Offline checks against reduced official numerical tables and SQLite restarts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import re
import threading

import pytest

from loto_enterprise.core.official_prizes import (
    ArchiveError,
    PrizeCache,
    parse_month_html,
)


FIXTURES = Path(__file__).parent / "testdata" / "official_prizes"


def source(name="649_2026_09.html"):
    return (FIXTURES / name).read_text(encoding="utf-8")


def block(html):
    return re.search(
        r'<div class="rezultate-extrageri-content.*?</table>\s*</div>', html, re.S
    ).group(0)


def test_official_649_report_is_not_a_paid_prize():
    draw = parse_month_html("6/49", source(), 2026, 9)[0]
    assert draw.draw_date == "2026-09-27"
    assert draw.numbers == (6, 7, 18, 43, 40, 22)
    first, second = draw.categories[:2]
    assert first.winners == 0
    assert first.amount_bani is None
    assert first.raw_value_bani == 100223040
    assert first.report_bani == 1113196456
    assert second.winners == 13
    assert second.amount_bani == 2569821


def test_official_540_order_and_first_category_marker_are_preserved():
    draw = parse_month_html("5/40", source("540_2026_10.html"), 2026, 10)[0]
    assert draw.numbers == (26, 39, 40, 18, 1, 2)
    assert draw.order_known
    assert draw.categories[0].matches_label == "5/6*"
    assert draw.categories[1].amount_bani == 3798200
    assert draw.categories[2].amount_bani == 70337


def test_official_joker_ball_and_categories_are_separate():
    draw = parse_month_html("joker", source("joker_2026_10.html"), 2026, 10)[0]
    assert draw.numbers == (42, 21, 43, 18, 37)
    assert draw.joker == 1
    assert len(draw.categories) == 8
    assert draw.categories[2].matches_label == "4/5+J"
    assert draw.categories[2].amount_bani == 3903244
    assert draw.categories[3].amount_bani == 84853


def test_old_amount_is_kept_as_published_without_ron_conversion():
    draw = parse_month_html("6/49", source("649_2004_01.html"), 2004, 1)[0]
    assert draw.draw_date == "2004-01-25"
    assert draw.currency == "lei"
    assert draw.categories[1].amount_bani == 20337879400


def test_ignored_month_filter_cannot_stamp_current_month_as_requested_month():
    with pytest.raises(ArchiveError, match="requested month"):
        parse_month_html("6/49", source(), 2025, 9)


@pytest.mark.parametrize("mutation", ["missing_row", "bad_money", "bad_ball"])
def test_changed_or_incomplete_source_is_rejected(mutation):
    html = source()
    if mutation == "missing_row":
        html = re.sub(r'<tr>\s*<td>II \(5/6\)</td>.*?</tr>', "", html, flags=re.S)
    elif mutation == "bad_money":
        html = html.replace("25.698,21", "not an amount")
    else:
        html = html.replace("/bile/6.png", "/bile/99.png")
    with pytest.raises(ArchiveError):
        parse_month_html("6/49", html, 2026, 9)


def test_companion_noroc_table_is_not_assigned_to_main_game():
    html = source()
    companion = block(html).replace("la 6/49 din", "la noroc din")
    found = parse_month_html("6/49", html + companion, 2026, 9)
    assert len(found) == 1


def test_conflicting_tables_for_one_identical_draw_are_rejected():
    html = source()
    conflicting = block(html).replace("25.698,21", "25.698,22")
    with pytest.raises(ArchiveError, match="Conflicting"):
        parse_month_html("6/49", html + conflicting, 2026, 9)


def test_cache_survives_restart_and_never_fetches_on_reads(tmp_path):
    calls = []

    def fetch(*args):
        calls.append(args)
        return source()

    path = tmp_path / "prizes.db"
    cache = PrizeCache(path, fetcher=fetch)
    assert not path.exists()  # Constructor does not touch network or disk.
    assert cache.get_draw("6/49", "27-09-2026", [6, 7, 18, 43, 40, 22]) is None
    assert calls == []
    assert cache.ensure_month("6/49", 2026, 9).status == "fetched"
    assert len(calls) == 1

    def offline(*args):
        raise AssertionError("Cached prizes must not need network access")

    restarted = PrizeCache(path, fetcher=offline)
    found = restarted.get_draws("loto_6_49", [
        ("2026-09-27", [22, 6, 18, 40, 43, 7], None),
        ("2026-09-27", [1, 2, 3, 4, 5, 6], None),
    ])
    assert found[0].numbers == (6, 7, 18, 43, 40, 22)
    assert found[1] is None  # Same date cannot substitute for matching numbers.
    assert restarted.ensure_month("6/49", 2026, 9).status == "cached"


def test_batch_uses_only_one_connection(tmp_path, monkeypatch):
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: source())
    cache.ensure_month("6/49", 2026, 9)
    connect = cache._connect
    calls = []

    def counted():
        calls.append(1)
        return connect()

    monkeypatch.setattr(cache, "_connect", counted)
    result = cache.get_draws("6/49", [
        ("2026-09-27", [6, 7, 18, 43, 40, 22], None),
        ("2026-09-27", [1, 2, 3, 4, 5, 6], None),
    ])
    assert len(calls) == 1
    assert result[0] is not None and result[1] is None


def test_sorted_540_history_gets_original_draw_order(tmp_path):
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: source("540_2026_10.html"))
    cache.ensure_month("5/40", 2026, 10)
    found = cache.get_draw("5/40", "29.10.2026", [1, 2, 18, 26, 39, 40])
    assert found.numbers == (26, 39, 40, 18, 1, 2)


def test_two_draws_on_same_date_are_matched_by_numbers(tmp_path):
    # Synthetic second draw: tests identity, not an asserted real-world payout.
    html = source()
    second = block(html).replace("/bile/6.png", "/bile/8.png")
    second = second.replace("25.698,21", "1.234,56")
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: html + second)
    assert cache.ensure_month("6/49", 2026, 9).draws_count == 2
    first = cache.get_draw("6/49", "2026-09-27", [6, 7, 18, 43, 40, 22])
    other = cache.get_draw("6/49", "2026-09-27", [8, 7, 18, 43, 40, 22])
    assert first.categories[1].amount_bani == 2569821
    assert other.categories[1].amount_bani == 123456


def test_unknown_joker_refuses_ambiguous_same_main_numbers(tmp_path):
    # Synthetic Joker draw with the same main numbers but a different second urn.
    html = source("joker_2026_10.html")
    second = block(html).replace("/bile/joker/1.png", "/bile/joker/2.png")
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: html + second)
    cache.ensure_month("joker", 2026, 10)
    numbers = [42, 21, 43, 18, 37]
    assert cache.get_draw("joker", "2026-10-29", numbers) is None
    assert cache.get_draw("joker", "2026-10-29", numbers, 1).joker == 1
    assert cache.get_draw("joker", "2026-10-29", numbers, 2).joker == 2


def test_fetch_true_keeps_generator_numbers_for_second_lookup(tmp_path):
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: source())
    found = cache.get_draw("6/49", "2026-09-27", iter([6, 7, 18, 43, 40, 22]), fetch=True)
    assert found is not None


def test_failure_backoff_prevents_repeated_http_requests(tmp_path):
    clock = [1_800_000_000.0]
    calls = []

    def broken(*args):
        calls.append(args)
        raise TimeoutError("offline")

    cache = PrizeCache(tmp_path / "prizes.db", fetcher=broken, now=lambda: clock[0])
    assert cache.ensure_month("6/49", 2026, 9).status == "error"
    assert cache.ensure_month("6/49", 2026, 9).status == "error"
    assert len(calls) == 1
    clock[0] += 61
    cache.ensure_month("6/49", 2026, 9)
    assert len(calls) == 2
    clock[0] += 61
    cache.ensure_month("6/49", 2026, 9)
    assert len(calls) == 2  # The second failure backs off for 120 seconds.


def test_empty_month_is_negative_cached(tmp_path):
    calls = []
    empty = source().replace(block(source()), "")

    def fetch(*args):
        calls.append(args)
        return empty

    cache = PrizeCache(tmp_path / "prizes.db", fetcher=fetch)
    assert cache.ensure_month("6/49", 2026, 9).status == "missing"
    assert cache.ensure_month("6/49", 2026, 9).status == "missing"
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["network", "empty", "wrong_month"])
def test_failed_refresh_preserves_previous_verified_data(tmp_path, bad):
    state = [source()]
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: state[0])
    cache.ensure_month("6/49", 2026, 9)
    if bad == "empty":
        state[0] = source().replace(block(source()), "")
    elif bad == "wrong_month":
        state[0] = source("540_2026_10.html")
    else:
        def broken(*args):
            raise OSError("offline")
        cache._fetcher = broken
    assert cache.ensure_month("6/49", 2026, 9, force=True).status == "error"
    found = cache.get_draw("6/49", "2026-09-27", [6, 7, 18, 43, 40, 22])
    assert found.categories[1].amount_bani == 2569821


def test_current_month_refreshes_when_new_results_can_arrive(tmp_path):
    clock = [datetime(2026, 10, 30, tzinfo=timezone.utc).timestamp()]
    calls = []

    def fetch(*args):
        calls.append(args)
        return source("540_2026_10.html")

    cache = PrizeCache(tmp_path / "prizes.db", fetcher=fetch, now=lambda: clock[0])
    cache.ensure_month("5/40", 2026, 10)
    assert cache.ensure_month("5/40", 2026, 10).status == "cached"
    clock[0] += 901
    assert cache.ensure_month("5/40", 2026, 10).status == "fetched"
    assert len(calls) == 2


def test_two_workers_share_a_persistent_month_lease(tmp_path):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_fetch(*args):
        calls.append(args)
        started.set()
        assert release.wait(10)
        return source()

    path = tmp_path / "prizes.db"
    first = PrizeCache(path, fetcher=slow_fetch)
    second = PrizeCache(path, fetcher=slow_fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.ensure_month, "6/49", 2026, 9)
        assert started.wait(10)
        try:
            assert second.ensure_month("6/49", 2026, 9).status == "busy"
            assert second.get_draw("6/49", "2026-09-27", [6, 7, 18, 43, 40, 22]) is None
        finally:
            release.set()
        assert pending.result(timeout=10).status == "fetched"
    assert len(calls) == 1
    assert second.ensure_month("6/49", 2026, 9).status == "cached"


def test_game_namespace_and_month_are_independent(tmp_path):
    def fetch(game, year, month):
        return source("540_2026_10.html" if game == "loto_5_40" else "649_2026_09.html")

    cache = PrizeCache(tmp_path / "prizes.db", fetcher=fetch)
    assert cache.ensure_month("6/49", 2026, 9).status == "fetched"
    assert cache.ensure_month("5/40", 2026, 10).status == "fetched"
    assert cache.get_draw("6/49", "2026-09-27", [6, 7, 18, 43, 40, 22]).game == "loto_6_49"
    assert cache.get_draw("5/40", "2026-10-29", [26, 39, 40, 18, 1, 2]).game == "loto_5_40"


def test_noninteger_draw_number_cannot_match_by_truncation(tmp_path):
    cache = PrizeCache(tmp_path / "prizes.db")
    with pytest.raises(ValueError, match="integers"):
        cache.get_draw("6/49", "2026-09-27", [6.1, 7, 18, 43, 40, 22])


def test_noninteger_joker_cannot_match_by_truncation(tmp_path):
    cache = PrizeCache(tmp_path / "prizes.db")
    with pytest.raises(ValueError, match="integer"):
        cache.get_draw("joker", "2026-10-29", [42, 21, 43, 18, 37], 1.5)


def test_partial_refresh_does_not_delete_other_verified_draws(tmp_path):
    html = source()
    second = block(html).replace("/bile/6.png", "/bile/8.png")
    payload = [html + second]
    cache = PrizeCache(tmp_path / "prizes.db", fetcher=lambda *a: payload[0])
    assert cache.ensure_month("6/49", 2026, 9).draws_count == 2
    payload[0] = html  # Complete-looking HTML with one previously present draw lost.
    assert cache.ensure_month("6/49", 2026, 9, force=True).status == "error"
    assert cache.get_draw("6/49", "2026-09-27", [8, 7, 18, 43, 40, 22]) is not None
