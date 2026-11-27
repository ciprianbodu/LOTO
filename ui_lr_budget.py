"""Generate and validate the official LR code nearest to the requested total price."""

import asyncio
import logging
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from math import comb, isfinite

from nicegui import run, ui

from covering.lr_budget import (
    ANY_PRIZE, DEFAULT_BUDGET_LEI, FOUR_PLUS, MIN_BUDGET_LEI, SINGLE_SCHEME_MODE, budget_in_bani, budget_in_lei,
    maximum_scheme_budget_bani, recommend_games,
)
from covering.lr_pool_budget import positional_recipe
from covering.lr_pool_ranking import LR_RESTRICTED_RANGE
from covering.lr_refinement_status import merge_refinements, refinement_complete
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, playable_plan_order, recommend_playable_games
from loto_enterprise.core.lr_range_analysis import RANGES, compare_budget_ranges
from loto_enterprise.core.lotteries import games_for_country
from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS
from loto_enterprise.core.ro_text import count, lei_text
from ui_lr_portfolio import _percent, _price
from ui_lr_rebench import lr_rebench_decisions
from ui_shared import report_restricted_range


_OBJECTIVES = {FOUR_PLUS: "4+ pe o variantă", ANY_PRIZE: "Orice premiu"}
LR_RESTRICTED_BASE = f"{LR_RESTRICTED_RANGE[0]}–{LR_RESTRICTED_RANGE[1]}"
LR_RESTRICTED_LABEL = f"Pool restrâns între {LR_RESTRICTED_RANGE[0]} și {LR_RESTRICTED_RANGE[1]}"


def _columns(fields):
    return [
        {"name": key, "field": key, "label": label, "align": "left"}
        for key, label in fields
    ]


def _bench_method(selection):
    method = selection["method"]
    if selection.get("source") == ONLINE_TICKET_EXPERTS:
        return "Frecvență · Urna 2 evaluată separat"
    if selection.get("source") == "lr_rebench":
        detail = " · metodă de rezervă" if selection.get("fallback") else ""
        return f"{method} · din Re-Bench pentru această selecție{detail}"
    if selection.get("source") == "lr_rebench_reference":
        return f"{method} · metoda de referință a pool-ului direct din Re-Bench"
    if selection.get("fallback"):
        reason = "lipsește decizia Re-Bench" if selection.get("no_decision") else "metodă de rezervă"
        return f"{method} ({reason})"
    return f"{method} (Re-Bench)"


def _ticket_validation(plan, result, *, auto_job=None):
    game = plan["game"]
    if (not result or result.get("game") != game or not result.get("history_hash")
            or result["history_hash"] != plan["forecast"].get("history_hash")):
        raise ValueError(f"Biletul pentru {game} și validarea nu au același istoric.")
    summary = result["summary"]
    if not summary.get("objective_evaluated") or not summary.get("evaluated"):
        raise ValueError(f"Validarea pentru {game} nu conține extrageri evaluabile.")
    settings = result["settings"]
    if settings.get("budget_mode") != plan.get("budget_mode"):
        raise ValueError(f"Regula de alegere a codului pentru {game} diferă între validare și bilet.")
    if plan.get("budget_mode") == SINGLE_SCHEME_MODE:
        codes = [[area["code"] for area in ticket] for ticket in plan["tickets"]]
        if (codes != [[result.get("scheme_code")]] or result["scheme_code"] != settings.get("scheme")
                or plan["budget_bani"] != budget_in_bani(settings["budget_lei"])
                or plan["price_bani"] != result.get("price_bani") or plan["variants"] != result.get("variants")):
            raise ValueError(f"Codul, costul sau numărul de variante pentru {game} diferă între validare și bilet.")
    uses_range = auto_job is not None or ("restricted" in settings and settings["restricted"] is not False)
    bounds = report_restricted_range(settings, result)
    actual_bounds = plan["forecast"].get("number_range")
    if (bounds is None or (uses_range and bounds != LR_RESTRICTED_RANGE)
            or (settings.get("restricted") and actual_bounds not in (LR_RESTRICTED_RANGE, list(LR_RESTRICTED_RANGE)))):
        raise ValueError(f"Intervalul validării pentru {game} diferă de intervalul biletului solicitat.")
    evidence = {key: result[key] for key in ("game", "source", "settings", "scheme_code", "history_hash", "summary")}
    if plan.get("budget_mode") == SINGLE_SCHEME_MODE:
        evidence.update(price_bani=result["price_bani"], variants=result["variants"])
    if auto_job is not None:
        compared = auto_job["auto_options_result"]
        matched = ("scheme", "budget_lei", "budget_mode", "objective", "lookback", "dynamic", "hit_target", "joker_size",
                   "inverse", "restricted", "selection_policy")
        if (report_restricted_range(settings, compared) != LR_RESTRICTED_RANGE
                or any(settings.get(key) != auto_job.get(key) for key in matched)
                or result.get("scheme_code") != auto_job["scheme"]
                or compared["inverse"] != auto_job["inverse"] or compared["restricted"] != auto_job["restricted"]
                or compared["fixed_share"] != (auto_job.get("selection_policy") == ONLINE_TICKET_EXPERTS)):
            raise ValueError(f"Validarea pentru {game} nu corespunde opțiunilor Auto ale biletului.")
        receipt = {key: compared[key] for key in ("inverse", "restricted", "fixed_share", "hits", "evaluated",
                                                  "days", "first_date", "last_date", "validation_start")}
        if "restricted_range" in compared:
            receipt["restricted_range"] = compared["restricted_range"]
        evidence["settings"] = {**settings, "auto_options_result": receipt}
    return evidence


def _show_ticket_validation(evidence):
    summary, settings = evidence["summary"], evidence["settings"]
    daily = settings.get("replay_policy") == "daily" or settings.get("selection_policy") == ONLINE_TICKET_EXPERTS
    policy = "selecția refăcută zilnic" if daily else "metoda fixată înaintea testului"
    ui.label(
        f"Validare istorică · {policy} · {_date_label(summary['first_date'])} – {_date_label(summary['last_date'])}"
    ).classes("text-subtitle2 text-weight-bold")
    ui.label(
        f"4+ pe aceeași variantă LR jucată atunci · {_rate_label(summary['ticket_4plus'], summary['evaluated'])} "
        "extrageri. "
        f"4+ în pool · {_rate_label(summary['pool_4plus'], summary['evaluated'])}."
    ).classes("text-body2")
    if settings["objective"] != FOUR_PLUS:
        ui.label(
            f"Obiectiv · {_OBJECTIVES[settings['objective']]} · "
            f"{_rate_label(summary['objective_hits'], summary['objective_evaluated'])} extrageri."
        ).classes("text-body2")
    if missing := summary["rows"] - summary["objective_evaluated"]:
        ui.label(f"Extrageri fără rezultat evaluabil pentru obiectiv · {missing}.").classes("text-caption")


