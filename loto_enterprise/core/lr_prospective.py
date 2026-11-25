"""Local, append-only evidence of Romanian LR recommendations made before draws."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from hashlib import sha256
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
from loto_enterprise.core.official_prizes import normalize_game
from runtime_paths import PROJECT_ROOT, RUNTIME_ROOT

_TIME_ZONE = ZoneInfo("Europe/Bucharest")
_ONLINE_CLOSE = time(15, 30)
# https://www.loto.ro/?page_id=1066
_WEEKDAYS = {3, 6}
_SCHEMA = 1
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


@contextmanager
def _connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
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
        """)
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


def _draw_identity(draw):
    main = tuple(draw.numbers)
    if normalize_game(draw.game) == "loto_5_40":
        main = (*sorted(main[:-1]), main[-1])
    else:
        main = tuple(sorted(main))
    return _hash([normalize_game(draw.game), draw.draw_date, main, draw.joker])


def append_official_draws(draws, *, archive_sha256, registry_path=None):
    """Evaluate exact target dates against official results without editing entries."""
    path = _location(registry_path)
    if not path.is_file():
        return 0
    if not isinstance(archive_sha256, str) or not _HASH.fullmatch(archive_sha256):
        raise ValueError("Rezultatele oficiale necesită amprenta paginii.")
    captured = _clock().astimezone(_TIME_ZONE)
    written = 0
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for draw in draws:
            day = _day(draw.draw_date)
            source = urlsplit(draw.source_url)
            if day > captured.date() or source.scheme != "https" or source.netloc not in {"www.loto.ro", "loto.ro"}:
                raise ValueError("Rezultatul trebuie să provină din arhiva oficială și să fie deja publicat.")
            game = {"loto_6_49": "6/49", "loto_5_40": "5/40", "joker": "joker"}[normalize_game(draw.game)]
            lot = require_lottery(game, "RO")
            if (len(draw.numbers) != lot.draw_n or len(set(draw.numbers)) != lot.draw_n
                    or any(type(ball) is not int or not 1 <= ball <= lot.max_n for ball in draw.numbers)):
                raise ValueError("Arhiva oficială conține o geometrie de extragere invalidă.")
            if lot.geo.second:
                if type(draw.joker) is not int or not 1 <= draw.joker <= lot.geo.second.max_n:
                    raise ValueError("Arhiva oficială nu confirmă bila Joker.")
            elif draw.joker is not None:
                raise ValueError("Arhiva oficială a indicat o a doua urnă la un joc cu o singură urnă.")
            if game == "5/40" and not draw.order_known:
                raise ValueError("Arhiva oficială nu confirmă bila a șasea la Loto 5/40.")
            draw_id = _draw_identity(draw)
            entries = db.execute("SELECT id, payload FROM predictions WHERE game=? AND target_day=?",
                                 (game, day.isoformat())).fetchall()
            for entry in entries:
                registered = json.loads(entry["payload"])
                created = datetime.fromisoformat(registered["captured_at"])
                closes = datetime.fromisoformat(registered["target_close"])
                if not created < closes <= captured:
                    continue
                if db.execute("SELECT 1 FROM evaluations WHERE prediction_id=? AND draw_id=?",
                              (entry["id"], draw_id)).fetchone():
                    continue
                portfolio = PlayedPortfolio(
                    game, frozenset(registered["main_pool"]),
                    tuple((tuple(main), joker, count) for main, joker, count in registered["variants"]),
                    registered["price_bani"], registered["paid_variants"],
                )
                # Both contracts agree for complete official results verified above.
                rule = registered.get("evaluation_contract_version", 1)
                if rule not in (1, CONTRACT_VERSION):
                    raise ValueError("Versiune necunoscută a regulii de evaluare înregistrate.")
                actual = {
                    "evaluation_contract_version": rule,
                    "game": game,
                    "date": day.isoformat(),
                    "numbers": draw.numbers,
                    "joker": draw.joker,
                    "official_url": draw.source_url,
                    "official_archive_sha256": archive_sha256,
                    "evaluated_at": captured.isoformat(),
                    "grade": grade_draw(portfolio, draw.numbers, draw.joker, draw_date=day),
                }
                db.execute("INSERT INTO evaluations VALUES (?, ?, ?)",
                           (entry["id"], draw_id, _canonical(actual)))
                written += 1
        db.commit()
    return written


def refresh_official_evaluations(*, registry_path=None):
    """Import official monthly pages only for dates with registered predictions."""
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
               WHERE p.target_day <= ? AND (p.target_day >= ? OR NOT EXISTS (
                   SELECT 1 FROM evaluations e WHERE e.prediction_id = p.id))""",
            (through.isoformat(), recent.isoformat()),
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
        fingerprint = sha256(html.encode("utf-8")).hexdigest()
        for target in sorted(days):
            try:
                official = parse_month_html(game, html, year, month, draw_dates={target})
                total += append_official_draws(official, archive_sha256=fingerprint, registry_path=path)
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(f"{game} {target}: {exc}")
    return total, errors
