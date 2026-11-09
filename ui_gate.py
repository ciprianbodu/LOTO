"""ui_gate.py — Panoul porții preînregistrate weibull_k0.85 (6/49, pool 16, garanție 6).

Afișează în UI starea porții fixate la 2026-11-09 și evaluează idempotent
extragerile noi. Protocolul (metodă, pool, prag, orizont) NU se schimbă aici —
el este înghețat prin commit în `scripts/analysis/preregister_weibull_k085_gate_2026_11_09.py`
și documentat în `scripts/analysis/preregister_weibull_k085_gate_2026-11-09.md`.

Starea trăiește în `RUNTIME_ROOT/gate_weibull_k085_2026_11_09_state.json`
(fișier local al stației, nu în Git). Panoul citește doar; evaluarea
(`evaluate_new_draws`) se face în thread prin `run.io_bound`, ca să nu blocheze
event-loop-ul UI.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import threading
from pathlib import Path

from nicegui import ui

import runtime_paths

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STATE_NAME = "gate_weibull_k085_2026_11_09_state.json"
PROTOCOL_DOC = "scripts/analysis/preregister_weibull_k085_gate_2026-11-09.md"

# Fallback-uri locale (coincid cu protocolul comis); sursa de adevăr rămâne
# fișierul de stare, care poartă protocolul complet.
_P0_5 = 0.010880577948108013
_P0_4 = 0.07960002
_MU0 = 1.9592

_EVAL_LOCK = threading.Lock()


def _state_path() -> Path:
    return runtime_paths.RUNTIME_ROOT / STATE_NAME


def load_gate_state() -> dict | None:
    """Starea porții sau None (lipsă / coruptă — afișată explicit, nu prinsă)."""
    path = _state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GATE] Starea porții nu poate fi citită (%s): %s", path, exc)
        return None


def _binom_sf_ge(k: int, n: int, p: float) -> float:
    """P(X >= k) pentru X ~ Binomial(n, p), calcul exact prin recurență.

    Aceeași formulă ca în runnerul widgetului; scipy nu e necesar aici.
    """
    if k <= 0:
        return 1.0
    if n <= 0:
        return 0.0
    pmf = (1.0 - p) ** n
    total = pmf
    for i in range(1, k):
        pmf *= (n - i + 1) / i * (p / (1.0 - p))
        total += pmf
    return max(0.0, 1.0 - total)


def evaluate_new_draws() -> bool:
    """Rulează update-ul idempotent al porții; True dacă s-au adăugat extrageri.

    Apelabil din thread. Protocolul comis decide singur ce zile sunt eligibile
    (strict după 2026-11-09, neevaluate anterior); fără extrageri noi nu
    calculează nimic.
    """
    if not _EVAL_LOCK.acquire(blocking=False):
        return False
    try:
        before = (load_gate_state() or {}).get("cum", {}).get("n", 0)
        try:
            from scripts.analysis import (
                preregister_weibull_k085_gate_2026_11_09 as _gate,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GATE] Protocolul nu se importă: %s", exc)
            return False
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                _gate.update()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GATE] Evaluarea a eșuat: %s", exc)
            return False
        for line in buf.getvalue().splitlines():
            logger.info("[GATE] %s", line)
        after = (load_gate_state() or {}).get("cum", {}).get("n", 0)
        return int(after) > int(before)
    finally:
        _EVAL_LOCK.release()


def _ro_int(x: float | int) -> str:
    return f"{int(x):,}".replace(",", ".")


def _verdict(n: int, ge5: int, crit: int, horizon: int) -> tuple[str, str]:
    if ge5 >= crit:
        return "✅ SUCCES — poarta deschisă", "text-positive"
    if n >= horizon:
        return "❌ ÎNCHISĂ — semnal neconfirmat pe orizontul preînregistrat", "text-negative"
    return "⏳ în desfășurare", "text-info"


@ui.refreshable
def gate_panel() -> None:
    state = load_gate_state()
    with ui.expansion(
        "🎯 Poarta preînregistrată — weibull_k0.85 · 6/49 · pool 16 · garanție 6",
        value=False,
    ).classes("w-full"):
        if state is None:
            ui.label(
                f"Starea porții lipsește sau nu poate fi citită ({_state_path()}). "
                f"Protocolul: {PROTOCOL_DOC}."
            ).classes("text-caption text-warning")
            return

        proto = state.get("protocol") or {}
        cum = state.get("cum") or {}
        n = int(cum.get("n", 0))
        horizon = int(proto.get("horizon_draws", 104))
        crit = int(proto.get("crit_5plus", 5))
        ge4 = int(cum.get("ge4", 0))
        ge5 = int(cum.get("ge5", 0))
        ge6 = int(cum.get("ge6", 0))
        p05 = float((proto.get("p0") or {}).get("5plus", _P0_5))
        p04 = float((proto.get("p0") or {}).get("4plus", _P0_4))
        cost = int(cum.get("cost_lei", 0))
        mean_hits = (cum.get("hits_sum", 0) / n) if n else 0.0
        p_binom = _binom_sf_ge(ge5, n, p05)
        verdict, vcls = _verdict(n, ge5, crit, horizon)

        ui.label(
            "Protocol înghețat la 2026-11-09 (comis în Git): pool top-16 din "
            "hazardul Weibull κ=0,85, cauzal pe zi; numără strict extragerile "
            "după înregistrare. Garanția 6 = sistem complet C(16,6)=8008 variante: "
            "6 în pool ⇒ exact o variantă cu 6."
        ).classes("text-caption text-grey")

        row = ui.row().classes("items-center gap-4 w-full")
        with row:
            ui.label(verdict).classes(f"text-bold {vcls}")
            ui.label(
                f"{n}/{horizon} extrageri evaluate"
            ).classes("text-caption")
        ui.linear_progress(value=min(1.0, n / max(1, horizon)), show_value=False)
        ui.label(
            f"5+ în pool: {ge5} (prag poartă: ≥{crit}; așteptat la hazard: {n * p05:.2f})"
        ).classes("text-caption")
        ui.linear_progress(value=min(1.0, ge5 / max(1, crit)), show_value=False).props(
            "color=positive"
        )

        stats = ui.row().classes("gap-6 w-full")
        with stats:
            ui.label(
                f"4+ în pool: {ge4} (așteptat {n * p04:.1f})"
            ).classes("text-caption")
            ui.label(f"6/6 în pool: {ge6}").classes("text-caption")
            ui.label(
                f"Medie hituri/extragere: {mean_hits:.3f} (referință {_MU0:.3f})"
            ).classes("text-caption")
            ui.label(
                "Variante cu 6/5/4/3: "
                f"{_ro_int(cum.get('tix6', 0))}/{_ro_int(cum.get('tix5', 0))}/"
                f"{_ro_int(cum.get('tix4', 0))}/{_ro_int(cum.get('tix3', 0))}"
            ).classes("text-caption")
            ui.label(f"Cost cumulat: {_ro_int(cost)} lei").classes("text-caption")
            ui.label(f"p binomial cumulat (5+): {p_binom:.5f}").classes("text-caption")

        evaluated = state.get("evaluated") or []
        if evaluated:
            rows = [
                {
                    "date": e["date"],
                    "pool": ", ".join(str(x) for x in e["pool"]),
                    "drawn": ", ".join(str(x) for x in e["drawn"]),
                    "hits": e["hits"],
                    "tix": "/".join(
                        str(e["tickets"].get(str(j), 0)) for j in (6, 5, 4, 3)
                    ),
                }
                for e in reversed(evaluated[-20:])
            ]
            ui.label("Ultimele extrageri evaluate (maxim 20):").classes(
                "text-caption text-grey"
            )
            ui.table(
                columns=[
                    {"name": "date", "label": "Data", "field": "date", "align": "left"},
                    {"name": "pool", "label": "Pool (16)", "field": "pool", "align": "left"},
                    {"name": "drawn", "label": "Extrase", "field": "drawn", "align": "left"},
                    {"name": "hits", "label": "Hits", "field": "hits", "align": "center"},
                    {"name": "tix", "label": "Variante 6/5/4/3", "field": "tix", "align": "left"},
                ],
                rows=rows,
            ).classes("w-full").props("dense flat")
        else:
            ui.label(
                "Nicio extragere evaluată încă — prima eligibilă: joi 12.11.2026."
            ).classes("text-caption")

        async def _reeval() -> None:
            from nicegui import run as _nrun

            _btn.disable()
            try:
                changed = await _nrun.io_bound(evaluate_new_draws)
            finally:
                _btn.enable()
            gate_panel.refresh()
            ui.notify(
                "Poarta a evaluat extrageri noi." if changed
                else "Nicio extragere nouă de evaluat.",
                type="positive" if changed else "info",
            )

        _btn = ui.button("↻ Re-evaluează poarta", on_click=_reeval).props(
            "outline no-caps dense"
        )