def _ticket_details(plan, *, expanded=False):
    fields = [
        ("ticket", "Bilet"), ("zone", "Zonă"), ("code", "Cod LR"),
        ("pool", "Pool"), ("numbers", "Numere de jucat"),
    ]
    if plan["game"] == "joker":
        fields.append(("joker", "Bila Joker"))
    rows = [
        {
            "id": f"{ticket_number}-{zone}",
            "ticket": ticket_number,
            "zone": chr(ord("A") + zone),
            "code": area["code"],
            "pool": area["pool_size"],
            "numbers": ", ".join(map(str, area["numbers"])) or "—",
            "joker": area.get("joker") or "—",
        }
        for ticket_number, ticket in enumerate(plan["tickets"], start=1)
        for zone, area in enumerate(ticket)
    ]
    with ui.expansion(f"Bilete · {plan['game_name']}", value=expanded).classes("w-full min-w-0"):
        if evidence := plan.get("walk_forward"):
            _show_ticket_validation(evidence)
        if not refinement_complete(plan):
            ui.label("Selecție provizorie. Calculul a folosit bilete de rezervă. La următoarea generare se reia rafinarea.").classes("text-warning")
        if error := plan.get("forecast_error"):
            ui.label(error).props('role="alert"').classes("text-warning")
        if rows:
            _responsive_table(
                fields, rows, row_key="id", page_size=12,
                label=f"Numere de jucat · {plan['game_name']}",
            ).classes("lr-ticket-table")
            if plan.get("template_adjusted"):
                ui.label("Scheme adaptate la pool-urile disponibile.").classes("text-caption")
        if forecast := plan.get("forecast"):
            pool = forecast["pool"]
            excluded = forecast.get("excluded_pool") or []
            inverse = forecast.get("inverse", bool(excluded))
            origin = "Pool din generare" if forecast["generated"] else "Pool calculat cu setările generatorului"
            if "expanded_from" in forecast:
                origin = "Pool LR maxim pentru buget" if forecast["expanded_to_max"] else "Pool pentru codul recomandat"
                origin += f" · de la {forecast['expanded_from']} la {len(pool)} numere"
            else:
                origin += f" · {len(pool)} numere"
            code = ""
            if forecast["code"] is not None:
                same_code = all(row["code"] == forecast["code"] for row in rows)
                prefix = "Cod" if same_code else "Cod de evaluare"
                code = f" · {prefix} {forecast['code']}"
            limits = forecast.get("number_range") or forecast.get("inverse_range")
            if inverse:
                ui.label("Pool normal exclus").classes("text-weight-bold")
                ui.label(", ".join(map(str, excluded))).classes("text-body2")
                eligible = count(forecast["eligible_count"], "numere eligibile", "număr eligibil")
                if limits:
                    eligible += f" între {limits[0]} și {limits[1]} inclusiv"
                origin = f"Pool invers · {count(len(pool), 'numere selectate', 'număr selectat')} din {eligible}"
            elif limits:
                eligible = count(forecast["eligible_count"], "numere eligibile", "număr eligibil")
                origin = f"Pool direct · {count(len(pool), 'numere selectate', 'număr selectat')} din {eligible} între {limits[0]} și {limits[1]} inclusiv"
            ui.label(f"{origin}{code}").classes("text-weight-bold")
            pool_text = ", ".join(map(str, pool))
            if len(rows) != 1 or rows[0]["numbers"] != pool_text:
                ui.label(pool_text).classes("text-body2")
            if excluded_jokers := forecast.get("excluded_jokers"):
                eligible = count(forecast["eligible_joker_count"], "numere eligibile", "număr eligibil")
                ui.label(f"Urna 2 Joker · pool normal exclus · {eligible} după excludere").classes("text-caption")
                ui.label(", ".join(map(str, excluded_jokers))).classes("text-body2")
            if forecast["jokers"]:
                ui.label("Pool Urna 2 · " + ", ".join(map(str, forecast["jokers"]))).classes("text-caption")
            date = f" · ultima extragere {forecast['date']}" if forecast["date"] else ""
            history = count(forecast["rows"], "extrageri folosite", "extragere folosită")
            ui.label(f"{forecast['filename']}{date}" + (f" · {history}" if forecast["rows"] else "")).classes("text-caption")
            if reason := forecast.get("portfolio_unmeasured"):
                ui.label(f"Comparația pe bilete este indisponibilă. {reason}").classes("text-caption text-warning")
            if mismatch := forecast.get("profile_reference_mismatch"):
                urns = ", ".join("urna a doua Joker" if key == "joker_urna2" else "urna principală" for key in mismatch)
                ui.label(
                    f"Re-Bench invers folosea altă referință directă pentru {urns}. "
                    "Metodele au fost recalculate pe complementul pool-urilor directe actuale."
                ).classes("text-caption text-warning")
            selection = forecast["selection"]
            if selection.get("method"):
                if selection.get("source") == ONLINE_TICKET_EXPERTS:
                    days = count(selection["training_days"], "zile evaluate", "zi evaluată")
                    evidence = f"Fixed Share pe bilete · {selection['method']} · {days}"
                elif selection.get("source") == "paid_budget":
                    days = count(selection["validation_days"], "zile evaluate", "zi evaluată")
                    objective = "orice premiu" if selection["portfolio_objective"] == "any_prize" else "4+ pe o variantă"
                    evidence = (
                        f"{selection['method']} · {objective} · {selection['portfolio_hits']} față de "
                        f"{selection['random_portfolio_hits']} pentru martor · {days}"
                    )
                    if selection["fallback"]:
                        evidence += " · frecvență de rezervă"
                elif mismatch:
                    evidence = f"{selection['method']} · recalculată pe complementul actual"
                elif selection.get("source") in ("lr_rebench", "lr_rebench_reference") or not forecast["dynamic"]:
                    evidence = _bench_method(selection)
                elif selection.get("reason") == "insufficient_history":
                    evidence = f"{selection['method']} · istoric prea scurt pentru selecția walk-forward"
                else:
                    evidence = selection["method"]
                    hits = selection.get("hits", {}).get("4")
                    random = selection.get("random_hits", {}).get("4")
                    draws = selection.get("validation_draws")
                    if hits is not None and random is not None and draws:
                        scope = "în pool" if selection.get("hit_scope") == "pool" else "pe o variantă LR"
                        evidence += (
                            f" · walk-forward 4+ {scope} · {hits} față de {random} pentru martorul aleator,"
                            f" din {draws} extrageri"
                        )
                    if selection.get("reason") not in (None, "winner"):
                        evidence += " · metodă de rezervă"
                method_label = "Metoda pool-ului invers" if inverse else "Metoda pool-ului"
                ui.label(f"{method_label} · {evidence}").classes("text-caption")
                if selection.get("source") not in ("lr_rebench", "lr_rebench_reference", ONLINE_TICKET_EXPERTS, "paid_budget"):
                    ui.label("Bilet calculat fără un rezultat Re-Bench LR pentru această selecție și acest istoric.").classes("text-caption")
            bonus = forecast["joker_selection"] or {}
            if bonus.get("method"):
                if bonus.get("source") == "paid_budget":
                    bonus_method = f"{bonus['method']} · evaluată împreună cu urna principală"
                else:
                    bonus_method = f"{bonus['method']} · recalculată" if mismatch else _bench_method(bonus)
                ui.label(f"Urna 2 Joker · {bonus_method}").classes("text-caption")


def _recommended_codes(tickets):
    codes = Counter(
        (area["code"], area["pool_size"])
        for ticket in tickets
        for area in ticket
    )
    labels = []
    for (code, pool_size), zones in codes.items():
        label = f"Cod {code} · {count(pool_size, 'numere')} în pool"
        labels.append(f"{zones} × {label}" if zones > 1 else label)
    return " + ".join(labels)


