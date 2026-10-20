"""Results / pool / cost rendering."""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime as _dt, timezone as _tz
from decimal import Decimal

import pandas as pd
from nicegui import ui

from ui_runtime import *
from loto_enterprise.core import lotteries as _LOT
from loto_enterprise.core.ro_text import count
from ui_shared import PROJECT_ROOT, atomic_write_text, render_html_safe

logger = logging.getLogger("app_nicegui")

def _badges(numbers, stats: dict | None = None):
    stats = stats or {}
    with ui.row().classes("flex-wrap gap-1"):
        for n in sorted(int(x) for x in (numbers or [])):
            freq = stats.get(str(n), stats.get(n))
            # Numărul de pool = mare/bold/alb; frecvența din paranteze = ștearsă
            # (opacitate redusă) ca să NU concureze vizual cu numărul.
            with ui.badge().props("color=primary").classes("text-sm"):
                if freq is not None:
                    ui.html(
                        render_html_safe(
                            t'<span style="font-weight:700;font-size:1.1em">{n}</span>'
                            t'<span style="opacity:0.45;font-size:0.68em;margin-left:3px">({freq})</span>'
                        )
                    )
                else:
                    ui.html(
                        render_html_safe(
                            t'<span style="font-weight:700;font-size:1.1em">{n}</span>'
                        )
                    )


# --------------------------------------------------------------------------- #
# Randare detaliată rezultate (audit, pipeline stages, cost variante)
# --------------------------------------------------------------------------- #
# Lei/variantă simplă. Taxa pe biletul fizic nu intră aici: depinde de câte
# variante sunt depuse împreună pe același bilet, deci nu poate fi dedusă din
# lista de variante generată de aplicație.
PRICES = {"6/49": 8.0, "5/40": 5.0, "joker": 7.0}

def _lr_table() -> dict:
    """(cod, n_variante) per (joc, numere jucate), din desfășurările verificate."""
    from covering.lr_schemes import lr_schemes

    out: dict = {}
    for s in lr_schemes():
        out.setdefault(s.game, {}).setdefault(s.n, []).append((s.label, s.size))
    return out


# Scheme reduse oficiale Loteria Română (`covering/lr_schemes.py`).
LR_SCHEMES = _lr_table()
STAGE_META = [
    (
        "1_nqi_raw",
        "1. Pool după scor",
        "#60a5fa",
        "Top-K după scorul de clasare, inclusiv penalizarea recentă dacă este activă. Scorul nu este o probabilitate de câștig.",
    ),
    (
        "2_smart_selector",
        "2. Pool brut (fără rafinare)",
        "#a78bfa",
        "Smart Selector ELIMINAT — pool-ul rămâne decizia PURĂ a scorerului câștigător "
        "(fără rafinare hibridă). Etapă păstrată doar pentru numerotare (Δ mereu 0).",
    ),
    (
        "3_anti_sequence",
        "3. Limita de consecutive (opțiunea utilizatorului)",
        "#f59e0b",
        "Cu bifa „Fără 3 numere consecutive”, numărul care ar forma secvența iese, "
        "iar locul lui îl ia următorul din clasamentul metodei. Fără bifă, pool-ul "
        "rămâne cel de la pasul 1. Preferință de compoziție, fără avantaj statistic.",
    ),
    (
        "4_post_hoc_final",
        "4. POST-HOC (dezactivat)",
        "#10b981",
        "Validare retrospectivă ELIMINATĂ — fără rescrieri post-scoring.",
    ),
]


def _fmt_num(x) -> str:
    """Formatează un număr pentru UI (evită repr numpy np.float64(...))."""
    if x is None:
        return "?"
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return str(x)


def _unplayed_reason(data: dict) -> str:
    """De ce lipsesc numere de pe bilete: designul (garanție întreagă) sau plafonul."""
    try:
        complete = float((data.get("context") or {}).get("coverage_pct")) >= 100.0
    except (TypeError, ValueError):
        complete = False
    if complete:
        return "designul își ține garanția fără ele"
    return "nu au încăput în limita de variante, iar garanția nu e completă"


def _render_audit(audit: dict) -> None:
    _jk_invalid = int(audit.get("joker_urna2_invalid_rows_dropped") or 0)
    if audit.get("joker_urna2_unavailable"):
        ui.markdown(
            "⚠️ **Joker Urna 2 indisponibilă:** nu există valori valide 1–20; "
            "biletele nu includ un număr Joker arbitrar."
        ).classes("text-warning")
    elif _jk_invalid:
        _jk_total = int(audit.get("joker_urna2_rows_total") or 0)
        ui.markdown(
            f"⚠️ **Joker Urna 2:** {_jk_invalid} valori invalide din {_jk_total} au fost ignorate "
            "(sunt acceptați doar întregi 1–20)."
        ).classes("text-warning")

    # Aici erau randate `consecutive_filter`, `timesfm_excluded`, `anomaly_filter`,
    # `smart_selector` și `kept_sequences`. Niciuna dintre chei nu mai are
    # PRODUCĂTOR în engine (filtrul anti-secvență, TimesFM, Smart Selector și
    # anti-anomalie au fost scoase din pipeline), deci ramurile nu se mai
    # executau niciodată. `consecutive_filter_warnings` era scrisă în audit de
    # același filtru, dar nu a fost randată niciodată aici.


def _render_stages(audit: dict) -> None:
    stages = audit.get("pipeline_stages") or {}
    if not stages:
        return
    with ui.expansion(
        "🔍 Evoluția Pool-ului — Pipeline Stage-by-Stage", value=False
    ).classes("w-full"):
        prev: set | None = None
        for key, title, color, desc in STAGE_META:
            pool_list = stages.get(key)
            if not pool_list:
                continue
            pool_set = set(int(x) for x in pool_list)
            added = (pool_set - prev) if prev is not None else set()
            removed = (prev - pool_set) if prev is not None else set()
            chips = []
            for n in sorted(pool_set):
                if n in added:
                    chips.append(
                        render_html_safe(
                            t"<span style='background:#064e3b;color:#6ee7b7;padding:2px 8px;border-radius:10px;margin:2px;font-weight:bold;'>+{n}</span>"
                        )
                    )
                else:
                    chips.append(
                        render_html_safe(
                            t"<span style='background:rgba(255,255,255,0.07);color:#e5e7eb;padding:2px 8px;border-radius:10px;margin:2px;'>{n}</span>"
                        )
                    )
            for n in sorted(removed):
                chips.append(
                    render_html_safe(
                        t"<span style='background:#7f1d1d;color:#fecaca;padding:2px 8px;border-radius:10px;margin:2px;text-decoration:line-through;'>−{n}</span>"
                    )
                )
            chips_html = "".join(chips)
            delta = f" (Δ: +{len(added)}, −{len(removed)})" if prev is not None else ""
            # `chips_html` e HTML DEJA randat (fiecare chip a trecut prin
            # render_html_safe). Interpolat într-un t-string ar fi escape-uit A
            # DOUA OARĂ → utilizatorul vedea textul literal
            # "<span style='...'>12</span>" în loc de chips colorate. Îl
            # concatenăm în AFARA t-string-ului, ca în restul fișierului.
            ui.html(
                render_html_safe(
                    t"<div style='margin-top:8px;padding:8px;background:rgba(255,255,255,0.03);border-left:3px solid {color};border-radius:4px;'>"
                    t"<div style='font-weight:700;color:{color};'>{title}{delta}</div>"
                    t"<div style='font-size:0.85em;color:#94a3b8;margin:2px 0 6px 0;'>{desc}</div>"
                    t"<div>"
                )
                + chips_html
                + "</div></div>"
            )
            prev = pool_set


def _render_cost(game: str, data: dict) -> None:
    spec = _game_spec_for(game, data)
    if not spec.is_romanian:
        _render_cost_foreign(game, data, spec)
        return
    gk = _game_label_for(game)
    price = PRICES.get(gk, 8.0)
    # Numere pe BILET (5/40 extrage 6, dar varianta are 5).
    draw_n = _ticket_pick(gk)
    # Mărimea pool-ului = numerele lui distincte (o bază restrânsă poate da mai
    # puține decât pool_size); pool_size numai fără numere.
    _nums = {int(x) for x in (data.get("hard_core") or [])}
    pool_used = len(_nums) or int(data.get("pool_size") or 0)

    full_vars = math.comb(pool_used, draw_n) if pool_used >= draw_n else 0
    full_cost = full_vars * price
    # FĂRĂ multiplicator de joker în NICIUNA dintre formulele de cost de mai jos.
    # Motiv (verificat în `loto_engine.generate_predictions`): jokerul se atașează
    # CICLIC — `assigned_joker = jokers[idx % len(jokers)]`, un singur număr per
    # variantă, NU produsul cartezian variante × jokeri. În plus nucleul Urnei 2 e
    # single-pick (`sorted_j[:1]` / `_get_hard_core_joker(pool_size=1)`, aliniat
    # bench), deci lista are oricum 1 element. Concluzie: nr. BILETE = nr. VARIANTE,
    # pe toate cele patru formule (scheme oficiale, sistem complet, bilete simple,
    # wheel) → toate se citesc pe ACEEAȘI bază.
    _jk_txt = " · 1 nr. joker/bilet" if gk == "joker" else ""

    # „Sistem complet" = TOATE combinațiile C(pool, draw_n) de la agenție (fără garanție
    # de acoperire — e exhaustiv). NU confunda cu „wheel-ul nostru" de mai jos, care e
    # un cover la garanția cerută; minimalitatea nu este demonstrată în general.
    _full_lbl = (
        f"Sistem complet C({pool_used},{draw_n}) = {full_vars} var.{_jk_txt} "
        f"≈ {_thousands(full_cost)} Lei în variante"
    )
    schemes = _lr_schemes_for(game, data)
    if schemes:
        parts = [
            f"**{s.label}** ({s.size} var.{_jk_txt} ≈ {_thousands(s.size * price)} Lei în variante)"
            for s in schemes
        ]
        ui.markdown(
            f"💡 **Scheme reduse oficiale la agenție** ({schemes[0].n} nr.): "
            + " sau ".join(parts)
            + f"\n\n*({_full_lbl} — toate combinațiile, exhaustiv)*"
        ).classes("text-info")
        for line in _lr_scheme_lines(game, data):
            ui.label(line).classes("text-caption")
        _render_lr_layouts(game, data, schemes)
    else:
        from covering.lr_schemes import unloaded_schemes

        _sizes = sorted(LR_SCHEMES.get(gk, {}))
        _bad = unloaded_schemes(gk, pool_used) if _nums else []
        if not _nums:
            _none = f"pool gol la {_game_title(game)}."
        elif _bad:
            _none = (
                f"schema redusă LR {', '.join(_bad)} pentru {pool_used} nr. nu s-a putut "
                "citi din covering_designs/lr (vezi jurnalul)."
            )
        else:
            _none = (
                f"fără schemă redusă oficială cu variante întregi pentru {pool_used} nr. "
                f"la {_game_title(game)}."
            )
        _have = (
            f" Loteria Română are scheme reduse cu variante întregi pentru "
            f"{', '.join(map(str, _sizes))} numere."
            if _sizes and _nums and not _bad
            else ""
        )
        if _nums:
            ui.markdown(
                f"💡 **Cost la agenție:** {_none} **{_full_lbl}** (toate combinațiile, "
                f"exhaustiv).{_have}"
            ).classes("text-info")
        else:
            # Fără numere nu există sistem complet de prețuit (pool_size e numai
            # mărimea cerută).
            ui.markdown(f"💡 **Cost la agenție:** {_none}").classes("text-info")

    variants = data.get("variants") or []
    if variants:
        n_simple = min(_simple_variants_count(), len(variants))
        # Garanția EFECTIV folosită la wheel (audit) — cea care face diferența față de
        # schemele oficiale de mai sus; fallback pe cea cerută din setări.
        _g_used = (data.get("audit") or {}).get("wheel_guarantee_used")
        if _g_used is None:
            _g_used = data.get("guarantee")
        _wc_top = (data.get("audit") or {}).get("wheel_condition_used") or data.get(
            "wheel_condition"
        )
        try:
            _wc_top_txt = (
                f" dacă {int(_wc_top)}"
                if _wc_top is not None
                and _g_used is not None
                and int(_wc_top) != int(_g_used)
                else ""
            )
        except (TypeError, ValueError):
            _wc_top_txt = ""
        _g_txt = (
            f"garanție {_g_used}{_wc_top_txt}"
            if _g_used is not None
            else "garanția configurată"
        )
        ui.markdown(
            f"🎟️ **Top {n_simple} bilete simple** ({n_simple} var.{_jk_txt}) ≈ "
            f"{_thousands(n_simple * price)} Lei în variante "
            f"| **Wheel-ul nostru** ({_g_txt}): {len(variants)} var.{_jk_txt} ≈ "
            f"{_thousands(len(variants) * price)} Lei în variante."
        ).classes("text-caption")
        if n_simple < len(variants):
            ui.label(
                f"Primele {n_simple} variante sunt doar un subset; garanția afișată se referă la întregul wheel."
            ).classes("text-caption text-grey")
        for line in _wheel_probability_lines(game, data):
            ui.label(line).classes("text-caption")

        ui.label(
            f"Estimare la tariful standard {price:g} lei/variantă; tragerile speciale pot avea alt tarif. Taxa fizică pe bilet nu este inclusă."
        ).classes("text-caption text-grey")


