"""Lansarea și afișarea analizei Re-Bench pe baza aleasă pentru biletele LR."""
from __future__ import annotations

import asyncio
import hashlib
import threading
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

from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN
from covering.lr_refinement_status import merge_refinements, refinement_complete
from covering.lr_budget import recommend_games
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark.lr_hypotheses import benchmark_lr_hypotheses
from loto_enterprise.benchmark.lr_provenance import lr_semantics
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_range_analysis import compare_budget_ranges
from process_lock import file_lock
from runtime_paths import RUNTIME_ROOT
from ui_shared import atomic_write_json


logger = logging.getLogger(__name__)
_RESULT_PATH = RUNTIME_ROOT / ".nicegui" / "lr_rebench_latest.json"
_BUCHAREST = ZoneInfo("Europe/Bucharest")
_task = None
_active = None
_last = None
_loaded = False
_disk_stamp = None
_storage_lock = threading.RLock()
_notice = ""
_revision = 0
_PROFILE_SETTINGS = ("scheme", "inverse", "restricted", "dynamic", "lookback", "hit_target", "joker_size")


def lr_rebench_running() -> bool:
    """True pentru toate filele care folosesc această instanță a aplicației."""
    return _task is not None and not _task.done()


def _file_stamp():
    stat = _RESULT_PATH.stat()
    return _RESULT_PATH, stat.st_mtime_ns, stat.st_size, stat.st_ino


def _read_report():
    saved = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
    if (not isinstance(saved, dict) or saved.get("schema") != 1
            or not isinstance(saved.get("entries"), list) or type(saved.get("total")) is not int):
        raise ValueError("format de raport necunoscut")
    datetime.fromisoformat(saved.get("finished_at", ""))
    return saved


def _restore_report():
    global _loaded, _last, _notice, _disk_stamp
    with _storage_lock:
        try:
            stamp = _file_stamp()
            if _loaded and stamp == _disk_stamp:
                return
            saved = _read_report()
            saved["decision_history"] = _prior_history(saved)
            saved["decision_profiles"] = _restore_profiles(saved.get("decision_profiles"))
            _last, _loaded, _disk_stamp = saved, True, stamp
            _notice = ""
            _changed()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            message = "Raportul anterior de Re-Bench pe baza aleasă nu poate fi citit."
            if _notice != message:
                logger.warning("[Re-Bench LR] Raportul local nu poate fi citit: %s", exc)
                _notice = message
                _changed()


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
    if not refinement_complete(result):
        return None
    if result.get("max_consecutive_run") != LR_MAX_CONSECUTIVE_RUN:
        return None
    if not isinstance(result.get("semantics"), dict) or result["semantics"].get("version") != 1:
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
        if settings["inverse"]:
            from loto_enterprise.benchmark.lr_budget_scoring import LIVE_BUDGET_REFERENCE
            if (decision.get("reference_policy") != LIVE_BUDGET_REFERENCE
                    and (not settings["dynamic"] or key == lot.bench_key_urna2)
                    and not _valid_members(decision.get("reference_members"))):
                return None
        fields = ("game_key", "name", "pool_size", "target", "eligible_count", "decision")
        if not all(field in urn for field in fields):
            return None
        urns.append({field: urn[field] for field in fields})
    captured_settings = {key: settings[key] for key in _PROFILE_SETTINGS}
    captured_settings.update({key: settings[key] for key in ("budget_lei", "objective") if key in settings})
    captured_settings["scheme"] = scheme
    return {
        "game": game, "source": entry["source"], "settings": captured_settings, "finished_at": finished_at,
        "result": {
            "game": game, "scheme_code": scheme, "history_hash": history_hash, "history_rows": history_rows,
            "semantics": result["semantics"],
            **({"portfolio_refinement": result["portfolio_refinement"]} if "portfolio_refinement" in result else {}),
            "as_of": result.get("as_of"), "max_consecutive_run": LR_MAX_CONSECUTIVE_RUN, "urns": urns,
        },
    }