def _budget_difference(plan):
    difference = plan["price_bani"] - plan["budget_bani"]
    if not difference:
        return "exact la suma aleasă"
    side = "peste" if difference > 0 else "sub"
    return f"{_price(abs(difference))} {side} suma aleasă"


def _render_recommendations(records, budget_lei, objective):
    if not records or not records[0]["tickets"]:
        ui.label("Scheme LR indisponibile pentru pool-urile și bugetul ales.").classes("text-warning")
        for plan in records:
            _ticket_details(plan, expanded=True)
        return
    first = records[0]
    single = first.get("budget_mode") == SINGLE_SCHEME_MODE
    with ui.card().props("flat bordered").classes("w-full border-l-4 border-positive gap-1"):
        ui.label(f"Sugestie pentru {lei_text(budget_lei)} · {first['game_name']}").classes("text-h6 text-weight-bold")
        ui.label(_recommended_codes(first["tickets"])).classes("text-subtitle2 text-weight-bold")
        ui.label(
            f"{_price(first['price_bani'])} cu taxă · "
            f"{count(len(first['tickets']), 'bilete', 'bilet')} · "
            f"{count(first['variants'], 'variante', 'variantă')}"
        ).classes("text-body2")
        if single:
            ui.label(_budget_difference(first)).classes("text-body2 text-weight-bold")
        ui.label(
            f"Șansă teoretică la o extragere uniformă · {_OBJECTIVES[objective]} · "
            f"{_percent(first['attained'], first['denominator'])}"
        ).classes("text-caption")
    _ticket_details(first, expanded=True)
    for plan in records[1:]:
        _ticket_details(plan, expanded=bool(plan.get("forecast_error")))

    games = {plan["game"] for plan in records}
    if objective == FOUR_PLUS:
        rule = "Se numără cel puțin patru numere principale pe aceeași variantă."
        if "5/40" in games:
            rule += " La 5/40 se iau în calcul toate cele șase bile extrase."
    else:
        rules = {
            "6/49": "La 6/49 premiile încep la trei numere pe o variantă.",
            "5/40": "La 5/40 premiile încep la patru numere pe o variantă.",
            "joker": "La Joker ajung trei numere principale sau un număr principal împreună cu bila Joker.",
        }
        rule = " ".join(text for game, text in rules.items() if game in games)
    ui.label(rule).classes("text-caption")

    fields = [
        ("game", "Joc"), ("codes", "Cod LR și pool"), ("tickets", "Bilete"),
        ("variants", "Variante"),
        ("price", "Total cu taxă"), ("chance", "Șansă teoretică"),
    ]
    if single:
        fields.append(("difference", "Diferență față de suma aleasă"))
    else:
        fields.append(("bound", "Limită superioară"))
    rows = []
    for plan in records:
        upper = plan["upper"]
        playable = bool(plan["tickets"])
        rows.append({
            "difference": _budget_difference(plan) if single and playable else "—",
            "game": plan["game_name"],
            "codes": _recommended_codes(plan["tickets"]) if playable else "—",
            "tickets": len(plan["tickets"]),
            "variants": plan["variants"], "price": _price(plan["price_bani"]) if playable else "—",
            "chance": _percent(plan["attained"], plan["denominator"]) if playable else "—",
            "bound": "Maxim demonstrat" if playable and plan["optimal"] else (
                ("Cea mai bună soluție găsită" + (f" · limită {_percent(upper, plan['denominator'])}"
                                                if upper is not None else "")) if playable else "—"
            ),
        })
    if len(records) > 1:
        _responsive_table(fields, rows, row_key="game", label="Compararea jocurilor pentru suma aleasă")
    elif not single and rows[0]["bound"] != "—":
        ui.label(f"Demonstrația pentru acest buget · {rows[0]['bound']}").classes("text-caption")


def _range_label(limits):
    return f"{limits[0]}–{limits[1]}" if limits else "Baza completă"


def _date_label(value):
    if not value:
        return "N/D"
    return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")


def _number_label(value):
    return "N/D" if value is None else f"{value:g}".replace(".", ",")


def _rate_label(successes, evaluated):
    if successes is None or not evaluated:
        return "N/D"
    rate = f"{100 * successes / evaluated:.1f}".replace(".", ",")
    return f"{successes} / {evaluated} ({rate}%)"


def _pair_label(draws, days):
    return f"{_number_label(draws)} / {_number_label(days)}"


def _responsive_table(fields, rows, *, row_key="base", page_size=0, label=None):
    table = ui.table(
        columns=_columns(fields), rows=rows, row_key=row_key, pagination={"rowsPerPage": page_size},
    ).props("dense flat wrap-cells").classes("w-full min-w-0 lr-range-table")
    if not page_size or len(rows) <= page_size:
        table.props("hide-bottom")
    if label:
        table.props(f'aria-label="{label}"')
    table.add_slot("body-cell", """
        <q-td :props="props" :class="{'lr-ticket-numbers': props.col.name === 'numbers'}">
            <span class="lt-sm lr-range-cell-label">{{ props.col.label }}</span>
            <span>{{ props.value }}</span>
        </q-td>
    """)
    return table