def _render_cost_foreign(game: str, data: dict, spec) -> None:
    """Costul unui joc din altă țară: tarif și monedă din registru; fără scheme
    reduse (sunt ale Loteriei Române); tarif neverificat → fără cifre."""
    pick = spec.pick_n
    pool_used = int(data.get("pool_size") or len(data.get("hard_core") or []))
    full_vars = math.comb(pool_used, pick) if pool_used >= pick else 0
    variants = data.get("variants") or []
    if spec.price is None:
        ui.markdown(
            f"💡 **{spec.display}: tarif necunoscut** — costul nu se afișează. "
            f"Sistem complet C({pool_used},{pick}) = {full_vars} var.; "
            f"wheel-ul nostru: {len(variants)} var."
        ).classes("text-info")
    else:
        cur = spec.currency
        ui.markdown(
            f"💡 **Cost {spec.display}:** sistem complet C({pool_used},{pick}) = "
            f"{full_vars} var. ≈ {_thousands(full_vars * spec.price, 2)} {cur} în variante"
            + (
                f" | **Wheel-ul nostru:** {len(variants)} var. ≈ "
                f"{_thousands(len(variants) * spec.price, 2)} {cur} în variante"
                if variants
                else ""
            )
            + "."
        ).classes("text-info")
    for line in _wheel_probability_lines(game, data):
        ui.label(line).classes("text-caption")
    if spec.price is not None:
        ui.label(
            f"Estimare la tariful {_fmt_price(spec)} ({spec.price_source}); "
            "taxele pe bilet nu sunt incluse."
        ).classes("text-caption text-grey")


def _lr_schemes_for(game: str, data: dict) -> list:
    """Schemele reduse LR pentru numerele pool-ului rezultatului; [] la alte țări."""
    pool = {int(x) for x in (data.get("hard_core") or [])}
    if not pool or not _game_spec_for(game, data).is_romanian:
        return []
    from covering.lr_schemes import lr_schemes

    return lr_schemes(_game_label_for(game), len(pool))


def _lr_pct(p: float) -> str:
    """Procent cu două zecimale; sub 0,1% două cifre semnificative."""
    value = 100.0 * float(p)
    return f"{value:.2f}%" if value >= 0.1 or value == 0 else f"{value:.2g}%"


def _lr_guarantee_text(scheme) -> str:
    """Ce prinde schema după câte numere ies dintre cele jucate, din profilul exact."""
    from covering.lr_schemes import scheme_guarantees

    parts = []
    for k, row in scheme_guarantees(scheme).items():
        top = max(row)
        sure = max((t for t, v in row.items() if v >= 1.0 - 1e-12), default=None)
        bits = [f"{sure} sigur"] if sure is not None else []
        bits += [
            f"{t}{'' if t == top else '+'} în {_lr_pct(v)}"
            for t, v in row.items()
            if (sure is None or t > sure) and v > 0
        ]
        parts.append(f"{k} {'ieșite ' if not parts else ''}→ " + (", ".join(bits) or "sub 3"))
    return "; ".join(parts)


def _lr_scheme_lines(game: str, data: dict) -> list[str]:
    """Garanția, șansele exacte și comparația cu wheel-ul nostru, în panou și raport."""
    schemes = _lr_schemes_for(game, data)
    if not schemes:
        return []
    from covering.lr_schemes import scheme_odds
    from covering.probability import wheel_hit_probabilities

    gk = _game_label_for(game)
    price = PRICES.get(gk, 8.0)
    pick = _ticket_pick(gk)
    draw_n, max_num = _hypergeo_params(gk)
    ours = None
    variants = data.get("variants") or []
    try:
        if variants:
            ours = wheel_hit_probabilities(
                data.get("hard_core") or [], [v[:pick] for v in variants], draw_n, max_num
            )["ticket"]
    except (ValueError, TypeError):
        ours = None
    lines = []
    for s in schemes:
        odds = scheme_odds(s)
        lines.append(
            f"{s.label}, cele mai multe numere pe o variantă, după câte ies din cele "
            f"{s.n}: {_lr_guarantee_text(s)} (procentul = partea cazurilor)."
        )
        lines.append(
            f"{s.label}, șansa ca cel puțin o variantă să prindă"
            f"{' (urna 1, fără numărul Joker)' if gk == 'joker' else ''}, "
            "la o extragere uniformă: "
            + " · ".join(
                f"{t}{'+' if t < max(odds) else ''} {_five_plus_pct(p)}"
                for t, p in odds.items()
            )
            + "."
        )
        lines.extend(_lr_quality_lines(s, odds, price))
        if ours is not None:
            diff = [
                f"{t}{'+' if t < max(odds) else ''} {_five_plus_pct(p)} față de "
                f"{_five_plus_pct(ours[t])}"
                for t, p in odds.items()
                if abs(p - ours[t]) > 1e-12
            ]
            delta = len(variants) - s.size
            cost = (
                f"cu {count(abs(delta), 'variante', 'variantă')} "
                f"{('mai puțin' if abs(delta) == 1 else 'mai puține') if delta > 0 else 'în plus'} "
                f"({_thousands(abs(delta) * price)} Lei)"
                if delta
                else "tot atâtea variante"
            )
            lines.append(
                f"{s.label} față de wheel-ul nostru ({len(variants)} var.): {cost}; "
                + ("; ".join(diff) if diff else "aceleași șanse la fiecare prag")
                + "."
            )
    _order = (
        "(broșura 6/49 loto.ro, exemplul 3)"
        if gk == "6/49"
        else "(așa arată broșura 6/49, exemplul 3; broșura acestui joc nu spune "
        "ordinea, deci desfășurarea pe pool presupune aceeași regulă)"
    )
    lines.append(
        "La agenție schema se joacă după cod: terminalul pune numerele jucate în ordine "
        f"crescătoare pe pozițiile 1..n ale schemei {_order}. Desfășurările sunt "
        "verificate pe indexul de câștiguri tipărit; pe extrageri uniforme șansele nu "
        "depind de numerele din pool."
    )
    return lines


def _lr_threshold(t: int, odds: dict) -> str:
    return f"{t}{'+' if t < max(odds) else ''}"


def _lr_pct_pair(a: float, b: float) -> tuple[str, str]:
    """Două șanse scrise ca `_five_plus_pct`, cu mai multe cifre când două valori
    diferite ar ieși la fel (5/40 Cod 18 la 5: 0,0452% și 0,0453%, ambele „0.045%”)."""
    x, y = _five_plus_pct(a), _five_plus_pct(b)
    digits = 3
    while x == y and abs(float(a) - float(b)) > 1e-15 and digits <= 10:
        x, y = (f"{Decimal(f'{100.0 * float(v):.{digits}g}'):f}%" for v in (a, b))
        digits += 1
    return x, y


def _lr_join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " și " + items[-1]


def _lr_lost_guarantee(scheme: dict, app: dict, n: int) -> str:
    """Garanția sigură a schemei pe care wheel-ul aplicației n-o mai dă: cel mai
    mare prag t, apoi cele mai puține numere ieșite k (Joker Cod 34: 5 ieșite ->
    4 sigur pe o variantă; aplicația, în 92,86% din cazuri)."""
    lost = [
        (t, k, app[k][t])
        for k, row in scheme.items()
        for t, f in row.items()
        if f >= 1.0 - 1e-12 and k in app and t in app[k] and app[k][t] < 1.0 - 1e-12
    ]
    if not lost:
        return ""
    t, k, f = max(lost, key=lambda x: (x[0], -x[1]))
    return (
        f"Codul dă însă sigur {t} pe o variantă când ies {k} dintre cele {n} numere "
        f"jucate; wheel-ul aplicației, în {100.0 * f:.2f}% din cazuri."
    )