def _profile_key(entry):
    settings = entry["settings"]
    return (
        entry["source"], settings["scheme"], settings["inverse"], settings["restricted"], settings["dynamic"],
        settings["lookback"] or 100, settings["hit_target"], settings["joker_size"] if entry["game"] == "joker" else None,
        settings.get("budget_lei"), settings.get("objective"),
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


def _decision_summary(entry, finished_at):
    if (not isinstance(entry, dict) or not isinstance(entry.get("settings"), dict)
            or not isinstance(entry.get("game"), str) or not isinstance(entry.get("result"), dict)):
        return None
    result = entry.get("result") or {}
    if not refinement_complete(result):
        return None
    urns = []
    for urn in result.get("urns", ()):
        if not isinstance(urn, dict):
            continue
        decision = urn.get("decision") or {}
        if not isinstance(decision, dict):
            continue
        members = decision.get("members") or []
        if members and not _valid_members(members):
            continue
        if not members and not urn.get("method"):
            continue
        urns.append({
            "urn": urn.get("name"), "method": urn.get("method") or " + ".join(m["method"] for m in members),
            "members": members, "reference_members": decision.get("reference_members") or [],
            "evaluated": decision.get("evaluated"), "folds": decision.get("folds") or urn.get("folds"),
            "target": decision.get("target", urn.get("target")), "fallback": decision.get("fallback", urn.get("fallback")),
        })
    if not urns:
        return None
    semantics = result.get("semantics")
    signature = hashlib.sha256(json.dumps(semantics, sort_keys=True).encode()).hexdigest() if semantics else None
    return {"game": entry["game"], "source": entry.get("source"), "settings": entry["settings"],
            "finished_at": finished_at, "as_of": result.get("as_of"), "history_rows": result.get("history_rows"),
            "history_hash": result.get("history_hash"), "signature": signature,
            "max_consecutive_run": result.get("max_consecutive_run"), "urns": urns}


def _history_key(summary):
    # An unsigned legacy report may lack a fingerprint. Keep distinguishable legacy winners.
    data = summary.get("history_hash") or (summary.get("as_of"), summary.get("history_rows"),
                                            [u.get("method") for u in summary["urns"]])
    return json.dumps((summary["game"], summary.get("source"), summary["settings"], data,
                       summary.get("signature"), summary.get("max_consecutive_run")), sort_keys=True)


def _known_decisions(report):
    summaries = [s for s in report.get("decision_history") or () if isinstance(s, dict)
                 and isinstance(s.get("settings"), dict) and s.get("game") and s.get("urns") and s.get("finished_at")]
    profiles = report.get("decision_profiles")
    if isinstance(profiles, dict):
        for records in profiles.values():
            for record in records if isinstance(records, list) else ():
                if isinstance(record, dict) and record.get("finished_at"):
                    if item := _decision_summary(record, record["finished_at"]):
                        summaries.append(item)
    for entry in report.get("entries", ()):
        if item := _decision_summary(entry, report["finished_at"]):
            summaries.append(item)
    return summaries


def _finished_time(entry):
    stamp = datetime.fromisoformat(entry["finished_at"])
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=_BUCHAREST)).timestamp()


def _prior_history(latest, *earlier):
    current = {_history_key(s) for entry in latest.get("entries", ())
               if (s := _decision_summary(entry, latest["finished_at"]))}
    by_identity = {}
    for report in (*earlier, latest):
        for summary in _known_decisions(report):
            key = _history_key(summary)
            if key not in current and (key not in by_identity or _finished_time(summary) >= _finished_time(by_identity[key])):
                by_identity[key] = summary
    return sorted(by_identity.values(), key=_finished_time, reverse=True)


def _remember_profiles(finished, previous):
    profiles = _restore_profiles(previous.get("decision_profiles"))
    for entry in finished["entries"]:
        profile = _compact_profile(entry, finished["finished_at"])
        if profile is None:
            continue
        game = profile["game"]
        candidates = [profile, *profiles.get(game, ())]
        candidates.sort(key=_finished_time, reverse=True)
        unique = {}
        for candidate in candidates:
            unique.setdefault(_profile_key(candidate), candidate)
        profiles[game] = list(unique.values())
    return profiles