def _range_comparison(report, game_name, selected, select_base, forced, *, expanded):
    best = _range_label(report.get("recommended_range"))
    choices = {"auto": f"Automată · {best}"}
    choices.update({_range_label(limits): _range_label(limits) for limits in RANGES})
    game = report["game"]
    with ui.expansion(f"{game_name} · recomandare {best}", value=expanded).classes("w-full") as game_panel:
        release_confirmation = None

        def select(event):
            nonlocal release_confirmation
            release_confirmation = select_base(game, event.value, actions)

        def expanded_changed(event):
            if not event.value and release_confirmation is not None:
                release_confirmation()

        game_panel.on_value_change(expanded_changed)
        if report.get("code") is not None:
            ui.label(f"Cod de evaluare {report['code']} · {count(report['pool_size'], 'numere în pool')}").classes("text-weight-bold")
        ui.label(report["reason"]).classes("text-body2")
        if not refinement_complete(report):
            ui.label("Comparație provizorie. Rafinarea biletelor a fost incompletă.").classes("text-warning")
        historical, timing = [], []
        for row in report["ranges"]:
            base = _range_label(row["range"])
            eligible = row.get("eligible_now")
            feasible = row.get("feasible_now")
            choices[base] = base if feasible is not False else f"{base} · fără bilete în condițiile alese"
            evaluated = row.get("evaluated", 0)
            recent = row.get("recent_evaluated", 0)
            objective_evaluated = row.get("objective_evaluated", evaluated)
            objective_score = _rate_label(row.get("successes"), objective_evaluated)
            if missing := row.get("objective_missing"):
                objective_score += f" · {count(missing, 'extrageri fără date complete', 'extragere fără date complete')}"
            phase = row.get("phase") or {}
            phase_label = "N/D"
            if row["range"] and phase:
                band = phase.get("phase") or "Pauză necunoscută"
                cases = int(phase.get("compared", 0))
                if phase.get("sufficient"):
                    phase_label = f"{band} · {phase['wins']} / {phase['losses']} din {cases}"
                else:
                    phase_label = f"{band} · {count(cases, 'comparații', 'comparație')}, date insuficiente"
            historical.append({
                "base": base, "eligible": _number_label(eligible),
                "evaluated": evaluated, "infeasible": row.get("infeasible", 0),
                "ticket": _pair_label(row.get("ticket_3plus"), row.get("ticket_4plus")) if evaluated else "N/D",
                "pool": _pair_label(row.get("pool_3plus"), row.get("pool_4plus")) if evaluated else "N/D",
                "objective": objective_score, "recent": _rate_label(row.get("recent_successes"), recent),
                "phase": phase_label,
            })
            chron = row.get("chronology") or {}
            timing.append({
                "base": base, "events": f"{chron['occurrences']} / {chron['window_draws']}" if chron.get("window_draws") else "N/D",
                "last": _date_label(chron.get("last_date")),
                "wait": _pair_label(chron.get("draws_since"), chron.get("days_since")),
                "median": _pair_label(chron.get("median_gap_draws"), chron.get("median_gap_days")),
            })
        value = LR_RESTRICTED_BASE if forced else selected
        if select_base is not None:
            with ui.row().classes("lr-choice-actions w-full items-center gap-2") as actions:
                picker = ui.select(
                    choices, value=value if value in choices else "auto", label=f"Baza de selecție · {game_name}",
                    on_change=select,
                ).props(f'aria-label="Baza de selecție · {game_name}"').classes("w-full sm:max-w-md")
                if forced:
                    picker.disable()
        if historical:
            period = f"{_date_label(report.get('first_date'))} – {_date_label(report.get('as_of'))}"
            ui.label(f"Comparație pe {count(report['rows'], 'extrageri', 'extragere')} · {period}").classes("text-subtitle2")
            if metric := report.get("metric"):
                ui.label(f"Ultimele 25 de extrageri · {metric}").classes("text-caption")
            _responsive_table([
                ("base", "Bază"), ("eligible", "Eligibile acum"), ("evaluated", "Evaluate"),
                ("infeasible", "Pool imposibil"), ("ticket", "Pe bilet 3+ / 4+"), ("pool", "În pool 3+ / 4+"),
                ("objective", "Obiectiv · reușite / evaluate"), ("recent", "Ultimele 25 · reușite / evaluate"), ("phase", "La pauze similare · victorii / înfrângeri"),
            ], historical)
            ui.label("Victoriile și înfrângerile compară intervalul cu baza completă în extrageri cu pauze similare.").classes("text-caption")
            with ui.expansion("Aparițiile intervalelor și pauzele dintre ele").classes("w-full"):
                chronology = report["ranges"][0].get("chronology") or {}
                window = chronology.get("window_draws", 0)
                as_of = chronology.get("since_as_of") or report.get("as_of")
                ui.label(
                    f"Aparițiile și pauza mediană folosesc ultimele {count(window, 'extrageri', 'extragere')}. "
                    "O apariție înseamnă că toate numerele principale extrase au fost în interval. "
                    f"Pauza actuală se măsoară de la ultima apariție cunoscută până la {_date_label(as_of)}."
                ).classes("text-caption")
                _responsive_table([
                    ("base", "Bază"), ("events", "Apariții / extrageri"), ("last", "Ultima apariție"),
                    ("wait", "Pauza actuală · extrageri / zile"), ("median", "Pauza mediană · extrageri / zile"),
                ], timing)
            if protocol := report.get("protocol"):
                ui.label(protocol).classes("text-caption")


def _compare_games(amount, goal, games, inputs, inverse):
    reports = []
    inputs = dict(inputs)
    recipes = inputs.pop("paid_recipes", {})
    for game in games:
        try:
            reports.append(compare_budget_ranges(amount, goal, game, inverse=inverse, recipe=recipes.get(game),
                                                 budget_mode=SINGLE_SCHEME_MODE, **inputs))
        except (OSError, ValueError) as exc:
            report = {"game": game, "ranges": [], "reason": f"Comparație indisponibilă. {exc}"}
            if isinstance(exc, OSError):
                report["retryable"] = True
            reports.append(report)
    return reports


def _budget_input_snapshot(forecast_inputs, game, budget_settings=None):
    import pandas as pd
    from covering import lr_schemes
    from covering.lr_catalog_cache import refresh_catalog
    from loto_enterprise.benchmark.lr_hypotheses import _methods
    from loto_enterprise.benchmark.lr_provenance import layout_formulas, method_formulas

    inputs = forecast_inputs(game)
    digest = hashlib.sha256(refresh_catalog(lr_schemes.LR_DIR).encode())
    digest.update(json.dumps(LR_RESTRICTED_RANGE).encode())
    methods = {name for lot in games_for_country("RO") if not game or game == lot.game_id
               for urn in lot.bench_keys for name in _methods(urn)}
    digest.update(method_formulas(methods).encode())
    digest.update(layout_formulas().encode())
    for key, (name, frame) in sorted((inputs.get("histories") or {}).items()):
        digest.update(json.dumps([key, name, list(frame.columns)], default=str).encode())
        digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    settings = {key: value for key, value in inputs.items() if key != "histories"}
    digest.update(json.dumps(settings, sort_keys=True, default=str).encode())
    if budget_settings is not None:
        recipes = {}
        for plan in recommend_games(*budget_settings, single_scheme=True):
            gid = plan["game"]
            slots = inputs.get("joker_size", 5) if gid == "joker" else 0
            if not plan["tickets"] or (gid == "joker" and (type(slots) is not int or not 1 <= slots <= 20)):
                continue
            recipe = positional_recipe(plan, budget_plan_scheme(plan).n, slots)
            recipes[gid] = recipe
            digest.update(json.dumps([gid, recipe.portfolio_refinement], sort_keys=True).encode())
        inputs = {**inputs, "paid_recipes": recipes}
    return digest.hexdigest(), inputs


def _generate_selection(amount, goal, selections, inputs, inverse, *, selection_policy=None, decisions=None,
                        single_scheme=True):
    if decisions is None:
        decisions = lr_rebench_decisions()
    plans = []
    for game, limits in selections.items():
        plans.extend(recommend_playable_games(
            amount, goal, game, inverse=inverse, number_range=limits, single_scheme=single_scheme,
            lr_rebench_entries=decisions.get(game, ()), **inputs,
            **({"selection_policy": selection_policy} if selection_policy else {}),
        ))
    return sorted(plans, key=playable_plan_order)


