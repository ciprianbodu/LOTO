"""Compare Romanian reduced-scheme tickets within the selected budget."""

import logging
from collections import Counter
from datetime import date

from nicegui import run, ui

from covering.lr_budget import ANY_PRIZE, DEFAULT_BUDGET_LEI, FOUR_PLUS, MIN_BUDGET_LEI, recommend_games
from loto_enterprise.core.lr_budget_forecast import playable_plan_order, recommend_playable_games
from loto_enterprise.core.lr_range_analysis import RANGES, compare_budget_ranges
from loto_enterprise.core.lotteries import games_for_country
from loto_enterprise.core.ro_text import count
from ui_lr_portfolio import _percent, _price


_OBJECTIVES = {FOUR_PLUS: "4+ pe o variantă", ANY_PRIZE: "Orice premiu"}
_MAX_SLIDER_BUDGET_LEI = 400
_INVERSE_RANGE = (10, 40)


def _columns(fields):
    return [
        {"name": key, "field": key, "label": label, "align": "left"}
        for key, label in fields
    ]


def _bench_method(selection):
    method = selection["method"]
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
    with ui.expansion(f"Bilete · {plan['game_name']}", value=expanded).classes("w-full"):
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
            ui.label(", ".join(map(str, pool))).classes("text-body2")
            if excluded_jokers := forecast.get("excluded_jokers"):
                eligible = count(forecast["eligible_joker_count"], "numere eligibile", "număr eligibil")
                ui.label(f"Urna 2 Joker · pool normal exclus · {eligible} după excludere").classes("text-caption")
                ui.label(", ".join(map(str, excluded_jokers))).classes("text-body2")
            if forecast["jokers"]:
                ui.label("Pool Urna 2 · " + ", ".join(map(str, forecast["jokers"]))).classes("text-caption")
            date = f" · ultima extragere {forecast['date']}" if forecast["date"] else ""
            history = count(forecast["rows"], "extrageri folosite", "extragere folosită")
            ui.label(f"{forecast['filename']}{date}" + (f" · {history}" if forecast["rows"] else "")).classes("text-caption")
        if error := plan.get("forecast_error"):
            ui.label(error).classes("text-warning")
        if rows:
            if plan.get("template_adjusted"):
                ui.label("Scheme adaptate la pool-urile disponibile.").classes("text-caption")
            table = ui.table(
                columns=_columns(fields), rows=rows, row_key="id",
                pagination={"rowsPerPage": 12},
            ).props("dense flat wrap-cells").classes("w-full")
            if len(rows) <= 12:
                table.props("hide-bottom")
        if forecast:
            selection = forecast["selection"]
            if selection.get("method"):
                if not forecast["dynamic"]:
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
            bonus = forecast["joker_selection"] or {}
            if bonus.get("method"):
                ui.label(f"Urna 2 Joker · {_bench_method(bonus)}").classes("text-caption")


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
    with ui.card().props("flat bordered").classes("w-full border-l-4 border-primary gap-1"):
        ui.label(f"Sugestie pentru {budget_lei} lei · {first['game_name']}").classes(
            "text-subtitle1 text-weight-bold"
        )
        ui.label(
            f"{_percent(first['attained'], first['denominator'])} · {_OBJECTIVES[objective]}"
        ).classes("text-h5 text-positive")
        ui.label(_recommended_codes(first["tickets"])).classes(
            "text-subtitle2 text-weight-bold"
        )
        ui.label(
            f"{_price(first['price_bani'])} cu taxă · "
            f"{count(len(first['tickets']), 'bilete', 'bilet')} · "
            f"{count(first['variants'], 'variante', 'variantă')}"
        ).classes("text-body2")
    _ticket_details(first, expanded=True)

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
                _percent(upper, plan["denominator"]) if playable and upper is not None else "—"
            ),
        })
    ui.table(
        columns=_columns(fields), rows=rows, row_key="game", pagination={"rowsPerPage": 0},
    ).props("dense flat hide-bottom wrap-cells").classes("w-full")
    for plan in records[1:]:
        _ticket_details(plan, expanded=bool(plan.get("forecast_error")))


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


def _analysis_table(fields, rows):
    table = ui.table(
        columns=_columns(fields), rows=rows, row_key="base", pagination={"rowsPerPage": 0},
    ).props("dense flat hide-bottom wrap-cells").classes("w-full lr-range-table")
    table.add_slot("body-cell", """
        <q-td :props="props">
            <span class="lt-sm lr-range-cell-label">{{ props.col.label }}</span>
            <span>{{ props.value }}</span>
        </q-td>
    """)