def _persist_report(finished):
    global _last, _loaded, _disk_stamp
    _RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _storage_lock, file_lock(_RESULT_PATH):
        try:
            previous = _read_report()
        except FileNotFoundError:
            previous = {}
        latest = previous if previous and _finished_time(previous) > _finished_time(finished) else finished
        merged = {**latest, "decision_profiles": _remember_profiles(finished, previous)}
        merged["decision_history"] = _prior_history(merged, previous, finished)
        atomic_write_json(_RESULT_PATH, merged)
        _last, _loaded, _disk_stamp = merged, True, _file_stamp()


def _changed():
    global _revision
    _revision += 1


def cancel_lr_rebench() -> bool:
    """Cere oprirea rulării pe baza aleasă, fără a schimba raportul anterior."""
    if not lr_rebench_running() or _active is None or _active["cancelled"]:
        return False
    _active["cancelled"] = True
    report = _active
    def signal():
        try:
            atomic_write_json(report["cancel_path"], {"cancelled": True})
        except OSError as exc:
            logger.warning("[Re-Bench LR] Nu se poate semnala oprirea jocului curent: %s", exc)
            report["cancel_after_game"] = True
    report["cancel_write"] = asyncio.get_running_loop().run_in_executor(None, signal)
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
                            joker_size=options["joker_size"], pool_codes={game: options["scheme"]}, return_recipe=True,
                        )
                        if report["cancelled"]:
                            break
                        if comparison is None:
                            raise ValueError("Analiza bazelor nu a returnat un rezultat.")
                        options["restricted"] = comparison.get("recommended_range") is not None
                        if "paid_recipe" in comparison:
                            options = {**options, "recipe": comparison["paid_recipe"]}
                        report["comparing"] = False
                        _changed()
                    result = await run.io_bound(
                        benchmark_lr_hypotheses, game, history, cancel_path=cancel_path, **options,
                    )
                    if result is None:
                        raise ValueError("Calculul nu a returnat un rezultat.")
                    if auto_budget is not None:
                        result["portfolio_refinement"] = merge_refinements(comparison, result)
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
        finished["finished_at"] = datetime.now(_BUCHAREST).isoformat(timespec="microseconds")
        try:
            await asyncio.to_thread(_persist_report, finished)
        except (OSError, ValueError, TypeError) as exc:
            previous = _last or {}
            _last = {**finished, "decision_profiles": _remember_profiles(finished, previous),
                     "decision_history": _prior_history(finished, previous)}
            logger.warning("[Re-Bench LR] Raportul local nu poate fi salvat: %s", exc)
            _notice = "Rezultatul este disponibil în această sesiune. Salvarea locală a eșuat."
    finally:
        try:
            if cancel_write := report.get("cancel_write"):
                await cancel_write
            await asyncio.to_thread(cancel_path.unlink, missing_ok=True)
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


def _report_compatibility(entry, source_for):
    from loto_enterprise.core.lr_budget_forecast import _forecast_engine
    result, settings = entry["result"], entry["settings"]
    lot = require_lottery(entry["game"], "RO")
    layout = None
    if settings.get("budget_lei") is not None:
        layout, = recommend_games(settings["budget_lei"], settings["objective"], lot.game_id)
    signature = lr_semantics(lot, resolve_lr_scheme(lot.geometry, settings["scheme"]), settings["hit_target"], layout=layout)
    if result.get("semantics") != signature:
        return "Raport istoric. Regulile sau schema diferă. Rulează din nou Re-Bench pentru a folosi decizia."
    if source_for is not None:
        current = _forecast_engine(lot, source_for(lot.game_id), 0)
        if result.get("history_hash") != current.audit["hash"]:
            return "Raport istoric. Extragerile disponibile s-au schimbat. Rulează din nou Re-Bench pentru istoricul actual."
    return ""


