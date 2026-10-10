"""Walk-forward hit history and analysis menu."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path

import pandas as pd
from nicegui import ui

from ui_runtime import *
from loto_enterprise.core import lotteries as _LOT
from loto_enterprise.core.ro_text import count
from ui_results import PRICES, _hypergeo_params, _random_rate_hypergeo, _bench_transform_note
from ui_bench import _render_bench_leaderboard, _render_last_csv_draw
from ui_shared import PROJECT_ROOT, render_html_safe

logger = logging.getLogger("app_nicegui")

def _parse_draw_date(s):
    """Parsează data unei extrageri → date. Acceptă dd-mm-yyyy sau yyyy-mm-dd.
    None dacă nu se poate (ex. eticheta '#index')."""
    raw = str(s).strip()
    # taie partea de oră dacă există (ex. '2025-04-27 00:00:00')
    raw = raw.split(" ")[0].split("T")[0]
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return _dt.strptime(raw, fmt).date()
        except Exception:  # noqa: BLE001
            continue
    return None


def _hit_gap_rows(items: list[tuple], today=None) -> list[dict]:
    """Construiește gap-uri pe secvența de hituri (draw_index), nu pe date unice.

    `items` = [(draw_index, d), ...] deja filtrate, orice ordine.
    Pentru fiecare hit (afișat newest-first):
      - cel mai recent: zile de la acel hit **până AZI** (cât a trecut de atunci)
      - restul: zile de la acel hit **până la hit-ul următor mai recent**
        (intervalul real dintre două hituri consecutive în WF)

    Asta elimină confuzia veche: Δ pe primul rând era „de la hit-ul anterior”,
    deci pe 11-01-2026 apărea „31 zile” (= Dec→Ian), deși de atunci trecuseră ~jumătate de an.
    """
    today = today or _dt.now().date()
    # Cronologic: vechi → nou (după draw_index = ordinea WF)
    chrono = sorted(items, key=lambda kv: kv[0])
    # gap_after[di] = zile de la acest hit până la următorul mai recent (sau azi)
    gap_after: dict[int, int | None] = {}
    for i, (di, d) in enumerate(chrono):
        d_here = _parse_draw_date(d.get("label"))
        if d_here is None:
            gap_after[di] = None
            continue
        if i + 1 < len(chrono):
            d_next = _parse_draw_date(chrono[i + 1][1].get("label"))
            gap_after[di] = (d_next - d_here).days if d_next is not None else None
        else:
            # cel mai recent hit din listă → până azi
            gap_after[di] = (today - d_here).days
    # Afișare: newest first
    rows_out = []
    newest_di = chrono[-1][0] if chrono else None
    for di, d in sorted(items, key=lambda kv: kv[0], reverse=True):
        g = gap_after.get(di)
        if g is None:
            gap_txt = "—"
        elif di == newest_di:
            gap_txt = f"acum {g} zile" if g != 1 else "acum 1 zi"
            if g == 0:
                gap_txt = "azi"
        else:
            gap_txt = f"{g} zile" if g != 1 else "1 zi"
        rows_out.append((di, d, gap_txt))
    return rows_out


def _bench_target() -> int:
    """Pragul de hituri pe care optimizează bench-ul (doar 3 sau 4)."""
    return _clamped_bench_target()


def _curation_banner_info():
    """Starea curării de metode (curated_methods.json) pentru banner-ul de Re-Bench.

    Întoarce None dacă nu e nicio curare activă (fișier absent/gol → bench-ul
    rulează toate metodele available). Curarea e complet REVERSIBILĂ — vezi
    AGENTS.md.
    """
    try:
        from loto_enterprise.benchmark.curated import apply_curation, curated_path
        from loto_enterprise.benchmark.methods import list_methods, method_meta

        avail = [
            m
            for m in list_methods()
            if method_meta(m).get("available", True)
        ]
        _kept, info = apply_curation(avail)
        if not info.get("active"):
            return None
        info["path"] = curated_path().name
        return info
    except Exception:  # noqa: BLE001
        return None


def _target_data_ready() -> bool:
    """True dacă folds.csv conține deja rata pentru pragul curent (≥BENCH_HIT_TARGET).
    Dacă NU (după bump de schemă cache v2→v3 sau schimbare de prag), următorul
    Re-Bench e un recalcul COMPLET (lent), nu rapid din cache — deci banner-ul nu
    trebuie să mintă cu „cache rapid"."""
    try:
        _T = _bench_target()
        f = PROJECT_ROOT / "bench_results" / "folds.csv"
        if not f.exists():
            return False
        cols = [
            c for c in pd.read_csv(f, nrows=0).columns if c.startswith(f"rate_{_T}plus")
        ]
        if not cols:
            return False
        df = pd.read_csv(f, usecols=cols)
        if df.empty:
            return False
        # fracția rândurilor cu măcar o valoare reală pentru prag (folduri calculate în schema curentă)
        return float(df.notna().any(axis=1).mean()) >= 0.9
    except Exception:  # noqa: BLE001
        # Fișier corupt/blocat/înlocuit în timpul citirii nu dovedește că schema
        # țintei există. True afișa fals bannerul verde „cache rapid”.
        return False


