"""Results / pool / cost rendering."""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime as _dt, timezone as _tz

import pandas as pd
from nicegui import ui

from ui_runtime import *
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

# Scheme reduse oficiale Loteria Română: (cod, n_variante) per (joc, pool_size)
LR_SCHEMES = {
    "6/49": {
        9: [("Cod 48", 12)],
        10: [("Cod 49", 15), ("Cod 50", 30)],
        11: [("Cod 56", 66)],
        12: [("Cod 57", 22), ("Cod 58", 132)],
        16: [("Cod 59", 112)],
    },
    "5/40": {
        7: [("Cod 15", 9)],
        8: [("Cod 16", 21)],
        9: [("Cod 17", 30)],
        10: [("Cod 18", 51)],
    },
    "joker": {
        7: [("Cod 45", 5)],
        8: [("Cod 35", 6)],
        9: [("Cod 34", 9)],
        10: [("Cod 24", 14)],
        11: [("Cod 15", 22)],
        12: [("Cod 14", 38)],
    },
}
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
        "3. Anti-Sequence (dezactivat)",
        "#f59e0b",
        "Filtru anti-secvență ELIMINAT — pool-ul rămâne decizia scorerului.",
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


def _fmt_g_range(g) -> str:
    if g is None:
        return "—"
    if isinstance(g, (list, tuple)) and len(g) >= 2:
        return f"[{_fmt_num(g[0])}, {_fmt_num(g[1])}]"
    return str(g)


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
    gk = _game_label_for(game)
    price = PRICES.get(gk, 8.0)
    draw_n = 6 if gk == "6/49" else 5
    pool_used = int(data.get("pool_size") or len(data.get("hard_core") or []))

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
        f"≈ {full_cost:,.0f} Lei în variante"
    )
    if gk in LR_SCHEMES and pool_used in LR_SCHEMES[gk]:
        parts = []
        for code, base in LR_SCHEMES[gk][pool_used]:
            parts.append(
                f"**{code}** ({base} var.{_jk_txt} ≈ {base * price:,.0f} Lei în variante)"
            )
        ui.markdown(
            f"💡 **Scheme reduse oficiale la agenție** ({pool_used} nr.): "
            + " sau ".join(parts)
            + f"\n\n*({_full_lbl} — toate combinațiile, exhaustiv)*"
        ).classes("text-info")
        # Garanția schemelor „Cod NN" NU e documentată nicăieri în proiect (doar codul
        # și numărul de variante) → nu o putem afirma. Fără avertisment, utilizatorul
        # poate crede că cele 15 variante de la „Cod 49" au aceeași garanție ca cele
        # 21 ale wheel-ului nostru (care ESTE verificată — vezi „Acoperire garanție").
        ui.markdown(
            "⚠️ **Garanția schemelor oficiale nu e documentată în app** (avem doar "
            "codul + numărul de variante). NU presupune că e aceeași cu garanția "
            "configurată aici — verific-o la agenție înainte să compari numărul de "
            "variante cu wheel-ul nostru de mai jos."
        ).classes("text-caption text-orange")
    else:
        ui.markdown(
            f"💡 **Cost la agenție:** fără schemă redusă oficială pentru {pool_used} nr. la "
            f"{game.upper()}. **{_full_lbl}** (toate combinațiile, exhaustiv)."
        ).classes("text-info")

    variants = data.get("variants") or []
    if variants:
        n_simple = min(10, len(variants))
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
            f"{n_simple * price:,.0f} Lei în variante "
            f"| **Wheel-ul nostru** ({_g_txt}): {len(variants)} var.{_jk_txt} ≈ "
            f"{len(variants) * price:,.0f} Lei în variante."
        ).classes("text-caption")
        if n_simple < len(variants):
            ui.label(
                "Primele 10 variante sunt doar un subset; garanția afișată se referă la întregul wheel."
            ).classes("text-caption text-grey")
        for line in _wheel_probability_lines(game, data):
            ui.label(line).classes("text-caption")

        ui.label(
            f"Estimare la tariful standard {price:g} lei/variantă; tragerile speciale pot avea alt tarif. Taxa fizică pe bilet nu este inclusă."
        ).classes("text-caption text-grey")