def _generate_rebench_tickets(jobs, profiles):
    """Play the completed run's exact decision, including the base chosen by Auto."""
    sources, codes = {}, {}
    options = jobs[0][2]
    shared = ("budget_lei", "objective", "lookback", "dynamic", "hit_target", "joker_size")
    auto = options.get("auto_options_result") is not None
    if set(profiles) != {game for game, _, _ in jobs}:
        raise ValueError("Re-Bench nu a produs decizii pentru toate jocurile selectate.")
    for game, source, config in jobs:
        profile, = profiles[game]
        if (source is None or any(config[key] != options[key] for key in shared)
                or config.get("budget_mode") != options.get("budget_mode")
                or (config.get("auto_options_result") is not None) != auto
                or (not auto and (config["inverse"] != options["inverse"]
                                  or config.get("selection_policy") != options.get("selection_policy")))):
            raise ValueError("Istoricul sau setările jocurilor diferă de cererea de recomandare.")
        settings = profile["settings"]
        bounds = report_restricted_range(settings, profile["result"])
        uses_range = settings["restricted"] is not False or auto or config.get("auto_budget")
        if (profile["source"] != source[0] or settings["scheme"] != config["scheme"]
                or settings.get("budget_mode") != config.get("budget_mode")
                or any(settings[key] != config[key] for key in (*shared, "inverse"))
                or bounds is None or (uses_range and bounds != LR_RESTRICTED_RANGE)
                or (not config.get("auto_budget") and settings["restricted"] != config["restricted"])
                or (auto and (settings.get("selection_policy") != config.get("selection_policy")
                              or settings.get("auto_options_result") != config["auto_options_result"]))):
            raise ValueError(f"Decizia pentru {game} nu corespunde setărilor de la Re-Bench.")
        sources[game] = source
        codes[game] = settings["scheme"]
    inputs = {key: options[key] for key in ("lookback", "dynamic", "hit_target", "joker_size")}
    inputs.update(histories=sources, pool_codes=codes)
    plans = []
    for game, _, config in jobs:
        profile, = profiles[game]
        limits = LR_RESTRICTED_RANGE if profile["settings"]["restricted"] else None
        policy = config.get("selection_policy")
        plan, = _generate_selection(config["budget_lei"], config["objective"], {game: limits}, inputs,
                                    config["inverse"], selection_policy=policy, decisions=profiles,
                                    single_scheme=config.get("budget_mode") == SINGLE_SCHEME_MODE)
        if plan.get("tickets") and not plan.get("forecast_error"):
            forecast = plan["forecast"]
            urns = ("selection", "joker_selection") if game == "joker" else ("selection",)
            if (plan["game"] != game or forecast.get("history_hash") != profile["result"]["history_hash"]
                    or not all((forecast.get(urn) or {}).get("source") == (policy or "lr_rebench") for urn in urns)):
                raise ValueError(f"Biletul pentru {game} nu folosește istoricul și selecția confirmate la Re-Bench.")
        plans.append(plan)
    return sorted(plans, key=playable_plan_order), inputs


def _same_rebench_histories(forecast_inputs, sources):
    game = next(iter(sources)) if len(sources) == 1 else None
    current = forecast_inputs(game).get("histories", {})
    return all(gid in current and current[gid][0] == name and frame.equals(current[gid][1])
               for gid, (name, frame) in sources.items())


def _register_live_plans(plans, inputs):
    from loto_enterprise.core.lr_prospective import register_lr_recommendations
    from loto_enterprise.core.history import history_dates

    settings = {key: inputs[key] for key in ("lookback", "hit_target", "joker_size") if key in inputs}
    known = {}
    sources = inputs.get("histories") or {}
    for plan in plans:
        game = plan["game"]
        if not plan.get("forecast") or game not in sources:
            continue
        _, frame = sources[game]
        dates = history_dates(frame)
        if dates is not None and len(dates) and dates.notna().any():
            known[game] = dates.max().date()
    return register_lr_recommendations(plans, settings=settings, known_days=known)


def lr_base_choice(manual_choices, game, inverse, restricted):
    if restricted:
        return LR_RESTRICTED_BASE
    choice = manual_choices.get((game, inverse), _range_label(None))
    return LR_RESTRICTED_BASE if choice == "10–40" else choice


def initial_budget_amount(value, *, maximum_bani=None):
    try:
        amount = budget_in_bani(value)
    except ValueError:
        amount = DEFAULT_BUDGET_LEI * 100
    if maximum_bani is None:
        maximum_bani = maximum_scheme_budget_bani()
    return budget_in_lei(max(MIN_BUDGET_LEI * 100, min(maximum_bani, amount)))


def lr_budget_slider(value=DEFAULT_BUDGET_LEI, *, with_entry=False):
    maximum_bani = max(MIN_BUDGET_LEI * 100, maximum_scheme_budget_bani())
    maximum = budget_in_lei(maximum_bani)
    value = initial_budget_amount(value, maximum_bani=maximum_bani)
    entry = ui.number("Buget bilete LR", min=MIN_BUDGET_LEI, max=maximum, step=0.01,
                      precision=2, value=value, suffix="lei").props("outlined dense").classes("w-full mt-2")
    slider = ui.slider(
        min=MIN_BUDGET_LEI, max=maximum, step=0.01, value=value,
    ).props('aria-label="Buget pentru bilete LR" label color=positive').classes("w-full")

    def apply_entry():
        entry.sanitize()
        if entry.value is None:
            entry.set_value(slider.value)
        else:
            slider.set_value(entry.value)

    entry.on("blur", apply_entry)
    entry.on("keydown.enter", apply_entry)
    slider.on_value_change(lambda event: entry.set_value(event.value))

    with ui.row().classes("w-full justify-between text-caption"):
        ui.label(f"{MIN_BUDGET_LEI} lei")
        ui.label(lei_text(maximum))
    return (slider, entry) if with_entry else slider


def on_budget_click(button, handler, slider, entry):
    """Carry the typed amount with the click, even before Quasar emits its blur update."""
    if entry is None:
        return button.on_click(handler)

    def clicked(event):
        # NiceGUI's browserless test client supplies no DOM payload.
        amount = event.args.get("budget") if isinstance(event.args, dict) and "budget" in event.args else entry.value
        if type(amount) not in (int, float) or not isfinite(amount):
            ui.notify("Introdu un buget pentru bilete.", type="warning")
            return
        entry.set_value(amount)
        entry.sanitize()
        slider.set_value(entry.value)
        return handler()

    return button.on("click", clicked, js_handler=(
        f"""() => emit({{budget: document.querySelector('input[id="{entry.html_id}"]')?.valueAsNumber ?? null}})"""
    ))


def lr_recommendation_progress(state, rebench_progress, walk_forward_progress):
    """Display only the current stage and counters reported by this run."""
    stages = {
        "general": "Pregătire · Re-Bench general",
        "rebench": "Etapa 1 din 3 · Re-Bench",
        "validation": "Etapa 2 din 3 · Validare walk-forward",
        "ticket": "Etapa 3 din 3 · Generare bilet",
    }
    with ui.column().classes("w-full gap-1").props('id="lr-recommendation-progress"') as panel:
        with ui.row().classes("w-full items-baseline justify-between"):
            title = ui.label().classes("text-subtitle2").props('role="status" aria-live="polite"')
            percent = ui.label().classes("text-body2 tabular-nums")
        bar = ui.linear_progress(size="10px", show_value=False).classes("w-full").props(
            'rounded aria-label="Progresul recomandării" aria-describedby="lr-progress-detail"'
        )
        detail = ui.label().classes("text-body2").props('id="lr-progress-detail"')
    previous = None

    def refresh():
        nonlocal previous
        if panel.is_deleted:
            return
        active = bool(state.get("_lr_ticket_flow_active"))
        stage = state.get("_lr_ticket_flow_stage")
        message = state.get("_lr_ticket_flow_status", "")
        fraction = None
        if active and stage in ("rebench", "validation"):
            snapshot = rebench_progress() if stage == "rebench" else walk_forward_progress()
            if snapshot is not None:
                message, fraction = snapshot
        complete = not active and stage == "done"
        if complete:
            fraction = 1.0
        updated = active, stage, message, fraction
        if previous == updated:
            return
        previous = updated
        panel.set_visibility(active or bool(message))
        heading = stages.get(stage, "Pregătire") if active else ("Ultima recomandare" if complete else "")
        title.set_text(heading)
        title.set_visibility(bool(heading))
        detail.set_text(message)
        bar.set_visibility(active or complete)
        percent.set_visibility(fraction is not None)
        # A moving indicator also makes the very first evaluation visible at 0/N.
        if active and (fraction is None or fraction == 0):
            bar.props("indeterminate")
        else:
            bar.props(remove="indeterminate")
        if fraction is not None:
            bar.set_value(fraction)
            unit = {"rebench": " din comparația Auto", "validation": " din extragerile jocului"}.get(stage, "")
            percent.set_text(f"{int(fraction * 100)}%{unit}")

    refresh()
    ui.timer(0.4, refresh)