def _range_comparison(report, game_name, selected, select_base, forced, *, expanded):
    best = _range_label(report.get("recommended_range"))
    choices = {"auto": f"Automată · {best}"}
    choices.update({_range_label(limits): _range_label(limits) for limits in RANGES})
    game = report["game"]
    with ui.expansion(f"{game_name} · recomandare {best}", value=expanded).classes("w-full"):
        if report.get("code") is not None:
            ui.label(f"Cod de evaluare {report['code']} · {count(report['pool_size'], 'numere în pool')}").classes("text-weight-bold")
        ui.label(report["reason"]).classes("text-body2")
        historical, timing = [], []
        for row in report["ranges"]:
            base = _range_label(row["range"])
            eligible = row.get("eligible_now")
            feasible = row.get("feasible_now")
            choices[base] = base if feasible is not False else f"{base} · insuficiente numere eligibile"
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
        if historical:
            period = f"{_date_label(report.get('first_date'))} – {_date_label(report.get('as_of'))}"
            ui.label(f"Comparație pe {count(report['rows'], 'extrageri', 'extragere')} · {period}").classes("text-subtitle2")
            if metric := report.get("metric"):
                ui.label(f"Ultimele 25 de extrageri · {metric}").classes("text-caption")
            _analysis_table([
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
                _analysis_table([
                    ("base", "Bază"), ("events", "Apariții / extrageri"), ("last", "Ultima apariție"),
                    ("wait", "Pauza actuală · extrageri / zile"), ("median", "Pauza mediană · extrageri / zile"),
                ], timing)
            if protocol := report.get("protocol"):
                ui.label(protocol).classes("text-caption")
        value = "10–40" if forced else selected
        picker = ui.select(
            choices, value=value if value in choices else "auto", label=f"Baza de selecție · {game_name}",
            on_change=lambda event: select_base(game, event.value),
        ).props(f'aria-label="Baza de selecție · {game_name}"').classes("w-full sm:max-w-md")
        if forced:
            picker.disable()


def _compare_games(amount, goal, games, inputs, inverse):
    reports = []
    for game in games:
        try:
            reports.append(compare_budget_ranges(amount, goal, game, inverse=inverse, **inputs))
        except (OSError, ValueError) as exc:
            reports.append({"game": game, "ranges": [], "reason": f"Comparație indisponibilă. {exc}"})
    return reports


def _generate_selection(amount, goal, selections, inputs, inverse):
    plans = []
    for game, limits in selections.items():
        plans.extend(recommend_playable_games(
            amount, goal, game, inverse=inverse, number_range=limits, **inputs,
        ))
    return sorted(plans, key=playable_plan_order)


def initial_budget_amount(value):
    value = value if type(value) is int else DEFAULT_BUDGET_LEI
    return max(MIN_BUDGET_LEI, min(_MAX_SLIDER_BUDGET_LEI, value))


def lr_budget_slider(value=DEFAULT_BUDGET_LEI):
    value = initial_budget_amount(value)
    budget_label = ui.label().classes("text-bold mt-2")
    slider = ui.slider(
        min=MIN_BUDGET_LEI, max=_MAX_SLIDER_BUDGET_LEI, step=1, value=value,
    ).props('aria-label="Buget pentru bilete LR" label').classes("w-full")
    budget_label.bind_text_from(
        slider, "value", lambda value: f"Buget bilete LR · {int(value)} lei"
    )

    with ui.row().classes("w-full justify-between text-caption"):
        ui.label(f"{MIN_BUDGET_LEI} lei")
        ui.label(f"{_MAX_SLIDER_BUDGET_LEI} lei")
    return slider


def lr_budget_panel(slider, forecast_inputs, *scoring_controls, on_budget_plan=None, initial_objective=FOUR_PLUS):
    ui.add_css("""
        .lr-range-table .q-table th { white-space: normal; }
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
        }
    """)
    with ui.row().classes("w-full flex-wrap items-center gap-x-4 gap-y-1"):
        generate = ui.button(
            "Generează recomandarea", icon="confirmation_number", color="positive",
        ).props("no-caps aria-busy=false").classes("w-full sm:w-auto")
        inverse = ui.switch("Metoda inversă", value=False).props('aria-label="Metoda inversă"').tooltip(
            "Reevaluează metodele pe numerele rămase după excluderea pool-ului normal."
        )
        range_filter = ui.switch("Doar numere principale între 10 și 40", value=False).props(
            'aria-label="Doar numere principale între 10 și 40"'
        ).classes("max-w-full").tooltip(
            "Limitele sunt incluse. La Joker, intervalul se aplică numai numerelor din prima urnă."
        )
        range_filter.bind_visibility_from(inverse, "value")
        range_filter.bind_enabled_from(inverse, "value")
    with ui.expansion("Bilete LR în buget", value=False).classes("w-full") as panel:
        ui.label(
            "Șansa teoretică se calculează pentru o extragere uniformă. "
            "Zonele folosesc pool-ul generatorului sau un pool calculat pentru recomandare. Totalul include taxa de bilet."
        ).classes("text-body2")
        game_options = {"all": "Toate jocurile"}
        game_options.update({lot.game_id: lot.name for lot in games_for_country("RO")})
        game = ui.select(game_options, value="all", label="Joc").props(
            'aria-label="Joc pentru bilete LR"'
        ).classes("w-full sm:w-72")
        objective = ui.toggle(_OBJECTIVES, value=initial_objective).props(
            'aria-label="Obiectivul biletelor LR" no-caps toggle-color=primary'
        )
        comparison = ui.column().classes("w-full gap-2")
        output = ui.column().classes("w-full gap-2")
        revision = 0
        running = False
        applied_budget = None
        cached_analysis = None
        displayed = None
        manual_choices = {}
        waiting_for_choice = False

        def selected_budget():
            return int(slider.value), objective.value, None if game.value == "all" else game.value

        def selected_method():
            enabled = bool(inverse.value)
            return enabled, bool(enabled and range_filter.value)

        def change_base(game_id, value):
            nonlocal revision, waiting_for_choice
            manual_choices[game_id, bool(inverse.value)] = value
            revision += 1
            waiting_for_choice = True
            output.clear()
            with output:
                ui.label("Confirmă baza aleasă pentru a genera biletele.").props('role="status"')

        def show_status(message):
            output.clear()
            with output:
                ui.label(message).props('role="status"').classes("text-caption")

        async def calculate():
            nonlocal revision, running, applied_budget, cached_analysis, displayed, waiting_for_choice
            revision += 1
            waiting_for_choice = False
            if output.is_deleted:
                return
            if not panel.value:
                generate.props("aria-busy=false", remove="loading")
                return
            generate.props("loading aria-busy=true")
            show_status(f"Se pregătește recomandarea pentru {int(slider.value)} lei…")
            if running:
                return
            running = True
            try:
                while panel.value and not output.is_deleted and not waiting_for_choice:
                    current = revision
                    budget_settings, method_settings = selected_budget(), selected_method()
                    amount, goal, game_id = budget_settings
                    inverse_mode, forced = method_settings

                    def obsolete():
                        return (current != revision or not panel.value or output.is_deleted
                                or selected_budget() != budget_settings or selected_method() != method_settings)

                    try:
                        if on_budget_plan is not None and applied_budget != budget_settings:
                            templates = await run.io_bound(recommend_games, *budget_settings)
                            if obsolete():
                                continue
                            if templates is None:
                                raise RuntimeError("Planificarea bugetului a fost întreruptă.")
                            on_budget_plan(templates)
                            applied_budget = budget_settings
                        analysis_key = (budget_settings, inverse_mode)
                        if cached_analysis is None or cached_analysis[0] != analysis_key:
                            comparison.clear()
                            inputs = forecast_inputs(game_id)
                            games = [game_id] if game_id else [key for key in game_options if key != "all"]
                            show_status(f"Se compară bazele de selecție pentru {amount} lei…")
                            reports = await run.io_bound(_compare_games, amount, goal, games, inputs, inverse_mode)
                            if obsolete():
                                continue
                            if reports is None:
                                raise RuntimeError("Analiza intervalelor a fost întreruptă.")
                            cached_analysis = (analysis_key, inputs, reports)
                        _, inputs, reports = cached_analysis
                        if displayed != (id(reports), forced):
                            comparison.clear()
                            with comparison:
                                ui.label("Analiza bazelor de selecție").classes("text-subtitle1 text-weight-bold")
                                for report in reports:
                                    gid = report["game"]
                                    _range_comparison(
                                        report, game_options[gid], manual_choices.get((gid, inverse_mode), "auto"),
                                        change_base, forced, expanded=len(reports) == 1,
                                    )
                                if not forced:
                                    ui.button("Generează cu baza aleasă", icon="confirmation_number", on_click=calculate).props("no-caps")
                            displayed = (id(reports), forced)
                        selections = {}
                        for report in reports:
                            gid = report["game"]
                            choice = manual_choices.get((gid, inverse_mode), "auto")
                            if forced or choice == _range_label(_INVERSE_RANGE):
                                limits = _INVERSE_RANGE
                            elif choice == "auto":
                                limits = report.get("recommended_range")
                            else:
                                limits = None
                            selections[gid] = limits
                        show_status(f"Se generează biletele pentru {amount} lei…")
                        plans = await run.io_bound(_generate_selection, amount, goal, selections, inputs, inverse_mode)
                        if plans is None:
                            raise RuntimeError("Generarea biletelor a fost întreruptă.")
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Calcul LR la %d lei pentru %s (%s)", amount, goal, game_id or "all"
                        )
                        plans = None
                    if obsolete():
                        continue
                    output.clear()
                    with output:
                        if plans is None:
                            ui.label("Calculul recomandării nu a putut fi finalizat.").classes("text-warning")
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

        panel.on_value_change(refresh)
        generate.on_click(show_recommendation)
        inverse.on_value_change(show_recommendation)
        range_filter.on_value_change(calculate)
        slider.on_value_change(show_recommendation)
        game.on_value_change(refresh)
        objective.on_value_change(refresh)
        for control in scoring_controls:
            control.on_value_change(refresh)
        return refresh
