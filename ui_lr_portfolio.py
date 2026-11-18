"""Compare exact main-number hit probabilities for complete Romanian reduced schemes."""
from collections import Counter
import logging

from nicegui import run, ui

from covering.lr_schemes import lr_schemes


def _percent(count, denominator):
    return f"{100 * count / denominator:.6f}%".replace(".", ",")


def _price(bani):
    return f"{bani // 100},{bani % 100:02d} lei"


def _codes(codes):
    return " + ".join(f"{n} × Cod {code}" if n > 1 else f"Cod {code}"
                      for code, n in Counter(codes).items())


def _render_portfolios(records):
    first = records[0]
    ui.label(f"{first['game']} · Cod {first['base']} · buget {_price(first['budget_bani'])}").classes("text-subtitle1")
    columns = (("target", "Prag"), ("original", "Codul inițial"), ("codes", "Scheme comparate"),
               ("variants", "Variante"), ("price", "Total cu taxă"), ("probability", "Șansă pe extragere"),
               ("bound", "Limită superioară"))
    rows = []
    for r in records:
        total = r["denominator"]
        rows.append(dict(target=f"{r['threshold']}+", original=_percent(r["base_hits"], total),
                         codes=_codes(r["codes"]), variants=r["variants"], price=_price(r["price_bani"]),
                         probability=_percent(r["attained"], total), bound=_percent(r["upper"], total)))
    ui.table(columns=[dict(name=k, label=label, field=k, align="left") for k, label in columns],
             rows=rows, row_key="target", pagination={"rowsPerPage": 0}).props("dense flat hide-bottom").classes("w-full")
    groups = {}
    for r in records:
        groups.setdefault((tuple(r["codes"]), r["anchor_size"], r["optimal"]), []).append(r)
    for group in groups.values():
        r = group[0]
        targets = " și ".join(f"{item['threshold']}+" for item in group)
        if r["codes"] == [r["base"]]:
            status = ("atinge maximul la acest buget" if r["optimal"]
                      else "este cea mai bună construcție găsită, cu maximul încă nedeterminat")
            ui.label(f"La {targets}, codul inițial {status}.").classes("text-caption")
            continue
        if len(r["codes"]) == 1:
            construction = f"un singur pool pentru Cod {r['codes'][0]}"
        elif r["anchor_size"]:
            construction = (f"{len(r['codes'])} pooluri cu {r['anchor_size']} numere comune tuturor "
                            "și cu toate celelalte numere distincte")
        else:
            construction = f"{len(r['codes'])} pooluri fără numere comune"
        status = "Maxim demonstrat" if r["optimal"] else "Acoperire demonstrată"
        ui.label(f"{targets} · {status} cu {construction}.").classes("text-caption")

        side = r["certified_other_thresholds"]
        for threshold, count in side.items():
            previous = r["base_thresholds"][threshold]
            if threshold in [item["threshold"] for item in group]:
                continue
            if count < previous or (r["game"] == "6/49" and threshold in (3, 6)):
                label = "Orice premiu (3+)" if threshold == 3 and r["game"] == "6/49" else f"{threshold}+"
                text = f"{label} · {_percent(previous, r['denominator'])} → {_percent(count, r['denominator'])}"
                ui.label(text).classes("text-caption text-warning" if count < previous else "text-caption")
        if r["game"] == "6/49" and 3 not in side:
            ui.label("Șansa de 3+ pentru această suprapunere nu a fost calculată.").classes("text-caption")


def lr_portfolio_panel(initial_code):
    from covering.lr_portfolio import compare_portfolios

    options = {f"{game}:{s.code}": f"{game if game != 'joker' else 'Joker'} · Cod {s.code} · {s.size} variante"
               for game in ("6/49", "5/40", "joker") for s in lr_schemes(game)}
    with ui.expansion("Compară schemele LR la același buget", value=False).classes("w-full"):
        ui.label("Probabilitatea ca cel puțin o variantă să atingă pragul la o extragere uniformă. "
                 "Calculul include taxa pe bilet și toate schemele reduse oficiale complete. "
                 "La Joker se numără bilele principale, iar la 5/40 se iau toate cele șase numere extrase.").classes("text-body2")
        code = ui.select(options, value=f"6/49:{initial_code}", label="Joc și cod de referință").classes("w-full sm:w-96")
        output = ui.column().classes("w-full gap-1")
        code.on_value_change(lambda _: output.clear())
        busy = False

        async def compare():
            nonlocal busy
            if busy:
                return
            busy = True
            code.disable()
            button.disable()
            output.clear()
            game, base = code.value.split(":")
            with output:
                ui.label("Se calculează acoperirea schemelor…").classes("text-caption")
            try:
                records = await run.cpu_bound(compare_portfolios, game, int(base))
                if not output.is_deleted:
                    output.clear()
                    with output:
                        if records:
                            _render_portfolios(records)
                        else:
                            ui.label("Calcul întrerupt.").classes("text-caption")
            except Exception:
                logging.getLogger("app_nicegui").exception("Comparația LR a eșuat pentru %s Cod %s", game, base)
                if not output.is_deleted:
                    output.clear()
                    with output:
                        ui.label("Calculul schemelor nu a putut fi finalizat.").classes("text-warning")
            finally:
                busy = False
                if not button.is_deleted:
                    button.enable()
                    code.enable()
        button = ui.button("Compară acoperirea", on_click=compare).props("no-caps")