def _wheel_probability_lines(game: str, data: dict) -> list[str]:
    """Same exact, unconditional ticket odds in the UI and the saved report."""
    from covering.probability import wheel_hit_probabilities

    params = _hypergeo_params(game)
    pool, variants = data.get("hard_core") or [], data.get("variants") or []
    if not params or not pool or not variants:
        return []
    pick, max_num = params
    is_joker = "joker" in str(game).lower()
    try:
        if any(len(v) != pick + int(is_joker) for v in variants):
            raise ValueError("lungime variantă invalidă")
        odds = wheel_hit_probabilities(pool, [v[:pick] for v in variants], pick, max_num)
    except (ValueError, TypeError):
        return ["Șanse teoretice indisponibile: pool sau variante invalide."]
    lines = [
        "Șanse teoretice la o extragere uniformă, pentru toate variantele generate:",
        " · ".join(
            f"{t}+ numere: în pool {100 * odds['pool'][t]:.3f}%; "
            f"pe cel puțin o variantă {100 * odds['ticket'][t]:.3f}%"
            for t in (3, 4)
        ),
    ]
    if max_num == 40:
        all_six = wheel_hit_probabilities(pool, variants, 6, 40)
        lines.append(
            f"5/40 — cel puțin 4 din toate cele 6 numere extrase pe o variantă: "
            f"{100 * all_six['ticket'][4]:.3f}%."
        )
        lines.append(
            "5/40: analiza folosește primele 5 numere extrase (referința categoriei I). "
            "3 numere nu aduc premiu; categoriile II/III folosesc toate cele 6 numere extrase."
        )
    elif is_joker:
        lines.append("Joker: procentele privesc urna 1; numărul Joker din urna 2 are separat șansa 1/20.")
    lines.append("Acoperirea 100% este o garanție condiționată de numerele prinse în pool, nu șansa de câștig.")
    return lines


def _hypergeo_params(game: str) -> tuple[int, int] | None:
    """(n numere extrase, M univers) pentru baseline-ul random hipergeometric.
    Acceptă etichete UI ("6/49", "5/40", "joker") și chei folds ("loto_6_49",
    "joker_urna1"). Urna 2 Joker are baseline exact separat în
    `_random_rate_hypergeo` (top-1 = 1/20)."""
    g = str(game).lower()
    if "6" in g and "49" in g:
        return (6, 49)
    if "5" in g and "40" in g:
        return (5, 40)
    if "urna2" in g:
        return None
    if "joker" in g:
        return (5, 45)
    return None


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