def _lr_quality_lines(s, odds: dict, price: float) -> list[str]:
    """Ce se știe despre schemă față de orice altă așezare cu tot atâtea variante.

    Un prag e optim când șansa schemei atinge marginea superioară sau maximul
    găsit offline (`optimal_thresholds`). O margine neatinsă nu dovedește că se
    poate mai bine: panoul spune „se poate mai bine” numai cu o așezare concretă
    (wheel-ul aplicației din `covering_designs/lr/app_same_size.json` sau cea din
    `optimal_offline.json`), altfel că nu se știe."""
    from covering.lr_schemes import (
        _GAMES,
        _upper_bound,
        app_same_size,
        app_same_size_guarantees,
        improvable_thresholds,
        optimal_thresholds,
        scheme_guarantees,
    )

    _, pick, draws, universe = _GAMES[s.game]
    variants = count(s.size, "variante", "variantă")
    proven = [t for t in odds if t in set(optimal_thresholds(s))]
    # Când biletul are cât extragerea (6/49 la 6, Joker la 5), orice așezare cu
    # tot atâtea variante distincte are aceeași șansă la pragul maxim: acolo
    # „optimă” n-ar spune nimic despre schemă.
    named = [t for t in proven if not (t == pick == draws)]
    lines = []
    if len(proven) == len(odds):
        lines.append(
            f"Schema {s.label} e optimă: nicio altă așezare de {variants} pe {s.n} "
            "numere nu prinde mai des, la niciun prag."
        )
    elif named:
        lines.append(
            f"Schema {s.label} e optimă la {_lr_join([_lr_threshold(t, odds) for t in named])}: "
            f"nicio altă așezare de {variants} pe {s.n} numere nu prinde mai des la "
            f"{'acest prag' if len(named) == 1 else 'aceste praguri'}."
        )
    designs = app_same_size(s)
    same = [(g, p) for g, v, p in designs if v == s.size]
    better = [
        (g, p)
        for g, p in same
        if all(p[t] >= odds[t] - 1e-15 for t in odds)
        and any(p[t] > odds[t] + 1e-15 for t in odds)
    ]
    shown: set[int] = set()
    if better:
        g, p = max(better, key=lambda d: tuple(d[1][t] for t in sorted(odds, reverse=True)))
        diff = []
        gain = 0.0
        for t, q in odds.items():
            if p[t] > q + 1e-15:
                ours, theirs = _lr_pct_pair(p[t], q)
                diff.append(f"{_lr_threshold(t, odds)} {ours} față de {theirs}")
                shown.add(t)
                gain = max(gain, p[t] / q - 1.0 if q > 0 else 1.0)
        if gain >= 0.01:
            lines.append(
                f"La același cost, aplicația prinde mai des decât {s.label}: cu plafonul "
                f"{s.size} și garanția {g}, {'; '.join(diff)}. Variantele aplicației se "
                "joacă la agenție ca variante simple."
            )
        else:
            lines.append(
                f"Cu plafonul {s.size} și garanția {g}, aplicația prinde abia mai des decât "
                f"{s.label}: {'; '.join(diff)}. Sub 1% în plus nu merită {variants} "
                "simple în locul codului."
            )
        lost = _lr_lost_guarantee(
            scheme_guarantees(s), app_same_size_guarantees(s).get(g) or {}, s.n
        )
        if lost:
            lines.append(lost)
    offline = improvable_thresholds(s)
    known = {}
    for t, q in odds.items():
        if t in proven or t in shown:
            continue
        found = [(p[t], f"wheel-ul aplicației cu garanția {g}") for g, p in same if p[t] > q + 1e-15]
        if t in offline:
            found.append((offline[t], "o așezare găsită prin căutare offline"))
        if found:
            known[t] = max(found)
    if known:
        parts = []
        for t, (q, source) in known.items():
            ours, theirs = _lr_pct_pair(q, odds[t])
            parts.append(f"{_lr_threshold(t, odds)} {ours} față de {theirs} ({source})")
        lines.append(f"Cu tot atâtea variante se poate prinde mai des: {'; '.join(parts)}.")
    unknown = [t for t in odds if t not in proven and t not in shown and t not in known]
    if unknown:
        pairs = [
            _lr_pct_pair(odds[t], _upper_bound(s.n, s.size, pick, t, draws, universe))
            for t in unknown
        ]
        lines.append(
            f"La {_lr_join([_lr_threshold(t, odds) for t in unknown])} nu se știe dacă "
            f"se poate mai bine: schema dă {_lr_join([a for a, _b in pairs])}, iar "
            f"marginea superioară pentru orice așezare de {variants} pe {s.n} numere e "
            f"{_lr_join([b for _a, b in pairs])}"
            f"{'' if len(unknown) == 1 else ', în aceeași ordine'}; marginea nu e mereu "
            "atinsă."
        )
    if better:
        return lines
    top = max(odds)
    cheaper = [
        (g, v, p)
        for g, v, p in designs
        if v < s.size and all(p[t] >= odds[t] - 1e-15 for t in odds if t < top)
    ]
    if cheaper:
        g, v, p = min(cheaper, key=lambda d: d[1])
        same_thr = ", ".join(_lr_threshold(t, odds) for t in odds if t < top)
        ours, theirs = _lr_pct_pair(p[top], odds[top])
        lines.append(
            f"Aplicația dă la fel ({same_thr}) cu {count(v, 'variante', 'variantă')}, "
            f"{_thousands((s.size - v) * price)} Lei mai puțin (garanția {g}, fără "
            f"plafon); la {_lr_threshold(top, odds)}: {ours} față de {theirs}."
        )
    return lines


def _render_lr_layouts(game: str, data: dict, schemes) -> None:
    """Variantele fiecărei scheme pe pool-ul de azi, cum le desfășoară terminalul."""
    from covering.lr_schemes import scheme_tickets

    joker = [int(x) for x in (data.get("hard_core_joker") or [])][:1]
    for s in schemes:
        try:
            tickets = scheme_tickets(s, data.get("hard_core") or [])
        except ValueError:
            continue
        tail = f"  +{joker[0]}" if joker else ""
        text = "\n".join(
            f"V{i:>3}: " + ", ".join(map(str, t)) + tail for i, t in enumerate(tickets, 1)
        )
        with ui.expansion(
            f"📋 {s.label} pe pool-ul de azi ({count(s.size, 'variante', 'variantă')})",
            value=False,
        ).classes("w-full"):
            if joker:
                ui.label("Numărul Joker (câmpul 2) intră pe toate variantele schemei.").classes(
                    "text-caption text-grey"
                )
            ui.label(text).classes("font-mono text-sm").style("white-space: pre")


def _wheel_probability_lines(game: str, data: dict) -> list[str]:
    """Same exact, unconditional ticket odds in the UI and the saved report."""
    from covering.probability import wheel_hit_probabilities

    params = _hypergeo_params(game)
    pool, variants = data.get("hard_core") or [], data.get("variants") or []
    if not params or not pool or not variants:
        return []
    draw_n, max_num = params
    pick = _ticket_pick(game)
    is_joker = "joker" in str(game).lower()
    try:
        # Joker fără numărul Urnei 2 (coloana lipsă sau invalidă): 5 numere.
        if any(len(v) not in {pick, pick + int(is_joker)} for v in variants):
            raise ValueError("lungime variantă invalidă")
        odds = wheel_hit_probabilities(
            pool, [v[:pick] for v in variants], draw_n, max_num
        )
    except (ValueError, TypeError):
        return ["Șanse teoretice indisponibile: pool sau variante invalide."]
    lines = [
        "Șanse teoretice la o extragere uniformă, pentru toate variantele generate:",
        " · ".join(
            f"{t}+ numere: în pool {100 * odds['pool'][t]:.3f}%; "
            f"pe cel puțin o variantă {100 * odds['ticket'][t]:.3f}%"
            for t in (3, 4, 5)
            if t <= draw_n
        ),
    ]
    if max_num == 40:
        lines.append(
            "5/40: hiturile se numără pe toate cele 6 numere extrase (varianta are 5); "
            "3 numere nu aduc premiu, de aceea ținta bench-ului la 5/40 este 4+. "
            "Categoria I (5 din primele 5 extrase) nu este modelată separat."
        )
    elif is_joker:
        lines.append("Joker: procentele privesc urna 1; numărul Joker din urna 2 are separat șansa 1/20.")
    lines.append("Acoperirea 100% este o garanție condiționată de numerele prinse în pool, nu șansa de câștig.")
    _g = (data.get("audit") or {}).get("wheel_guarantee_used")
    gap = _guarantee_gap_line(data.get("guarantee") if _g is None else _g, pick, draw_n)
    if gap:
        lines.append(gap)
    return lines


def _guarantee_gap_line(
    guarantee, pick: int, draw_n: int, tail: str = "compară probabilitățile separate"
) -> str:
    """„Garanția g nu asigură g+1”, cu garanția efectivă; "" când nu mai e prag peste ea."""
    try:
        g = int(guarantee)
    except (TypeError, ValueError):
        return ""
    if not 1 <= g < min(int(pick), int(draw_n)):
        return ""
    return f"Garanția {g} nu asigură {g + 1} pe o variantă; {tail}."


def _hypergeo_params(game: str) -> tuple[int, int] | None:
    """(n numere extrase, M univers) pentru baseline-ul random hipergeometric.
    5/40 = (6, 40): hiturile se numără pe toate cele 6 numere extrase.
    Acceptă etichete UI ("6/49", "5/40", "joker") și chei folds ("loto_6_49",
    "joker_urna1"). Urna 2 Joker are baseline exact separat în
    `_random_rate_hypergeo` (top-1 = 1/20)."""
    g = str(game).lower()
    if "urna2" not in g:
        lot = _LOT.lottery_by_id(str(game)) or _LOT.lottery_by_bench_key(str(game))
        if lot is not None and not lot.is_romanian:
            return (lot.draw_n, lot.max_n)
    if "6" in g and "49" in g:
        return (6, 49)
    if "5" in g and "40" in g:
        return (6, 40)  # 5/40: se extrag 6 numere, hiturile se numără pe toate 6
    if "urna2" in g:
        return None
    if "joker" in g:
        return (5, 45)
    return None


def _ticket_pick(game: str) -> int:
    """Numere pe BILET: 6 la 6/49, 5 la 5/40 și Joker (Urna 1)."""
    lot = _LOT.lottery_by_id(str(game)) or _LOT.lottery_by_bench_key(str(game))
    if lot is not None and not lot.is_romanian:
        return lot.pick_n
    g = str(game).lower()
    return 6 if ("6" in g and "49" in g) else 5


def _random_rate_hypergeo(game: str, k_pool: int, t_min: int) -> float | None:
    """Rata PUR aleatoare (hipergeometrică) de „≥t_min numere ghicite" pentru un
    pool de k_pool numere la jocul n-din-M:
        P = Σ_{k=t..n} C(K,k)·C(M−K,n−k) / C(M,n)
    Baseline-ul onest al hazardului — orice rată WF/bench trebuie comparată cu el
    (nu inventăm cifre: totul iese din parametrii jocului). None dacă jocul e
    necunoscut sau K invalid."""
    # Urna 2 Joker are o singură bilă în universul 1..20. Nu o trimitem prin
    # formula hipergeometrică pentru jocurile de pool: baseline-ul top-1 este
    # exact 1/20, iar 3+/4+ sunt imposibile.
    if "urna2" in str(game).lower():
        return 1.0 / 20.0 if int(k_pool) == 1 and int(t_min) <= 1 else 0.0

    params = _hypergeo_params(game)
    if not params:
        return None
    n, M = params
    K = int(k_pool or 0)
    if K <= 0 or K > M:
        return None
    denom = math.comb(M, n)
    return (
        sum(
            math.comb(K, k) * math.comb(M - K, n - k)
            for k in range(int(t_min), min(n, K) + 1)
            if n - k <= M - K
        )
        / denom
    )


