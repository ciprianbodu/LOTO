"""Inspect, commit, export or settle actual pre-recorded worker variants."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from loto_enterprise.core.history import chronological_history, history_dates
from loto_enterprise.core.official_prizes import PrizeCache
from loto_enterprise.core.prospective_ledger import ProspectiveLedger
from scripts.evaluate_chronological import lottery_for_experiment


def settle_from_history(ledger, record_id, *, history=None, cache=None, fetch=True):
    generation, commitment = ledger.get(record_id), ledger.get(record_id, "commitment")
    root = Path(__file__).resolve().parents[1]
    source = Path(history) if history else root / lottery_for_experiment(generation["game"]).csv
    frame = chronological_history(pd.read_csv(source))
    dates = history_dates(frame)
    if dates is None:
        raise ValueError("dated official draw history required")
    matches = frame.loc[dates.dt.strftime("%Y-%m-%d") == commitment["target_date"]]
    if len(matches) != 1:
        raise ValueError("target missing or multiple draws on that day; exact official identity required")
    row = matches.iloc[0]
    from loto_enterprise.core.ticket_experiment import GAMES

    game = GAMES[generation["game"]]
    numbers = [int(row[f"n{i}"]) for i in range(1, game.drawn + 1)]
    joker = int(row["joker"]) if game.joker else None
    # Guard before network access; settle() repeats the authoritative check.
    if ledger._clock().astimezone(ZoneInfo("Europe/Bucharest")).date() <= date.fromisoformat(commitment["target_date"]):
        raise ValueError("target day has not ended")
    official = (cache or PrizeCache()).get_draw(game.key, commitment["target_date"], numbers, joker, fetch=fetch)
    if official is None:
        raise ValueError("exact official prize record unavailable")
    return ledger.settle(record_id, official)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("summary")
    commands.add_parser("verify")
    export = commands.add_parser("export")
    export.add_argument("output", type=Path)
    commit = commands.add_parser("commit")
    commit.add_argument("record_id")
    commit.add_argument("target_date", help="YYYY-MM-DD, future date")
    commit.add_argument("--ticket-fees-bani", type=int, required=True, help="total fees for each compared portfolio, explicitly include 0")
    settle = commands.add_parser("settle")
    settle.add_argument("record_id")
    settle.add_argument("--history", type=Path)
    settle.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    ledger = ProspectiveLedger(args.db)
    if args.command == "commit":
        result = ledger.commit(args.record_id, args.target_date, ticket_fees_bani=args.ticket_fees_bani)
    elif args.command == "settle":
        result = settle_from_history(ledger, args.record_id, history=args.history, fetch=not args.offline)
    elif args.command == "verify":
        result = ledger.verify()
    elif args.command == "summary":
        result = ledger.summary()
    elif args.command == "export":
        args.output.write_text(json.dumps(ledger.export(), indent=2, ensure_ascii=False), encoding="utf-8")
        result = str(args.output)
    else:
        result = [{k: e[k] for k in ("kind", "record_id", "recorded_at", "event_hash")} for e in ledger.events()]
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
