"""Lansarea și afișarea analizei Re-Bench pe baza aleasă pentru biletele LR."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from concurrent.futures import CancelledError
from copy import deepcopy
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

from nicegui import run, ui

from loto_enterprise.benchmark.lr_hypotheses import benchmark_lr_hypotheses
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_range_analysis import compare_budget_ranges
from runtime_paths import RUNTIME_ROOT
from ui_shared import atomic_write_json


logger = logging.getLogger(__name__)
_RESULT_PATH = RUNTIME_ROOT / ".nicegui" / "lr_rebench_latest.json"
_BUCHAREST = ZoneInfo("Europe/Bucharest")
_task = None
_active = None
_last = None
_loaded = False
_notice = ""
_revision = 0
_PROFILE_SETTINGS = ("scheme", "inverse", "restricted", "dynamic", "lookback", "hit_target", "joker_size")


def lr_rebench_running() -> bool:
    """True pentru toate filele care folosesc această instanță a aplicației."""
    return _task is not None and not _task.done()


def _restore_report():
    global _loaded, _last, _notice
    if _loaded:
        return
    _loaded = True
    try:
        saved = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or saved.get("schema") != 1
                or not isinstance(saved.get("entries"), list) or not isinstance(saved.get("total"), int)):
            raise ValueError("format de raport necunoscut")
        datetime.fromisoformat(saved.get("finished_at", ""))
        saved["decision_profiles"] = _restore_profiles(saved.get("decision_profiles"))
        _last = saved
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("[Re-Bench LR] Raportul local nu poate fi citit: %s", exc)
        _notice = "Raportul anterior de Re-Bench pe baza aleasă nu poate fi citit."


def _valid_members(members):
    return isinstance(members, list) and bool(members) and all(
        isinstance(member, dict) and isinstance(member.get("method"), str) and member["method"]
        and type(weight := member.get("weight")) in (int, float) and isfinite(weight) and weight > 0
        for member in members
    )


def _compact_profile(entry, finished_at):
    if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
        return None
    result = entry.get("result")
    settings = entry.get("settings")
    if entry.get("error") or not isinstance(result, dict) or result.get("error"):
        return None
    if not isinstance(settings, dict) or not all(key in settings for key in _PROFILE_SETTINGS):
        return None
    if (any(type(settings[key]) is not bool for key in ("inverse", "restricted", "dynamic"))
            or type(settings["lookback"]) not in (int, float)
            or type(settings["hit_target"]) is not int or type(settings["joker_size"]) is not int):
        return None
    history_hash = result.get("history_hash")
    history_rows = result.get("history_rows")
    scheme = result.get("scheme_code")
    game = entry.get("game")
    if (not isinstance(history_hash, str) or not history_hash or type(history_rows) is not int or history_rows < 1
            or type(scheme) is not int or str(scheme) != str(settings["scheme"]) or result.get("game") != game):
        return None
    lot = require_lottery(game, "RO")
    urns = []
    by_urn = {
        urn["game_key"]: urn for urn in result.get("urns") or ()
        if isinstance(urn, dict) and isinstance(urn.get("game_key"), str)
    }
    for key in lot.bench_keys:
        urn = by_urn.get(key, {})
        decision = urn.get("decision")
        if urn.get("error") or not isinstance(decision, dict) or not _valid_members(decision.get("members")):
            return None
        if (type(decision.get("evaluated")) is not int or decision["evaluated"] < 1
                or not decision.get("folds") or decision.get("target") != urn.get("target")):
            return None
        if settings["inverse"] and (not settings["dynamic"] or key == lot.bench_key_urna2):
            if not _valid_members(decision.get("reference_members")):
                return None
        fields = ("game_key", "name", "pool_size", "target", "eligible_count", "decision")
        if not all(field in urn for field in fields):
            return None
        urns.append({field: urn[field] for field in fields})
    captured_settings = {key: settings[key] for key in _PROFILE_SETTINGS}
    captured_settings["scheme"] = scheme
    return {
        "game": game, "source": entry["source"], "settings": captured_settings, "finished_at": finished_at,
        "result": {
            "game": game, "scheme_code": scheme, "history_hash": history_hash, "history_rows": history_rows,
            "as_of": result.get("as_of"), "urns": urns,
        },
    }


def _profile_key(entry):
    settings = entry["settings"]
    return (
        entry["source"], settings["scheme"], settings["inverse"], settings["restricted"], settings["dynamic"],
        settings["lookback"] or 100, settings["hit_target"], settings["joker_size"] if entry["game"] == "joker" else None,
    )


def _restore_profiles(raw):
    profiles = {}
    for game, entries in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(entries, list):
            continue
        current = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("game") != game:
                continue
            try:
                finished_at = entry["finished_at"]
                datetime.fromisoformat(finished_at)
                profile = _compact_profile(entry, finished_at)
            except (KeyError, ValueError, TypeError):
                continue
            if profile is not None:
                current.append(profile)
        if current:
            profiles[game] = current
    return profiles


def lr_rebench_decisions():
    """Întoarce deciziile complete păstrate pe joc și configurație, de la cele mai noi."""
    _restore_report()
    return deepcopy((_last or {}).get("decision_profiles", {}))


def _remember_profiles(finished):
    # Fișierul raportului și al deciziilor se înlocuiește prin aceeași scriere atomică.
    profiles = lr_rebench_decisions()
    for entry in finished["entries"]:
        profile = _compact_profile(entry, finished["finished_at"])
        if profile is None:
            continue
        game = profile["game"]
        key = _profile_key(profile)
        profiles[game] = [profile, *(older for older in profiles.get(game, ()) if _profile_key(older) != key)]
    return profiles


def _changed():
    global _revision
    _revision += 1


def cancel_lr_rebench() -> bool:
    """Cere oprirea rulării pe baza aleasă, fără a schimba raportul anterior."""
    if not lr_rebench_running() or _active is None or _active["cancelled"]:
        return False
    _active["cancelled"] = True
    try:
        atomic_write_json(_active["cancel_path"], {"cancelled": True})
    except OSError as exc:
        logger.warning("[Re-Bench LR] Nu se poate semnala oprirea jocului curent: %s", exc)
        _active["cancel_after_game"] = True
    _changed()
    return True


async def _run_games(jobs, report):
    global _active, _last, _notice
    cancel_path = report["cancel_path"]
    try:
        for index, (entry, history, options, auto_budget) in enumerate(jobs, start=1):
            if report["cancelled"]:
                break
            report["current"] = (index, entry["name"])
            report["comparing"] = bool(auto_budget and history is not None)
            _changed()
            outcome = {**entry}
            if history is None:
                outcome["error"] = "Istoricul jocului este indisponibil."
            else:
                try:
                    game = entry["game"]
                    if auto_budget is not None:
                        budget, objective = auto_budget
                        comparison = await run.io_bound(
                            compare_budget_ranges, budget, objective, game, {game: history},
                            inverse=options["inverse"], dynamic=options["dynamic"],
                            lookback=options["lookback"], hit_target=options["hit_target"],
                            joker_size=options["joker_size"], pool_codes={game: options["scheme"]},
                        )
                        if report["cancelled"]:
                            break
                        if comparison is None:
                            raise ValueError("Analiza bazelor nu a returnat un rezultat.")
                        options["restricted"] = comparison.get("recommended_range") is not None
                        report["comparing"] = False
                        _changed()
                    result = await run.io_bound(
                        benchmark_lr_hypotheses, game, history, cancel_path=cancel_path, **options,
                    )
                    if result is None:
                        raise ValueError("Calculul nu a returnat un rezultat.")
                    outcome["result"] = result
                except (asyncio.CancelledError, CancelledError):
                    if report["cancelled"]:
                        break
                    raise
                except (OSError, ValueError) as exc:
                    outcome["error"] = str(exc)
                except Exception:
                    logger.exception("[Re-Bench LR] Eroare la %s", entry["game"])
                    outcome["error"] = "Calculul a eșuat. Vezi jurnalul aplicației."
            if report["cancelled"]:
                break
            report["entries"].append(outcome)
            _changed()
        if report["cancelled"]:
            _notice = "Rularea Re-Bench pe baza aleasă a fost anulată."
            return
        finished = {key: report[key] for key in ("schema", "entries", "total", "started_at")}
        finished["finished_at"] = datetime.now(_BUCHAREST).isoformat(timespec="seconds")
        finished["decision_profiles"] = _remember_profiles(finished)
        _last = finished
        try:
            atomic_write_json(_RESULT_PATH, finished)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("[Re-Bench LR] Raportul local nu poate fi salvat: %s", exc)
            _notice = "Rezultatul este disponibil în această sesiune. Salvarea locală a eșuat."
    finally:
        try:
            cancel_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[Re-Bench LR] Marcajul de oprire nu poate fi șters: %s", exc)
        _active = None
        _changed()


async def run_lr_rebench(jobs) -> bool:
    """Capturează setările și sursele la lansare. Rularea continuă la închiderea filei."""
    global _task, _active, _notice
    if lr_rebench_running():
        return False
    captured = []
    for game, history, settings in jobs:
        lot = require_lottery(game, "RO")
        if isinstance(history, tuple):
            source, frame = history
            history = (source, frame.copy(deep=True)) if frame is not None else None
        else:
            source = Path(lot.csv).name if history is None else "Istoric încărcat"
            if history is not None:
                history = (source, history.copy(deep=True))
        options = dict(settings)
        auto_budget = options.pop("auto_budget", None)
        options["scheme"] = getattr(options["scheme"], "code", options["scheme"])
        entry = {"game": game, "name": lot.name, "source": source, "settings": options}
        captured.append((entry, history, options, tuple(auto_budget) if auto_budget is not None else None))
    if not captured:
        return False
    _restore_report()
    _notice = ""
    _active = {
        "schema": 1, "entries": [], "total": len(captured),
        "started_at": datetime.now(_BUCHAREST).isoformat(timespec="seconds"),
        "current": None, "cancelled": False,
        "cancel_path": _RESULT_PATH.with_name(f".lr-rebench-{uuid.uuid4().hex}.cancel"),
    }
    _task = asyncio.create_task(_run_games(captured, _active), name="lr-rebench")
    _changed()
    await asyncio.shield(_task)
    return True


def _date_text(raw):
    if not raw:
        return "N/D"
    try:
        return date.fromisoformat(str(raw)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(raw)


def _decimal(value, places):
    return f"{value:.{places}f}".replace(".", ",") if value is not None else "—"


def _urn_report(urn):
    with ui.column().classes("w-full min-w-0 gap-1"):
        ui.label(urn["name"]).classes("text-subtitle2 text-weight-bold")
        target = urn["target"]
        threshold = "Top-1" if target == 1 else f"Prag {target}+ în pool"
        ui.label(
            f"{threshold} · Pool de {urn['pool_size']} · {urn['eligible_count']} numere eligibile"
        ).classes("text-caption")
        if error := urn.get("error"):
            ui.label(error).classes("text-warning").props('role="status"')
        if method := urn.get("method"):
            selection = f"Metodă de rezervă sau avantaj nedemonstrat · {method}" if urn.get("fallback") else f"Metoda selectată · {method}"
            ui.label(selection).classes("text-body2")
        if folds := urn.get("folds"):
            ui.label("Ferestre de validare · " + ", ".join(f"{pct}%" for pct in folds)).classes("text-caption")
        if rows := urn.get("rows"):
            columns = [
                {"name": "method", "label": "Metodă", "field": "method", "align": "left"},
                {"name": "evaluated", "label": "Extrageri evaluate", "field": "evaluated"},
                {"name": "hit_rate", "label": "Rata la prag", "field": "hit_rate"},
                {"name": "avg_hits", "label": "Potriviri medii", "field": "avg_hits"},
            ]
            rates = [
                {
                    "method": row["method"], "evaluated": row["evaluated"],
                    "hit_rate": _decimal(100 * row["hit_rate"], 2) + "%",
                    "avg_hits": _decimal(row.get("avg_hits"), 3),
                }
                for row in rows
            ]
            ui.table(columns=columns, rows=rates, row_key="method", pagination=8).props(
                'dense flat wrap-cells aria-label="Metode evaluate în ' + urn["name"] + '"'
            ).classes("w-full min-w-0")
        if protocol := urn.get("protocol"):
            ui.label(protocol).classes("text-caption")


def _game_report(entry):
    settings = entry["settings"]
    mode = "Inversă" if settings["inverse"] else "Directă"
    scope = "10–40 inclusiv" if settings["restricted"] else "Baza completă"
    lookback = f"{settings['lookback']}%" if 0 < settings["lookback"] < 100 else "integral"
    with ui.card().props("flat bordered").classes("w-full min-w-0 gap-2"):
        ui.label(entry["name"]).classes("text-h6")
        ui.label(f"Cod LR {settings['scheme']} · Metodă {mode.lower()} · {scope}").classes("text-body2")
        result = entry.get("result") or {}
        ui.label(f"{entry['source']} · Ultima extragere {_date_text(result.get('as_of'))} · Istoric {lookback}").classes(
            "text-caption"
        )
        if settings["inverse"]:
            selector = "dinamic" if settings["dynamic"] else "fix"
            ui.label(f"Selector {selector} pentru pool-ul direct din urna principală").classes("text-caption")
        if entry["game"] == "joker" and settings["inverse"]:
            ui.label(f"Pool direct din Urna 2 Joker · {settings['joker_size']} bile").classes("text-caption")
        if error := entry.get("error") or result.get("error"):
            ui.label(error).classes("text-warning").props('role="status"')
        for urn in result.get("urns", ()):
            _urn_report(urn)


def lr_rebench_panel():
    """Redă progresul și ultimul raport, independent de rezultatele Re-Bench obișnuite."""
    _restore_report()
    panel = ui.column().classes("w-full min-w-0 gap-2").props('tabindex="-1"')
    seen = -1

    def refresh():
        nonlocal seen
        if panel.is_deleted or seen == _revision:
            return
        seen = _revision
        panel.clear()
        report = _active or _last
        with panel:
            if report:
                ui.label("Rezultate Re-Bench pe baza aleasă").classes("text-h6 text-weight-bold")
                if _active and report["cancelled"]:
                    if report.get("comparing"):
                        status = "Rularea se va opri după analiza bazei jocului curent."
                    else:
                        status = (
                            "Rularea se va opri după jocul curent."
                            if report.get("cancel_after_game") else "Se oprește rularea."
                        )
                elif _active and (current := report["current"]):
                    operation = "Se compară bazele pentru" if report.get("comparing") else "Se evaluează"
                    status = f"{operation} {current[1]} · jocul {current[0]} din {report['total']}."
                elif _active:
                    status = "Se pregătește rularea."
                else:
                    finished = datetime.fromisoformat(report["finished_at"]).strftime("%d.%m.%Y, %H:%M")
                    status = f"Rulare încheiată la {finished} · Jocuri procesate {len(report['entries'])} din {report['total']}."
                ui.label(status).classes("text-body2").props('role="status" aria-live="polite"')
                if _active and not report["cancelled"]:
                    ui.button("Anulează Re-Bench pe baza aleasă", on_click=cancel_lr_rebench).props(
                        "outline no-caps color=warning"
                    )
                for entry in report["entries"]:
                    _game_report(entry)
            if _notice:
                ui.label(_notice).classes("text-warning").props('role="status"')
    refresh()
    ui.timer(0.75, refresh)
    return panel
