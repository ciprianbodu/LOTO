"""Compare Romanian reduced-scheme tickets within the selected budget."""

import logging
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from math import comb

from nicegui import run, ui

from covering.lr_budget import (
    ANY_PRIZE, DEFAULT_BUDGET_LEI, FOUR_PLUS, MIN_BUDGET_LEI, budget_in_bani, budget_in_lei,
    maximum_scheme_budget_bani, recommend_games,
)
from covering.lr_pool_budget import positional_recipe
from covering.lr_refinement_status import merge_refinements, refinement_complete
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, playable_plan_order, recommend_playable_games
from loto_enterprise.core.lr_range_analysis import RANGES, compare_budget_ranges
from loto_enterprise.core.lotteries import games_for_country
from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS
from loto_enterprise.core.ro_text import count, lei_text
from ui_lr_portfolio import _percent, _price
from ui_lr_rebench import lr_rebench_decisions


_OBJECTIVES = {FOUR_PLUS: "4+ pe o variantă", ANY_PRIZE: "Orice premiu"}
_INVERSE_RANGE = (10, 40)


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
            excluded = forecast.get("excluded_pool")
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
            if excluded is not None:
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
                method_label = "Metoda pool-ului invers" if excluded is not None else "Metoda pool-ului"
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


def _render_recommendations(records, budget_lei, objective):
    if not records or not records[0]["tickets"]:
        ui.label("Scheme LR indisponibile pentru pool-urile și bugetul ales.").classes("text-warning")
        for plan in records:
            _ticket_details(plan, expanded=True)
        return
    first = records[0]
    with ui.card().props("flat bordered").classes("w-full border-l-4 border-positive gap-1"):
        ui.label(f"Sugestie pentru {lei_text(budget_lei)} · {first['game_name']}").classes("text-h6 text-weight-bold")
        ui.label(_recommended_codes(first["tickets"])).classes("text-subtitle2 text-weight-bold")
        ui.label(
            f"{_price(first['price_bani'])} cu taxă · "
            f"{count(len(first['tickets']), 'bilete', 'bilet')} · "
            f"{count(first['variants'], 'variante', 'variantă')}"
        ).classes("text-body2")
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
        ("bound", "Limită superioară"),
    ]
    rows = []
    for plan in records:
        upper = plan["upper"]
        playable = bool(plan["tickets"])
        rows.append({
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
        _responsive_table(fields, rows, row_key="game", label="Compararea jocurilor în buget")
    elif rows[0]["bound"] != "—":
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
    with ui.expansion(f"{game_name} · recomandare {best}", value=expanded).classes("w-full"):
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
        value = "10–40" if forced else selected
        with ui.row().classes("lr-choice-actions w-full items-center gap-2") as actions:
            picker = ui.select(
                choices, value=value if value in choices else "auto", label=f"Baza de selecție · {game_name}",
                on_change=lambda event: select_base(game, event.value, actions),
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
            reports.append(compare_budget_ranges(amount, goal, game, inverse=inverse, recipe=recipes.get(game), **inputs))
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
        for plan in recommend_games(*budget_settings):
            gid = plan["game"]
            slots = inputs.get("joker_size", 5) if gid == "joker" else 0
            if not plan["tickets"] or (gid == "joker" and (type(slots) is not int or not 1 <= slots <= 20)):
                continue
            recipe = positional_recipe(plan, budget_plan_scheme(plan).n, slots)
            recipes[gid] = recipe
            digest.update(json.dumps([gid, recipe.portfolio_refinement], sort_keys=True).encode())
        inputs = {**inputs, "paid_recipes": recipes}
    return digest.hexdigest(), inputs


def _generate_selection(amount, goal, selections, inputs, inverse, *, selection_policy=None):
    decisions = lr_rebench_decisions()
    plans = []
    for game, limits in selections.items():
        plans.extend(recommend_playable_games(
            amount, goal, game, inverse=inverse, number_range=limits,
            lr_rebench_entries=decisions.get(game, ()), **inputs,
            **({"selection_policy": selection_policy} if selection_policy else {}),
        ))
    return sorted(plans, key=playable_plan_order)


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
        return _range_label(_INVERSE_RANGE)
    return manual_choices.get((game, inverse), _range_label(None))


def initial_budget_amount(value, *, maximum_bani=None):
    try:
        amount = budget_in_bani(value)
    except ValueError:
        amount = DEFAULT_BUDGET_LEI * 100
    if maximum_bani is None:
        maximum_bani = maximum_scheme_budget_bani()
    return budget_in_lei(max(MIN_BUDGET_LEI * 100, min(maximum_bani, amount)))


def lr_budget_slider(value=DEFAULT_BUDGET_LEI):
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
    return slider


def lr_budget_panel(
    slider, forecast_inputs, *scoring_controls,
    on_budget_plan=None, initial_objective=FOUR_PLUS, on_objective_change=None,
    initial_game="all", on_game_change=None,
    initial_inverse=False, initial_restricted=False, on_method_change=None,
    base_choices=None, on_base_change=None, initial_fixed_share=False, on_fixed_share_change=None,
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
        with ui.row().classes("w-full items-end gap-x-6 gap-y-2"):
            game = ui.select(game_options, value=initial_game, label="Joc").props(
                'outlined dense aria-label="Joc pentru bilete LR"'
            ).classes("w-full sm:w-64")
            with ui.column().classes("gap-1"):
                ui.label("Obiectiv").classes("text-caption")
                objective = ui.toggle(_OBJECTIVES, value=initial_objective).props(
                    'aria-label="Obiectivul biletelor LR" no-caps toggle-color=primary'
                )
        with ui.row().classes("w-full flex-wrap items-center gap-x-4 gap-y-1"):
            inverse = ui.switch("Metoda inversă", value=initial_inverse is True).props('aria-label="Metoda inversă"').tooltip(
                "Reevaluează metodele pe numerele rămase după excluderea pool-ului normal."
            )
            range_filter = ui.switch("Pool restrâns între 10 și 40", value=initial_restricted is True).props(
                'aria-label="Pool restrâns între 10 și 40"'
            ).classes("max-w-full").tooltip(
                "Limitele sunt incluse. La Joker, intervalul se aplică numai numerelor din prima urnă."
            )
            selection_policy = ui.switch("Fixed Share pe bilete", value=initial_fixed_share is True).props(
                'aria-label="Fixed Share pe bilete"'
            ).tooltip(
                "Alege metoda după rezultatele biletelor complete din ultimele 104 de zile cu extrageri. "
                "Evaluările de până acum nu au demonstrat un avantaj predictiv."
            )
        ui.label("Pool-ul principal permite perechi consecutive. Dacă un număr ar forma o serie de trei, "
                 "generatorul trece la următorul din clasament.").classes("text-caption")

        with ui.column().classes("w-full gap-1 border-l-4 border-primary pl-3"):
            low, high = _INVERSE_RANGE
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
        applied_budget = None
        cached_analysis = None
        displayed = None
        manual_choices = {} if base_choices is None else base_choices
        confirm_button = None
        waiting_for_choice = False
        registration = None

        def selected_budget():
            return slider.value, objective.value, None if game.value == "all" else game.value

        def selected_method():
            return bool(inverse.value), bool(range_filter.value)

        def registration_settings():
            return (selected_budget(), selected_method(), bool(selection_policy.value),
                    tuple(sorted(manual_choices.items())), tuple(control.value for control in scoring_controls))

        def clear_confirmation():
            nonlocal confirm_button
            if confirm_button is not None and not confirm_button.is_deleted:
                confirm_button.delete()
            confirm_button = None

        def change_base(game_id, value, actions):
            nonlocal revision, waiting_for_choice, confirm_button
            if not panel.value or actions.is_deleted:
                return
            manual_choices[game_id, bool(inverse.value)] = value
            if on_base_change is not None:
                on_base_change()
            revision += 1
            waiting_for_choice = True
            clear_confirmation()
            with actions:
                confirm_button = ui.button(
                    "Generează cu baza aleasă", icon="confirmation_number", on_click=confirm_generation,
                ).props("no-caps").classes("w-full sm:w-auto")
            show_status(f"Confirmă baza aleasă lângă selectorul {game_options[game_id]} pentru a actualiza biletele.")

        def show_status(message):
            output.clear()
            with output:
                ui.label(message).props('role="status"').classes("text-caption")

        async def calculate():
            nonlocal revision, running, applied_budget, cached_analysis, displayed, waiting_for_choice
            nonlocal registration
            revision += 1
            waiting_for_choice = False
            clear_confirmation()
            if output.is_deleted:
                return
            if not panel.value:
                generate.props("aria-busy=false", remove="loading")
                return
            generate.props("loading aria-busy=true")
            show_status(f"Se pregătește recomandarea pentru {lei_text(slider.value)}…")
            if running:
                return
            running = True
            try:
                while panel.value and not output.is_deleted and not waiting_for_choice:
                    current = revision
                    budget_settings, method_settings = selected_budget(), selected_method()
                    amount, goal, game_id = budget_settings
                    inverse_mode, forced = method_settings
                    fixed_share = bool(selection_policy.value)
                    settings_at_start = registration_settings()

                    def obsolete():
                        return (current != revision or not panel.value or output.is_deleted
                                or selected_budget() != budget_settings or selected_method() != method_settings
                                or bool(selection_policy.value) != fixed_share)

                    try:
                        if on_budget_plan is not None and applied_budget != budget_settings:
                            templates = await run.io_bound(recommend_games, *budget_settings)
                            if obsolete():
                                continue
                            if templates is None:
                                raise RuntimeError("Planificarea bugetului a fost întreruptă.")
                            on_budget_plan(templates)
                            applied_budget = budget_settings
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
                                        change_base, forced, expanded=len(reports) == 1,
                                    )
                            displayed = (id(reports), forced) if cached_analysis and cached_analysis[2] is reports else None
                        selections = {}
                        auto_reports = {}
                        for report in reports:
                            gid = report["game"]
                            choice = lr_base_choice(manual_choices, gid, inverse_mode, forced)
                            if choice == _range_label(_INVERSE_RANGE):
                                limits = _INVERSE_RANGE
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
                    return
            finally:
                running = False
                if not generate.is_deleted:
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
            registration = registration_settings()
            await show_recommendation()

        async def confirm_generation():
            nonlocal registration
            registration = registration_settings()
            await calculate()

        def change_method(recalculate):
            if on_method_change is not None:
                on_method_change(*selected_method())
            return recalculate()

        def change_selection(callback, control):
            if callback is not None:
                callback(control.value)
            return refresh()

        panel.on_value_change(refresh)
        generate.on_click(generate_and_register)
        inverse.on_value_change(lambda: change_method(refresh))
        range_filter.on_value_change(lambda: change_method(calculate))
        selection_policy.on_value_change(lambda: change_selection(on_fixed_share_change, selection_policy))
        slider.on_value_change(show_recommendation)
        game.on_value_change(lambda: change_selection(on_game_change, game))
        objective.on_value_change(lambda: change_selection(on_objective_change, objective))
        for control in scoring_controls:
            control.on_value_change(refresh)
        return refresh