def _decision_target_mismatch(country=None) -> dict[str, int]:
    """{joc: ținta deciziei} unde decizia salvată nu e pe ținta selectată.

    `_target_data_ready` vede numai coloanele din folds.csv; decizia scrisă de
    un bench din consolă, de o schimbare de țintă refuzată sau de un bench
    terminat după repornirea UI-ului putea rămâne pe 3+ cu 4+ selectat, iar
    panoul spunea „Benchmark la zi”. Fișier lipsă sau ilizibil: {} (prospețimea
    are mesajele ei pentru asta)."""
    import json

    try:
        from loto_enterprise.benchmark.decision import decision_target_mismatch

        cc = _selected_country() if country is None else country
        path = _LOT.decision_path_for(cc, PROJECT_ROOT)
        if not path.exists():
            return {}
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cc = _LOT.normalize_country(cc)
        meta = cfg.get("_meta") if isinstance(cfg.get("_meta"), dict) else {}
        if cc != _LOT.RO and str(meta.get("country") or "").strip().upper() != cc:
            return {}  # producția o tratează ca lipsă (frequency), nu ca decizie
        return decision_target_mismatch(cfg, _bench_target())
    except Exception:  # noqa: BLE001
        return {}


def _decision_target_text(country=None) -> str | None:
    """Avertismentul persistent pentru decizia salvată pe altă țintă, sau None."""
    stale = _decision_target_mismatch(country)
    if not stale:
        return None
    names = {v: k for k, v in GK_MATRIX.items()}

    def _name(gk: str) -> str:
        if gk in names:
            return names[gk]
        lot = _LOT.lottery_by_bench_key(gk)
        return lot.name if lot is not None else gk

    parts = ", ".join(f"{_name(gk)} pe {t}+" for gk, t in stale.items())
    return (
        f"⚠️ Decizia Auto-Pilot salvată nu e pe ținta selectată "
        f"({_bench_target()}+): {parts}. Generarea joacă metoda aleasă pentru "
        "ținta veche. Decizia se recalculează singură la pornirea UI-ului sau la "
        "finalul Re-Bench-ului, dacă folds.csv acoperă bench-ul deciziei; altfel "
        "rulează un Re-Bench complet."
    )


def _n_extrageri(n: int) -> str:
    """„1 extragere" / „2 extrageri" — acordul românesc, nu «1 extrageri»."""
    return "1 extragere" if int(n) == 1 else f"{int(n)} extrageri"


# Reluarea „Bilet complet” pe pașii WF rulează în fundal (sute de pași). Rezultatul
# se ține pe obiectul `flat` afișat: o listă nouă = calcul nou.
_REPLAY_LOCK = threading.Lock()
_REPLAY_DONE: dict = {}
_REPLAY_PENDING: dict = {}
# `current` = (cheie, flat) aflată în calcul: temporizatorul de 1 s n-o mai pune
# înapoi în coadă, altfel fiecare reluare se calcula de două ori.
_REPLAY_THREAD: dict = {"running": False, "current": None}
_REPLAY_KEEP = 12


def _replay_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _replay_loop() -> None:
    from loto_enterprise.core.ticket_replay import replay_full_tickets

    while True:
        with _REPLAY_LOCK:
            if not _REPLAY_PENDING:
                _REPLAY_THREAD["running"] = False
                _REPLAY_THREAD["current"] = None
                return
            key = next(reversed(_REPLAY_PENDING))  # cererea cea mai recentă întâi
            flat, game, tickets, guarantee = _REPLAY_PENDING.pop(key)
            _REPLAY_THREAD["current"] = (key, flat)
        try:
            res = replay_full_tickets(
                flat, game, tickets, guarantee, workers=_replay_workers()
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BILET] reluare în Istoric hits eșuată: %s", exc)
            res = {"failed": str(exc)}
        with _REPLAY_LOCK:
            _REPLAY_DONE[key] = (flat, res)
            again = _REPLAY_PENDING.get(key)
            if again is not None and again[0] is flat:
                del _REPLAY_PENDING[key]  # aceeași cerere, deja calculată
            _REPLAY_THREAD["current"] = None
            while len(_REPLAY_DONE) > _REPLAY_KEEP:
                _REPLAY_DONE.pop(next(iter(_REPLAY_DONE)))