def _render_adaptive(audit: dict) -> None:
    ast = audit.get("adaptive_state")
    if not ast:
        return
    event = ast.get("event")
    meta = {
        "normal": ("✅", "#28a745", "Performanță peste baseline"),
        "underperf": ("⚠️", "#ffc107", "Sub baseline (1 hit) — corecție moderată"),
        "catastrophe": (
            "🔥",
            "#dc3545",
            "CATASTROFĂ (0 hituri) — corecție amplificată + diversificare",
        ),
        "regime_reset": ("🚨", "#a020f0", "REGIM RESETAT — ponderi NQI rebalansate"),
    }
    icon, color, msg = meta.get(event, ("ℹ️", "#17a2b8", "Fără date pentru comparație"))
    baseline = ast.get("baseline", 0.0) or 0.0
    rolling = ast.get("rolling_avg")
    _active_bg = "#a020f0" if ast.get("active_mode") == "reset" else "#28a745"
    _active_lbl = "RESET" if ast.get("active_mode") == "reset" else "NORMAL"
    parts = [
        render_html_safe(
            t"<div style='font-weight:bold;margin-bottom:6px;'>{icon} Învățare Adaptivă: {msg} "
            t"<span style='background:{_active_bg};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em;'>{_active_lbl}</span></div>"
        )
    ]
    if event is not None:
        ext = render_html_safe(
            t"Ultima extragere: <strong>{ast.get('last_hits')}</strong> hituri în pool"
        )
        if baseline:
            ext += render_html_safe(
                t" <small style='color:#888;'>(baseline aleator: {baseline})</small>"
            )
        # `ext` e HTML deja randat (<strong>/<small>) — concatenare, NU
        # interpolare, altfel render_html_safe îl escape-uiește a doua oară.
        parts.append("<div>" + ext + "</div>")
    if ast.get("streak_zero", 0) >= 1:
        parts.append(
            render_html_safe(
                t"<div>Streak catastrofe consecutive: <strong>{ast['streak_zero']}</strong></div>"
            )
        )
    if rolling is not None:
        rc = "#dc3545" if rolling < baseline else "#28a745"
        parts.append(
            render_html_safe(
                t"<div>Media rolling (5 extrageri): <strong style='color:{rc};'>{rolling:.2f}</strong></div>"
            )
        )
    if ast.get("missed"):
        _missed = ", ".join(map(str, ast["missed"]))
        parts.append(
            render_html_safe(
                t"<div style='color:#dc3545;'>Numere ratate: {_missed}</div>"
            )
        )
    if ast.get("false_positives"):
        _fp = ", ".join(map(str, ast["false_positives"][:10]))
        parts.append(
            render_html_safe(
                t"<div style='color:#6c757d;'>Prezise dar absente: {_fp}</div>"
            )
        )
    cd = audit.get("catastrophe_diversification")
    if cd and cd.get("injected"):
        inj = ", ".join(f"{n}(gap×{gr})" for n, gr in cd["injected"])
        ev = ", ".join(str(n) for n, _ in cd.get("evicted", []))
        parts.append(
            render_html_safe(
                t"<div style='color:#f4a261;'>💉 Diversificare forțată: injectate <strong>{inj}</strong> "
                t"în locul lui <strong>{ev}</strong></div>"
            )
        )
    hi = audit.get("hard_inversion")
    if hi:
        excl = hi.get("excluded", [])
        _excl_txt = ", ".join(str(n) for n in excl[:20])
        parts.append(
            render_html_safe(
                t"<div style='color:#e63946;'>🚫 Hard Inversion: <strong>{hi.get('n_excluded', len(excl))}</strong> "
                t"numere excluse temporar → {_excl_txt}</div>"
            )
        )
    ui.html(
        render_html_safe(
            t"<div style='margin-top:10px;padding:12px;background:rgba(20,30,50,0.5);border-left:4px solid {color};"
            t"border-radius:8px;font-size:0.9em;'>"
        )
        + "".join(parts)
        + render_html_safe(t"</div>")
    )


def _bench_transform_note(data: dict) -> str:
    """Precizează când ratele scorerului brut nu descriu configurația generată."""
    audit = data.get("audit") or {}
    rp = audit.get("recent_penalty") or {}
    changes = []
    if int(rp.get("draws") or 0) > 0 and (
        rp.get("penalized") or rp.get("penalized_urna2")
    ):
        changes.append(f"penalizarea ultimelor {int(rp['draws'])} extrageri")
    if 0 < float(audit.get("lookback_pct") or 0) < 100:
        changes.append(f"istoric limitat la {float(audit['lookback_pct']):g}%")
    _rb_text = _restrict_base_text(audit)
    if _rb_text:
        changes.append(_rb_text)
    # Numită oricând e activă, ca restrângerea: bench-ul măsoară top-K brut la
    # fiecare pas din istoric, iar limita schimbă pool-ul jucat la mulți dintre
    # ei, chiar dacă pool-ul de azi n-a avut nevoie de nicio înlocuire.
    if int((audit.get("consecutive_limit") or {}).get("requested") or 0) > 0:
        changes.append(
            f"limita de consecutive ({_consecutive_limit_text(audit, details=False)})"
        )
    if not changes:
        return ""
    return (
        "Configurația generată include " + " și ".join(changes) + ". "
        "Clasamentul bench măsoară scorerul fără aceste ajustări; "
        "rezultatele configurației ajustate se verifică în walk-forward."
    )


def _conditional_cover_note(data: dict | None) -> str:
    """Text când 100% e acoperirea „t dacă p”, nu coverul clasic t-din-t."""
    if not isinstance(data, dict):
        return ""
    audit = data.get("audit") or {}
    guarantee = audit.get("wheel_guarantee_used")
    if guarantee is None:
        guarantee = data.get("guarantee")
    condition = audit.get("wheel_condition_used")
    if condition is None:
        condition = data.get("wheel_condition")
    try:
        if (
            condition is not None
            and guarantee is not None
            and int(condition) > int(guarantee)
        ):
            return (
                f"acoperire condițională 100% ({int(guarantee)} dacă {int(condition)}); "
                "hitul pe bilet nu e garantat sub această condiție"
            )
    except (TypeError, ValueError):
        return ""
    return ""


def _five_plus_pct(p) -> str:
    """Șansa 5+ în procente: trei zecimale de la 0,1% în sus, altfel două cifre
    semnificative. La Joker cu un bilet (2 variante) 5+ e 0,00016%, iar cu trei
    zecimale apărea „0.000%”, ca și cum n-ar exista."""
    value = 100.0 * float(p or 0.0)
    # Decimal: două cifre semnificative fără exponent (6 la 6/49, 0,000086%,
    # ieșea „8.6e-05%”).
    return f"{value:.3f}%" if value >= 0.1 else f"{Decimal(f'{value:.2g}'):f}%"


def _thousands(x, decimals: int = 0) -> str:
    """Sume și numărători lungi cu spațiu îngust la mii: „1 216”, nu „1,216”,
    pe care un cititor român îl ia drept 1,2."""
    return f"{float(x):,.{int(decimals)}f}".replace(",", "\u202f")


def _method_from_bench(data: dict | None) -> bool:
    """Pool-ul vine dintr-o decizie bench, nu din rezerva `frequency`.

    Rezerva (`fallback` în `bench_winner`, fără Urna 2) nu a fost aleasă pe
    extragerile validate; un rezultat vechi fără `bench_winner` rămâne pe
    formularea cu decizia, ca înainte."""
    bw = ((data or {}).get("audit") or {}).get("bench_winner") or {}
    return not any(
        (v or {}).get("fallback") for k, v in bw.items() if not str(k).endswith("_urna2")
    )


def _wf_summary(flat, data: dict | None = None, game: str | None = None) -> str | None:
    if not flat:
        return None
    from loto_enterprise.core.walk_forward_adapter import per_draw_hit_summary

    per_draw = per_draw_hit_summary(flat)
    nn = len(per_draw)
    ap = sum(row["pool"] for row in per_draw.values()) / max(nn, 1)
    av = sum(row["best_ticket"] for row in per_draw.values()) / max(nn, 1)
    bp = max(row["pool"] for row in per_draw.values())
    bv = max(row["best_ticket"] for row in per_draw.values())
    try:
        from loto_enterprise.core.walk_forward_adapter import wheel_coverage_summary

        cov = wheel_coverage_summary(flat)
    except Exception:  # noqa: BLE001
        cov = None
    if not cov or not cov["known"]:
        cov_txt = " | acoperire wheel: necunoscută (cache WF vechi)"
    elif cov["below_100"]:
        cov_txt = (
            f" | ⚠️ wheel INCOMPLET la {cov['below_100']}/{cov['known']} extrageri "
            f"(min {cov['min']:.2f}%) → cifrele de pool sunt un PLAFON"
        )
    elif cov["unknown"]:
        cov_txt = f" | acoperire wheel: 100% pe {cov['known']}/{cov['n_draws']} extrageri (restul necunoscute)"
    else:
        _cond = _conditional_cover_note(data)
        cov_txt = f" | {_cond}" if _cond else " | acoperire wheel: 100%"
    p3 = sum(row["pool"] >= 3 for row in per_draw.values())
    p4 = sum(row["pool"] >= 4 for row in per_draw.values())
    p5 = sum(row["pool"] >= 5 for row in per_draw.values())
    b3 = sum(row["best_ticket"] >= 3 for row in per_draw.values())
    b4 = sum(row["best_ticket"] >= 4 for row in per_draw.values())
    b5 = sum(row["best_ticket"] >= 5 for row in per_draw.values())
    jk = [row.get("joker") for row in per_draw.values() if row.get("joker") is not None]
    jk_txt = (
        f" | Joker urna 2: {sum(jk)}/{len(jk)} ({sum(jk) / len(jk) * 100:.1f}%; "
        "aleator 5%)"
        if jk
        else ""
    )
    # Ca panoul: rata la întâmplare a unui pool de aceeași mărime și limita
    # selecției (metoda din decizia de azi a fost aleasă pe aceste extrageri).
    rnd_txt = ""
    sizes = [
        len((getattr(p, "ticket_context", None) or {}).get("hard_core") or ())
        for p in flat
    ]
    k_pool = max(set(sizes) - {0}, key=sizes.count, default=0) or int(
        (data or {}).get("pool_size") or len((data or {}).get("hard_core") or ())
    )
    if game and k_pool:
        rates = [_random_rate_hypergeo(game, k_pool, t) for t in (3, 4, 5)]
        if None not in rates:
            rnd_txt = (
                f" | la întâmplare (pool {k_pool}) 3+/4+/5+: {rates[0] * 100:.1f}%/"
                f"{rates[1] * 100:.2f}%/{_five_plus_pct(rates[2])} pe extragere"
                + (
                    " | metoda e cea din decizia de azi, aleasă pe ferestre bench care"
                    " cuprind aceste extrageri: nu e o validare externă"
                    if _method_from_bench(data)
                    else " | metoda e rezerva frequency (fără decizie bench), nu o"
                    " alegere pe aceste extrageri"
                )
            )
    return (
        f"{count(nn, 'extrageri', 'extragere')} | avg pool={ap:.2f} | avg best bilet={av:.2f} "
        f"| best pool={bp} | best bilet={bv} "
        f"| pool 3+/4+: {p3}/{p4}; bilet 3+/4+: {b3}/{b4} "
        f"| pool 5+: {p5}; bilet 5+: {b5}{cov_txt}{jk_txt}{rnd_txt}"
    )


