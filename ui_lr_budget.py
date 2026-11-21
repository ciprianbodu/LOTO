"""Compare Romanian reduced-scheme tickets within the selected budget."""

import logging

from nicegui import run, ui

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, MIN_BUDGET_LEI, recommend_games
from loto_enterprise.core.lotteries import games_for_country
from loto_enterprise.core.ro_text import count
from ui_lr_portfolio import _codes, _percent, _price


_OBJECTIVES = {FOUR_PLUS: "4+ pe o variantă", ANY_PRIZE: "Orice premiu"}


def _columns(fields):
    return [
        {"name": key, "field": key, "label": label, "align": "left"}
        for key, label in fields
    ]


def _ticket_details(plan):
    fields = [
        ("ticket", "Bilet"), ("zone", "Zonă"), ("code", "Cod LR"),
        ("pool", "Numere în pool"), ("numbers", "Numere de completat (exemplu)"),
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
            "numbers": ", ".join(map(str, area["numbers"])),
            "joker": area.get("joker"),
        }
        for ticket_number, ticket in enumerate(plan["tickets"], start=1)
        for zone, area in enumerate(ticket)
    ]
    with ui.expansion(f"Bilete · {plan['game_name']}").classes("w-full"):
        ui.label(
            "Numerele ilustrează completarea pentru care este calculată probabilitatea."
        ).classes("text-caption")
        table = ui.table(
            columns=_columns(fields), rows=rows, row_key="id",
            pagination={"rowsPerPage": 12},
        ).props("dense flat wrap-cells").classes("w-full")
        if len(rows) <= 12:
            table.props("hide-bottom")


def _render_recommendations(records, budget_lei, objective):
    if not records:
        ui.label("Nu există o combinație de scheme LR în bugetul ales.").classes("text-warning")
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
        ("price", "Total cu taxă"), ("chance", "Șansă pe extragere"),
        ("bound", "Limită superioară"),
    ]
    rows = []
    for plan in records:
        upper = plan["upper"]
        rows.append({
            "game": plan["game_name"], "tickets": len(plan["tickets"]),
            "variants": plan["variants"], "price": _price(plan["price_bani"]),
            "chance": _percent(plan["attained"], plan["denominator"]),
            "bound": "Maxim demonstrat" if plan["optimal"] else (
                _percent(upper, plan["denominator"]) if upper is not None else "—"
            ),
        })
    ui.table(
        columns=_columns(fields), rows=rows, row_key="game", pagination={"rowsPerPage": 0},
    ).props("dense flat hide-bottom wrap-cells").classes("w-full")
    for plan in records:
        _ticket_details(plan)


def lr_budget_slider():
    budget_label = ui.label().classes("text-bold mt-2")
    slider = ui.slider(min=MIN_BUDGET_LEI, max=200, step=1, value=200).props(
        'aria-label="Buget pentru bilete LR" label'
    ).classes("w-full")
    budget_label.bind_text_from(
        slider, "value", lambda value: f"Buget bilete LR · {int(value)} lei"
    )

    def extend_range():
        maximum = int(slider.props["max"]) * 2
        slider.props["max"] = maximum
        limits.set_text(f"Interval · {MIN_BUDGET_LEI}–{maximum} lei")

    with ui.row().classes("w-full items-center justify-between"):
        limits = ui.label(f"Interval · {MIN_BUDGET_LEI}–200 lei").classes("text-caption")
        ui.button("Extinde intervalul", on_click=extend_range).props("flat dense no-caps")
    return slider


def lr_budget_panel(slider):
    with ui.expansion("Bilete LR în buget", value=False).classes("w-full") as panel:
        ui.label(
            "Compară schemele oficiale pentru o singură extragere uniformă. "
            "Totalul include taxa pentru fiecare bilet. La Joker, fiecare zonă folosește o singură bilă Joker."
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
            output.clear()
            if not panel.value:
                return
            with output:
                ui.label("Se compară biletele…").classes("text-caption")
            if running:
                return
            running = True
            try:
                while panel.value and not output.is_deleted:
                    current = revision
                    amount, goal = int(slider.value), objective.value
                    game_id = None if game.value == "all" else game.value
                    try:
                        plans = await run.io_bound(recommend_games, amount, goal, game_id)
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