def _full_ticket_replay(flat, game: str, tickets: int, guarantee) -> dict | None:
    """Reluarea memorată pentru acest `flat`, sau None după ce o cere în fundal."""
    key = (id(flat), len(flat), str(game), int(tickets), guarantee)
    with _REPLAY_LOCK:
        done = _REPLAY_DONE.get(key)
        if done is not None and done[0] is flat:
            return done[1]
        current = _REPLAY_THREAD.get("current")
        if current is not None and current[0] == key and current[1] is flat:
            return None  # în calcul chiar acum
        # Alt număr de bilete pe același istoric: cererea veche nu mai e afișată.
        for old in [k for k in _REPLAY_PENDING if k[:3] == key[:3]]:
            del _REPLAY_PENDING[old]
        _REPLAY_PENDING[key] = (flat, str(game), int(tickets), guarantee)
        if not _REPLAY_THREAD["running"]:
            _REPLAY_THREAD["running"] = True
            threading.Thread(target=_replay_loop, name="ticket-replay", daemon=True).start()
    return None


def _full_ticket_replay_rows(res: dict, tickets: int) -> tuple[list[dict], int]:
    """Rândul „Bilet complet” și numărul de extrageri reluate."""
    best = res.get("best") or {}
    exact = res.get("uniform")
    m = len(best)
    cells = {}
    for t in (3, 4, 5):
        k = sum(1 for row in best.values() if int(row.get("hits", 0)) >= t)
        cells[f"p{t}"] = f"{k} ({k / m * 100:.2f}%)" if m else "—"
    n_var = max((int(row.get("variants", 0)) for row in best.values()), default=0)
    rnd = (
        f"{exact.get(3, 0) * 100:.1f}% / {exact.get(4, 0) * 100:.2f}% / "
        f"{exact.get(5, 0) * 100:.3f}%"
        if exact
        else "—"
    )
    row = {
        "src": "🎟️ Bilet complet",
        **cells,
        "rnd": rnd,
        "var": count(n_var, "variante", "o variantă"),
    }
    return [row], m


def _render_full_ticket_replay(flat, game: str, guarantee) -> None:
    """„Bilet complet” refăcut pe aceleași extrageri WF."""
    from loto_enterprise.core.full_ticket import clamp_tickets

    box = ui.column().classes("w-full gap-1")
    shown: dict = {"sig": None}

    def _fill() -> None:
        tickets = clamp_tickets(SETTINGS.get("full_ticket_count_val"))
        res = _full_ticket_replay(flat, game, tickets, guarantee)
        sig = (tickets, res is not None)
        if sig == shown["sig"]:
            return
        shown["sig"] = sig
        box.clear()
        with box:
            slips = "un bilet" if tickets == 1 else f"{tickets} bilete"
            ui.label(
                f"🎟️ „Bilet complet” pe aceleași extrageri ({slips} pe extragere):"
            ).classes("text-bold text-caption mt-2")
            if res is None:
                ui.label(
                    "⏳ Refac biletele la fiecare extragere din validare…"
                ).classes("text-caption text-grey")
                return
            if res.get("failed"):
                ui.label(f"Reluarea a eșuat: {res['failed']}").classes(
                    "text-caption text-warning"
                )
                return
            rows, m = _full_ticket_replay_rows(res, tickets)
            n_all = int(res.get("n_draws") or 0)
            missing = int(res.get("missing") or 0)
            if not m:
                if missing:
                    ui.label(
                        "Indisponibil: validarea din cache a fost calculată înainte ca "
                        "pașii să-și păstreze pool-ul și clasamentul. Pașii se refac la "
                        "următoarea validare walk-forward, pornită după o generare."
                    ).classes("text-caption text-grey")
                else:
                    reasons = "; ".join(sorted(res.get("errors") or {})) or "fără variante"
                    ui.label(f"Indisponibil: {reasons}.").classes(
                        "text-caption text-grey"
                    )
                return
            ui.table(
                columns=[
                    {"name": "src", "label": "", "field": "src", "align": "left"},
                    {"name": "p3", "label": "+3 (extrageri)", "field": "p3", "align": "center"},
                    {"name": "p4", "label": "+4 (extrageri)", "field": "p4", "align": "center"},
                    {"name": "p5", "label": "+5 (extrageri)", "field": "p5", "align": "center"},
                    {
                        "name": "rnd",
                        "label": "🎲 exact, extragere uniformă (3+ / 4+ / 5+)",
                        "field": "rnd",
                        "align": "center",
                    },
                    {"name": "var", "label": "Variante/extragere", "field": "var", "align": "center"},
                ],
                rows=rows,
            ).classes("w-full").props("dense")
            note = (
                f"Extrageri cu cel puțin o variantă la prag, din {_n_extrageri(m)}. "
                "La fiecare extragere, biletele sunt cele pe care le-ar fi dat butonul "
                "„🎟️ Bilet complet” în ziua aceea: pool-ul și clasamentul pasului "
                "walk-forward, garanția rezultatului afișat. 🎲 = șansa exactă ca cel "
                "puțin o variantă să atingă pragul la o extragere uniformă, calculată pe "
                "biletele celei mai recente extrageri; nu depinde de numerele alese, ci "
                "de cum se suprapun variantele. Rândul urmează hiturile pool-ului din "
                "tabelul de mai sus; metoda a fost aleasă pe același istoric, deci un "
                "rezultat peste 🎲 aici nu e o validare externă. La pragurile rare "
                "(câteva evenimente), frecvența observată variază mult în jurul valorii 🎲."
            )
            if game == "joker":
                note += " Joker: numai Urna 1."
            if m < n_all:
                parts = []
                if missing:
                    parts.append(
                        f"{_n_extrageri(missing)} din cache-ul vechi, refăcute la "
                        "următoarea validare"
                    )
                other = n_all - m - missing
                if other:
                    parts.append(f"{_n_extrageri(other)} fără bilet construibil")
                note += f" Lipsesc {n_all - m} din {n_all}: " + "; ".join(parts) + "."
            ui.label(note).classes("text-caption text-grey")

    _fill()
    ui.timer(1.0, _fill)


