"""Local, append-only evidence of Romanian LR recommendations made before draws."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from covering.lr_schemes import LR_DIR
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_played_portfolio import CONTRACT_VERSION, PlayedPortfolio, expand_plan, grade_draw
from loto_enterprise.core.official_prizes import PrizeDraw, normalize_game
from runtime_paths import PROJECT_ROOT, RUNTIME_ROOT

_TIME_ZONE = ZoneInfo("Europe/Bucharest")
_ONLINE_CLOSE = time(15, 30)
# https://www.loto.ro/?page_id=1066
_WEEKDAYS = {3, 6}
_SCHEMA = 1
_DB_SCHEMA = 3
_RECENT_DRAW_DAYS = 31
_HASH = re.compile(r"[0-9a-f]{64}")
_ENGINE_HASH = re.compile(r"[0-9a-f]{16}")
_FILE = "lr_prospective.sqlite3"


def _clock():
    return datetime.now(_TIME_ZONE)


def _location(path):
    if path is not None:
        return Path(path)
    if override := os.getenv("LOTO_RUNTIME_DIR"):
        return Path(override).expanduser() / _FILE
    if RUNTIME_ROOT != PROJECT_ROOT:
        return RUNTIME_ROOT / _FILE
    if local := os.getenv("LOCALAPPDATA"):
        return Path(local) / "LOTO" / _FILE
    return Path.home() / ".local" / "state" / "loto" / _FILE


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.strptime(value, "%d-%m-%Y").date()
    raise ValueError("Lipsește data ultimei extrageri folosite pentru predicție.")


def draw_target(captured, history_day, *, requested=None, schedule=None):
    """Select a prospective Romanian local date, with optional published exceptions.

    Schedule exceptions map local dates to the closing time published for them,
    or None when a normally scheduled drawing was moved or cancelled.
    """
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("Înregistrarea necesită un moment cu fus orar.")
    captured = captured.astimezone(_TIME_ZONE)
    history_day = _day(history_day)
    if history_day > captured.date():
        raise ValueError("Istoricul conține extrageri ulterioare momentului înregistrării.")
    schedule = schedule or {}

    def cutoff(day):
        close = schedule.get(day, _ONLINE_CLOSE if day.weekday() in _WEEKDAYS else None)
        return datetime.combine(day, close, _TIME_ZONE) if close is not None else None

    if requested is not None:
        requested = _day(requested)
        target_close = cutoff(requested)
        if target_close is None or history_day >= requested or captured >= target_close:
            raise ValueError("Tragerea țintă este închisă, deja cunoscută sau absentă din program.")
        return requested, target_close

    candidate = max(captured.date(), history_day + timedelta(days=1))
    # Inspect a complete regular cycle after the last published exception.
    horizon = max(candidate, *schedule) + timedelta(days=7) if schedule else candidate + timedelta(days=7)
    while candidate <= horizon:
        target_close = cutoff(candidate)
        if target_close is not None and captured < target_close:
            return candidate, target_close
        candidate += timedelta(days=1)
    raise ValueError("Programul nu conține o tragere viitoare eligibilă.")


def _build_identity():
    source = {path for path in PROJECT_ROOT.glob("*.py") if not path.name.startswith("test_")}
    source.update((PROJECT_ROOT / "covering").rglob("*.py"))
    source.update((PROJECT_ROOT / "loto_enterprise").rglob("*.py"))
    source.update(path for path in LR_DIR.rglob("*") if path.is_file())
    digest = sha256()
    for path in sorted(source):
        if path.is_file():
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(sha256(path.read_bytes()).digest())
    return {"git_sha": _git_identity(), "source_sha256": digest.hexdigest()}


@lru_cache(maxsize=1)
def _git_identity():
    code_sha = None
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=2, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if re.fullmatch(r"[0-9a-f]{40,64}", result.stdout.strip()):
            code_sha = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    return code_sha


_BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS predictions (
        id TEXT PRIMARY KEY,
        game TEXT NOT NULL,
        target_day TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS lr_predictions_by_day ON predictions(target_day, game);
    CREATE TABLE IF NOT EXISTS evaluations (
        prediction_id TEXT NOT NULL REFERENCES predictions(id),
        draw_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (prediction_id, draw_id)
    );
    CREATE TRIGGER IF NOT EXISTS lr_predictions_check_insert BEFORE INSERT ON predictions
        WHEN EXISTS(SELECT 1 FROM predictions WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'prediction already registered'); END;
    CREATE TRIGGER IF NOT EXISTS lr_predictions_no_update BEFORE UPDATE ON predictions
        BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS lr_predictions_no_delete BEFORE DELETE ON predictions
        BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS lr_evaluations_check_insert BEFORE INSERT ON evaluations
        WHEN EXISTS(SELECT 1 FROM evaluations
                    WHERE prediction_id=NEW.prediction_id AND draw_id=NEW.draw_id)
        BEGIN SELECT RAISE(ABORT, 'draw already evaluated'); END;
    CREATE TRIGGER IF NOT EXISTS lr_evaluations_no_update BEFORE UPDATE ON evaluations
        BEGIN SELECT RAISE(ABORT, 'evaluations are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS lr_evaluations_no_delete BEFORE DELETE ON evaluations
        BEGIN SELECT RAISE(ABORT, 'evaluations are immutable'); END;
"""

