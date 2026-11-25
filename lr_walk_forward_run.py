"""Ciclul validării walk-forward LR și ultimul raport local."""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import CancelledError
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from nicegui import run

from loto_enterprise.core.lotteries import require_lottery
from runtime_paths import RUNTIME_ROOT
from ui_shared import atomic_write_json


logger = logging.getLogger(__name__)
_RESULT_PATH = RUNTIME_ROOT / ".nicegui" / "lr_walk_forward_latest.json"
_BUCHAREST = ZoneInfo("Europe/Bucharest")


@dataclass
class _RunState:
    active: dict | None = None
    latest: dict | None = None
    task: asyncio.Task | None = None
    cancel_path: Path | None = None
    notice: str = ""
    revision: int = 0
    loaded: bool = False


_state = _RunState()


def _restore():
    if _state.loaded:
        return
    _state.loaded = True
    try:
        report = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
        if (
            not isinstance(report, dict) or report.get("schema") != 1
            or not isinstance(report.get("entries"), list) or type(report.get("total")) is not int
        ):
            raise ValueError("format de raport necunoscut")
        datetime.fromisoformat(report["finished_at"])
        _state.latest = report
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("[WF LR] Raportul local nu poate fi citit: %s", exc)
        _state.notice = "Ultimul raport walk-forward LR nu poate fi citit."


def lr_walk_forward_snapshot(since=None, *, include_active_entries=True, include_previous=True):
    """Întoarce înregistrări detașate când revizia s-a schimbat.

    Panoul poate omite rezultatele intermediare și raportul deja afișat
    cât timp rulează o validare. La final primește întotdeauna ultimul raport.
    """
    _restore()
    if since == _state.revision:
        return None
    active, latest = _state.active, _state.latest
    if active is not None:
        if not include_active_entries:
            active = {key: value for key, value in active.items() if key != "entries"}
        if not include_previous:
            latest = None
    return _state.revision, deepcopy(active), deepcopy(latest), _state.notice


def lr_walk_forward_running() -> bool:
    return _state.task is not None and not _state.task.done()


def cancel_lr_walk_forward() -> bool:
    report = _state.active
    if not lr_walk_forward_running() or report is None or report["cancelled"]:
        return False
    report["cancelled"] = True
    try:
        atomic_write_json(_state.cancel_path, {"cancelled": True})
    except OSError as exc:
        report["cancel_after_game"] = True
        logger.warning("[WF LR] Nu se poate semnala oprirea jocului curent: %s", exc)
    _state.revision += 1
    return True


def _progress(report, index, done, total):
    if _state.active is not report or report["cancelled"] or report["current"][0] != index:
        return
    update = {"done": done, "total": total}
    if report["progress"] != update:
        report["progress"] = update
        _state.revision += 1


async def _run_games(jobs, report, cancel_path):
    loop = asyncio.get_running_loop()
    try:
        for index, (entry, history) in enumerate(jobs, start=1):
            if report["cancelled"]:
                break
            report["current"] = (index, entry["name"])
            report["current_settings"] = entry["settings"]
            report["progress"] = None
            _state.revision += 1
            outcome = dict(entry)
            if history is None:
                outcome["error"] = "Istoricul jocului este indisponibil."
            else:
                def progress_cb(done, total, item=index):
                    with suppress(RuntimeError):
                        loop.call_soon_threadsafe(_progress, report, item, done, total)

                try:
                    from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward

                    result = await run.io_bound(
                        validate_lr_walk_forward, entry["game"], history, **entry["settings"],
                        cancel_path=cancel_path, progress_cb=progress_cb,
                    )
                    if not isinstance(result, dict):
                        raise ValueError("Validarea nu a returnat un rezultat.")
                    outcome["result"] = result
                except (CancelledError, asyncio.CancelledError):
                    if report["cancelled"]:
                        break
                    raise
                except (OSError, ValueError) as exc:
                    outcome["error"] = str(exc)
                except Exception:
                    logger.exception("[WF LR] Eroare la %s", entry["game"])
                    outcome["error"] = "Validarea a eșuat. Vezi jurnalul aplicației."
            if report["cancelled"]:
                break
            report["entries"].append(outcome)
            _state.revision += 1
        if report["cancelled"]:
            _state.notice = "Validarea walk-forward LR a fost anulată."
            return
        finished = {key: report[key] for key in ("schema", "entries", "total", "started_at")}
        finished["finished_at"] = datetime.now(_BUCHAREST).isoformat(timespec="seconds")
        _state.latest = finished
        try:
            atomic_write_json(_RESULT_PATH, finished)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("[WF LR] Raportul local nu poate fi salvat: %s", exc)
            _state.notice = "Raportul este disponibil în această sesiune. Salvarea locală a eșuat."
    finally:
        try:
            cancel_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[WF LR] Marcajul de oprire nu poate fi șters: %s", exc)
        _state.cancel_path = None
        _state.active = None
        _state.revision += 1


async def run_lr_walk_forward(jobs) -> bool:
    """Pornește o singură validare pe instantaneele sesiunii, independent de fila activă."""
    if lr_walk_forward_running():
        return False
    captured = []
    for game, history, options in jobs:
        lot = require_lottery(game, "RO")
        if isinstance(history, tuple):
            source, frame = history
            history = (source, frame.copy(deep=True)) if frame is not None else None
        elif history is None:
            source = Path(lot.csv).name
        else:
            source = "Istoric încărcat"
            history = (source, history.copy(deep=True))
        settings = deepcopy(options)
        settings["scheme"] = getattr(settings["scheme"], "code", settings["scheme"])
        captured.append(({"game": game, "name": lot.name, "source": source, "settings": settings}, history))
    if not captured:
        return False
    _restore()
    _state.notice = ""
    _state.active = {
        "schema": 1, "total": len(captured), "entries": [], "settings": [entry for entry, _ in captured],
        "started_at": datetime.now(_BUCHAREST).isoformat(timespec="seconds"),
        "current": None, "current_settings": None, "progress": None, "cancelled": False,
    }
    _state.cancel_path = _RESULT_PATH.with_name(f".lr-walk-forward-{uuid4().hex}.cancel")
    _state.task = asyncio.create_task(_run_games(captured, _state.active, _state.cancel_path), name="lr-walk-forward")
    _state.revision += 1
    await asyncio.shield(_state.task)
    return True