def _wf_coverage_note(flat, label: str = "") -> tuple[str, str] | None:
    """(clase_css, text) de avertizare când hiturile de POOL ≠ hituri de BILET.

    `hits_union` numără numere din pool ieșite la extragere. O acoperire sub
    100% nu garantează ținta cerută. La designurile condiționale, nici 100% nu
    echivalează hiturile de pool cu cele de bilet sub condiția cerută.
    None = fără avertisment de acoperire, nu dovadă că hiturile coincid.
    """
    try:
        from loto_enterprise.core.walk_forward_adapter import wheel_coverage_summary

        cov = wheel_coverage_summary(flat)
    except Exception:  # noqa: BLE001
        return None
    _sfx = f" ({label})" if label else ""
    if cov["below_100"]:
        return (
            "text-warning text-caption text-bold",
            f"⚠️ Wheel INCOMPLET{_sfx}: {cov['below_100']} din {_n_extrageri(cov['known'])} "
            f"sub 100% acoperire (minim {cov['min']:.2f}%) — cifrele de POOL sunt un "
            "PLAFON, nu ce prinde un bilet. «Variante maxime» = 0 scoate doar plafonul; "
            "procentul măsurat rămâne decisiv.",
        )
    if cov["unknown"] and not cov["known"]:
        return (
            "text-caption text-grey",
            f"ℹ️ Acoperire wheel NECUNOSCUTĂ{_sfx} — cache WF scris înainte de măsurarea ei. "
            "Comparați hiturile de POOL cu cele de BILET; egalitatea depinde de garanția "
            "și condiția wheel-ului. Acoperirea se completează la următorul walk-forward.",
        )
    if cov["unknown"]:
        return (
            "text-caption text-grey",
            f"ℹ️ Acoperire wheel{_sfx}: 100% pe {_n_extrageri(cov['known'])}, necunoscută pe "
            f"{cov['unknown']} (cache WF mai vechi).",
        )
    return None


def _wf_pool_size(flat, fallback: int) -> int:
    """Mărimea pool-ului jucat la pașii WF (cea mai frecventă), altfel `fallback`.

    Pool-ul cerut poate fi mai mare decât intervalul restrâns: atunci fiecare pas
    joacă mai puține numere, iar referința aleatoare trebuie calculată pe ele.
    """
    sizes: dict[int, int] = {}
    seen: set = set()
    for row in flat or ():
        di = getattr(row, "draw_index", None)
        if di in seen:
            continue
        seen.add(di)
        hard_core = (getattr(row, "ticket_context", None) or {}).get("hard_core")
        if hard_core:
            sizes[len(hard_core)] = sizes.get(len(hard_core), 0) + 1
    if not sizes:
        return int(fallback or 0)
    return max(sizes, key=lambda k: (sizes[k], k))


def _wf_per_draw_stats(flat) -> dict:
    """Dedup walk-forward pe extragere: pool hits_union.

    `flat` are o intrare per (extragere × variantă); aici păstrăm o singură
    intrare per extragere, fiindcă `hits_union` (câte numere din POOL au ieșit)
    e identic pentru toate variantele aceleiași extrageri."""
    per: dict = {}
    from loto_enterprise.core.walk_forward_adapter import per_draw_hit_summary

    hits = per_draw_hit_summary(flat)
    for p in flat:
        di = getattr(p, "draw_index", 0)
        if di not in per:
            dd = getattr(p, "draw_date", getattr(p, "target_draw_date", None))
            per[di] = {
                "label": str(dd) if dd and str(dd) != "None" else f"#{di}",
                **hits[int(di)],
            }
    return per