def _build_report() -> str:
    res = STATE.get("results")
    if not isinstance(res, tuple) or len(res) != 2:
        return "(fără rezultate)"
    rb, _ = res
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "=" * 72,
        "LOTO ENTERPRISE WHEELING — RAPORT COMPLET",
        f"Generat: {ts}",
        "=" * 72,
    ]

    def _dump_pool(d: dict, label: str | None, indent: str = "  ", game: str = "") -> None:
        if label:
            out.append(f"\n{indent}{'-' * 60}\n{indent}{label}\n{indent}{'-' * 60}")
        pool = sorted(int(x) for x in (d.get("hard_core") or []))
        stats = d.get("hard_core_stats") or {}
        eff, req = d.get("pool_size"), d.get("pool_size_requested")
        # Garanția EFECTIV folosită la wheel (audit.wheel_guarantee_used) vs cea
        # CERUTĂ din setări — pot diferi (rezultate vechi/recuperate, engine-ul
        # clampează intern). Același fallback ca în _render_pool_body/_render_cost,
        # ca raportul exportat (raport_complet.txt / dialogul "Raport integral")
        # să nu contrazică panoul afișat pe ecran pentru același pool.
        _g_req = d.get("guarantee")
        _g_used = (d.get("audit") or {}).get("wheel_guarantee_used")
        if _g_used is None:
            _g_used = _g_req
        _wc = (d.get("audit") or {}).get("wheel_condition_used") or d.get(
            "wheel_condition"
        )
        try:
            _wc_txt = (
                f" dacă {int(_wc)}"
                if _wc is not None and int(_wc) != int(_g_used)
                else ""
            )
        except (TypeError, ValueError):
            _wc_txt = ""
        try:
            _g_diff_txt = (
                f" (cerută: {_g_req})"
                if _g_req is not None and int(_g_used) != int(_g_req)
                else ""
            )
        except (TypeError, ValueError):
            _g_diff_txt = ""
        out.append(
            f"{indent}Pool efectiv: {eff}"
            + (f" (cerut {req})" if req and req != eff else "")
            + f" | Garanție: {_g_used}{_wc_txt}{_g_diff_txt}"
            + f" | Variante simple: {len(d.get('variants') or [])}"
            + f" | Extrageri: {d.get('total_draws')}"
        )
        cov = (d.get("context") or {}).get("coverage_pct")
        out.append(
            f"{indent}Acoperire garanție (wheel generat): "
            + (f"{float(cov):.2f}%" if cov is not None else "necunoscută")
        )
        out.extend(f"{indent}{line}" for line in _wheel_probability_lines(game, d))
        out.extend(f"{indent}Schemă LR: {line}" for line in _lr_scheme_lines(game, d))
        # Metoda și nota ei de încredere, ca în panou (Urna 2 cu nota ei).
        _bw = (d.get("audit") or {}).get("bench_winner") or {}
        for _key, _info in _bw.items():
            out.append(
                f"{indent}Metodă ({_key}): {_info.get('method', '?')}"
                + (f" — rezervă: {_info.get('reason') or 'fallback'}" if _info.get("fallback") else "")
            )
        if game and _bw:
            _sp_d = _game_spec_for(game, d)
            _gk_d = (
                _LABEL_TO_FOLDS_GAME.get(_game_label_for(game), "")
                if _sp_d.is_romanian
                else _sp_d.bench_key
            )
            out.extend(
                f"{indent}{note}"
                for note in _decision_notes(_bw, _gk_d, d.get("pool_size_requested") or eff)
            )
        transform_note = _bench_transform_note(d)
        if transform_note:
            out.append(f"{indent}{transform_note}")
        _rp = (d.get("audit") or {}).get("recent_penalty") or {}
        if int(_rp.get("draws") or 0) > 0:
            out.append(
                f"{indent}Penalizare recentă: ultimele {int(_rp['draws'])} extrageri × "
                f"{float(_rp.get('factor', 0.5)):.2f}; numere penalizate: "
                + (
                    ", ".join(
                        str(k)
                        for k in sorted(int(x) for x in (_rp.get("penalized") or {}))
                    )
                    or "niciunul"
                )
                + (
                    "; numărul Joker penalizat: "
                    + ", ".join(
                        str(k) for k in sorted(int(x) for x in _rp["penalized_urna2"])
                    )
                    if _rp.get("penalized_urna2")
                    else ""
                )
            )
        _rb = (d.get("audit") or {}).get("restrict_base") or {}
        _rb_text = _restrict_base_text(d.get("audit"))
        if _rb_text:
            out.append(
                f"{indent}{_rb_text[0].upper() + _rb_text[1:]} (preferință utilizator, "
                f"fără avantaj statistic demonstrat); excluse: "
                + ", ".join(str(n) for n in _rb.get("excluded") or [])
            )
        elif _rb.get("ignored"):
            out.append(f"{indent}Restrângere de bază ignorată: {_rb.get('reason', '')}")
        _cl_text = _consecutive_limit_text(d.get("audit"))
        if _cl_text:
            out.append(
                f"{indent}{_cl_text[0].upper() + _cl_text[1:]} (preferință "
                "utilizator, fără avantaj statistic demonstrat)"
            )
        out.append(
            f"{indent}Nucleu dur (nr(frecvență)): "
            + ", ".join(f"{n}({stats.get(str(n), stats.get(n, '?'))})" for n in pool)
        )
        _cd = _consecutive_pool_diagnostic(d)
        if _cd:
            out.append(f"{indent}{_cd}")
        _cw = _consecutive_pool_warning(pool, d.get("audit"))
        if _cw:
            out.append(f"{indent}⚠️ {_cw}")
        _unplayed = (d.get("audit") or {}).get("pool_numbers_not_on_tickets") or []
        if _unplayed:
            out.append(
                f"{indent}⚠️ Numere din pool care NU apar pe niciun bilet: "
                + ", ".join(str(int(n)) for n in _unplayed)
                + f" — {_unplayed_reason(d)}, dar hiturile de pool le numără, iar "
                "biletele nu le pot prinde."
            )
        if d.get("hard_core_joker"):
            out.append(
                f"{indent}Joker: "
                + ", ".join(str(int(x)) for x in sorted(d["hard_core_joker"]))
            )
        if d.get("p10") is not None:
            out.append(
                f"{indent}Interval p10–p90 (frecvențe pe tot universul, nu pe pool): "
                f"{_fmt_num(d.get('p10'))} – {_fmt_num(d.get('p90'))}"
            )
        au = dict(d.get("audit") or {})
        # Alias de afișare pentru payload-urile istorice: aplicația nu rulează
        # TimesFM; valorile sunt scorurile metodei CPU consemnate în bench_winner.
        if "timesfm_predictions" in au:
            au["ranking_scores_top25"] = au.pop("timesfm_predictions")
        au.pop("pure_bench_mode", None)  # flag legacy: nu descrie penalizarea recentă
        if "pool_selection_note" in au:
            au["pool_selection_note"] = (
                "top-N după scorul final, cu limita de consecutive a utilizatorului"
                if au.get("pool_selection") == "top_score_max_run"
                else "top-N după scorul final, cu departajarea canonică"
            )
        if isinstance(au.get("hit_forecast"), dict):
            forecast = dict(au["hit_forecast"])
            forecast["note"] = (
                "Baseline matematic pentru orizontul n_draws. Mărimile teoretice "
                "de pool pot depăși limita UI; 3 evenimente în medie nu sunt o garanție."
            )
            au["hit_forecast"] = forecast
        if au:
            out.append(f"{indent}--- Audit complet (JSON) ---")
            for line in json.dumps(
                au, indent=2, ensure_ascii=False, default=str
            ).splitlines():
                out.append(f"{indent}{line}")
        vs = d.get("variants") or []
        out.append(f"{indent}--- Variante simple ({len(vs)}) ---")
        # La Joker ultimul element al variantei e NUMĂRUL DE JOKER, nu un al 6-lea
        # număr din urnă (engine-ul îl atașează ciclic în `generate_predictions`).
        # Îl separăm cu „+", ca în UI — altfel raportul îl arăta ca număr obișnuit,
        # deseori duplicând vizual o valoare deja prezentă în variantă.
        _is_jk = bool(d.get("hard_core_joker"))
        for i, v in enumerate(vs, 1):
            if _is_jk and len(v) == 6:
                nums = ", ".join(str(int(x)) for x in v[:5]) + f"  + joker {int(v[-1])}"
            else:
                nums = ", ".join(str(int(x)) for x in v)
            out.append(f"{indent}  V{i}: " + nums)

    for fn, outs in rb:
        out.append(f"\n{'#' * 72}\nFIȘIER: {fn}\n{'#' * 72}")
        for g, raw_data in _ordered_game_items(outs):
            d = _primary_pool_data(raw_data)
            out.append(f"\n=================  JOC: {_game_title(g, d)}  =================")
            _sp = _game_spec_for(g, d)
            if not _sp.is_romanian:
                out.append(f"  Tarif: {_fmt_price(_sp)}")
                if _training_only_note(_sp):
                    out.append(f"  ({_training_only_note(_sp)})")
            flat = STATE["retro"].get(f"{fn}_{g}")
            _dump_pool(d, None, game=g)
            wf = _wf_summary(flat, d, g)
            if ((STATE.get("retro_meta") or {}).get(f"{fn}_{g}") or {}).get("decision_moved"):
                out.append(
                    "  Walk-forward: sărit — decizia de bench s-a schimbat după generare."
                )
            if ((STATE.get("retro_meta") or {}).get(f"{fn}_{g}") or {}).get("budget_skipped"):
                out.append(
                    "  Walk-forward: neînceput — bugetul de timp s-a terminat înaintea "
                    "acestui joc."
                )
            if wf:
                # Aceleași avertismente ca panoul: raportul nu prezintă o validare
                # parțială sau amestecată drept completă.
                _wm = (STATE.get("retro_meta") or {}).get(f"{fn}_{g}") or {}
                if _wm.get("partial"):
                    wf += (
                        f" | PARȚIAL: {_wm.get('n_test_draws')} din "
                        f"{_wm.get('n_expected')} extrageri "
                        + (
                            "(lipsesc cele mai noi)"
                            if _wm.get("newest_missing")
                            else "(cele mai recente)"
                        )
                    )
                if _wm.get("decision_changed"):
                    wf += " | decizia s-a schimbat în timpul validării"
                out.append(f"  Walk-forward: {wf}")
    return "\n".join(out)