def lr_budget_panel(
    slider, forecast_inputs, *scoring_controls,
    on_budget_plan=None, initial_objective=FOUR_PLUS, on_objective_change=None,
    initial_game="all", on_game_change=None,
    initial_inverse=False, initial_restricted=False, on_method_change=None,
    base_choices=None, on_base_change=None, initial_fixed_share=False, on_fixed_share_change=None,
    on_generation_ready=None, budget_entry=None, on_request_recommendation=None,
    initial_auto_options=False, on_auto_options_change=None,
):
    ui.add_css("""
        .lr-range-table .q-table th { white-space: normal; }
        .lr-ticket-table .lr-ticket-numbers { font-weight: 650; font-variant-numeric: tabular-nums; }
        @media (max-width: 599.98px) {
            .lr-range-table .q-table, .lr-range-table .q-table tbody,
            .lr-range-table .q-table tbody tr { display: block; width: 100%; height: auto; }
            .lr-range-table .q-table thead { display: none; }
            .lr-range-table .q-table tbody tr { padding: .4rem 0; border-bottom: 1px solid #8886; }
            .lr-range-table .q-table tbody td {
                display: flex; align-items: baseline; justify-content: space-between; gap: .6rem;
                width: 100%; height: auto; padding: .2rem .25rem; border: 0; white-space: normal;
            }
            .lr-range-table .lr-range-cell-label { opacity: .78; }
            .lr-range-table td span:last-child { text-align: right; }
            .lr-ticket-table .q-table td.lr-ticket-numbers { display: block; }
            .lr-ticket-table td.lr-ticket-numbers span { display: block; text-align: left; }
            .loto-main .lr-recommendation-form { padding: 12px !important; }
            .lr-recommendation-form .q-btn, .lr-choice-actions .q-btn { min-height: 44px; }
            .lr-ticket-table td.lr-ticket-numbers span:last-child {
                font-size: 1.05rem; line-height: 1.55; overflow-wrap: anywhere;
            }
        }
    """)
    lotteries = games_for_country("RO")
    game_options = {"all": "Toate jocurile"}
    game_options.update({lot.game_id: lot.name for lot in lotteries})
    if not isinstance(initial_game, str) or initial_game not in game_options:
        initial_game = "all"
    with ui.card().props("flat bordered").classes("lr-recommendation-form w-full min-w-0 gap-3"):
        ui.label("Recomandare de bilete LR").classes("text-h6 text-weight-bold")
        ui.label("Pentru fiecare joc se alege un singur cod LR, cu prețul total cel mai apropiat de suma introdusă. "
                 "Prețul poate depăși suma. La distanțe egale se alege codul mai ieftin.").classes("text-caption")
        with ui.row().classes("w-full items-end gap-x-6 gap-y-2"):
            game = ui.select(game_options, value=initial_game, label="Joc").props(
                'outlined dense aria-label="Joc pentru bilete LR"'
            ).classes("w-full sm:w-64")
            ui.label("Obiectiv · cel puțin 4 numere pe aceeași variantă").classes("text-body2")
        auto_options = None
        if on_auto_options_change is not None:
            auto_options = ui.switch("Auto · alege cele trei opțiuni", value=initial_auto_options is True).props(
                'aria-label="Auto · alege cele trei opțiuni"'
            )
            ui.label("La generare, Auto alege separat pentru fiecare joc dacă folosește metoda inversă, "
                     f"pool-ul {LR_RESTRICTED_BASE} și Fixed Share.").classes("text-caption")
        with ui.row().classes("w-full flex-wrap items-center gap-x-4 gap-y-1") as manual_controls:
            inverse = ui.switch("Metoda inversă", value=initial_inverse is True).props('aria-label="Metoda inversă"').tooltip(
                "Reevaluează metodele pe numerele rămase după excluderea pool-ului normal."
            )
            range_filter = ui.switch(LR_RESTRICTED_LABEL, value=initial_restricted is True).props(
                f'aria-label="{LR_RESTRICTED_LABEL}"'
            ).classes("max-w-full").tooltip(
                "Limitele sunt incluse. La Joker, intervalul se aplică numai numerelor din prima urnă."
            )
            selection_policy = ui.switch("Fixed Share pe bilete", value=initial_fixed_share is True).props(
                'aria-label="Fixed Share pe bilete"'
            ).tooltip(
                "Alege metoda care propune numerele în limitele stabilite de celelalte două opțiuni, "
                "pe baza rezultatelor istorice ale biletelor."
            )
        auto_controls = None
        if auto_options is not None:
            manual_controls.bind_visibility_from(auto_options, "value", backward=lambda value: not value)
            for switch in (inverse, range_filter, selection_policy):
                switch.bind_enabled_from(auto_options, "value", backward=lambda value: not value)
            auto_controls = ui.column().classes("w-full gap-1").bind_visibility_from(auto_options, "value")
        ui.label("Pool-ul principal permite perechi consecutive. Dacă un număr ar forma o serie de trei, "
                 "generatorul trece la următorul din clasament.").classes("text-caption")

        with ui.column().classes("w-full gap-1 border-l-4 border-primary pl-3"):
            low, high = LR_RESTRICTED_RANGE
            ui.label(
                f"Șansa ca toate numerele principale extrase să fie între {low} și {high} inclusiv"
            ).classes("text-subtitle2 text-weight-bold")
            for lot in lotteries:
                in_range = max(0, min(high, lot.max_n) - max(1, low) + 1)
                favorable = comb(in_range, lot.draw_n)
                total = comb(lot.max_n, lot.draw_n)
                percent = f"{100 * favorable / total:.2f}%".replace(".", ",")
                numbers = f"{lot.draw_n} numere din prima urnă" if lot.geo.second else f"{lot.draw_n} numere"
                ui.label(f"{lot.name} ({numbers}) · {percent}").classes("text-body2").tooltip(
                    f"{favorable:,} combinații în interval din {total:,} combinații posibile.".replace(",", " ")
                ).bind_visibility_from(
                    game, "value", backward=lambda selected, game_id=lot.game_id: selected in ("all", game_id),
                )
        generate = ui.button(
            "Generează recomandarea", icon="confirmation_number", color="positive",
        ).props("no-caps aria-busy=false").classes("w-full sm:w-auto sm:ml-auto")

    with ui.expansion("Bilete și analiză", value=False).classes("w-full min-w-0") as panel:
        output = ui.column().classes("w-full min-w-0 gap-2")
        comparison = ui.column().classes("w-full min-w-0 gap-2")
        revision = 0
        running = False
        preview_idle = asyncio.Event()
        preview_idle.set()
        automatic_scope = None
        applied_budget = None
        cached_analysis = None
        validated_settings = None
        history_check = asyncio.Lock()
        displayed = None
        manual_choices = {} if base_choices is None else base_choices
        confirm_button = None
        waiting_for_choice = False
        registration = None

        def selected_budget():
            return slider.value, FOUR_PLUS, None if game.value == "all" else game.value

        def selected_method():
            return bool(inverse.value), bool(range_filter.value)

        def registration_settings():
            return (selected_budget(), selected_method(), bool(selection_policy.value),
                    bool(auto_options and auto_options.value),
                    tuple(sorted(manual_choices.items())), tuple(control.value for control in scoring_controls))

        def clear_auto_results():
            if auto_controls is not None and not auto_controls.is_deleted:
                auto_controls.clear()

        def display_auto_results(jobs):
            if auto_options is None or not auto_options.value or auto_controls.is_deleted:
                return
            auto_controls.clear()
            with auto_controls:
                for game_id, _, config in jobs:
                    with ui.row().classes("w-full flex-wrap items-center gap-x-4 gap-y-1"):
                        ui.label(game_options[game_id]).classes("text-body2 text-weight-bold")
                        for label, selected in (
                            ("Metoda inversă", config["inverse"]),
                            (LR_RESTRICTED_LABEL, config["restricted"]),
                            ("Fixed Share pe bilete", config.get("selection_policy") == ONLINE_TICKET_EXPERTS),
                        ):
                            ui.switch(label, value=selected).props(f'aria-label="{game_options[game_id]} · {label}"').disable()
                    choice = config["auto_options_result"]
                    ui.label(
                        f"Alegerea opțiunilor · {_date_label(choice['first_date'])} – {_date_label(choice['last_date'])}"
                        f" · {_rate_label(choice['hits'], choice['evaluated'])} extrageri cu obiectiv atins."
                    ).classes("text-caption")

        def clear_confirmation():
            nonlocal confirm_button
            if confirm_button is not None:
                if not confirm_button.is_deleted:
                    confirm_button.delete()
                confirm_button = None
                if not generate.is_deleted:
                    generate.set_visibility(True)

        def change_base(game_id, value, actions):
            nonlocal revision, waiting_for_choice, confirm_button, validated_settings
            if not panel.value or actions.is_deleted:
                return
            validated_settings = None
            manual_choices[game_id, bool(inverse.value)] = value
            if on_base_change is not None:
                on_base_change()
            revision += 1
            waiting_for_choice = True
            clear_confirmation()
            generate.set_visibility(False)
            with actions:
                confirm_button = ui.button(
                    "Generează recomandarea", icon="confirmation_number",
                    on_click=confirm_generation if budget_entry is None else None,
                ).props("no-caps").classes("w-full sm:w-auto")
                if budget_entry is not None:
                    on_budget_click(confirm_button, confirm_generation, slider, budget_entry)
            show_status(f"Apasă „Generează recomandarea” pentru a folosi baza aleasă la {game_options[game_id]}.")
            local_button = confirm_button

            def release():
                if confirm_button is local_button:
                    clear_confirmation()

            return release

        def show_status(message):
            output.clear()
            with output:
                ui.label(message).props('role="status"').classes("text-caption")

        def display(plans, amount, goal, registered, registration_errors):
            output.clear()
            with output:
                for entry in registered:
                    created = datetime.fromisoformat(entry["captured_at"]).strftime("%d.%m.%Y %H:%M %z")
                    target = date.fromisoformat(entry["target_day"]).strftime("%d.%m.%Y")
                    ui.label(
                        f"{game_options[entry['game']]} · înregistrat la {created} pentru {target}"
                    ).props('role="status"').classes("text-caption")
                for game_key, error in registration_errors.items():
                    label = game_options.get(game_key, "Registru prospectiv")
                    ui.label(f"{label} · {error}").props('role="alert"').classes("text-warning")
                if plans is None:
                    ui.label("Calculul recomandării a eșuat. Poți încerca din nou cu butonul verde.").props(
                        'role="alert"'
                    ).classes("text-warning")
                else:
                    _render_recommendations(plans, amount, goal)

        async def calculate():
            nonlocal revision, running, applied_budget, cached_analysis, displayed, waiting_for_choice
            nonlocal registration, validated_settings
            if automatic_scope is not None and automatic_scope():
                return
            revision += 1
            if validated_settings is not None:
                receipt = validated_settings
                last_selection, current, sources = receipt
                check_revision = revision
                if last_selection == registration_settings() and current():
                    async with history_check:
                        if check_revision != revision or validated_settings is not receipt:
                            return
                        try:
                            same = await run.io_bound(_same_rebench_histories, forecast_inputs, sources)
                        except (OSError, ValueError):
                            same = False
                        if check_revision != revision or validated_settings is not receipt or output.is_deleted:
                            return
                        if same and last_selection == registration_settings() and current():
                            return
                validated_settings = None
                if not output.is_deleted:
                    output.clear()
            clear_auto_results()
            waiting_for_choice = False
            clear_confirmation()
            if output.is_deleted:
                return
            if not panel.value:
                generate.props("aria-busy=false", remove="loading")
                return
            if on_request_recommendation is None:
                generate.props("loading aria-busy=true")
            show_status(f"Se verifică opțiunile pentru {lei_text(slider.value)}…")
            if running:
                return
            running = True
            preview_idle.clear()
            try:
                while panel.value and not output.is_deleted and not waiting_for_choice:
                    if automatic_scope is not None and automatic_scope():
                        return
                    current = revision
                    budget_settings, method_settings = selected_budget(), selected_method()
                    amount, goal, game_id = budget_settings
                    inverse_mode, forced = method_settings
                    fixed_share = bool(selection_policy.value)
                    auto_enabled = bool(auto_options and auto_options.value)
                    settings_at_start = registration_settings()

                    def obsolete():
                        return (current != revision or not panel.value or output.is_deleted
                                or selected_budget() != budget_settings or selected_method() != method_settings
                                or bool(selection_policy.value) != fixed_share
                                or bool(auto_options and auto_options.value) != auto_enabled)

                    try:
                        if on_budget_plan is not None and applied_budget != budget_settings:
                            templates = await run.io_bound(recommend_games, *budget_settings, single_scheme=True)
                            if obsolete():
                                continue
                            if templates is None:
                                raise RuntimeError("Planificarea bugetului a fost întreruptă.")
                            on_budget_plan(templates)
                            applied_budget = budget_settings
                        if auto_enabled:
                            comparison.clear()
                            show_status("Apasă „Generează recomandarea” pentru a alege și valida opțiunile.")
                            return
                        fingerprint, inputs = await run.io_bound(_budget_input_snapshot, forecast_inputs, game_id, budget_settings)
                        if obsolete():
                            continue
                        analysis_key = (budget_settings, inverse_mode, fixed_share, fingerprint)
                        if cached_analysis is None or cached_analysis[0] != analysis_key:
                            comparison.clear()
                            games = [game_id] if game_id else [key for key in game_options if key != "all"]
                            show_status(f"Se compară bazele de selecție pentru {lei_text(amount)}…")
                            analysis_inputs = {**inputs, "selection_policy": ONLINE_TICKET_EXPERTS} if fixed_share else inputs
                            reports = await run.io_bound(_compare_games, amount, goal, games, analysis_inputs, inverse_mode)
                            if obsolete():
                                continue
                            if reports is None:
                                raise RuntimeError("Analiza intervalelor a fost întreruptă.")
                            if all(refinement_complete(report) and not report.get("retryable") for report in reports):
                                cached_analysis = (analysis_key, inputs, reports)
                        else:
                            _, inputs, reports = cached_analysis
                        if displayed != (id(reports), forced):
                            comparison.clear()
                            with comparison:
                                ui.label("Analiza bazelor de selecție").classes("text-subtitle1 text-weight-bold")
                                for report in reports:
                                    gid = report["game"]
                                    _range_comparison(
                                        report, game_options[gid], lr_base_choice(manual_choices, gid, inverse_mode, forced),
                                        change_base if auto_options is None else None, forced, expanded=len(reports) == 1,
                                    )
                            displayed = (id(reports), forced) if cached_analysis and cached_analysis[2] is reports else None
                        if on_request_recommendation is not None:
                            show_status("Apasă „Generează recomandarea” pentru Re-Bench, validare și bilet.")
                            return
                        selections = {}
                        auto_reports = {}
                        for report in reports:
                            gid = report["game"]
                            choice = lr_base_choice(manual_choices, gid, inverse_mode, forced)
                            if choice == LR_RESTRICTED_BASE:
                                limits = LR_RESTRICTED_RANGE
                            elif choice == "auto":
                                auto_reports[gid] = report
                                limits = report.get("recommended_range")
                            else:
                                limits = None
                            selections[gid] = limits
                        show_status(f"Se generează biletele pentru {lei_text(amount)}…")
                        options = {"selection_policy": ONLINE_TICKET_EXPERTS} if fixed_share else {}
                        plans = await run.io_bound(_generate_selection, amount, goal, selections, inputs, inverse_mode, **options)
                        if plans is None:
                            raise RuntimeError("Generarea biletelor a fost întreruptă.")
                        if auto_reports:
                            for plan in plans:
                                if report := auto_reports.get(plan["game"]):
                                    plan["portfolio_refinement"] = merge_refinements(plan, report)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Calcul LR la %d lei pentru %s (%s)", amount, goal, game_id or "all"
                        )
                        plans = None
                    if obsolete():
                        continue
                    registered, registration_errors = [], {}
                    if registration is not None:
                        requested = registration
                        registration = None
                        if requested == settings_at_start == registration_settings() and plans is not None:
                            try:
                                stored = await run.io_bound(_register_live_plans, plans, inputs)
                                if stored is None:
                                    raise RuntimeError("Înregistrarea a fost întreruptă.")
                                registered, registration_errors = stored
                            except Exception:
                                logging.getLogger(__name__).exception("Înregistrare prospectivă LR")
                                registration_errors = {"": "Registrul local nu a putut salva generarea."}
                    if obsolete():
                        continue
                    display(plans, amount, goal, registered, registration_errors)
                    return
            finally:
                running = False
                preview_idle.set()
                if not generate.is_deleted and not (automatic_scope is not None and automatic_scope()):
                    generate.props("aria-busy=false", remove="loading")

        async def refresh():
            nonlocal cached_analysis, displayed
            cached_analysis = displayed = None
            await calculate()

        async def show_recommendation():
            if not panel.value:
                panel.set_value(True)
                return
            await refresh()

        async def generate_and_register():
            nonlocal registration
            if automatic_scope is not None and automatic_scope():
                return
            if waiting_for_choice:
                await confirm_generation()
                return
            registration = registration_settings()
            await show_recommendation()

        async def confirm_generation():
            nonlocal registration
            if on_request_recommendation is not None:
                return await on_request_recommendation()
            registration = registration_settings()
            await calculate()

        async def generate_after_rebench(jobs, still_current, profiles, validation_results=None):
            nonlocal revision, automatic_scope, registration, cached_analysis, displayed, waiting_for_choice
            nonlocal validated_settings
            if not jobs or automatic_scope is not None or not still_current():
                return False
            automatic_scope = still_current
            validated_settings = None
            revision += 1
            registration = None
            cached_analysis = displayed = None
            try:
                if not output.is_deleted:
                    clear_auto_results()
                    waiting_for_choice = False
                    clear_confirmation()
                    panel.set_value(True)
                    generate.props("loading aria-busy=true")
                    show_status("Se pregătesc biletele pentru selecția verificată.")
                await preview_idle.wait()
                if not still_current():
                    return False
                plans, inputs = await run.io_bound(_generate_rebench_tickets, jobs, profiles)
                if not still_current():
                    return False
                ready = ({plan["game"] for plan in plans if plan.get("tickets") and not plan.get("forecast_error")
                          and refinement_complete(plan)} == set(profiles))
                registered, errors = [], {}
                if ready:
                    if validation_results is not None or on_request_recommendation is not None:
                        if not validation_results or set(validation_results) != set(profiles):
                            raise ValueError("Lipsește validarea pentru unul dintre jocurile selectate.")
                        configs = {game: config for game, _, config in jobs}
                        for plan in plans:
                            game = plan["game"]
                            config = configs[game]
                            plan["walk_forward"] = _ticket_validation(
                                plan, validation_results[game], auto_job=config if config.get("auto_options_result") else None,
                            )
                    if not await run.io_bound(_same_rebench_histories, forecast_inputs, inputs["histories"]):
                        raise ValueError("Istoricul disponibil s-a schimbat după Re-Bench.")
                    if not still_current():
                        return False
                    registered, errors = await run.io_bound(_register_live_plans, plans, inputs)
                if not still_current():
                    return False
                if not output.is_deleted:
                    settings = jobs[0][2]
                    if ready or on_request_recommendation is None:
                        display(plans, settings["budget_lei"], settings["objective"], registered, errors)
                        if ready:
                            display_auto_results(jobs)
                        if ready and on_request_recommendation is not None:
                            validated_settings = registration_settings(), still_current, inputs["histories"]
                    else:
                        reasons = [plan.get("forecast_error") for plan in plans if plan.get("forecast_error")]
                        show_status(" ".join(reasons) or "Biletul nu a putut fi generat pentru selecția validată.")
                return ready and not errors and {entry["game"] for entry in registered} == set(profiles)
            except Exception as exc:
                if not output.is_deleted and still_current():
                    show_status(f"Recomandarea după Re-Bench s-a oprit. {exc}")
                raise
            finally:
                automatic_scope = None
                if not generate.is_deleted and not running:
                    generate.props("aria-busy=false", remove="loading")

        def change_method(recalculate):
            if on_method_change is not None:
                on_method_change(*selected_method())
            return recalculate()

        def change_selection(callback, control):
            if callback is not None:
                callback(control.value)
            return refresh()

        if on_generation_ready is not None:
            on_generation_ready(generate_after_rebench)
        panel.on_value_change(refresh)
        on_budget_click(generate, on_request_recommendation or generate_and_register, slider, budget_entry)
        if auto_options is not None:
            auto_options.on_value_change(lambda: change_selection(on_auto_options_change, auto_options))
        inverse.on_value_change(lambda: change_method(refresh))
        range_filter.on_value_change(lambda: change_method(calculate))
        selection_policy.on_value_change(lambda: change_selection(on_fixed_share_change, selection_policy))
        slider.on_value_change(show_recommendation)
        game.on_value_change(lambda: change_selection(on_game_change, game))
        for control in scoring_controls:
            control.on_value_change(refresh)
        return refresh