def _render_hits_4plus(
    flat,
    game: str,
    meta: dict | None = None,
    pool_n: int | None = None,
    restrict_base_text: str = "",
    consecutive_limit_text: str = "",
    full_ticket: dict | None = None,
) -> None:
    """Istoric hits pentru pool-ul unic; folosește mărimea efectivă din rezultat.

    `full_ticket` = {"game", "guarantee"} adaugă „Bilet complet” refăcut pe
    aceleași extrageri (`_render_full_ticket_replay`).

    `restrict_base_text`: eticheta intervalului aplicat (din audit-ul aceluiași
    rezultat), sau "" fără restricție. `run_honest_walk_forward` primește exact
    aceleași praguri prin `_wf_generation_options` — istoricul de mai jos NU e
    calculat pe universul complet cât timp restricția era activă la generare.
    `consecutive_limit_text`: la fel pentru limita de consecutive; apare ori de
    câte ori limita a fost cerută, chiar dacă pool-ul de azi n-a avut nevoie de ea.
    """
    if not flat:
        return
    per = _wf_per_draw_stats(flat)
    n = len(per)
    if not n:
        ui.label("Niciun istoric walk-forward.").classes("text-caption text-grey")
        return
    pool3 = sum(1 for d in per.values() if d["pool"] >= 3)
    pool4 = sum(1 for d in per.values() if d["pool"] >= 4)
    pool5 = sum(1 for d in per.values() if d["pool"] >= 5)
    ticket3 = sum(1 for d in per.values() if d["best_ticket"] >= 3)
    ticket4 = sum(1 for d in per.values() if d["best_ticket"] >= 4)
    ticket5 = sum(1 for d in per.values() if d["best_ticket"] >= 5)

    def _cell(k, denom):
        return f"{k} ({k / denom * 100:.2f}%)" if denom else "—"

    ui.label(f"🎯 Istoric hits (din {n} extrageri walk-forward):").classes(
        "text-bold text-caption mt-2"
    )
    if restrict_base_text:
        ui.label(
            f"{restrict_base_text[0].upper() + restrict_base_text[1:]} — istoricul "
            "de mai jos folosește ACEEAȘI restricție ca pool-ul generat, nu "
            "universul complet."
        ).classes("text-caption text-grey")
    if consecutive_limit_text:
        ui.label(
            f"{consecutive_limit_text[0].upper() + consecutive_limit_text[1:]} — "
            "istoricul de mai jos aplică aceeași limită la fiecare pas, ca la "
            "pool-ul generat."
        ).classes("text-caption text-grey")
    _wg = (meta or {}).get("wheel_guarantee")
    if _wg is not None:
        _wc = (meta or {}).get("wheel_condition") or _wg
        _cap = int((meta or {}).get("max_variants") or 0)
        _cap_note = f"plafon {_cap} variante" if _cap else "fără plafon de variante"
        ui.label(
            f"Wheel evaluat în WF: garanție {_wg} dacă {_wc} numere sunt în pool, "
            f"{_cap_note}."
        ).classes("text-caption text-grey")
        if int(_wc) > int(_wg):
            ui.label(
                f"Acoperirea de 100% garantează {_wg} pe un bilet numai de la {_wc} "
                "numere în pool. Pentru rezultate, urmăriți rândul BILET."
            ).classes("text-caption text-warning")
    _cn = _wf_coverage_note(flat)
    if _cn:
        ui.label(_cn[1]).classes(_cn[0])
    if meta and meta.get("partial"):
        _which = (
            "lipsesc tocmai extragerile CELE MAI NOI"
            if meta.get("newest_missing")
            else "extragerile CELE MAI RECENTE"
        )
        ui.label(
            f"⚠️ Validare PARȚIALĂ: {meta.get('n_test_draws')} din "
            f"{meta.get('n_expected')} extrageri — {_which}."
        ).classes("text-warning text-caption text-bold")
    if meta and meta.get("decision_changed"):
        ui.label(
            "⚠️ Decizia de bench s-a schimbat în timpul validării (Re-Bench terminat "
            "sau țintă schimbată): pașii nu provin dintr-un singur scorer. Rezultatul "
            "nu s-a salvat în cache; rulează din nou walk-forward-ul."
        ).classes("text-warning text-caption text-bold")
    # (1) Sumar comparabil: +3 / +4 / +5 pe pool, baseline hipergeometric și volumul
    # real de variante din WF. Premiile nu pot fi deduse din hiturile Urnei 1.
    _foreign = _LOT.lottery_by_id(str(game))
    if _foreign is not None and _foreign.is_romanian:
        _foreign = None
    # Străin: id-ul din registru (geometria și ținta lui); România: eticheta veche.
    gk = _foreign.game_id if _foreign is not None else _game_label_for(game)
    # Ținta per joc, ca decizia: 5/40 rămâne pe 4+ (3 numere nu aduc premiu).
    from loto_enterprise.benchmark.hit_target import game_hit_target

    if _foreign is not None:
        _TT = game_hit_target(_foreign.bench_key, _bench_target())
    else:
        _TT = game_hit_target("loto_5_40" if gk == "5/40" else str(gk), _bench_target())
    # Pool-ul REAL (nu string fix „din 16"): mărimea jucată la pașii WF (o bază
    # restrânsă mai îngustă decât pool-ul o micșorează), apoi meta WF, apoi
    # rezultatul pasat de apelant (pool_size / len(hard_core)); 0 = necunoscut.
    _pn = _wf_pool_size(flat, int((meta or {}).get("pool_size") or pool_n or 0))
    n_tick = len(flat)
    tick_avg = (n_tick / n) if n else 0.0

    # Baseline-ul PUR aleator: Pool = K numere (hipergeometric).
    def _bcell(K):
        b3 = _random_rate_hypergeo(gk, K, 3)
        b4 = _random_rate_hypergeo(gk, K, 4)
        b5 = _random_rate_hypergeo(gk, K, 5)
        if b3 is None or b4 is None or b5 is None:
            return "—"
        return f"{b3 * 100:.1f}% / {b4 * 100:.2f}% / {b5 * 100:.3f}%"

    def _tick_cell(n_tick, avg):
        return f"{n_tick:,} ({avg:.2f}/extr.)"

    rows = [
        {
            "src": f"🎯 Pool (din {_pn})" if _pn else "🎯 Pool",
            "p3": _cell(pool3, n),
            "p4": _cell(pool4, n),
            "p5": _cell(pool5, n),
            "rnd": _bcell(_pn),
            "tick": _tick_cell(n_tick, tick_avg),
        },
        {
            "src": "🎟️ Cel puțin un bilet WF",
            "p3": _cell(ticket3, n),
            "p4": _cell(ticket4, n),
            "p5": _cell(ticket5, n),
            "rnd": "—",
            "tick": "același wheel",
        },
    ]
    ui.table(
        columns=[
            {"name": "src", "label": "Sursă", "field": "src", "align": "left"},
            {"name": "p3", "label": "+3 (extrageri)", "field": "p3", "align": "center"},
            {"name": "p4", "label": "+4 (extrageri)", "field": "p4", "align": "center"},
            {"name": "p5", "label": "+5 (extrageri)", "field": "p5", "align": "center"},
            {
                "name": "rnd",
                "label": "🎲 random (3+ / 4+ / 5+)",
                "field": "rnd",
                "align": "center",
            },
            {
                "name": "tick",
                "label": "🎟️ Variante WF",
                "field": "tick",
                "align": "center",
            },
        ],
        rows=rows,
    ).classes("w-full").props("dense")
    _cap = (
        "🎟️ = variantele efectiv evaluate (o intrare walk-forward = o variantă la o extragere). "
        "🎲 = baseline PUR aleator (hipergeometric, calculat din parametrii jocului și "
        "mărimea pool-ului). +3 / +4 / +5 = extrageri cu ≥3 / ≥4 / ≥5 numere nimerite. "
        "Premiile și ROI-ul nu sunt estimate: CSV-ul nu conține categoria de premiu, "
        "iar Joker cere și validarea Urnei 2 pe același bilet. "
    )
    _cap += f" Bilete evaluate = {n_tick:,} (extragere × variantă, {tick_avg:.2f}/extragere)."
    _cap += " Rândul de bilete numără extrageri cu cel puțin un bilet care atinge pragul; baseline-ul de pool nu este un baseline separat pentru bilete."
    _price = PRICES.get(gk, 8.0)
    if _foreign is not None:
        if n_tick and _foreign.price is not None:
            _cap += (
                f" Cost estimat pe fereastra WF: {n_tick:,} × {_fmt_price(_foreign)} "
                f"≈ {n_tick * _foreign.price:,.2f} {_foreign.currency} "
                "(fără taxe pe bilet, fără câștig)."
            )
        elif n_tick:
            _cap += " Cost: tarif necunoscut."
    elif n_tick:
        _cap += (
            f" Cost estimat pe fereastra WF: {n_tick:,} × {_price:g} lei/variantă "
            f"≈ {n_tick * _price:,.0f} lei (tarif standard, fără taxă fizică, fără câștig)."
        )
    ui.label(_cap).classes("text-caption text-grey")
    ui.label(
        "Garanția 4 nu asigură 5 pe un bilet; compară separat hiturile pool-ului "
        "și ale biletelor."
    ).classes("text-caption text-grey")
    if full_ticket:
        _render_full_ticket_replay(flat, full_ticket["game"], full_ticket.get("guarantee"))
    # Onestitate: rata WF observată la ținta bench vs baseline-ul PUR aleator —
    # dacă nu-l bate, spune EXPLICIT (nu lăsa o rată „~10%" să pară edge).
    _tt_checks = [("Pool", sum(1 for d in per.values() if d["pool"] >= _TT), n, _pn)]
    _losers = []
    for _lbl3, _cnt, _den, _K in _tt_checks:
        _b = _random_rate_hypergeo(gk, _K, _TT) if _K else None
        if _b is not None and _den and (_cnt / _den) <= _b:
            _losers.append(
                f"{_lbl3}: {_cnt / _den * 100:.1f}% ≤ random {_b * 100:.1f}%"
            )
    if _losers:
        ui.label(
            f"⚠️ Onestitate (≥{_TT}): "
            + " · ".join(_losers)
            + " — pe fereastra validată metoda NU a bătut hazardul (diferența e zgomot)."
        ).classes("text-caption text-warning text-bold")

    # (2) DATELE prinderii — listă ≥3 (acoperire); 🔥 marchează targetul bench (≥_TT).
    # Δ pe cel mai recent = „acum X zile” (până azi); pe rest = interval până la hit-ul următor.
    def _pool_badge(d):
        h = int(d["pool"])
        return f"🔥 {h}" if h >= _TT else f"⭐ {h}"

    def _dates_table(title, pred, badge, empty_msg, gap_on=None, gap_label=None):
        items = [(di, d) for di, d in per.items() if pred(d)]
        if not items:
            ui.label(empty_msg).classes("text-caption text-grey mt-2")
            return
        _gp = gap_on or (lambda d: True)
        # Gap-uri doar pe hiturile care ating ținta; restul (dacă apar) fără Δ.
        gap_items = [(di, d) for di, d in items if _gp(d)]
        gap_txt = {di: g for di, _d, g in _hit_gap_rows(gap_items)}
        _gl = gap_label or f"Δ până la următorul ≥{_TT} (sau azi)"
        rows = []
        for di, d in sorted(items, key=lambda kv: kv[0], reverse=True):
            rows.append(
                {
                    "draw": d["label"],
                    "hits": badge(d),
                    "gap": gap_txt.get(di, "—") if _gp(d) else "—",
                }
            )
        ui.label(f"{title} ({len(rows)} extrageri, cele mai recente întâi):").classes(
            "text-bold text-caption mt-3"
        )
        ui.table(
            columns=[
                {"name": "draw", "label": "Data", "field": "draw", "align": "left"},
                {
                    "name": "hits",
                    "label": "Nimerite",
                    "field": "hits",
                    "align": "center",
                },
                {"name": "gap", "label": _gl, "field": "gap", "align": "center"},
            ],
            rows=rows,
            pagination=15,
        ).classes("w-full").props("dense")

    # Legendă condiționată de țintă: la _TT==3 orice ≥3 primește 🔥, deci ⭐ nu
    # apare niciodată → mențiunea lui ar fi text mort/contradictoriu.
    _star_leg = (
        ("⭐ = exact 3; " if _TT == 4 else f"⭐ = 3–{_TT - 1}; ") if _TT > 3 else ""
    )
    ui.label(
        f"🗓️ Istoric: extrageri cu ≥3 în pool-ul walk-forward de LA DATA extragerii "
        f"(antrenat doar pe trecut, fără lookahead). 🔥 = target bench (≥{_TT}); "
        f"{_star_leg}Δ pe cel mai recent rând care atinge ținta = „acum X zile”; "
        f"pe celelalte rânduri care ating ținta = zile până la hit-ul următor mai recent. "
        f"Nucleul de producție de lângă ultima extragere CSV e ALT set — nu compara 🔥 cu el."
    ).classes("text-caption text-grey mt-2")
    _dates_table(
        "🗓️ POOL",
        lambda d: d["pool"] >= 3,
        _pool_badge,
        "Pool-ul n-a prins ≥3 în istoricul walk-forward.",
        gap_on=lambda d: d["pool"] >= _TT,
        gap_label=f"Δ → următorul ≥{_TT} / azi",
    )
    with ui.expansion("🎟️ Bilete WF cu 4+ și 5+ — date și intervale", value=False).classes(
        "w-full"
    ):
        ui.label(
            "Fiecare rând este o extragere cu cel puțin un bilet la pragul indicat. "
            "Intervalele sunt calculate separat pentru 4+ și 5+, nu sunt predicții."
        ).classes("text-caption text-grey")
        for threshold in (4, 5):
            _dates_table(
                f"🎟️ BILET {threshold}+",
                lambda d, t=threshold: d["best_ticket"] >= t,
                lambda d: f"{int(d['best_ticket'])} numere",
                f"Niciun bilet nu a prins ≥{threshold} în istoricul walk-forward.",
                gap_label=f"Δ → următorul ≥{threshold} pe bilet / azi",
            )