def _save_report_file() -> None:
    """Scrie raport_complet.txt (atomic) după generare. Îl poți deschide/lipi oricând."""
    try:
        atomic_write_text(REPORT_FILE, _build_report())
    except Exception as exc:  # noqa: BLE001
        logger.warning("save raport: %s", exc)


def _show_report() -> None:
    _save_report_file()
    with ui.dialog() as dlg, ui.card().classes("w-11/12 max-w-3xl"):
        ui.label("Raport integral").classes("text-bold")
        ui.label(
            f"Salvat și în fișier: {REPORT_FILE.name} (în folderul proiectului)"
        ).classes("text-caption text-positive")
        ui.textarea(value=_build_report()).classes("w-full").props(
            "readonly autogrow filled"
        )
        ui.button("Închide", on_click=dlg.close)
    dlg.open()


# Copierea rulează în browser, chiar în click. Safari și iOS scriu în clipboard
# numai cât durează gestul utilizatorului; un `ui.run_javascript` trimis de server
# după click ajunge prea târziu și e refuzat. Serverul primește doar rezultatul,
# pentru mesaj. `navigator.clipboard` există doar pe pagini sigure
# (localhost/HTTPS); pe adresa de rețea (http://192.168...) se folosește
# `execCommand("copy")`, cu textarea pusă în dialog: capcana de focus a
# dialogului Quasar ar anula o selecție făcută în afara lui.
_COPY_JS = """(e) => {
  const text = __TEXT__;
  const host = (e && e.target && e.target.closest && e.target.closest(".q-card")) || document.body;
  const viaTextarea = () => {
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.opacity = "0";
    host.appendChild(box);
    box.select();
    box.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (err) {}
    box.remove();
    return ok;
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => emit(true), () => emit(viaTextarea()));
  } else {
    emit(viaTextarea());
  }
}"""


def _copy_js(text: str) -> str:
    return _COPY_JS.replace("__TEXT__", json.dumps(text))


def _copy_feedback(e) -> None:
    if e.args is True:
        ui.notify("📋 Numerele au fost copiate.", type="positive")
    else:
        ui.notify(
            "Browserul nu a permis copierea; selectați numerele din fereastră.",
            type="warning",
        )


def _full_ticket_summary(t: dict, game: str) -> str:
    """Linia de sub variante: câte, acoperirea garanției și costul."""

    n = len(t["variants"])
    _lot = _LOT.lottery_by_id(str(game))
    if _lot is not None and not _lot.is_romanian:
        cost_txt = (
            "tarif necunoscut"
            if _lot.price is None
            else f"≈ {n * _lot.price:.2f} {_lot.currency}"
        )
    else:
        cost_txt = f"≈ {n * PRICES.get(game, 0.0):.0f} Lei"
    slip = "biletului" if t["tickets"] == 1 else "biletelor"
    head = f"{n}/{count(t['requested'], 'variante')} ({t['tickets']} × {t['per_ticket']})"
    text = (
        f"{head} · "
        f"acoperire garanție {t['guarantee']} pe cele {count(len(t['pool']), 'numere')} "
        f"ale {slip}: {t['coverage']:.2f}%"
    )
    upper = t.get("upper_coverage")
    if upper and t.get("refined"):
        # Garanția era completă înainte de umplere; variantele au fost apoi
        # rearanjate toate, cu acoperirea păstrată, pentru grupele următoare.
        level, pct = upper
        text += (
            f" · garanția e completă cu toate cele {n}, rearanjate pentru "
            f"grupele de {level}: acoperite {pct:.2f}%"
        )
    elif upper:
        level, pct = upper
        text += (
            f" · garanția e completă cu {count(t['guarantee_variants'], 'variante', 'variantă')}; "
            f"cu toate cele {n}, grupele de {level} sunt acoperite {pct:.2f}%"
        )
    return text + f" · {cost_txt}"


def _pct(p: float) -> str:
    value = 100.0 * float(p)
    return f"{value:.2f}%" if value >= 1 else f"{value:.3g}%"


def _full_ticket_chances(t: dict, is_joker: bool) -> list[str]:
    """Șansa exactă (extragere uniformă) ca cel puțin o variantă afișată să prindă."""
    ch = t.get("chances") or {}
    ts = ch.get("thresholds") or []
    if not ts:
        return []
    urn = " (Urna 1)" if is_joker else ""

    def fmt(values) -> str:
        return " · ".join(f"{k}+ {_pct(values[k])}" for k in ts)

    return [f"Șansa exactă ca cel puțin o variantă să prindă{urn}: {fmt(ch['shown'])}."]


def _simple_variants_count() -> int:
    """Variantele simple afișate (setarea din sidebar), 1..500; câmp gol -> 10."""
    try:
        n = int(round(float(SETTINGS.get("simple_variants_val"))))
    except (TypeError, ValueError, OverflowError):
        return 10
    return max(1, min(500, n))


# Un singur calcul „Bilet complet” odată: un al doilea clic cât rulează primul
# ar porni încă o recoacere rece pe aceleași geometrii.
_FULL_TICKET_RUN: dict = {"busy": False}


async def _show_full_ticket() -> None:
    """Bilete complete per joc (1-10 bilete × 3/4/2 variante) din pool-ul afișat.

    Biletele se calculează într-un fir (`run.io_bound`), nu pe bucla NiceGUI: la
    pool 16 primul clic costă ~14 s pe trei jocuri, iar un handler sincron oprea
    temporizatoarele, progresul WF și celelalte taburi cât dura."""
    from nicegui import run

    from loto_enterprise.core import full_ticket as _ft
    from loto_enterprise.core.full_ticket import clamp_tickets
    from loto_enterprise.core.lotteries import display_name

    res = STATE.get("results")
    if not isinstance(res, tuple) or len(res) != 2:
        ui.notify("Generează întâi un rezultat; biletul se face din pool-ul lui.")
        return
    if _FULL_TICKET_RUN["busy"]:
        ui.notify("⏳ Biletele se calculează deja.")
        return
    rb, _ = res
    tickets = clamp_tickets(SETTINGS.get("full_ticket_count_val"))
    items = []
    for _fn, outs in rb:
        for g, raw in _ordered_game_items(outs):
            _data = _primary_pool_data(raw)
            _spec = _game_spec_for(g, _data)
            # România: eticheta de dinainte; străin: id-ul din registru.
            game = _game_label_for(str(g)) if _spec.is_romanian else _spec.game_id
            items.append((game, _data, _spec))
    _FULL_TICKET_RUN["busy"] = True
    wait = ui.notification("⏳ Calculez biletele…", spinner=True, timeout=None)
    try:
        built = await run.io_bound(
            lambda: [_ft.build_full_ticket(game, d, tickets) for game, d, _s in items]
        )
    finally:
        _FULL_TICKET_RUN["busy"] = False
        wait.dismiss()
    if built is None:  # aplicația se oprește
        return
    copy_lines: list[str] = []
    with ui.dialog() as dlg, ui.card().classes("w-11/12 max-w-2xl"):
        title = "un bilet fizic" if tickets == 1 else f"{tickets} bilete fizice"
        ui.label(f"🎟️ Bilet complet ({title} pe joc)").classes("text-bold")
        ui.label(
            "Variantele se aleg din pool-ul afișat, potrivit automat după "
            "clasamentul metodei când e prea mic sau prea mare pentru bilete. "
            "Acoperirea e cea a acestor variante, nu a wheel-ului complet; "
            "nu e o șansă de câștig."
        ).classes("text-caption")
        # Nu ui.scroll_area: NiceGUI îi fixează înălțimea la 16rem (256px).
        with ui.column().classes("w-full no-wrap gap-1").style(
            "max-height:65vh; overflow-y:auto"
        ):
            for (game, _data, _spec), t in zip(items, built):
                lottery = display_name(game)  # = _game_title(g)
                ui.separator()
                ui.label(lottery).classes("text-bold")
                if _training_only_note(_spec):
                    ui.label(f"ℹ️ {_training_only_note(_spec)}").classes(
                        "text-caption text-warning"
                    )
                if t.get("error"):
                    ui.label(f"⚠️ {t['error']}").classes("text-warning")
                    continue
                if t.get("note"):
                    ui.label(f"ℹ️ {t['note']}").classes("text-caption text-info")
                if copy_lines:
                    copy_lines.append("")
                copy_lines.append(lottery)
                for i, v in enumerate(t["variants"], 1):
                    if tickets > 1 and (i - 1) % t["per_ticket"] == 0:
                        slip = f"Biletul {(i - 1) // t['per_ticket'] + 1} · {lottery}"
                        ui.label(slip).classes("text-caption text-bold q-mt-xs")
                        copy_lines.append(slip)
                    if t["joker"] is not None:
                        txt = ", ".join(str(n) for n in v[:-1]) + f" +{v[-1]}"
                    else:
                        txt = ", ".join(str(n) for n in v)
                    ui.label(f"V{i}: {txt}").classes("font-mono")
                    copy_lines.append(f"V{i}: {txt}")
                ui.label(_full_ticket_summary(t, game)).classes("text-caption")
                for line in _full_ticket_chances(
                    t, t["joker"] is not None or game == "joker"
                ):
                    ui.label(line).classes("text-caption")
        copy_text = "\n".join(copy_lines)
        with ui.row().classes("w-full justify-end gap-2"):
            copy_btn = ui.button("📋 Copiază numerele").props(
                "color=primary no-caps"
            )
            copy_btn.on("click", _copy_feedback, js_handler=_copy_js(copy_text))
            if not copy_text:
                copy_btn.disable()
            ui.button("Închide", on_click=dlg.close).props("no-caps")
    dlg.open()


# Descrierea unei metode vine din `notes`-ul ei din registry
# (`methods.method_meta`), prin `_method_desc` de mai jos. `_METHOD_DESC` e
# doar un overlay pentru cele doua baseline-uri, care n-au o nota lizibila.
# De ce asa, si nu un dictionar scris de mana cu toate metodele: pana la
# 15.09.2026 exact un astfel de dictionar tinea in viata numele vechilor
# filtre structurale (parity_balance, sum_affinity s.a.) la mult timp dupa
# stergerea lor, iar cele 50 de metode noi nu aveau nicio descriere. O a doua
# lista, oricat de corecta azi, ramane iar in urma la prima metoda adaugata.
# Descriere lizibilă per metodă — afișată lângă scorerul de generare.
# Overlay scurt pentru baseline-uri; restul vine din notele registry-ului
# (METHODS), ca să nu rămână nume moarte sau claim-uri de performanță.
_METHOD_DESC = {
    "frequency": "euristică simplă · frecvență recentă ponderată",
    "random": "baseline aleator (prag de referință)",
}


def _method_desc(name: str) -> str:
    """Textul de lângă metodă: overlay UI, altfel notele din registry."""
    overlay = _METHOD_DESC.get(name)
    if overlay:
        return overlay
    try:
        from loto_enterprise.benchmark.methods import method_meta

        meta = method_meta(name)
        notes = str(meta.get("notes") or "").strip()
        if notes and meta.get("available", True):
            return notes
    except Exception:  # noqa: BLE001
        pass
    return ""


