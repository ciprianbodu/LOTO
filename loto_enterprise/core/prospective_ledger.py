"""Local append-only generation / commitment / official-result journal.

SQL guards and a hash chain detect accidental edits. This is NOT an externally
attested timestamp: an administrator can replace the entire local database.
Export a receipt/hash to an independent timestamp service for stronger evidence.
Conservative deadline: commit before 00:00 Europe/Bucharest on the target day;
settle after that whole day has ended. No guessed lottery closing times.
"""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time as clock_time
from zoneinfo import ZoneInfo

from loto_enterprise.core.chronological_evaluation import digest
from loto_enterprise.core.ticket_experiment import GAMES, integer, random_tickets, realized, validate_tickets


ZONE = ZoneInfo("Europe/Bucharest")
ZERO_HASH = "0" * 64


def code_identity(root=None):
    """Commit plus actual Python-source hash, including uncommitted experiment code."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    sha = hashlib.sha256()
    paths = sorted(p for p in root.rglob("*.py")
                   if not any(part in {".venv", ".git", "__pycache__", "scratch"} for part in p.relative_to(root).parts))
    for path in paths:
        sha.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"git_commit": commit, "python_sources_sha256": sha.hexdigest(), "source_files": len(paths)}


class ProspectiveLedger:
    def __init__(self, path=None, *, now=None):
        if path is None:
            from runtime_paths import RUNTIME_ROOT

            path = os.environ.get("LOTO_PROSPECTIVE_DB") or RUNTIME_ROOT / "prospective_ledger.db"
        self.path = Path(path)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._schema_lock = threading.Lock()
        self._initialized = False

    def _clock(self):
        now = self.now()
        if now.tzinfo is None:
            raise ValueError("ledger clock must be timezone aware")
        return now.astimezone(timezone.utc)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        schema = """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('generation','commitment','settlement')),
                record_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
                payload TEXT NOT NULL, previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE, UNIQUE(kind,record_id)
            );
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT,'append-only ledger: UPDATE forbidden'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT,'append-only ledger: DELETE forbidden'); END;
        """
        try:
            with self._schema_lock:
                if not self._initialized:
                    deadline = clock_time.monotonic() + 15
                    while True:
                        try:
                            # WAL is persistent. Reassigning it on every read
                            # races a concurrent writer even with busy_timeout.
                            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                            if str(mode).lower() != "wal":
                                connection.execute("PRAGMA journal_mode=WAL")
                            connection.executescript(schema)
                            self._initialized = True
                            break
                        except sqlite3.OperationalError as exc:
                            if (not any(word in str(exc).lower() for word in ("locked", "busy"))
                                    or clock_time.monotonic() >= deadline):
                                raise
                            connection.rollback()
                            clock_time.sleep(.025)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _event_hash(kind, record_id, recorded_at, payload, previous):
        return digest([kind, record_id, recorded_at, payload, previous])

    def _append(self, kind, record_id, payload, *, validate=None):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM events WHERE kind=? AND record_id=?",
                                          (kind, record_id)).fetchone()
            if existing:
                if existing["payload"] != encoded:
                    raise ValueError("immutable record already exists with different content")
                return existing["event_hash"]
            # Clock/deadline validation occurs AFTER acquiring the write lock.
            now = self._clock()
            if validate:
                validate(connection, now)
            stamp = now.isoformat()
            row = connection.execute("SELECT event_hash,recorded_at FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
            if row and now < datetime.fromisoformat(row["recorded_at"]):
                raise ValueError("ledger clock moved backwards")
            previous = row[0] if row else ZERO_HASH
            event_hash = self._event_hash(kind, record_id, stamp, payload, previous)
            connection.execute("INSERT INTO events(kind,record_id,recorded_at,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?)",
                               (kind, record_id, stamp, encoded, previous, event_hash))
            return event_hash

    def events(self):
        with self._connect() as connection:
            return [{**dict(row), "payload": json.loads(row["payload"])}
                    for row in connection.execute("SELECT * FROM events ORDER BY sequence")]

    def verify(self):
        previous = ZERO_HASH
        for event in self.events():
            expected = self._event_hash(event["kind"], event["record_id"], event["recorded_at"],
                                        event["payload"], previous)
            if event["previous_hash"] != previous or event["event_hash"] != expected:
                raise ValueError(f"ledger chain invalid at {event['sequence']}")
            previous = expected
        return previous

    def get(self, record_id, kind="generation"):
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM events WHERE record_id=? AND kind=?",
                                     (record_id, kind)).fetchone()
            if row is None:
                raise ValueError(f"missing {kind}: {record_id}")
            return json.loads(row[0])

    def record_generation(self, *, record_id, game, tickets, source_sha256,
                          source_last_date, method, parameters, code,
                          variant_price_bani, reference_seed=104729):
        geometry = GAMES[game]
        lines = validate_tickets(geometry, tickets)
        variant_price_bani = integer(variant_price_bani, minimum=1)
        reference_seed = integer(reference_seed)
        if not re.fullmatch(r"[0-9a-f]{64}", str(source_sha256)) or not re.fullmatch(r"[0-9a-f]{64}", str(code.get("python_sources_sha256", ""))):
            raise ValueError("source and code signatures are required")
        last = date.fromisoformat(source_last_date)
        reference = random_tickets(geometry, len(lines), reference_seed)
        body = {"schema": 1, "game": game, "tickets": lines, "tickets_sha256": digest(lines),
                "source_sha256": source_sha256, "source_last_date": last.isoformat(),
                "method": method, "parameters": parameters, "code": code,
                "variants": len(lines), "variant_price_bani": variant_price_bani,
                "reference_seed": reference_seed, "random_reference": reference,
                "classification": "generated_unassigned_not_yet_prospective"}
        return self._append("generation", str(record_id), body)

    def commit(self, record_id, target_date, *, ticket_fees_bani):
        self.verify()
        generation = self.get(record_id)
        target = date.fromisoformat(target_date)
        fees = integer(ticket_fees_bani)
        if date.fromisoformat(generation["source_last_date"]) >= target:
            raise ValueError("source history reaches target day or later")
        body = {"target_date": target.isoformat(), "game": generation["game"],
                "generation_sha256": digest(generation), "ticket_fees_bani": fees,
                "full_cost_bani": generation["variants"] * generation["variant_price_bani"] + fees,
                "deadline_policy": "before_target_day_00:00_Europe/Bucharest",
                "reference_same_variants_and_cost": True,
                "financial_basis": "declared full participation cost before tax"}

        def check(connection, now):
            if now >= datetime.combine(target, time.min, ZONE).astimezone(timezone.utc):
                raise ValueError("prospective commitment deadline has passed")
            if date.fromisoformat(generation["source_last_date"]) > now.astimezone(ZONE).date():
                raise ValueError("source history contains future dates")
            if not connection.execute("SELECT 1 FROM events WHERE kind='generation' AND record_id=?", (record_id,)).fetchone():
                raise ValueError("missing generation")

        return self._append("commitment", str(record_id), body, validate=check)

    def settle(self, record_id, official):
        self.verify()
        generation, commitment = self.get(record_id), self.get(record_id, "commitment")
        target = date.fromisoformat(commitment["target_date"])
        if official.game != generation["game"] or official.draw_date != target.isoformat():
            raise ValueError("official draw does not match the committed game/date")
        # Only the official archive source can justify settlement. Synthetic
        # draws remain usable in retrospective tests but not as live evidence.
        from urllib.parse import urlparse

        source = urlparse(official.source_url)
        if source.scheme != "https" or source.hostname not in {"www.loto.ro", "loto.ro"}:
            raise ValueError("official loto.ro prize provenance required")
        geometry = GAMES[generation["game"]]
        outcomes = {name: realized(geometry, lines, official.numbers, target.isoformat(),
            joker=official.joker, official=official,
            variant_price_bani=generation["variant_price_bani"],
            ticket_fees_bani=commitment["ticket_fees_bani"])
            for name, lines in {"generated": generation["tickets"],
                                "random_reference": generation["random_reference"]}.items()}
        if any(r["gross_bani"] is None for r in outcomes.values()):
            raise ValueError("official prizes incomplete or report-only; settlement unavailable")
        body = {"official_draw": asdict(official), "official_sha256": digest(asdict(official)),
                "commitment_sha256": digest(commitment), "outcomes": outcomes,
                "purchase_verified": False, "inference": "prospective_simulation_not_proof_of_predictive_advantage"}

        def check(_connection, now):
            end = datetime.combine(target + timedelta(days=1), time.min, ZONE)
            if now < end.astimezone(timezone.utc):
                raise ValueError("target day has not ended; result cannot be appended yet")

        return self._append("settlement", str(record_id), body, validate=check)

    def export(self):
        return {"schema": "prospective_ledger_v1", "head_sha256": self.verify(),
                "timestamp_assurance": "local_clock_and_database_not_independently_attested",
                "events": self.events()}

    def summary(self):
        from loto_enterprise.core.ticket_experiment import proportion

        events = self.events()
        settled = [e for e in events if e["kind"] == "settlement"]
        summary = {"generations": sum(e["kind"] == "generation" for e in events),
                   "commitments": sum(e["kind"] == "commitment" for e in events),
                   "settled": len(settled), "unsettled": sum(e["kind"] == "commitment" for e in events) - len(settled),
                   "inference": "descriptive_small_sample_no_demonstrated_predictive_advantage"}
        for name in ("generated", "random_reference"):
            rows = [e["payload"]["outcomes"][name] for e in settled]
            summary[name] = {metric: proportion(sum(bool(r[metric]) for r in rows), len(rows))
                             for metric in ("any_prize", "4plus", "recovered_cost")}
            for key in ("full_cost_bani", "gross_bani", "net_bani"):
                summary[name][key] = sum(r[key] for r in rows) if rows else None
        return summary


def record_worker_outputs(results_bundle, datasets, *, job_id, ledger=None):
    """Log real worker results only. WF never calls this; cached results included.

    No target is inferred. The user must append a timely commitment with the
    future date and fees; a stored old history never becomes prospective by itself.
    """
    import pandas as pd
    from io import StringIO
    from loto_enterprise.core.history import chronological_history, history_dates

    ledger = ledger or ProspectiveLedger()
    identity = code_identity()
    for dataset_index, ((_, outputs), source) in enumerate(zip(results_bundle, datasets, strict=True)):
        source_json = str(source["df_json"])
        frame = chronological_history(pd.read_json(StringIO(source_json), orient="split", convert_dates=False))
        dates = history_dates(frame)
        if dates is None or dates.empty:
            continue
        for label, result in outputs.items():
            game = {"6/49": "loto_6_49", "5/40": "loto_5_40", "joker": "joker"}.get(result.get("game_id"))
            if game not in GAMES or not result.get("variants"):
                continue
            from loto_enterprise.core.lotteries import require_lottery

            lot = require_lottery(result["game_id"])
            # A retry with different inputs/code must not collide with an old
            # immutable run after a queue reset or a requeued job.
            run_digest = digest([source_json, result["variants"], identity])[:16]
            record_id = f"job:{job_id}:dataset:{dataset_index}:game:{game}:{run_digest}"
            try:
                receipt = ledger.record_generation(
                    record_id=record_id, game=game, tickets=result["variants"],
                    source_sha256=hashlib.sha256(source_json.encode()).hexdigest(),
                    source_last_date=dates.max().strftime("%Y-%m-%d"),
                    method=result.get("audit", {}).get("bench_winner", {}),
                    parameters={k: result.get(k) for k in ("pool_size", "guarantee", "wheel_condition",
                        "lookback", "max_consecutive_run", "recent_penalty_draws", "recent_penalty_factor",
                        "restrict_base_min", "restrict_base_max")}, code=identity,
                    variant_price_bani=int(round(lot.price * 100)),
                )
                result["prospective_ledger"] = {"record_id": record_id, "generation_event_sha256": receipt,
                    "status": "generated_unassigned", "commitment_required": True}
            except (OSError, ValueError, sqlite3.Error) as exc:
                # A ledger failure is visible in the result; it must never turn
                # an unrecorded portfolio into claimed prospective evidence.
                result["prospective_ledger"] = {"status": "recording_failed", "error": str(exc)}
    return results_bundle
