"""Immutable tickets, deadline enforcement, real finance and worker journalling."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw
from loto_enterprise.core.prospective_ledger import ProspectiveLedger, record_worker_outputs
from loto_enterprise.core.ticket_experiment import GAMES, realized


@pytest.fixture
def clock():
    return [datetime(2026, 11, 2, 10, tzinfo=timezone.utc)]


@pytest.fixture
def ledger(tmp_path, clock):
    return ProspectiveLedger(tmp_path / "ledger.db", now=lambda: clock[0])


def generation(**changes):
    args = dict(record_id="test:1", game="loto_6_49", tickets=[[1, 2, 3, 4, 5, 6], [1, 2, 3, 7, 8, 9]],
                source_sha256="a" * 64, source_last_date="2026-11-01", method={"scorer": "frequency"},
                parameters={"pool_size": 16}, code={"git_commit": "test", "python_sources_sha256": "b" * 64},
                variant_price_bani=800)
    args.update(changes)
    return args


def official(**changes):
    args = dict(game="loto_6_49", draw_date="2026-11-05", numbers=(1, 2, 3, 4, 5, 6), joker=None,
                source_url="https://www.loto.ro/rezultate",
                categories=(PrizeCategory("I", "6/6", 1, 100_000, None, 100_000),
                            PrizeCategory("II", "5/6", 1, 20_000, None, 20_000),
                            PrizeCategory("III", "4/6", 1, 10_000, None, 10_000),
                            PrizeCategory("IV", "3/6", 1, 5000, None, 5000)))
    args.update(changes)
    return PrizeDraw(**args)


def test_generation_keeps_every_ticket_and_random_reference_and_retries_idempotently(ledger):
    receipt = ledger.record_generation(**generation())
    assert ledger.record_generation(**generation()) == receipt
    assert len(ledger.events()) == 1
    stored = ledger.get("test:1")
    assert stored["tickets"] == generation()["tickets"]
    assert len(stored["random_reference"]) == stored["variants"] == 2
    assert stored["source_sha256"] == "a" * 64
    assert stored["classification"] == "generated_unassigned_not_yet_prospective"
    assert ledger.verify() == receipt
    assert ledger.summary()["commitments"] == 0


def test_cannot_rewrite_tickets_or_delete_events_even_through_sql(ledger):
    ledger.record_generation(**generation())
    with pytest.raises(ValueError, match="immutable"):
        ledger.record_generation(**generation(tickets=[[1, 2, 3, 4, 5, 8]]))
    with sqlite3.connect(ledger.path) as connection:
        for sql in ("UPDATE events SET payload='{}'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(sql)


def test_future_commitment_is_separate_append_and_can_never_change_target(ledger):
    ledger.record_generation(**generation())
    original = ledger.get("test:1")
    receipt = ledger.commit("test:1", "2026-11-05", ticket_fees_bani=100)
    assert ledger.commit("test:1", "2026-11-05", ticket_fees_bani=100) == receipt
    assert ledger.get("test:1") == original
    assert [e["kind"] for e in ledger.events()] == ["generation", "commitment"]
    with pytest.raises(ValueError, match="immutable"):
        ledger.commit("test:1", "2026-11-08", ticket_fees_bani=100)


@pytest.mark.parametrize("stamp", [datetime(2026, 11, 4, 22, tzinfo=timezone.utc), datetime(2026, 11, 6, tzinfo=timezone.utc)])
def test_refuses_commitment_at_or_after_romanian_midnight(ledger, clock, stamp):
    ledger.record_generation(**generation())
    clock[0] = stamp
    with pytest.raises(ValueError, match="deadline"):
        ledger.commit("test:1", "2026-11-05", ticket_fees_bani=0)
    assert ledger.summary()["commitments"] == 0


def test_source_on_target_date_and_future_sources_cannot_be_committed(ledger):
    ledger.record_generation(**generation(source_last_date="2026-11-05"))
    with pytest.raises(ValueError, match="source history reaches"):
        ledger.commit("test:1", "2026-11-05", ticket_fees_bani=0)
    with pytest.raises(ValueError, match="future dates"):
        ledger.commit("test:1", "2026-11-08", ticket_fees_bani=0)


def test_results_append_after_day_end_with_exact_cost_and_no_ticket_changes(ledger, clock):
    ledger.record_generation(**generation())
    ledger.commit("test:1", "2026-11-05", ticket_fees_bani=100)
    original = ledger.get("test:1")
    with pytest.raises(ValueError, match="day has not ended"):
        ledger.settle("test:1", official())
    clock[0] = datetime(2026, 11, 5, 22, tzinfo=timezone.utc)
    receipt = ledger.settle("test:1", official())
    assert ledger.settle("test:1", official()) == receipt
    result = ledger.get("test:1", "settlement")["outcomes"]
    assert result["generated"]["full_cost_bani"] == 1700
    assert result["generated"]["gross_bani"] == 105_000
    assert result["generated"]["net_bani"] == 103_300
    assert result["random_reference"]["full_cost_bani"] == 1700
    assert ledger.get("test:1") == original
    assert ledger.summary()["settled"] == 1
    assert ledger.verify() == receipt
    changed = replace(official(), numbers=(1, 2, 3, 4, 5, 7))
    with pytest.raises(ValueError, match="immutable|incomplete"):
        ledger.settle("test:1", changed)


@pytest.mark.parametrize("changes", [{"game": "loto_5_40"}, {"draw_date": "2026-11-08"},
    {"source_url": "https://example.com/loto.ro/results"}, {"source_url": "http://www.loto.ro/results"},
    {"categories": ()}, {"categories": (PrizeCategory("I", "6/6", 0, None, 100000, 100000),)}])
def test_refuses_wrong_or_incomplete_official_result(ledger, clock, changes):
    ledger.record_generation(**generation())
    ledger.commit("test:1", "2026-11-05", ticket_fees_bani=0)
    clock[0] = datetime(2026, 11, 6, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        ledger.settle("test:1", official(**changes))
    assert ledger.summary()["settled"] == 0


def test_ledger_detects_manual_tampering_beyond_application_guards(ledger):
    ledger.record_generation(**generation())
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER events_no_update")
        connection.execute("UPDATE events SET payload='{}'")
    with pytest.raises(ValueError, match="chain invalid"):
        ledger.export()
    with pytest.raises(ValueError, match="chain invalid"):
        ledger.commit("test:1", "2026-11-05", ticket_fees_bani=0)


def test_concurrent_identical_registration_has_one_append(ledger):
    def append(_):
        return ledger.record_generation(**generation())
    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(append, range(8)))
    assert len(set(hashes)) == len(ledger.events()) == 1


def test_separate_ledger_instances_can_initialize_and_append_concurrently(ledger, clock):
    def append(_):
        independent = ProspectiveLedger(ledger.path, now=lambda: clock[0])
        return independent.record_generation(**generation())
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(append, range(24)))
    assert len(set(hashes)) == 1
    assert len(ledger.events()) == 1
    assert ledger.verify() == hashes[0]


def test_unsettled_ledger_does_not_report_unknown_results_as_zero(ledger):
    ledger.record_generation(**generation())
    ledger.commit("test:1", "2026-11-05", ticket_fees_bani=100)
    assert ledger.get("test:1", "commitment")["full_cost_bani"] == 1700
    summary = ledger.summary()
    assert summary["unsettled"] == 1
    assert summary["generated"]["net_bani"] is None
    assert summary["generated"]["any_prize"]["probability"] is None


def test_clock_rollback_is_not_accepted_as_earlier_evidence(ledger, clock):
    ledger.record_generation(**generation())
    clock[0] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="backwards"):
        ledger.record_generation(**generation(record_id="other"))


def test_finances_unknown_fees_or_payouts_remain_unknown_and_no_imaginary_full_system():
    tickets = generation()["tickets"]
    result = realized(GAMES["loto_6_49"], tickets, official().numbers, official().draw_date,
                      official=official(), variant_price_bani=800, ticket_fees_bani=None)
    assert result["gross_bani"] == 105_000
    assert result["variant_cost_bani"] == 1600
    assert result["full_cost_bani"] is None and result["net_bani"] is None
    missing = realized(GAMES["loto_6_49"], tickets, official().numbers, official().draw_date,
                       variant_price_bani=800, ticket_fees_bani=0)
    assert missing["any_prize"] and missing["gross_bani"] is None


def test_zero_prize_is_known_only_when_all_variants_lose():
    result = realized(GAMES["loto_6_49"], generation()["tickets"], [40, 41, 42, 43, 44, 45], "2026-11-05",
                      variant_price_bani=800, ticket_fees_bani=100)
    assert result["gross_bani"] == 0 and result["net_bani"] == -1700


def test_missing_drawn_joker_is_not_misreported_as_a_losing_variant():
    with pytest.raises(ValueError, match="incomplete"):
        realized(GAMES["joker"], [[1, 2, 3, 4, 5, 7]], [1, 10, 20, 30, 40], "2026-11-05",
                 variant_price_bani=700, ticket_fees_bani=0)


def test_540_prizes_use_first_five_official_numbers_and_single_category():
    draw = official(game="loto_5_40", numbers=(1, 2, 3, 4, 5, 6), categories=(
        PrizeCategory("I", "5/5", 1, 100000, None, 100000),
        PrizeCategory("II", "5/6", 1, 20000, None, 20000)))
    result = realized(GAMES["loto_5_40"], [[1, 2, 3, 4, 5], [1, 2, 3, 4, 6]], draw.numbers,
                      draw.draw_date, official=draw, variant_price_bani=500, ticket_fees_bani=0)
    assert result["category_counts"] == {"I": 1, "II": 1}
    assert result["gross_bani"] == 120000 and result["net_bani"] == 119000


def test_historical_unconfirmed_currency_cannot_be_reported_as_lei_net():
    draw = official(draw_date="2004-11-05")
    result = realized(GAMES["loto_6_49"], [[1, 2, 3, 4, 5, 6]], draw.numbers, draw.draw_date,
                      official=draw, variant_price_bani=800, ticket_fees_bani=0)
    assert result["gross_bani"] is None and result["net_bani"] is None


def test_worker_journals_actual_result_without_assigning_future_date(ledger, monkeypatch):
    import loto_enterprise.core.prospective_ledger as module

    monkeypatch.setattr(module, "code_identity", lambda: generation()["code"])
    frame = pd.DataFrame([{"date": "2026-11-01", **{f"n{i}": i for i in range(1, 7)}}])
    source = {"df_json": frame.to_json(orient="split")}
    result = {"game_id": "6/49", "variants": generation()["tickets"], "audit": {"bench_winner": {"scorer": "frequency"}}, "pool_size": 16}
    bundle = [("history.csv", {"Loto 6/49": result})]
    record_worker_outputs(bundle, [source], job_id=37, ledger=ledger)
    assert result["prospective_ledger"]["status"] == "generated_unassigned"
    assert ledger.get(result["prospective_ledger"]["record_id"])["tickets"] == result["variants"]
    assert ledger.summary()["commitments"] == 0
    assert ledger.get(result["prospective_ledger"]["record_id"])["source_sha256"] == hashlib.sha256(source["df_json"].encode()).hexdigest()


def test_failed_registry_write_is_visible_and_preserves_actual_variants(ledger, monkeypatch):
    import loto_enterprise.core.prospective_ledger as module

    monkeypatch.setattr(module, "code_identity", lambda: generation()["code"])
    monkeypatch.setattr(ledger, "record_generation", lambda **kw: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    frame = pd.DataFrame([{"date": "2026-11-01"}])
    result = {"game_id": "6/49", "variants": generation()["tickets"]}
    record_worker_outputs([("x", {"x": result})], [{"df_json": frame.to_json(orient="split")}], job_id=1, ledger=ledger)
    assert result["prospective_ledger"]["status"] == "recording_failed"
    assert result["variants"] == generation()["tickets"]


def test_cli_settlement_checks_exact_history_and_does_not_reconstruct_tickets(ledger, clock, tmp_path):
    from scripts.prospective_registry import settle_from_history

    ledger.record_generation(**generation())
    ledger.commit("test:1", "2026-11-05", ticket_fees_bani=0)
    clock[0] = datetime(2026, 11, 6, tzinfo=timezone.utc)
    path = tmp_path / "history.csv"
    pd.DataFrame([{"date": "05-11-2026", **{f"n{i}": i for i in range(1, 7)}}]).to_csv(path, index=False)
    class Cache:
        def get_draw(self, game, day, numbers, joker, *, fetch):
            assert game == "loto_6_49" and day == "2026-11-05"
            assert numbers == list(range(1, 7)) and not fetch
            return official()
    settle_from_history(ledger, "test:1", history=path, cache=Cache(), fetch=False)
    assert ledger.summary()["settled"] == 1
    assert ledger.export()["timestamp_assurance"] == "local_clock_and_database_not_independently_attested"
