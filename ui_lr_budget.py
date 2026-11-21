"""Compare Romanian reduced-scheme tickets within the selected budget."""

import logging

from nicegui import run, ui

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, MIN_BUDGET_LEI
from loto_enterprise.core.lr_budget_forecast import recommend_playable_games
from loto_enterprise.core.lotteries import games_for_country
from loto_enterprise.core.ro_text import count
from ui_lr_portfolio import _codes, _percent, _price


_OBJECTIVES = {FOUR_PLUS: "4+ pe o variantă", ANY_PRIZE: "Orice premiu"}
_DEFAULT_BUDGET_LEI = 200
_MAX_SLIDER_BUDGET_LEI = 400


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
            origin = "Pool din generare" if forecast["generated"] else "Pool calculat cu setările generatorului"
            code = f" · Cod {forecast['code']}" if forecast["code"] is not None else ""
            ui.label(f"{origin} · {len(pool)} numere{code}").classes("text-weight-bold")
            ui.label(", ".join(map(str, pool))).classes("text-body2")
            if forecast["jokers"]:
                ui.label("Pool Urna 2 · " + ", ".join(map(str, forecast["jokers"]))).classes("text-caption")
            date = f" · ultima extragere {forecast['date']}" if forecast["date"] else ""
            history = count(forecast["rows"], "extrageri folosite", "extragere folosită")
            ui.label(f"{forecast['filename']}{date}" + (f" · {history}" if forecast["rows"] else "")).classes("text-caption")
        if error := plan.get("forecast_error"):
            ui.label(error).classes("text-warning")
        if rows:
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
                    if selection.get("hit_scope") == "pool" and hits is not None and random is not None and draws:
                        evidence += (
                            f" · walk-forward 4+ în pool · {hits} față de {random} pentru martorul aleator,"
                            f" din {draws} extrageri"
                        )
                    if selection.get("reason") not in (None, "winner"):
                        evidence += " · metodă de rezervă"
                ui.label(f"Metoda pool-ului · {evidence}").classes("text-caption")
            bonus = forecast["joker_selection"] or {}
            if bonus.get("method"):
                ui.label(f"Urna 2 Joker · {_bench_method(bonus)}").classes("text-caption")


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
        ui.label(_codes(area["code"] for ticket in first["tickets"] for area in ticket)).classes(
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
        ("game", "Joc"), ("tickets", "Bilete"), ("variants", "Variante"),
        ("price", "Total cu taxă"), ("chance", "Șansă teoretică"),
        ("bound", "Limită superioară"),
    ]
    rows = []
    for plan in records:
        upper = plan["upper"]
        playable = bool(plan["tickets"])
        rows.append({
            "game": plan["game_name"], "tickets": len(plan["tickets"]),
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
        _ticket_details(plan)


def lr_budget_slider():
    budget_label = ui.label().classes("text-bold mt-2")
    slider = ui.slider(
        min=MIN_BUDGET_LEI, max=_MAX_SLIDER_BUDGET_LEI, step=1, value=_DEFAULT_BUDGET_LEI,
    ).props('aria-label="Buget pentru bilete LR" label').classes("w-full")
    budget_label.bind_text_from(
        slider, "value", lambda value: f"Buget bilete LR · {int(value)} lei"
    )

    with ui.row().classes("w-full justify-between text-caption"):
        ui.label(f"{MIN_BUDGET_LEI} lei")
        ui.label(f"{_MAX_SLIDER_BUDGET_LEI} lei")
    return slider


def lr_budget_panel(slider, forecast_inputs, *scoring_controls):
    with ui.expansion("Bilete LR în buget", value=False).classes("w-full") as panel:
        ui.label(
            "Șansa teoretică se calculează pentru o extragere uniformă. "
            "Zonele folosesc pool-ul generatorului pentru jocul respectiv. Totalul include taxa de bilet."
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

        async def refresh():
            nonlocal revision, running
            revision += 1
            if output.is_deleted:
                return
            output.clear()
            if not panel.value:
                return
            with output:
                ui.label("Se compară schemele din pool…").classes("text-caption")
            if running:
                return
            running = True
            try:
                while panel.value and not output.is_deleted:
                    current = revision
                    amount, goal = int(slider.value), objective.value
                    game_id = None if game.value == "all" else game.value
                    try:
                        plans = await run.io_bound(
                            recommend_playable_games, amount, goal, game_id, **forecast_inputs(game_id),
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Calcul LR la %d lei pentru %s (%s)", amount, goal, game_id or "all"
                        )
                        plans = None
                    if output.is_deleted:
                        return
                    if current != revision:
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

        async def on_budget_change():
            if not panel.value:
                panel.set_value(True)
                return
            await refresh()

        panel.on_value_change(refresh)
        game.on_value_change(refresh)
        slider.on_value_change(on_budget_change)
        objective.on_value_change(refresh)
        for control in scoring_controls:
            control.on_value_change(refresh)
        return refresh
