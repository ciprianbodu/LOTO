"""Rezultatele studiului cronologic al ferestrelor, pe aceleași ținte pentru fiecare procent."""

import json
from pathlib import Path

from nicegui import ui


_STUDY = Path(__file__).parent / "scripts/analysis/history_windows_2026_11_18_results.json"


def history_windows_panel() -> None:
    with ui.expansion("Cât istoric ajută? Rezultate pe fiecare joc", value=False).classes("w-full"):
        try:
            study = json.loads(_STUDY.read_text(encoding="utf-8"))
            experiments = study["experiments"]
            if not experiments:
                raise ValueError("empty study")
        except (OSError, ValueError, KeyError):
            ui.label("Rezultatele comparației istorice sunt indisponibile.").classes("text-warning")
            return

        ui.label(f"Studiu retrospectiv din {study['date']}. Ultimele 30% din zilele eligibile "
                 "au fost rezervate evaluării finale. În fiecare rând, cele patru ferestre "
                 "au fost testate pe exact aceleași extrageri.").classes("text-body2")
        with ui.row().classes("w-full items-end gap-4"):
            choice = ui.select(
                {key: exp["title"] for key, exp in experiments.items()},
                value=next(iter(experiments)), label="Ce compari",
            ).classes("w-full sm:w-96")
            threshold = ui.toggle({5: "5+", 4: "4+"}, value=5).props(
                "aria-label='Pragul de hituri' toggle-color=primary"
            )
            ui.label("hituri").classes("text-caption")
        body = ui.column().classes("w-full gap-2")

        def show_results() -> None:
            experiment = experiments[choice.value]
            pcts = study["percentages"]
            cutoff = int(threshold.value)
            columns = [
                {"name": name, "field": name, "label": label, "align": "left"}
                for name, label in (("game", "Joc și rezultat măsurat"), ("n", "Extrageri"))
            ]
            for percentage in pcts:
                field = f"p{percentage}"
                columns.append({"name": field, "field": field, "label": f"{percentage}%", "align": "right"})
            columns.append({"name": "selected", "field": "selected", "label": "Ales anterior", "align": "right"})
            rows = []
            for result in experiment["games"]:
                row = {"game": result["label"], "n": result["n"], "selected": f"{result['selected']}%"}
                events = result["primary"] if cutoff == 5 else result["secondary"]
                row.update({f"p{pct}": count for pct, count in zip(pcts, events, strict=True)})
                rows.append(row)
            body.clear()
            with body:
                ui.label(experiment["description"]).classes("text-body2")
                ui.label(f"Fiecare celulă numără extragerile cu {cutoff}+ hituri. La Joker Urna 2, "
                         "celula numără întotdeauna bilele ghicite. Ultima coloană arată "
                         "procentul ales înainte de perioada finală.").classes("text-caption")
                ui.table(columns=columns, rows=rows, row_key="game",
                         pagination={"rowsPerPage": 0}).props("dense flat hide-bottom").classes("w-full")

        choice.on_value_change(lambda _: show_results())
        threshold.on_value_change(lambda _: show_results())
        show_results()