def _consecutive_pool_diagnostic(data: dict) -> str:
    """Describe actual runs in the played pool and its recorded raw selection.

    The sidebar describes the next generation, not this result. Legacy payloads
    without the raw stage cannot reconstruct it from a rounded top-25 ranking.
    """
    from loto_enterprise.core.ranking import longest_consecutive_run

    pool = data.get("hard_core")
    if not isinstance(pool, (list, tuple)) or not pool:
        return ""
    try:
        played_run = longest_consecutive_run(pool)
    except (TypeError, ValueError):
        return ""
    if not played_run:
        return ""
    unit = "număr" if played_run == 1 else "numere"
    parts = [f"Secvența consecutivă maximă în pool-ul jucat: {played_run} {unit}."]
    audit = data.get("audit") or {}
    raw = (audit.get("pipeline_stages") or {}).get("1_nqi_raw")
    if isinstance(raw, (list, tuple)) and raw:
        try:
            raw_run = longest_consecutive_run(raw)
        except (TypeError, ValueError):
            raw_run = 0
        if raw_run:
            raw_unit = "număr" if raw_run == 1 else "numere"
            parts.append(f"În selecția inițială după scor: {raw_run} {raw_unit}.")
            if raw_run != played_run:
                parts.append(
                    "Auditul arată și selecția inițială; variantele sistemului folosesc pool-ul jucat."
                )
    requested = data.get("max_consecutive_run")
    if requested is None:
        requested = (audit.get("consecutive_limit") or {}).get("requested")
    try:
        is_off = requested is not None and int(requested) == 0
    except (TypeError, ValueError):
        is_off = False
    if is_off:
        parts.append("Limita de consecutive era oprită la generarea acestui rezultat.")
    return " ".join(parts)


def _consecutive_pool_warning(pool, audit: dict | None = None) -> str | None:
    """Avertisment dacă tot pool-ul e un interval fără găuri (ex. Joker 18–28).

    Calculat din numere, nu din audit — ca să apară și pe rezultate vechi,
    generate înainte de flag-ul din pool_selection. Excepție: pool-ul care ia
    tot intervalul restrâns de utilizator. Blocul vine atunci din interval, iar
    scorerul n-a avut ce alege.
    """
    from loto_enterprise.core.ranking import is_consecutive_block

    nums = [int(x) for x in (pool or [])]
    if not is_consecutive_block(nums, min_size=6):
        return None
    lo, hi = min(nums), max(nums)
    rb = (audit or {}).get("restrict_base") or {}
    if rb and not rb.get("ignored") and (rb.get("min"), rb.get("max")) == (lo, hi):
        return None
    return (
        f"POOL CONSECUTIV — {lo}–{hi} ({len(nums)} numere la rând). "
        "Asta e degenerare a scorer-ului pe axa 1…N, nu un pattern real."
    )


def _decision_notes(bw: dict, game_key: str, pool_fallback) -> list[str]:
    """Nota de încredere a deciziei, pe fiecare urnă din `bench_winner`.

    Pool-ul jocului (`game_key`) și, la Joker, Urna 2 (top-1), fiecare cu
    intrarea deciziei ei. Intrarea e cea a pool-ului cu care s-a ales metoda
    (`pool_hint`), nu a pool-ului efectiv: restrângerea bazei îl poate micșora.
    La rezervă nota lipsește (motivul e afișat deja): ar descrie o metodă care
    nu a produs pool-ul.
    """
    if not game_key:
        return []
    keys = [game_key] + [k for k in bw if k != game_key and str(k).endswith("_urna2")]
    notes = []
    for key in keys:
        info = bw.get(key) or {}
        method = info.get("method")
        if info.get("fallback"):
            continue
        hint = info.get("pool_hint") or (1 if info.get("single_pick") else pool_fallback)
        entry = _decision_entry(key, int(hint or SETTINGS.get("pool_size_val") or 10))
        now = _decision_entry_method(entry, key) if entry and method else method
        prefix = ("Urna 2: " if key.endswith("_urna2") else "Urna 1: ") if len(keys) > 1 else ""
        if now != method:
            # Nota de încredere ar fi a altei metode decât cea care a dat pool-ul.
            notes.append(
                f"ℹ️ {prefix}Decizia de bench s-a schimbat după generare (Re-Bench "
                "terminat sau țintă 3+/4+ schimbată): acum alege "
                f"{now or 'altă metodă'} la pool {hint}, nu {method}. "
                "Nota ei de încredere nu descrie metoda care a produs acest "
                "pool; generează din nou pentru decizia de acum."
            )
        elif _decision_low_confidence(entry) is True:
            notes.append(
                f"⚠️ {prefix}Decizie low_confidence: nicio metodă n-a bătut random "
                "consistent pe acest pool. Scorer-ul e conservator — diferențele sunt zgomot."
            )
        elif mult := _multiplicity_note(entry):
            notes.append(mult.replace("⚠️ ", f"⚠️ {prefix}", 1))
    return notes


