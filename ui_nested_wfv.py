"""Run a dated, out-of-sample selection audit without modifying the active benchmark."""

import logging

from nicegui import run, ui

from loto_enterprise.core.lotteries import GAMES, lottery_by_bench_key


_POLICIES = (
    ("frequency", "frequency", "Fixă"),
    ("decision_frozen", "Decizie pe pool", "La începutul testului"),
    ("decision_block26", "Decizie pe pool", "La fiecare 26 de zile de extragere"),
    ("decision_hindsight", "Decizie pe pool", "La final, retrospectiv"),
    ("ticket_frozen", "Hituri pe variantă", "La începutul testului"),
    ("ticket_block26", "Hituri pe variantă", "La fiecare 26 de zile de extragere"),
    ("ticket_daily", "Hituri pe variantă", "Înaintea fiecărei zile"),
    ("ticket_hindsight", "Hituri pe variantă", "La final, retrospectiv"),
)


def _nested_results(result):
    game = lottery_by_bench_key(result["key"])
    single = result["key"] == game.bench_key_urna2
    title = f"{game.display} · Urna 2" if single else game.display
    if scheme := result["scheme"]:
        kind = "Cod" if scheme["official_for_game"] else "Cod simulat"
        title += f" · {kind} {scheme['code']} · {scheme['variants']} variante · pool {result['k']}"
    ui.label(title).classes("text-subtitle1")
    ui.label(f"{result['n']} extrageri, între {result['start']} și {result['end']}, "
             f"în {result['n_dates']} zile de extragere.").classes("text-caption")
    labels = [("policy", "Criteriu"), ("origin", "Când s-a ales metoda")]
    labels += [("primary", "Bile ghicite")] if single else [
        ("pool", "5+ în pool"), ("primary", "5+ pe variantă"), ("four", "4+ pe variantă"),
    ]
    rows = []
    for key, policy, origin in _POLICIES:
        counts = result["arms"][key]
        rows.append({"id": key, "policy": "Potrivirea bilei" if single and key.startswith("ticket") else policy,
                     "origin": origin, "pool": counts["pool_5plus"], "primary": counts["primary"],
                     "four": counts["ticket_4plus"]})
    columns = [{"name": name, "label": label, "field": name, "align": "left"} for name, label in labels]
    ui.table(columns=columns, rows=rows, row_key="id",
             pagination={"rowsPerPage": 0}).props("dense flat hide-bottom").classes("w-full")


def nested_wfv_panel(initial_key, selected_lr_scheme):
    games = {key: (f"{g.display} · Urna 2" if key == g.bench_key_urna2 else g.display)
             for g in GAMES for key in g.bench_keys}
    with ui.expansion("Walk-forward cu alegerea metodei înainte de test", value=False).classes("w-full"):
        ui.label("Pe ultimele 30% din zilele eligibile, compară o metodă fixată la început cu "
                 "una realeasă din trecut. Rândurile retrospective aleg metoda după ce perioada "
                 "s-a încheiat și permit măsurarea efectului acestei alegeri.").classes("text-body2")
        game = ui.select(games, value=initial_key, label="Joc sau urnă").classes("w-full sm:w-96")
        ui.label("Calculul folosește CSV-ul din arhiva aplicației, nouă metode, istoricul anterior "
                 "integral și limita de două consecutive. Pentru România preia codul LR selectat "
                 "la lansare. La jocurile străine simulează o schemă pe 10 numere.").classes("text-caption")
        status = ui.label("").classes("text-caption")
        output = ui.column().classes("w-full gap-1")
        busy = False

        async def calculate():
            nonlocal busy
            if busy:
                return
            busy = True
            button.disable()
            game.disable()
            output.clear()
            key = game.value
            lot = lottery_by_bench_key(key)
            try:
                from scripts.analysis.nested_wfv_2026_11_18 import study_game

                code = selected_lr_scheme(lot.game_id).code if lot.is_romanian and key != lot.bench_key_urna2 else None
                status.set_text(f"Se evaluează {games[key]} pe extragerile din {lot.csv}.")
                result = await run.cpu_bound(study_game, key, scheme_code=code)
                if not output.is_deleted and result is not None:
                    status.set_text(f"Sursă · {lot.csv}")
                    with output:
                        _nested_results(result)
                elif not status.is_deleted:
                    status.set_text("Calcul întrerupt.")
            except Exception as exc:
                logging.getLogger("app_nicegui").exception("Validarea cronologică a eșuat pentru %s", key)
                if not status.is_deleted:
                    status.set_text(f"Validarea nu a putut fi finalizată. {exc}")
            finally:
                busy = False
                if not button.is_deleted:
                    button.enable()
                    game.enable()

        button = ui.button("Calculează validarea cronologică", on_click=calculate).props("no-caps")