def _render_analysis_menu(results_bundle, res_prefix: str = "") -> None:
    """Meniu global: metoda câștigătoare + istoric ≥4 hits per joc. Închis implicit."""
    has_folds = (PROJECT_ROOT / "bench_results" / "folds.csv").exists()
    has_wf = any(
        STATE["retro"].get(f"{res_prefix}{fn}_{g}")
        for fn, outs in results_bundle
        for g, _ in outs.items()
    )
    if not (has_folds or has_wf):
        return

    with ui.card().classes("w-full"):
        with ui.expansion("📊 Analiză & Clasament", value=False).classes("w-full"):
            # Aplatizăm toate jocurile din toate fișierele și le sortăm GLOBAL: 6/49, Joker, 5/40.
            flat_games = [
                (fname, game, data)
                for fname, outs in results_bundle
                for game, data in outs.items()
            ]
            flat_games.sort(
                key=lambda t: _GAME_DISPLAY_ORDER.get(_game_label_for(str(t[1])), 99)
            )
            for fname, game, raw_data in flat_games:
                data = _primary_pool_data(raw_data)
                ui.separator().classes("my-3")
                ui.label(f"🎯 {_game_title(game, data)}").classes("text-bold text-lg")

                # Reper: ultima extragere reală din CSV (deasupra clasamentului).
                _render_last_csv_draw(
                    fname,
                    pool=data.get("hard_core"),
                    joker_pick=data.get("hard_core_joker"),
                )

                # Pool-ul din rezultatul afișat rămâne reperul și dacă utilizatorul
                # a schimbat între timp setarea pentru următoarea generare.
                _pn = int(data.get("pool_size") or len(data.get("hard_core") or []))
                _spec = _game_spec_for(game, data)
                if _spec.is_romanian:
                    _render_bench_leaderboard(game, pool_size=_pn or None)
                else:
                    _render_bench_leaderboard(game, pool_size=_pn or None, spec=_spec)
                _penalty_note = _bench_transform_note(data)
                if _penalty_note:
                    ui.label(_penalty_note).classes("text-caption text-warning")

                # --- Istoric ≥4 hits — PLIABIL (în cadrul clasamentului, îl poți ascunde) ---
                # Pool-ul EFECTIV din REZULTAT, nu din setarea care se poate schimba ulterior.
                flat = STATE["retro"].get(f"{res_prefix}{fname}_{game}")
                if (STATE.get("retro_meta", {}).get(f"{res_prefix}{fname}_{game}") or {}).get(
                    "decision_moved"
                ):
                    ui.label(
                        "⚠️ Walk-forward sărit: decizia de bench s-a schimbat după generare "
                        "(Re-Bench terminat sau țintă 3+/4+ schimbată). Validarea ar fi "
                        "măsurat altă metodă decât cea care a produs pool-ul afișat; "
                        "generează din nou."
                    ).classes("text-warning text-caption text-bold")
                if flat:
                    # Deschis implicit (apare după ce termină walk-forward), dar pliabil
                    # → îl poți ascunde dacă vrei. Apare DOAR după WF (vine din STATE["retro"]).
                    with ui.expansion(
                        "📜 Istoric hits (walk-forward) — click pentru ascunde",
                        value=True,
                    ).classes("w-full"):
                        _render_hits_4plus(
                            flat,
                            game,
                            meta=STATE.get("retro_meta", {}).get(
                                f"{res_prefix}{fname}_{game}"
                            ),
                            pool_n=_pn,
                            # `_wf_generation_options(data)` (folosit la pornirea WF de
                            # mai sus) citește exact acest audit — istoricul de mai jos
                            # a rulat cu ACEEAȘI restrângere ca pool-ul afișat, nu una
                            # nerestrânsă. Fără linia asta, întrebarea „e cu pragul
                            # bifat sau fără?" nu avea răspuns lângă tabel, doar sus,
                            # lângă clasamentul bench.
                            restrict_base_text=_restrict_base_text(data.get("audit")),
                            consecutive_limit_text=_consecutive_limit_text(
                                data.get("audit"), details=False
                            ),
                            # Același joc și aceeași garanție ca „🎟️ Bilet complet”.
                            full_ticket={
                                "game": _game_label_for(str(game))
                                if _spec.is_romanian
                                else _spec.game_id,
                                "guarantee": data.get("guarantee"),
                            },
                        )