_REVISION_TABLE = """
    CREATE TABLE IF NOT EXISTS official_revisions (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES official_events(id),
        sequence INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
        supersedes TEXT REFERENCES official_revisions(id), observed_at TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE (event_id, sequence)
    );
"""

_EVENT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS official_events (
        id TEXT PRIMARY KEY, game TEXT NOT NULL, draw_date TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS official_events_by_day ON official_events(game, draw_date);
    CREATE TABLE IF NOT EXISTS official_event_keys (
        key TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES official_events(id), kind TEXT NOT NULL,
        PRIMARY KEY (key, event_id)
    );
    CREATE INDEX IF NOT EXISTS official_keys_by_event ON official_event_keys(event_id);
""" + _REVISION_TABLE + """
    CREATE UNIQUE INDEX IF NOT EXISTS official_revision_successor ON official_revisions(supersedes);
    CREATE TABLE IF NOT EXISTS official_observations (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES official_events(id),
        revision_id TEXT NOT NULL REFERENCES official_revisions(id), disposition TEXT NOT NULL,
        observed_utc TEXT NOT NULL, archive_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
        source_version INTEGER, payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS official_observation_watermark
        ON official_observations(event_id, observed_utc DESC) WHERE disposition IN ('revision', 'confirmation');
    CREATE INDEX IF NOT EXISTS official_observation_page
        ON official_observations(event_id, archive_sha256, content_sha256, source_version);
    CREATE INDEX IF NOT EXISTS official_observation_version
        ON official_observations(event_id, source_version DESC) WHERE source_version IS NOT NULL;
    CREATE TABLE IF NOT EXISTS official_conflicts (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS legacy_evaluation_links (
        prediction_id TEXT NOT NULL, legacy_draw_id TEXT NOT NULL,
        revision_id TEXT NOT NULL REFERENCES official_revisions(id),
        PRIMARY KEY (prediction_id, legacy_draw_id),
        FOREIGN KEY (prediction_id, legacy_draw_id) REFERENCES evaluations(prediction_id, draw_id)
    );
    CREATE VIEW IF NOT EXISTS active_official_evaluations AS
        SELECT e.prediction_id, e.draw_id, e.payload
        FROM evaluations AS e JOIN official_revisions AS r ON r.id = e.draw_id
        WHERE NOT EXISTS (SELECT 1 FROM official_revisions AS n WHERE n.supersedes = r.id);
"""

def _execute_schema(db, sql):
    # executescript commits an already open transaction on older sqlite3 APIs.
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            db.execute(pending)
            pending = ""


def _observation_time(moment):
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _store_observation(db, event_id, revision_id, evidence, observed_at, disposition):
    timestamp = _observation_time(observed_at)
    page = evidence["official_archive_sha256"]
    content = evidence["content_sha256"]
    version = evidence.get("source_version")
    record = {**evidence, "event_id": event_id, "revision_id": revision_id, "disposition": disposition,
              "observed_at": observed_at.isoformat()}
    observation_id = _hash([event_id, timestamp, page, content, version, evidence["identity"],
                            revision_id, disposition == "conflict"])
    if not db.execute("SELECT 1 FROM official_observations WHERE id=?", (observation_id,)).fetchone():
        db.execute("INSERT INTO official_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (observation_id, event_id, revision_id, disposition, timestamp, page,
                    content, version, _canonical(record)))


def _migrate(db):
    if db.execute("PRAGMA user_version").fetchone()[0] >= _DB_SCHEMA:
        return
    # Replace the v2 table in place: evaluations and legacy links continue to
    # refer to precisely the same immutable IDs and payload bytes.
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= _DB_SCHEMA:
            db.commit()
            return
        has_revisions = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_revisions'"
        ).fetchone()
        if has_revisions:
            db.execute("DROP VIEW IF EXISTS active_official_evaluations")
            db.execute(_REVISION_TABLE.replace("official_revisions", "replacement_revisions"))
            db.execute("INSERT INTO replacement_revisions SELECT * FROM official_revisions")
            db.execute("DROP TABLE official_revisions")
            db.execute("ALTER TABLE replacement_revisions RENAME TO official_revisions")
        _execute_schema(db, _BASE_SCHEMA + _EVENT_SCHEMA)
        for table, identity in (
            ("official_events", "id=NEW.id"),
            ("official_event_keys", "key=NEW.key AND event_id=NEW.event_id"),
            ("official_revisions", """id=NEW.id OR (event_id=NEW.event_id AND sequence=NEW.sequence)
                                       OR supersedes=NEW.supersedes"""),
            ("official_observations", "id=NEW.id"),
            ("official_conflicts", "id=NEW.id"),
            ("legacy_evaluation_links", "prediction_id=NEW.prediction_id AND legacy_draw_id=NEW.legacy_draw_id"),
        ):
            db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS lr_{table}_insert BEFORE INSERT ON {table}
                WHEN EXISTS (SELECT 1 FROM {table} WHERE {identity})
                BEGIN SELECT RAISE(ABORT, 'official evidence already exists'); END
            """)
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS lr_{table}_{action.lower()} BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'official evidence is immutable'); END
                """)
        if has_revisions:
            for row in db.execute("SELECT * FROM official_revisions ORDER BY rowid"):
                _store_observation(db, row["event_id"], row["id"], json.loads(row["payload"]),
                                   datetime.fromisoformat(row["observed_at"]), "revision")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("Migrarea ar lăsa o referință invalidă în registrul oficial.")
        db.execute(f"PRAGMA user_version={_DB_SCHEMA}")
        db.commit()
    finally:
        if db.in_transaction:
            db.rollback()
        db.execute("PRAGMA foreign_keys=ON")


@contextmanager
def _connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        _migrate(db)
        yield db
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()

def _snapshot(plan, settings, captured, known_days, build, target_day=None, schedule=None):
    forecast = plan.get("forecast") or {}
    history_hash = forecast.get("history_hash")
    if not isinstance(history_hash, str) or not (_ENGINE_HASH.fullmatch(history_hash) or _HASH.fullmatch(history_hash)):
        raise ValueError("Predicția nu are amprenta istoricului folosit.")
    full_hash = forecast.get("history_sha256") or (history_hash if len(history_hash) == 64 else None)
    if full_hash is not None and (
        not isinstance(full_hash, str) or not _HASH.fullmatch(full_hash) or not full_hash.startswith(history_hash)
    ):
        raise ValueError("Amprentele istoricului folosit diferă.")
    history_day = _day(forecast.get("date"))
    last_known = max(history_day, _day(known_days.get(plan["game"], history_day)))
    selected, close = draw_target(captured, last_known, requested=target_day, schedule=schedule)
    expanded = expand_plan(plan)
    support = {
        "schema": _SCHEMA,
        "evaluation_contract_version": CONTRACT_VERSION,
        "game": plan["game"],
        "target_day": selected.isoformat(),
        "target_close": close.isoformat(),
        "build": build,
        "history": {
            "engine_hash": history_hash,
            "sha256": full_hash,
            "source": forecast.get("filename"),
            "rows": int(forecast["rows"]),
            "as_of": history_day.isoformat(),
            "last_known_at_registration": last_known.isoformat(),
        },
        "settings": {
            **settings,
            "objective": plan["objective"],
            "budget_bani": plan["budget_bani"],
            **({"budget_mode": plan["budget_mode"]} if "budget_mode" in plan else {}),
            "main_scheme": forecast.get("code"),
            "inverse": forecast.get("inverse"),
            "dynamic": forecast.get("dynamic"),
            "number_range": forecast.get("number_range"),
            "max_consecutive_run": forecast.get("max_consecutive_run"),
            "selection_policy": forecast.get("selection_policy"),
        },
        "methods": {key: forecast.get(key) for key in
                    ("selection", "joker_selection", "excluded_pool", "excluded_jokers")},
        "main_pool": sorted(expanded.pool),
        "joker_pool": list(forecast.get("jokers") or ()),
        "tickets": plan["tickets"],
        "paid_variants": expanded.paid,
        "price_bani": expanded.price_bani,
        "variants": expanded.variants,
    }
    if plan.get("portfolio_refinement") is not None:
        support["portfolio_refinement"] = plan["portfolio_refinement"]
    if "walk_forward" in plan:
        support["walk_forward"] = plan["walk_forward"]
    identity = _hash(support)
    return {**support, "id": identity, "captured_at": captured.isoformat()}


def register_lr_recommendations(plans, *, settings=None, known_days=None, target_day=None, schedule=None, registry_path=None):
    """Store each successful explicit generation. Return registered records and errors by game."""
    captured = _clock().astimezone(_TIME_ZONE)
    records, errors = [], {}
    build = None
    for plan in plans:
        if not plan.get("tickets") or not plan.get("forecast"):
            continue
        try:
            if build is None:
                build = _build_identity()
            entry = _snapshot(plan, settings or {}, captured, known_days or {}, build, target_day, schedule)
        except (OSError, TypeError, ValueError) as exc:
            errors[plan["game"]] = str(exc)
            continue
        records.append(entry)
    if not records:
        return [], errors

    expired = "Ora-limită a trecut înainte de salvarea predicției."
    result, inserted = [], {}
    with _connect(_location(registry_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        writable_at = _clock()
        for entry in records:
            previous = db.execute("SELECT payload FROM predictions WHERE id=?", (entry["id"],)).fetchone()
            if previous is None:
                closes = datetime.fromisoformat(entry["target_close"])
                if not captured <= writable_at < closes:
                    errors[entry["game"]] = expired
                    continue
                db.execute(
                    "INSERT INTO predictions VALUES (?, ?, ?, ?)",
                    (entry["id"], entry["game"], entry["target_day"], _canonical(entry)),
                )
                inserted[entry["id"]] = closes
            else:
                entry = json.loads(previous["payload"])
            result.append({k: entry[k] for k in ("id", "game", "target_day", "captured_at")})
        # Building the evidence or waiting for another writer may run past
        # closing time. A receipt is valid only if its first write can commit.
        if inserted and not captured <= _clock() < min(inserted.values()):
            db.rollback()
            for entry in result:
                if entry["id"] in inserted:
                    errors[entry["game"]] = expired
            result = [entry for entry in result if entry["id"] not in inserted]
        else:
            db.commit()
    return result, errors


@dataclass(frozen=True)
class _Observation:
    game: str
    day: date
    draw: PrizeDraw
    content_sha: str
    keys: dict[str, str]
    evidence: dict

    @property
    def supplemental(self):
        return self.evidence["supplementary"] is True


def _observe(draw, captured):
    day = _day(draw.draw_date)
    source = urlsplit(draw.source_url)
    if day > captured.date() or source.scheme != "https" or source.netloc not in {"www.loto.ro", "loto.ro"}:
        raise ValueError("Rezultatul trebuie să provină din arhiva oficială și să fie deja publicat.")
    game = {"loto_6_49": "6/49", "loto_5_40": "5/40", "joker": "joker"}[normalize_game(draw.game)]
    lot = require_lottery(game, "RO")
    main = tuple(draw.numbers)
    if (len(main) != lot.draw_n or len(set(main)) != lot.draw_n
            or any(type(ball) is not int or not 1 <= ball <= lot.max_n for ball in main)):
        raise ValueError("Arhiva oficială conține o geometrie de extragere invalidă.")
    if lot.geo.second:
        if type(draw.joker) is not int or not 1 <= draw.joker <= lot.geo.second.max_n:
            raise ValueError("Arhiva oficială nu confirmă bila Joker.")
    elif draw.joker is not None:
        raise ValueError("Arhiva oficială a indicat o a doua urnă la un joc cu o singură urnă.")
    if game == "5/40" and not draw.order_known:
        raise ValueError("Arhiva oficială nu confirmă bila a șasea la Loto 5/40.")

    identity = getattr(draw, "official_id", None)
    order = getattr(draw, "draw_order", None)
    supplementary = getattr(draw, "supplementary", None)
    if identity is not None and (
        not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9:_./-]{1,128}", identity)
    ):
        raise ValueError("Identificator oficial de tragere invalid.")
    if order is not None and (type(order) is not int or not 1 <= order <= 999):
        raise ValueError("Ordinea oficială a tragerii este invalidă.")
    if supplementary is not None and type(supplementary) is not bool:
        raise ValueError("Tipul oficial al tragerii este invalid.")

    version = draw.result_version
    if version is not None and (type(version) is not int or not 1 <= version < 10**18):
        raise ValueError("Versiunea oficială a rezultatului este invalidă.")

    evidence = {"official_id": identity, "draw_order": order, "supplementary": supplementary}
    keys = {}
    if identity is not None:
        keys["id"] = _hash(["official-id", game, identity])
    scope = [game, day.isoformat()]
    if order is not None:
        keys["order"] = _hash(["order", *scope, order])
    if supplementary is not None:
        keys["role"] = _hash(["role", *scope, supplementary])
    return _Observation(game, day, draw, _result_fingerprint(game, main, draw.joker), keys, evidence)


def _result_fingerprint(game, numbers, joker):
    main = (*sorted(numbers[:-1]), numbers[-1]) if game == "5/40" else sorted(numbers)
    return _hash([main, joker])


def _snapshot_conflicts(observations):
    matches = defaultdict(list)
    for index, observation in enumerate(observations):
        for kind, key in observation.keys.items():
            matches[kind, key].append(index)
    rejected = {}
    for (kind, _), indexes in matches.items():
        if len(indexes) < 2:
            continue
        group = [observations[index] for index in indexes]
        # The verified principal role is unique. Identical repeats of that
        # result or of a verified ID or ordinal are idempotent.
        signatures = {(_canonical(row.keys), _canonical(row.evidence), row.content_sha, row.draw.result_version) for row in group}
        first = group[0]
        principal = first.evidence["supplementary"] is False
        if len(signatures) == 1 and (principal or "id" in first.keys or "order" in first.keys):
            continue
        # Explicit IDs (or orders) can distinguish multiple supplements even
        # when the archive assigns all of them the same generic heading.
        if kind == "role" and all(row.supplemental for row in group):
            discriminators = [{row.keys.get(key) for row in group} for key in ("id", "order")]
            if any(None not in values and len(values) == len(group) for values in discriminators):
                continue
        for index in indexes:
            rejected[index] = "Mai multe rezultate au aceeași identitate în acea pagină oficială."
    return rejected


class _IdentityConflict(ValueError):
    pass


def _source_evidence(observation, archive_sha256):
    draw = observation.draw
    return {
        "game": observation.game, "date": observation.day.isoformat(), "identity": observation.evidence,
        "content_sha256": observation.content_sha, "numbers": draw.numbers, "joker": draw.joker,
        "official_url": draw.source_url, "official_archive_sha256": archive_sha256,
        "source_version": draw.result_version,
    }


def _record_conflict(db, observation, event_id, reason, archive_sha256, observed_at):
    payload = {**_source_evidence(observation, archive_sha256), "reason": reason,
               "observed_at": _observation_time(observed_at), "event_id": event_id}
    if event_id is not None:
        latest = db.execute("SELECT id FROM official_revisions WHERE event_id=? ORDER BY sequence DESC LIMIT 1",
                            (event_id,)).fetchone()
        if latest:
            _store_observation(db, event_id, latest["id"], payload, observed_at, "conflict")
    conflict_id = _hash(payload)
    if not db.execute("SELECT 1 FROM official_conflicts WHERE id=?", (conflict_id,)).fetchone():
        db.execute("INSERT INTO official_conflicts VALUES (?, ?)", (conflict_id, _canonical(payload)))


def _resolve_event(db, observation):
    if not observation.keys:
        raise _IdentityConflict("Sursa nu confirmă identitatea, tipul sau ordinea tragerii.")
    keys = observation.keys
    matches = {
        kind: {row[0] for row in db.execute("SELECT event_id FROM official_event_keys WHERE key=?", (key,))}
        for kind, key in keys.items()
    }
    stronger = matches.get("id", set()) | matches.get("order", set())
    role = matches.get("role", set())
    if len(stronger) > 1:
        raise _IdentityConflict("Identificatorul oficial și ordinea indică trageri diferite.")

    def bindings(event_id, kind):
        return {row[0] for row in db.execute(
            "SELECT key FROM official_event_keys WHERE event_id=? AND kind=?", (event_id, kind)
        )}

    # Supplemental role is non-unique when several known events share its
    # label. An explicit identifier can still resolve each of these events.
    if not stronger and observation.supplemental and ("id" in keys or "order" in keys):
        role = {event for event in role
                if not any(bindings(event, kind) - {keys[kind]} for kind in ("id", "order") if kind in keys)}
    candidates = stronger or role
    if len(candidates) > 1:
        raise _IdentityConflict("Tipul tragerii nu identifică un singur eveniment oficial.")
    if stronger and role and not observation.supplemental and stronger != role:
        raise _IdentityConflict("Identificatorul oficial și tipul indică trageri diferite.")
    event_id = next(iter(candidates), None)
    if event_id is not None:
        event = db.execute("SELECT game, draw_date FROM official_events WHERE id=?", (event_id,)).fetchone()
        if tuple(event) != (observation.game, observation.day.isoformat()):
            raise _IdentityConflict("Identificatorul oficial apare la o altă dată.")
        if any(bindings(event_id, kind) - {key} for kind, key in keys.items()):
            raise _IdentityConflict("Sursa a schimbat identitatea sau ordinea atribuită tragerii.")
        return event_id

    # A new identifier is separate only if there is evidence that it differs
    # from every existing event on that date. An old role-only entry and a
    # new ID-only entry could describe the same event.
    nearby = defaultdict(dict)
    for other_event, kind, key in db.execute("""
        SELECT e.id, k.kind, k.key FROM official_events AS e
        JOIN official_event_keys AS k ON e.id=k.event_id WHERE e.game=? AND e.draw_date=?
    """, (observation.game, observation.day.isoformat())):
        nearby[other_event][kind] = key
    if any(not any(key != other_keys[kind] for kind, key in keys.items() if kind in other_keys)
           for other_keys in nearby.values()):
        raise _IdentityConflict("Sursa nu permite deosebirea de o tragere deja înregistrată în acea zi.")
    return None


def _classify_observation(db, latest, evidence, observed_at, previous_archive):
    """Return a historical revision ID for a replay. Raise on conflicting evidence."""
    event_id = latest["event_id"]
    archive_sha256 = evidence["official_archive_sha256"]
    content = evidence["content_sha256"]
    version = evidence["source_version"]
    timestamp = _observation_time(observed_at)
    # A previous edition stays historical even if a replay is given today's
    # retrieval time, or its numbers happen to match a later active revision.
    known_page = db.execute("""
        SELECT revision_id, source_version FROM official_observations
        WHERE event_id=? AND archive_sha256=? AND content_sha256=?
          AND disposition IN ('revision', 'confirmation') ORDER BY source_version DESC, rowid DESC LIMIT 1
    """, (event_id, archive_sha256, content)).fetchone()
    if known_page:
        if version is not None and known_page["source_version"] not in (None, version):
            raise _IdentityConflict("Aceeași pagină atribuie rezultatului versiuni oficiale incompatibile.")
        if known_page["revision_id"] != latest["id"]:
            return known_page["revision_id"]
    if not known_page and db.execute("""
        SELECT 1 FROM official_observations WHERE event_id=? AND archive_sha256=? AND content_sha256<>?
          AND disposition IN ('revision', 'confirmation') LIMIT 1
    """, (event_id, archive_sha256, content)).fetchone():
        raise _IdentityConflict("Aceeași pagină oficială indică rezultate incompatibile pentru această tragere.")
    rejected = db.execute("""
        SELECT payload FROM official_observations WHERE event_id=? AND archive_sha256=? AND content_sha256=?
        AND source_version IS ? AND disposition='conflict'
    """, (event_id, archive_sha256, content, version)).fetchone()
    watermark = db.execute("""
        SELECT MAX(observed_utc) FROM official_observations
        WHERE event_id=? AND disposition IN ('revision', 'confirmation')
    """, (event_id,)).fetchone()[0]
    last_numbered = None
    if version is not None or (known_page and known_page["source_version"] is not None):
        last_numbered = db.execute("""
            SELECT revision_id, source_version, content_sha256 FROM official_observations
            WHERE event_id=? AND source_version IS NOT NULL AND disposition IN ('revision', 'confirmation')
            ORDER BY source_version DESC LIMIT 1
        """, (event_id,)).fetchone()
    changed = latest["content_sha256"] != content
    if rejected and not changed:
        raise _IdentityConflict(json.loads(rejected["payload"])["reason"])
    if (known_page and known_page["source_version"] is not None and last_numbered
            and known_page["source_version"] < last_numbered["source_version"]):
        return known_page["revision_id"]
    if last_numbered is not None and version is not None:
        if version < last_numbered["source_version"] or (
            version == last_numbered["source_version"] and content != last_numbered["content_sha256"]
        ):
            raise _IdentityConflict("Versiunea oficială este mai veche sau are rezultate contradictorii.")
    if changed:
        if previous_archive:
            raise _IdentityConflict("Pagina figurează în registrul vechi cu un rezultat anterior reviziei active.")
        prior = db.execute("""
            SELECT id, observed_at FROM official_revisions WHERE event_id=? AND content_sha256=?
            ORDER BY sequence DESC LIMIT 1
        """, (event_id, content)).fetchone()
        newer_edition = (version is not None and last_numbered is not None
                         and last_numbered["revision_id"] == latest["id"]
                         and version > last_numbered["source_version"])
        if rejected and not newer_edition:
            raise _IdentityConflict(json.loads(rejected["payload"])["reason"])
        if (prior is not None and version is None
                and timestamp == _observation_time(datetime.fromisoformat(prior["observed_at"]))):
            # Another archived copy of an already observed historical result at the same instant.
            return prior["id"]
        if timestamp < watermark or (timestamp == watermark and not newer_edition):
            raise _IdentityConflict("Observația diferită este anterioară sau simultană ultimei confirmări acceptate.")
        if prior is not None and not newer_edition:
            raise _IdentityConflict("Revenire la numere anterioare fără o versiune oficială ulterioară confirmată.")

    return None


def _accept_revision(db, observation, event_id, archive_sha256, observed_at, previous_archive):
    if event_id is None:
        # Derive the opaque local key from the first confirmed official
        # discriminator. Better evidence can later become an alias.
        event_id = _hash(["lr-official-event", next(iter(observation.keys.values()))])
        db.execute("INSERT INTO official_events VALUES (?, ?, ?)",
                   (event_id, observation.game, observation.day.isoformat()))
    latest = db.execute(
        "SELECT * FROM official_revisions WHERE event_id=? ORDER BY sequence DESC LIMIT 1", (event_id,),
    ).fetchone()
    content = observation.content_sha
    evidence = _source_evidence(observation, archive_sha256)
    if latest is not None:
        historical = _classify_observation(db, latest, evidence, observed_at, previous_archive)
        if historical is not None:
            _store_observation(db, event_id, historical, evidence, observed_at, "historical")
            return None

    for kind, key in observation.keys.items():
        if not db.execute("SELECT 1 FROM official_event_keys WHERE key=? AND event_id=?", (key, event_id)).fetchone():
            db.execute("INSERT INTO official_event_keys VALUES (?, ?, ?)", (key, event_id, kind))
    if latest is not None and latest["content_sha256"] == content:
        _store_observation(db, event_id, latest["id"], evidence, observed_at, "confirmation")
        return latest

    supersedes = None if latest is None else latest["id"]
    sequence = 1 if latest is None else latest["sequence"] + 1
    revision_id = _hash(["lr-official-revision-v3", event_id, sequence, supersedes, content])
    body = {
        **evidence, "event_id": event_id, "revision_id": revision_id, "revision": sequence,
        "supersedes_revision_id": supersedes, "observed_at": observed_at.isoformat(),
    }
    payload = _canonical(body)
    db.execute("INSERT INTO official_revisions VALUES (?, ?, ?, ?, ?, ?, ?)", (
        revision_id, event_id, sequence, observation.content_sha, supersedes, body["observed_at"], payload,
    ))
    _store_observation(db, event_id, revision_id, evidence, observed_at, "revision")
    return {"id": revision_id, "payload": payload}


def _evaluate_revision(db, entries, observation, revision, captured):
    result = json.loads(revision["payload"])
    written = 0
    for entry in entries:
        registered = json.loads(entry["payload"])
        created = datetime.fromisoformat(registered["captured_at"])
        closes = datetime.fromisoformat(registered["target_close"])
        if not created < closes <= captured:
            continue
        if db.execute("SELECT 1 FROM evaluations WHERE prediction_id=? AND draw_id=?",
                      (entry["id"], revision["id"])).fetchone():
            continue
        portfolio = PlayedPortfolio(
            observation.game, frozenset(registered["main_pool"]),
            tuple((tuple(main), joker, count) for main, joker, count in registered["variants"]),
            registered["price_bani"], registered["paid_variants"],
        )
        # Both contracts agree for complete official results verified above.
        rule = registered.get("evaluation_contract_version", 1)
        if rule not in (1, CONTRACT_VERSION):
            raise ValueError("Versiune necunoscută a regulii de evaluare înregistrate.")
        actual = {
            **result,
            "evaluation_contract_version": rule,
            "evaluated_at": captured.isoformat(),
            "grade": grade_draw(portfolio, result["numbers"], result["joker"], draw_date=observation.day),
        }
        db.execute("INSERT INTO evaluations VALUES (?, ?, ?)", (entry["id"], revision["id"], _canonical(actual)))
        written += 1
    return written


_LEGACY_FROM = """
    FROM evaluations AS e LEFT JOIN official_revisions AS r ON r.id = e.draw_id
    LEFT JOIN legacy_evaluation_links AS link
        ON link.prediction_id=e.prediction_id AND link.legacy_draw_id=e.draw_id
"""


def _legacy_archive_matches(db, game, day, wanted_sha):
    matches = defaultdict(list)
    for entry in db.execute(
        "SELECT e.* " + _LEGACY_FROM + """
        JOIN predictions AS p ON p.id=e.prediction_id
        WHERE r.id IS NULL AND p.game=? AND p.target_day=?
        """, (game, day),
    ):
        content = json.loads(entry["payload"])
        if content.get("official_archive_sha256") != wanted_sha or not content.get("numbers"):
            continue
        fingerprint = _result_fingerprint(game, content["numbers"], content.get("joker"))
        matches[fingerprint].append((entry["prediction_id"], entry["draw_id"]))
    return matches


def append_official_draws(draws, *, archive_sha256, registry_path=None, observed_at=None, problems=None):
    """Append evaluations for verified events on exact prediction target dates."""
    path = _location(registry_path)
    if not path.is_file():
        return 0
    if not isinstance(archive_sha256, str) or not _HASH.fullmatch(archive_sha256):
        raise ValueError("Rezultatele oficiale necesită amprenta paginii.")
    captured = _clock().astimezone(_TIME_ZONE)
    if observed_at is None:
        observed_at = captured
    if (observed_at.tzinfo is None or observed_at.utcoffset() is None
            or observed_at.astimezone(UTC) > captured.astimezone(UTC)):
        raise ValueError("Momentul consultării paginii oficiale este invalid.")
    observations = [_observe(draw, captured) for draw in draws]
    conflicts = _snapshot_conflicts(observations)
    written = 0
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        # Resolve known aliases against one pre-import snapshot. Two incompatible
        # rows for the same event must not become successive revisions merely
        # because the archive lists one before the other.
        by_event = defaultdict(list)
        by_day = defaultdict(list)
        for index, observation in enumerate(observations):
            if index in conflicts:
                continue
            try:
                event_id = _resolve_event(db, observation)
            except _IdentityConflict as exc:
                conflicts[index] = str(exc)
                continue
            if event_id is not None:
                by_event[event_id].append(index)
            by_day[observation.game, observation.day].append((index, event_id))
        for rows in by_day.values():
            for (left, left_id), (right, right_id) in combinations(rows, 2):
                if (left_id is not None and right_id is not None
                        or observations[left].keys.keys() & observations[right].keys.keys()):
                    continue
                conflicts[left] = conflicts[right] = "Pagina nu permite deosebirea celor două trageri raportate."
        for indexes in by_event.values():
            group = [observations[index] for index in indexes]
            distinct_results = {row.content_sha for row in group}
            distinct_ids = {row.keys["id"] for row in group if "id" in row.keys}
            distinct_orders = {row.keys["order"] for row in group if "order" in row.keys}
            versions = {row.draw.result_version for row in group if row.draw.result_version is not None}
            if any(len(values) > 1 for values in (distinct_results, distinct_ids, distinct_orders, versions)):
                for index in indexes:
                    conflicts[index] = "Aceeași tragere are date incompatibile în aceeași pagină oficială."
        old_sources = {}
        accepted = defaultdict(set)
        for index, observation in enumerate(observations):
            day = observation.day.isoformat()
            scope = observation.game, day
            entries = db.execute("SELECT id, payload FROM predictions WHERE game=? AND target_day=?",
                                 (observation.game, day)).fetchall()
            if not entries:
                continue
            event_id = None
            try:
                if index in conflicts:
                    raise _IdentityConflict(conflicts[index])
                if scope not in old_sources:
                    old_sources[scope] = _legacy_archive_matches(db, *scope, archive_sha256)
                event_id = _resolve_event(db, observation)
                revision = _accept_revision(
                    db, observation, event_id, archive_sha256, observed_at,
                    observation.content_sha in old_sources[scope],
                )
                if revision is not None:
                    written += _evaluate_revision(db, entries, observation, revision, captured)
                    accepted[scope, observation.content_sha].add(revision["id"])
            except _IdentityConflict as exc:
                conflicts[index] = reason = str(exc)
                _record_conflict(db, observation, event_id, reason, archive_sha256, observed_at)
                if problems is not None:
                    message = f"{observation.game} {day}: {reason}"
                    if message not in problems:
                        problems.append(message)
        uncertain = {(observations[index].game, observations[index].day.isoformat()) for index in conflicts}
        for (scope, content_sha), revisions in accepted.items():
            if scope in uncertain or len(revisions) != 1:
                continue
            for prediction_id, legacy_id in old_sources[scope].get(content_sha, ()):
                if db.execute("""
                    SELECT 1 FROM legacy_evaluation_links WHERE prediction_id=? AND legacy_draw_id=?
                """, (prediction_id, legacy_id)).fetchone() is None:
                    db.execute("INSERT INTO legacy_evaluation_links VALUES (?, ?, ?)",
                               (prediction_id, legacy_id, next(iter(revisions))))
        db.commit()
    return written


def read_prospective_report(*, registry_path=None, include_audit=False):
    """Report active official evaluations. Historical and legacy rows are opt-in."""
    report = {"predictions_count": 0, "active_evaluations": [], "conflicts": [], "legacy_pending_count": 0}
    if include_audit:
        report.update(superseded_evaluations=[], legacy_evaluations=[], event_revisions=[], event_observations=[])
    path = _location(registry_path)
    if not path.is_file():
        return report

    def evaluations(db, sql):
        return [{**json.loads(row["payload"]), "prediction_id": row["prediction_id"], "draw_id": row["draw_id"]}
                for row in db.execute(sql)]
    with _connect(path) as db:
        # Every part of a report must refer to the same database snapshot.
        db.execute("BEGIN")
        report["predictions_count"] = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        report["active_evaluations"] = evaluations(db, """
            SELECT * FROM active_official_evaluations ORDER BY prediction_id, draw_id
        """)
        report["conflicts"] = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM official_conflicts ORDER BY rowid"
        )]
        report["legacy_pending_count"] = db.execute(
            "SELECT COUNT(*) " + _LEGACY_FROM + "WHERE r.id IS NULL AND link.revision_id IS NULL"
        ).fetchone()[0]
        if include_audit:
            report["superseded_evaluations"] = evaluations(db, """
                SELECT e.* FROM evaluations AS e
                JOIN official_revisions AS successor ON successor.supersedes = e.draw_id
                ORDER BY e.rowid
            """)
            report["legacy_evaluations"] = [
                {**json.loads(row["payload"]), "prediction_id": row["prediction_id"], "draw_id": row["draw_id"],
                 "verified_revision_id": row["revision_id"]}
                for row in db.execute(
                    "SELECT e.*, link.revision_id " + _LEGACY_FROM + "WHERE r.id IS NULL ORDER BY e.rowid"
                )
            ]
            report["event_observations"] = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM official_observations ORDER BY rowid"
            )]
            report["event_revisions"] = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM official_revisions ORDER BY rowid"
            )]
    return report


def refresh_official_evaluations(*, registry_path=None, include_settled=False):
    """Import monthly pages for registered predictions.

    Set include_settled to check old, already verified targets for corrections.
    """
    from loto_enterprise.core.official_prizes import _fetch_month_html, parse_month_html

    path = _location(registry_path)
    if not path.is_file():
        return 0, []
    captured = _clock().astimezone(_TIME_ZONE)
    today = captured.date()
    through = today if captured.time() >= _ONLINE_CLOSE else today - timedelta(days=1)
    recent = today - timedelta(days=_RECENT_DRAW_DAYS)
    with _connect(path) as db:
        targets = db.execute(
            """SELECT DISTINCT p.game, p.target_day FROM predictions AS p
               WHERE p.target_day <= ? AND (? OR p.target_day >= ? OR NOT EXISTS (
                   SELECT 1 FROM active_official_evaluations e WHERE e.prediction_id = p.id))""",
            (through.isoformat(), include_settled, recent.isoformat()),
        ).fetchall()
    months = {}
    for game, day in targets:
        day = _day(day)
        months.setdefault((normalize_game(game), day.year, day.month), set()).add(day.isoformat())
    total, errors = 0, []
    for (game, year, month), days in sorted(months.items()):
        try:
            html = _fetch_month_html(game, year, month)
        except (OSError, ValueError) as exc:
            errors.append(f"{game} {year:04}-{month:02}: {exc}")
            continue
        observed_at = _clock().astimezone(_TIME_ZONE)
        fingerprint = sha256(html.encode("utf-8")).hexdigest()
        for target in sorted(days):
            try:
                official = parse_month_html(game, html, year, month, draw_dates={target}, preserve_event_identity=True)
                total += append_official_draws(
                    official, archive_sha256=fingerprint, registry_path=path, observed_at=observed_at, problems=errors,
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(f"{game} {target}: {exc}")
    return total, errors
