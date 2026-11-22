from datetime import date, timedelta

import pytest

from loto_enterprise.core.lr_range_timing import phase_evidence, range_timing


def rows(*hits):
    return [[10, 40] if yes else [9, 40] for yes in hits]


def day_series(n):
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_inclusive_occurrences_and_supplementary_draws():
    days = day_series(16)
    timing = range_timing(rows(True, True, False, True, False),
                          [days[0], days[0], days[3], days[10], days[15]], (10, 40))
    assert timing["window_draws"] == 5
    assert timing["occurrences"] == 3
    assert timing["events"][1] == {"draw_index": 1, "date": "2026-01-01"}
    assert timing["draws_since"] == 1 and timing["days_since"] == 5
    assert timing["last_date"] == "2026-01-11" and timing["since_as_of"] == "2026-01-16"
    assert timing["median_gap_draws"] == 1.5 and timing["median_gap_days"] == 5
    assert timing["_phase_keys"][0][0] is None and timing["_phase_keys"][1][0] is None


def test_cutoff_sees_only_completed_dates():
    ds = day_series(30)
    dates = [ds[0], ds[25], ds[26], ds[26], ds[29]]
    flags = [True, False, True, False, False]
    report = range_timing(rows(*flags), dates, (10, 40))
    assert [label for label, _ in report["_phase_keys"]] == [
        None, "0–7 zile", "15–28 zile", "15–28 zile", "0–7 zile",
    ]
    for end in (1, 2, 4):
        past = range_timing(rows(*flags[:end]), dates[:end], (10, 40))
        assert past["current_phase"] == report["_phase_keys"][end][0]


def test_draw_index_fallback_and_pair_filter():
    flags = [True] * 11
    report = range_timing(rows(*flags), None, (10, 40))
    assert report["days_since"] is None and report["median_gap_days"] is None
    assert report["last_date"] is None and report["phase_basis"] == "draws"
    assert report["median_gap_draws"] == 1
    result = phase_evidence(report, [1, None, 1, 0, -1, -1, 0, 0, 0, 0, 0])
    assert (result["compared"], result["wins"], result["losses"]) == (9, 1, 2)
    assert result["sufficient"] and not result["supports"]
    with pytest.raises(ValueError):
        phase_evidence(report, [1])


def test_no_previous_occurrence_is_an_unknown_pause():
    timing = range_timing(rows(False, False), day_series(2), (10, 40))
    assert timing["current_phase"] is None and timing["days_since"] is None
    assert timing["median_gap_draws"] is None and timing["events"] == []
    assert not phase_evidence(timing, [0, 1])["supports"]


def test_latest_occurrence_can_be_older_than_reporting_window():
    result = range_timing(rows(True, *([False] * 102)), day_series(103), (10, 40))
    assert result["occurrences"] == 0 and result["window_draws"] == 100
    assert result["last_date"] == "2026-01-01" and result["days_since"] == 102
    assert result["current_phase"] == "29+ zile" and result["median_gap_days"] is None


def test_same_day_observations_count_as_one_comparable_period():
    ds = day_series(8)
    dates = [ds[0]] + [ds[1]] * 10 + ds[2:]
    timing = range_timing(rows(*([True] * len(dates))), dates, (10, 40))
    result = phase_evidence(timing, [1] * len(dates))
    assert result["compared"] == 16 and result["periods"] == 7
    assert not result["supports"] and not result["sufficient"]


def test_one_occurrence_and_partial_dates():
    result = range_timing(rows(True, False), [date(2026, 1, 1), None], (10, 40))
    assert result["draws_since"] == 1
    assert result["median_gap_draws"] is None and result["days_since"] is None
    assert result["phase_basis"] == "draws"


def test_every_main_ball_counts_at_five_forty():
    result = range_timing([[10, 11, 12, 13, 14, 8], [15, 16, 17, 18, 19, 40]], day_series(2), (10, 40))
    assert result["occurrences"] == 1 and result["events"][0]["draw_index"] == 1