def _game_report(entry, source_for=None):
    settings = entry["settings"]
    mode = "Inversă" if settings["inverse"] else "Directă"
    scope = "10–40 inclusiv" if settings["restricted"] else "Baza completă"
    lookback = f"{settings['lookback']}%" if 0 < settings["lookback"] < 100 else "integral"
    with ui.card().props("flat bordered").classes("w-full min-w-0 gap-2"):
        ui.label(entry["name"]).classes("text-h6")
        ui.label(f"Cod LR {settings['scheme']} · Metodă {mode.lower()} · {scope}").classes("text-body2")
        result = entry.get("result") or {}
        if result:
            notice = ui.label().classes("text-warning")
            if result.get("semantics"):
                async def verify():
                    try:
                        message = await run.io_bound(_report_compatibility, entry, source_for)
                    except (KeyError, OSError, TypeError, ValueError):
                        message = "Raport istoric. Compatibilitatea cu selecția actuală nu a putut fi verificată."
                    if not notice.is_deleted:
                        notice.set_text(message or "")
                        notice.set_visibility(bool(message))
                notice.set_visibility(False)
                ui.timer(.05, verify, once=True)
            else:
                notice.set_text("Raport istoric. Regulile nu au fost înregistrate. Rulează din nou Re-Bench pentru a folosi decizia.")
        if not refinement_complete(result):
            ui.label("Re-Bench provizoriu. Rafinarea biletelor a fost incompletă. Reia calculul pentru a folosi decizia.").classes("text-warning")
        if result and result.get("max_consecutive_run") != LR_MAX_CONSECUTIVE_RUN:
            ui.label("Raport anterior regulii de maximum două numere consecutive. "
                     "Rulează din nou Re-Bench pentru selecția actuală.").classes("text-warning")
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
        if selection := result.get("portfolio"):
            if selection.get("error"):
                ui.label(selection["error"]).classes("text-warning")
            else:
                choice = " / ".join(n for n in selection["ticket_choice"] if n)
                ui.label(f"Selecție pe biletele LR jucate · {choice} · "
                         f"{selection['draws']} extrageri în {selection['days']} zile").classes("text-weight-bold")
                winning = next((c for c in selection["candidates"]
                                if [c["main"], c["joker"] or ""] == selection["ticket_choice"]), {})
                successes = winning.get(selection["objective"], 0)
                ui.label(f"Obiectiv atins la {successes} extrageri din {winning.get('n_eval', 0)} evaluate.").classes("text-caption")
        for urn in result.get("urns", ()):
            _urn_report(urn)


def _historical_decision(summary):
    settings = summary["settings"]
    method = "Metoda inversă" if settings.get("inverse") else "Metoda normală"
    scope = "10–40" if settings.get("restricted") else "Baza completă"
    finished = datetime.fromisoformat(summary["finished_at"]).strftime("%d.%m.%Y, %H:%M")
    with ui.expansion(f"{finished} · {summary['game']} · Cod LR {settings.get('scheme')} · {method} · {scope}").classes("w-full"):
        ui.label(f"{summary['source']} · Ultima extragere {_date_text(summary.get('as_of'))}").classes("text-caption")
        if not summary.get("signature"):
            ui.label("Decizie istorică fără semnătura regulilor.").classes("text-warning")
        if settings.get("budget_lei") is not None:
            goal = "Orice premiu" if settings.get("objective") == "any_prize" else "4+ pe o variantă"
            ui.label(f"{settings['budget_lei']} lei · {goal}").classes("text-caption")
        for urn in summary["urns"]:
            details = ", ".join(f"{m['method']} ({100 * m['weight']:.1f}%)" for m in urn.get("members") or ())
            sample = f" · {urn['evaluated']} extrageri evaluate" if urn.get("evaluated") else ""
            ui.label(f"{urn.get('urn') or ''} · {details or urn.get('method')}{sample}").classes("text-body2")


def lr_rebench_panel(source_for=None):
    """Redă progresul și ultimul raport, independent de rezultatele Re-Bench obișnuite."""
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
                    _game_report(entry, source_for)
                if history := (_last or {}).get("decision_history"):
                    with ui.expansion("Decizii din rulări anterioare").classes("w-full"):
                        for summary in history:
                            _historical_decision(summary)
            if _notice:
                ui.label(_notice).classes("text-warning").props('role="status"')
    refresh()
    async def check_disk():
        await asyncio.to_thread(_restore_report)
        refresh()
    ui.timer(0.75, check_disk)
    return panel