def _render_pool_body(
    fname: str, game: str, data: dict, *, skey_suffix: str = ""
) -> None:
    """Randează pool-ul unic (badges, p10/p90, audit, cost, variante, stages).

    Walk-forward-ul NU se randează aici: statisticile lui apar o singură dată, în
    „📊 Analiză & Clasament" (`_render_analysis_menu` → `_render_hits_4plus`)."""
    _lot_key = _LOT.lottery_by_id(str(game))
    if _lot_key is not None and not _lot_key.is_romanian:
        # Cheie străină: ecoul worker-ului trebuie să confirme jocul.
        if str(data.get("game_id") or "") != _lot_key.game_id:
            ui.label(
                "⚠️ Worker vechi / nepotrivire țară: rezultatul nu confirmă jocul "
                f"{_lot_key.display}."
            ).classes("text-caption text-negative")
        elif _training_only_note(_lot_key):
            ui.label(f"ℹ️ {_training_only_note(_lot_key)}.").classes("text-caption text-warning")
    pool = data.get("hard_core") or []
    stats = data.get("hard_core_stats") or {}
    eff = data.get("pool_size")
    req = data.get("pool_size_requested")
    variants = data.get("variants") or []

    with ui.row().classes("gap-6 items-center"):
        ui.label(
            f"Pool efectiv: {eff}" + (f" (cerut {req})" if req and req != eff else "")
        )
        # Garanția EFECTIV folosită la wheel (audit.wheel_guarantee_used) vs cea CERUTĂ
        # din setări — pot diferi; rezultate vechi n-au cheia → fallback pe setare.
        _g_req = data.get("guarantee")
        _g_used = (data.get("audit") or {}).get("wheel_guarantee_used")
        if _g_used is None:
            _g_used = _g_req
        try:
            _g_diff = _g_req is not None and int(_g_used) != int(_g_req)
        except (TypeError, ValueError):
            _g_diff = _g_used != _g_req
        _wc = (data.get("audit") or {}).get("wheel_condition_used") or data.get(
            "wheel_condition"
        )
        try:
            _wc_txt = (
                f" dacă {int(_wc)}"
                if _wc is not None and int(_wc) != int(_g_used)
                else ""
            )
        except (TypeError, ValueError):
            _wc_txt = ""
        ui.label(
            f"Garanție: {_g_used}{_wc_txt}"
            + (f" (cerută: {_g_req})" if _g_diff else "")
        )
        ui.label(f"Variante simple: {len(variants)}")
        _rp = (data.get("audit") or {}).get("recent_penalty") or {}
        if int(_rp.get("draws") or 0) > 0:
            _pen = _rp.get("penalized") or {}
            _pen_j = _rp.get("penalized_urna2") or {}
            ui.label(
                f"Penalizare recentă: ultimele {int(_rp['draws'])} extrageri × {float(_rp.get('factor', 0.5)):.2f}"
                f" ({len(_pen)} numere: {', '.join(str(k) for k in sorted(int(x) for x in _pen))})"
                + (
                    f"; Urna 2 (numărul Joker): "
                    f"{', '.join(str(k) for k in sorted(int(x) for x in _pen_j))}"
                    if _pen_j
                    else ""
                )
            ).classes("text-caption")
        _rb = (data.get("audit") or {}).get("restrict_base") or {}
        _rb_text = _restrict_base_text(data.get("audit"))
        if _rb_text:
            ui.label(
                f"{_rb_text[0].upper() + _rb_text[1:]} — preferință personală, "
                "fără avantaj statistic demonstrat"
            ).classes("text-caption text-warning")
        elif _rb.get("ignored"):
            ui.label(
                f"Restrângere de bază ignorată: {_rb.get('reason', '')}"
            ).classes("text-caption text-warning")
        # Acoperirea REALĂ a garanției (set-cover), pe setul FINAL de bilete —
        # 100% = orice grup de `guarantee` numere prinse în pool apare garantat
        # pe cel puțin un bilet. Niciun filtru nu mai elimină bilete DUPĂ wheeling
        # (a doua ramură, pe `audit.anomaly_filter`, nu se mai executa niciodată —
        # engine-ul nu mai scrie cheia), deci cauzele uzuale pentru <100% sunt:
        #   1. limita «Variante maxime»;
        #   2. garanția = câte numere se extrag (cerere degenerată: singurul cover
        #      100% e sistemul complet — 5/40 pool 15 → C(15,5) = 3003 bilete —, iar
        #      un algoritm/fallback poate rămâne fără cover complet).
        #   3. un override de metodă de wheeling care nu găsește cover complet.
        # Nu deducem garanția din «Variante maxime»: 0 scoate plafonul de cost,
        # dar procentul MĂSURAT rămâne sursa de adevăr.
        _cov = (data.get("context") or {}).get("coverage_pct")
        if _wc_txt:
            ui.label(
                f"Lotto design „{_g_used}{_wc_txt}”: garanția se aplică doar când cad "
                f"{int(_wc)} numere din pool; cu {int(_g_used)} numere prinse, hitul pe bilet nu e garantat."
            ).classes("text-caption text-warning")
        if _cov is not None:
            if float(_cov) >= 100.0:
                ui.html(
                    render_html_safe(
                        t"<b style='color:#22c55e'>✅ Acoperire garanție: 100%</b>"
                    )
                )
            else:
                try:
                    _mv = int((data.get("context") or {}).get("max_variants") or 0)
                except (TypeError, ValueError):
                    _mv = 0
                if _mv > 0:
                    reason = (
                        "este activă o limită de variante; 0 elimină plafonul, "
                        "iar acoperirea trebuie reverificată după generare"
                    )
                else:
                    reason = (
                        f"wheel-ul nu a acoperit toate țintele pentru garanția {_g_used} — "
                        "folosește metoda implicită La Jolla sau redu garanția"
                    )
                ui.html(
                    render_html_safe(
                        t"<b style='color:#ef4444'>⚠️ Acoperire garanție: {float(_cov):.2f}%</b> "
                        t"<span style='opacity:.7'>({reason})</span>"
                    )
                )
        else:
            # Necunoscut ≠ 100%: un rezultat vechi (payload fără coverage_pct) nu
            # trebuie să pară acoperit complet doar fiindcă rândul lipsește.
            ui.html(
                render_html_safe(
                    t"<b style='color:#f59e0b'>⚠️ Acoperire garanție: necunoscută</b> "
                    t"<span style='opacity:.7'>(rezultat fără măsurătoare; regenerează)</span>"
                )
            )
        ui.label(f"Extrageri: {data.get('total_draws')}")
        # Timp de scoring (CPU — GPU eliminat complet).
        _au = data.get("audit") or {}
        _sms = (_au.get("performance") or {}).get("score_time_ms")
        if _sms is not None:
            ui.html(
                render_html_safe(
                    t"<b style='color:#f97316'>🖥️ CPU</b> "
                    t"<span style='opacity:.6'>({_fmt_score_time(_sms)})</span>"
                )
            )

    # Metoda câștigătoare folosită de scorer (din bench/best_methods.json)
    bw = (data.get("audit") or {}).get("bench_winner") or {}
    if bw:
        parts = []
        for gkey, info in bw.items():
            m = info.get("method", "?")
            ph = info.get("pool_hint")
            fam = info.get("family", "")
            desc = _method_desc(m)
            tail = ""
            if desc:
                tail += render_html_safe(t" <span style='opacity:.65'>— {desc}</span>")
            meta = ", ".join(x for x in [fam, (f"pool {ph}" if ph else "")] if x)
            if meta:
                tail += render_html_safe(t" <span style='opacity:.45'>[{meta}]</span>")
            _ens = info.get("ensemble") or []
            _n_ens = len(_ens)
            if _n_ens > 1:
                _ens_str = " + ".join(
                    f"{e.get('method')} ({float(e.get('weight', 0)) * 100:.0f}%)"
                    for e in _ens
                )
                tail += render_html_safe(
                    t"<br><span style='opacity:.75;font-size:.85em'>— pool-ul folosește ensemble-ul ACTIV de {_n_ens} metode (după decorelare)</span>"
                    t"<br><span style='opacity:.6;font-size:.85em'>⚖️ ensemble (variance-reduction): {_ens_str}</span>"
                )
                # Cu ensemble >1, pool-ul NU vine dintr-o singură metodă →
                # eticheta onestă e „cap de listă", nu „metoda folosită".
                head = render_html_safe(
                    t"{gkey} → primul membru al ensemble-ului activ: "
                    t"<b style='color:#ff4d4f;font-size:1.05em'>{m}</b>"
                )
            else:
                head = render_html_safe(
                    t"{gkey} → <b style='color:#ff4d4f;font-size:1.05em'>{m}</b>"
                )
            if info.get("single_pick"):
                tail += render_html_safe(
                    t"<br><span style='opacity:.7;font-size:.85em'>Urna 2: benchmark separat "
                    t"top-1 (1/1), cu baseline random 5%.</span>"
                )
            # Rezerva se spune și pe Urna 2: altfel `frequency` apărea drept câștigător.
            if info.get("fallback"):
                _why = info.get("reason") or "fallback"
                tail += render_html_safe(
                    t"<br><span style='opacity:.7;font-size:.85em'>fallback: {_why}</span>"
                )
            _dropped = info.get("ensemble_dropped") or []
            if _dropped:
                _dparts = []
                for d in _dropped:
                    if isinstance(d, dict):
                        nm = d.get("method") or "?"
                        reason = d.get("reason") or ""
                        vs = d.get("vs")
                        r = d.get("r")
                        extra = ""
                        if reason and reason not in ("correlated", "anticorrelated"):
                            extra += f" ({reason})"
                        elif vs:
                            extra += f" vs {vs}"
                        if r is not None:
                            try:
                                extra += f", r={float(r):.2f}"
                            except (TypeError, ValueError):
                                extra += f", r={r}"
                        _dparts.append(f"{nm}{extra}")
                    else:
                        _dparts.append(str(d))
                _dstr = "; ".join(_dparts)
                tail += render_html_safe(
                    t"<br><span style='opacity:.6;font-size:.85em'>săriți la generare (corelație/plat): {_dstr}</span>"
                )
            parts.append(head + tail)
        ui.html(
            render_html_safe(t"🎯 Metodă folosită la generare: ") + "<br>".join(parts)
        ).classes("text-caption")
        _spec_p = _game_spec_for(game, data)
        _gk_pool = (
            _LABEL_TO_FOLDS_GAME.get(_game_label_for(game), "")
            if _spec_p.is_romanian
            else _spec_p.bench_key
        )
        for _note in _decision_notes(bw, _gk_pool, req or eff):
            ui.label(_note).classes("text-caption text-warning")
    else:
        ui.label(
            "🎯 Metodă scorer: fallback implicit (fără decizie bench disponibilă)"
        ).classes("text-caption text-grey")

    # Frecvența din paranteză e numărată pe istoricul folosit la generare: cu
    # lookback, doar pe fereastra lui, nu pe tot CSV-ul.
    _lb = float((data.get("audit") or {}).get("lookback_pct") or 0)
    _where = f"în ultimele {_lb:g}% din istoric" if 0 < _lb < 100 else "în istoric"
    ui.label(
        f"Nucleu dur (pool) — în paranteză, de câte ori a ieșit numărul {_where}:"
    ).classes("text-bold mt-2")
    _badges(pool, stats)
    _cd = _consecutive_pool_diagnostic(data)
    if _cd:
        ui.label(_cd).classes("text-caption text-grey mt-1")
    _cw = _consecutive_pool_warning(pool, data.get("audit"))
    if _cw:
        ui.label(f"⚠️ {_cw}").classes("text-bold text-negative mt-1")
    _cl_text = _consecutive_limit_text(data.get("audit"))
    if _cl_text:
        ui.label(
            f"🔗 {_cl_text[0].upper() + _cl_text[1:]}. Preferință de compoziție, "
            "fără avantaj statistic demonstrat."
        ).classes("text-caption text-info mt-1")
    _unplayed = (data.get("audit") or {}).get("pool_numbers_not_on_tickets") or []
    if _unplayed:
        ui.label(
            "⚠️ Nu apar pe niciun bilet: "
            + ", ".join(str(int(n)) for n in _unplayed)
            + f" — {_unplayed_reason(data)}, dar hiturile de pool le numără, iar "
            "biletele nu le pot prinde."
        ).classes("text-caption text-warning mt-1")
    if data.get("hard_core_joker"):
        ui.label("Joker:").classes("text-bold mt-1")
        _badges(data.get("hard_core_joker"), data.get("hard_core_joker_stats"))

    if data.get("p10") is not None:
        ui.label(
            f"Interval p10–p90 (frecvențe pe tot universul, nu pe pool): "
            f"{_fmt_num(data.get('p10'))} – {_fmt_num(data.get('p90'))}"
        ).classes("text-caption")

    audit = data.get("audit") or {}
    if audit:
        _render_audit(audit)
        _render_adaptive(audit)

    _render_cost(game, data)

    if variants:
        is_jk = "joker" in game.lower()
        skey = f"{fname}_{game}{skey_suffix}"
        show_all = STATE["show_all"].get(skey, False)
        with ui.expansion(f"Variante simple ({len(variants)})", value=False).classes(
            "w-full"
        ):
            n_show = _simple_variants_count()
            shown = variants if show_all else variants[:n_show]
            for i, v in enumerate(shown, 1):
                if is_jk and len(v) == 6:
                    nums = ", ".join(str(int(x)) for x in v[:5]) + f"  +{int(v[-1])}"
                else:
                    nums = ", ".join(str(int(x)) for x in v)
                ui.html(
                    render_html_safe(
                        t"<span style='color:#6b7280;font-weight:600'>V{i:>3}:</span> "
                        t"<span style='color:#e5e7eb'>{nums}</span>"
                    )
                ).classes("font-mono text-sm")
            if len(variants) > n_show:

                def _toggle(k=skey):
                    STATE["show_all"][k] = not STATE["show_all"].get(k, False)
                    results_panel.refresh()

                ui.button(
                    "🔼 Ascunde" if show_all else f"🔽 Arată toate ({len(variants)})",
                    on_click=_toggle,
                ).props("flat dense")

    if audit:
        _render_stages(audit)


@ui.refreshable
def wf_progress_panel() -> None:
    """Progres walk-forward, SEPARAT de results_panel: tick-ul (1s) refreshează DOAR
    asta, nu tot bundle-ul de rezultate — altfel expansion-urile deschise de user
    (ex. 🏆 Clasament bench, Variante, Pipeline) s-ar reseta/închide la fiecare poll."""
    if not STATE.get("wf_status"):
        return
    _wfp = float(STATE.get("wf_progress") or 0.0)
    # ETA walk-forward: estimare liniară din progres (elapsed × (1-p)/p), PLAFONATĂ
    # la bugetul de timp rămas (WF_TOTAL_BUDGET_S e deadline DUR — estimarea liniară
    # arăta „~14m rămas" când bugetul mai permitea doar câteva minute).
    _eta = ""
    _ws = STATE.get("wf_start")
    if _ws and 0.02 < _wfp < 1.0:
        _rem = (time.time() - _ws) * (1.0 - _wfp) / _wfp
        _dl = STATE.get("wf_deadline")
        if _dl:
            _budget_left = max(0.0, float(_dl) - time.time())
            if _rem > _budget_left:
                _eta = (
                    f"  ·  rămas ≤{_fmt_dur(_budget_left)} (buget; estimare liniară "
                    f"~{_fmt_dur(_rem)} → validarea poate ieși PARȚIALĂ)"
                )
            else:
                _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
        else:
            _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
    if not STATE.get("wf_running"):
        # Text final („anulat"/„eșuat") fără bară și ETA: WF nu mai rulează.
        ui.label(STATE["wf_status"]).classes("text-warning")
        return
    # Rândul de stare are procentul jocului curent; bara, pe cel al tuturor
    # jocurilor. Timpul rămas apare o singură dată, lângă total.
    ui.label(STATE["wf_status"]).classes("text-info")
    ui.linear_progress(value=_wfp, show_value=False).props(
        "instant-feedback rounded"
    ).classes("w-full")
    ui.label(f"Total walk-forward, toate jocurile: {int(_wfp * 100)}%" + _eta).classes(
        "text-caption text-info"
    )


@ui.refreshable
def results_panel() -> None:
    wf_progress_panel()

    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return

    elapsed = ""
    if STATE.get("job_elapsed") is not None:
        elapsed = f" (generare: {_fmt_dur(STATE['job_elapsed'])}"
        if STATE.get("wf_elapsed") is not None:
            elapsed += f" · total cu walk-forward: {_fmt_dur(STATE['wf_elapsed'])}"
        elif STATE.get("wf_running"):
            elapsed += " · walk-forward încă rulează"
        elapsed += ")"
    with ui.row().classes("items-center gap-3 mt-2"):
        ui.label(f"Rezultate{elapsed}").classes("text-h6")
        ui.button("📋 Raport integral", on_click=_show_report).props("flat dense")

    _render_results_bundle(results[0])
