"""Compare Romanian reduced-scheme tickets within the selected budget."""

import logging
from collections import Counter

from nicegui import run, ui

from covering.lr_budget import ANY_PRIZE, DEFAULT_BUDGET_LEI, FOUR_PLUS, MIN_BUDGET_LEI, recommend_games
from loto_enterprise.core.lr_budget_forecast import recommend_playable_games
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
            code = f" · Cod {forecast['code']}" if forecast["code"] is not None else ""
            if excluded is not None:
                ui.label("Pool normal exclus").classes("text-weight-bold")
                ui.label(", ".join(map(str, excluded))).classes("text-body2")
                eligible = count(forecast["eligible_count"], "numere eligibile", "număr eligibil")
                if limits := forecast.get("inverse_range"):
                    eligible += f" între {limits[0]} și {limits[1]} inclusiv"
                origin = f"Pool invers · {count(len(pool), 'numere selectate', 'număr selectat')} din {eligible}"
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


def lr_budget_slider(value=DEFAULT_BUDGET_LEI):
    value = value if type(value) is int else DEFAULT_BUDGET_LEI
    value = max(MIN_BUDGET_LEI, min(_MAX_SLIDER_BUDGET_LEI, value))
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


def lr_budget_panel(slider, forecast_inputs, *scoring_controls, on_budget_plan=None):
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
        objective = ui.toggle(_OBJECTIVES, value=FOUR_PLUS).props(
            'aria-label="Obiectivul biletelor LR" no-caps toggle-color=primary'
        )
        output = ui.column().classes("w-full gap-2")
        revision = 0
        running = False
        applied_budget = None

        def selected_budget():
            return int(slider.value), objective.value, None if game.value == "all" else game.value

        def selected_method():
            enabled = bool(inverse.value)
            return enabled, _INVERSE_RANGE if enabled and range_filter.value else None

        async def refresh():
            nonlocal revision, running, applied_budget
            revision += 1
            if output.is_deleted:
                return
            output.clear()
            if not panel.value:
                generate.props("aria-busy=false", remove="loading")
                return
            generate.props("loading aria-busy=true")
            with output:
                inverse_mode, limits = selected_method()
                mode = " inversă" if inverse_mode else ""
                interval = f" · interval {limits[0]}–{limits[1]}" if limits else ""
                ui.label(f"Se calculează recomandarea{mode} pentru {int(slider.value)} lei{interval}…").props(
                    'role="status"'
                ).classes("text-caption")
            if running:
                return
            running = True
            try:
                while panel.value and not output.is_deleted:
                    current = revision
                    budget_settings = selected_budget()
                    amount, goal, game_id = budget_settings
                    method_settings = selected_method()
                    inverse_mode, limits = method_settings
                    try:
                        if on_budget_plan is not None and applied_budget != budget_settings:
                            templates = await run.io_bound(recommend_games, *budget_settings)
                            if output.is_deleted or not panel.value:
                                return
                            if current != revision or selected_budget() != budget_settings:
                                continue
                            if templates is None:
                                raise RuntimeError("Planificarea bugetului a fost întreruptă.")
                            on_budget_plan(templates)
                            applied_budget = budget_settings
                        plans = await run.io_bound(
                            recommend_playable_games, amount, goal, game_id,
                            inverse=inverse_mode, inverse_range=limits, **forecast_inputs(game_id),
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Calcul LR la %d lei pentru %s (%s)", amount, goal, game_id or "all"
                        )
                        plans = None
                    if output.is_deleted or not panel.value:
                        return
                    if (current != revision or selected_budget() != budget_settings
                            or selected_method() != method_settings):
                        continue
                    output.clear()
                    with output:
                        if plans is None:
                            ui.label("Calculul biletelor nu a putut fi finalizat.").classes("text-warning")
                        else:
                            _render_recommendations(plans, amount, goal)
                    return
            finally:
                running = False
                if not generate.is_deleted:
                    generate.props("aria-busy=false", remove="loading")

        async def show_recommendation():
            if not panel.value:
                panel.set_value(True)
                return
            await refresh()

        panel.on_value_change(refresh)
        generate.on_click(show_recommendation)
        inverse.on_value_change(show_recommendation)
        range_filter.on_value_change(show_recommendation)
        slider.on_value_change(show_recommendation)
        game.on_value_change(refresh)
        objective.on_value_change(refresh)
        for control in scoring_controls:
            control.on_value_change(refresh)
        return refresh