def _wf_summary(flat, data: dict | None = None) -> str | None:
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
            f"(min {cov['min']:.1f}%) → cifrele de pool sunt un PLAFON"
        )
    elif cov["unknown"]:
        cov_txt = f" | acoperire wheel: 100% pe {cov['known']}/{cov['n_draws']} extrageri (restul necunoscute)"
    else:
        _cond = _conditional_cover_note(data)
        cov_txt = f" | {_cond}" if _cond else " | acoperire wheel: 100%"
    p3 = sum(row["pool"] >= 3 for row in per_draw.values())
    p4 = sum(row["pool"] >= 4 for row in per_draw.values())
    b3 = sum(row["best_ticket"] >= 3 for row in per_draw.values())
    b4 = sum(row["best_ticket"] >= 4 for row in per_draw.values())
    return (
        f"{nn} extrageri | avg pool={ap:.2f} | avg best bilet={av:.2f} "
        f"| best pool={bp} | best bilet={bv} "
        f"| pool 3+/4+: {p3}/{p4}; bilet 3+/4+: {b3}/{b4}{cov_txt}"
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
        out.append(
            f"{indent}Nucleu dur (nr(frecvență)): "
            + ", ".join(f"{n}({stats.get(str(n), stats.get(n, '?'))})" for n in pool)
        )
        _cw = _consecutive_pool_warning(pool)
        if _cw:
            out.append(f"{indent}⚠️ {_cw}")
        _unplayed = (d.get("audit") or {}).get("pool_numbers_not_on_tickets") or []
        if _unplayed:
            out.append(
                f"{indent}⚠️ Numere din pool care NU apar pe niciun bilet: "
                + ", ".join(str(int(n)) for n in _unplayed)
                + " — garanția designului rămâne validă, dar hiturile de pool "
                "le numără, iar biletele nu le pot prinde."
            )
        if d.get("hard_core_joker"):
            out.append(
                f"{indent}Joker: "
                + ", ".join(str(int(x)) for x in sorted(d["hard_core_joker"]))
            )
        if d.get("p10") is not None:
            out.append(
                f"{indent}Interval p10–p90 (frecvențe pe tot universul, nu pe pool): "
                f"{_fmt_num(d.get('p10'))} – {_fmt_num(d.get('p90'))} "
                f"(g_range={_fmt_g_range(d.get('g_range'))})"
            )
        au = dict(d.get("audit") or {})
        # Alias de afișare pentru payload-urile istorice: aplicația nu rulează
        # TimesFM; valorile sunt scorurile metodei CPU consemnate în bench_winner.
        if "timesfm_predictions" in au:
            au["ranking_scores_top25"] = au.pop("timesfm_predictions")
        au.pop("pure_bench_mode", None)  # flag legacy: nu descrie penalizarea recentă
        if "pool_selection_note" in au:
            au["pool_selection_note"] = (
                "top-N după scorul final, cu departajarea canonică"
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
            out.append(f"\n=================  JOC: {g.upper()}  =================")
            flat = STATE["retro"].get(f"{fn}_{g}")
            _dump_pool(d, None, game=g)
            wf = _wf_summary(flat, d)
            if wf:
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


def _consecutive_pool_warning(pool) -> str | None:
    """Avertisment dacă tot pool-ul e un interval fără găuri (ex. Joker 18–28).

    Calculat din numere, nu din audit — ca să apară și pe rezultate vechi,
    generate înainte de flag-ul din pool_selection.
    """
    from loto_enterprise.core.ranking import is_consecutive_block

    nums = [int(x) for x in (pool or [])]
    if not is_consecutive_block(nums, min_size=6):
        return None
    lo, hi = min(nums), max(nums)
    return (
        f"POOL CONSECUTIV — {lo}–{hi} ({len(nums)} numere la rând). "
        "Asta e degenerare a scorer-ului pe axa 1…N, nu un pattern real."
    )


def _render_pool_body(
    fname: str, game: str, data: dict, *, skey_suffix: str = ""
) -> None:
    """Randează pool-ul unic (badges, p10/p90, audit, cost, variante, stages).

    Walk-forward-ul NU se randează aici: statisticile lui apar o singură dată, în
    „📊 Analiză & Clasament" (`_render_analysis_menu` → `_render_hits_4plus`)."""
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
            ui.label(
                f"Penalizare recentă: ultimele {int(_rp['draws'])} extrageri × {float(_rp.get('factor', 0.5)):.2f}"
                f" ({len(_pen)} numere: {', '.join(str(k) for k in sorted(int(x) for x in _pen))})"
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
                        t"<b style='color:#ef4444'>⚠️ Acoperire garanție: {float(_cov):.1f}%</b> "
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
            elif info.get("fallback"):
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
        _gk_pool = _LABEL_TO_FOLDS_GAME.get(_game_label_for(game), "")
        if _gk_pool:
            _dec_p = _decision_entry(
                _gk_pool, int(eff or SETTINGS.get("pool_size_val") or 10)
            )
            if _decision_low_confidence(_dec_p) is True:
                ui.label(
                    "⚠️ Decizie low_confidence: nicio metodă n-a bătut random consistent "
                    "pe acest pool. Scorer-ul e conservator — diferențele sunt zgomot."
                ).classes("text-caption text-warning")
    else:
        ui.label(
            "🎯 Metodă scorer: fallback implicit (fără decizie bench disponibilă)"
        ).classes("text-caption text-grey")

    ui.label("Nucleu dur (pool):").classes("text-bold mt-2")
    _badges(pool, stats)
    _cw = _consecutive_pool_warning(pool)
    if _cw:
        ui.label(f"⚠️ {_cw}").classes("text-bold text-negative mt-1")
    _unplayed = (data.get("audit") or {}).get("pool_numbers_not_on_tickets") or []
    if _unplayed:
        ui.label(
            "⚠️ Nu apar pe niciun bilet: "
            + ", ".join(str(int(n)) for n in _unplayed)
            + " — designul își ține garanția fără ele, dar hiturile de pool le "
            "numără, iar biletele nu le pot prinde."
        ).classes("text-caption text-warning mt-1")
    if data.get("hard_core_joker"):
        ui.label("Joker:").classes("text-bold mt-1")
        _badges(data.get("hard_core_joker"), data.get("hard_core_joker_stats"))

    if data.get("p10") is not None:
        ui.label(
            f"Interval p10–p90 (frecvențe pe tot universul, nu pe pool): "
            f"{_fmt_num(data.get('p10'))} – {_fmt_num(data.get('p90'))} "
            f"(g_range={_fmt_g_range(data.get('g_range'))})"
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
            shown = variants if show_all else variants[:10]
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
            if len(variants) > 10:

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
                    f"~{_fmt_dur(_rem)} → jocurile rămase pot ieși PARȚIALE)"
                )
            else:
                _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
        else:
            _eta = f"  ·  rămas ~{_fmt_dur(_rem)}"
    if not STATE.get("wf_running"):
        # Text final („anulat"/„eșuat") fără bară și ETA: WF nu mai rulează.
        ui.label(STATE["wf_status"]).classes("text-warning")
        return
    ui.label(STATE["wf_status"] + _eta).classes("text-info")
    ui.linear_progress(value=_wfp, show_value=False).props(
        "instant-feedback rounded"
    ).classes("w-full")
    ui.label(f"{int(_wfp * 100)}%" + _eta).classes("text-caption text-info")


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
