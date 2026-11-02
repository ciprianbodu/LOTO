"""Explicit research/prospective controls inside Analiză & Clasament.

Nothing starts at app startup or Re-Bench; existing generation and histories
retain their contract. Long calculations and official I/O leave the UI thread.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import threading

from nicegui import ui


def render_selection_experiments():
    with ui.expansion("Evaluare cronologică și registru prospectiv", value=False).classes("w-full"):
        ui.label("Selecția metodei și a parametrilor se repetă înaintea fiecărei extrageri, pe date anterioare. Replay-ul retrospectiv nu demonstrează avantaj predictiv.").classes("text-caption")
        ui.label("Calcul explicit, separat de Re-Bench. Folosește istoricul local al jocului și toate metodele din registru.").classes("text-caption")
        with ui.row().classes("items-center w-full"):
            game = ui.select({"loto_6_49": "Loto 6/49", "joker": "Joker (ambele urne)", "loto_5_40": "Loto 5/40"}, value="loto_6_49", label="Joc").classes("w-52")
            pools = ui.input("Pooluri candidate", value="16").classes("w-40")
            lookbacks = ui.input("Istorice candidate (0 = tot)", value="0").classes("w-52")
            objective = ui.select({"4plus": "Cel puțin 4 numere pe variantă", "any_prize": "Cel puțin un premiu"}, value="4plus", label="Obiectiv").classes("w-64")
            method_target = ui.select({3: "3+ în pool", 4: "4+ în pool"}, value=4,
                                     label="Ținta selecției metodelor").classes("w-60")
        ui.label("Ținta metodelor și obiectivul variantelor sunt declarate separat. La 5/40 ținta metodelor este minimum 4+; Urna 2 Joker folosește 1/1.").classes("text-caption")
        with ui.row().classes("w-full"):
            budget = ui.number("Variante distincte / strategie", value=8, min=1, precision=0)
            inner = ui.number("Extrageri anterioare pentru selecție", value=30, min=5, precision=0)
            outer = ui.number("Extrageri pentru evaluare", value=5, min=1, precision=0)
            price = ui.number("Preț variantă (lei)", value=8, min=.01, precision=2)
            fees = ui.number("Taxe totale pe bilete / strategie (lei)", value=None, min=0, precision=2)
        ui.label("Taxele lăsate necompletate înseamnă cost complet și net necunoscute. Introdu explicit 0 dacă presupui lipsa taxelor. Bugetul rămâne fix, indiferent de pierderi.").classes("text-caption")
        with ui.expansion("Parametrii candidați", value=False).classes("w-full"):
            ui.label("Poolurile și lungimile istoricului formează o grilă; opțiunile de mai jos se aplică fiecărui candidat. Toate alegerile folosesc numai extrageri anterioare țintei.").classes("text-caption")
            with ui.row():
                guarantee = ui.number("Garanție cerută", value=6, min=1, max=6, precision=0).classes("w-40")
                consecutive = ui.number("Limită consecutive (0 = fără)", value=0, min=0, precision=0).classes("w-60")
                recent = ui.number("Penalizează ultimele N extrageri", value=0, min=0, precision=0).classes("w-64")
                factor = ui.number("Factor penalizare", value=.5, min=0, max=1).classes("w-40")
                minimum = ui.number("Număr minim (0 = liber)", value=0, min=0, precision=0).classes("w-52")
                maximum = ui.number("Număr maxim (0 = liber)", value=0, min=0, precision=0).classes("w-52")
            samples = ui.number("Extrageri uniforme pentru estimarea șanselor", value=20_000, min=100, precision=0).classes("w-96 max-w-full")
            design_samples = ui.number("Extrageri pentru căutarea pe univers", value=4000, min=100, precision=0).classes("w-96 max-w-full")
        def change_game(event):
            defaults = {"loto_6_49": (16, 8, 6), "joker": (12, 7, 5), "loto_5_40": (19, 5, 5)}
            pool, tariff, pick = defaults[event.value]
            pools.set_value(str(pool))
            price.set_value(tariff)
            guarantee.set_value(pick)
        game.on_value_change(change_game)
        status = ui.label("").classes("text-caption")
        output = ui.column().classes("w-full")
        cancellation = threading.Event()
        report = {}

        async def calculate():
            from loto_enterprise.core.chronological_evaluation import Parameters, Protocol
            from scripts.evaluate_chronological import run_experiment, markdown_report

            try:
                pool_values = [int(n.strip()) for n in pools.value.split(",")]
                history_values = [int(n.strip()) for n in lookbacks.value.split(",")]
                parameters = tuple(Parameters(pool_size=p, lookback=h, guarantee=int(guarantee.value),
                    max_consecutive_run=int(consecutive.value), recent_penalty_draws=int(recent.value),
                    recent_penalty_factor=float(factor.value), restrict_base_min=int(minimum.value),
                    restrict_base_max=int(maximum.value)) for p in pool_values for h in history_values)
                protocol = Protocol(game=game.value, budget=int(budget.value), parameters=parameters,
                    objective=objective.value, hit_target=int(method_target.value), inner_draws=int(inner.value),
                    variant_price_bani=round(price.value * 100),
                    ticket_fees_bani=None if fees.value is None else round(fees.value * 100),
                    probability_samples=int(samples.value), design_samples=int(design_samples.value))
                protocol.validate()
                count = int(outer.value)
                start.disable()
                cancellation.clear()
                status.set_text("Calculez selecția cronologică. Toate metodele sunt păstrate; calculul poate dura.")
                def progress(done, total, day):
                    if cancellation.is_set():
                        raise InterruptedError("Calcul anulat")
                result = await asyncio.to_thread(run_experiment, protocol, outer_draws=count, progress=progress,
                                                cancel_check=cancellation.is_set)
                report.clear()
                report.update(result)
                output.clear()
                with output:
                    ui.markdown(markdown_report(result)).classes("w-full overflow-x-auto")
                status.set_text("Calcul încheiat. Rezultatul este retrospectiv; premiile lipsă rămân indisponibile.")
            except Exception as exc:
                status.set_text(f"Calcul oprit: {exc}")
            finally:
                start.enable()

        with ui.row():
            start = ui.button("Evaluează selecția cronologică", on_click=calculate).props("no-caps")
            ui.button("Oprește calculul", on_click=cancellation.set).props("no-caps")
            ui.button("Descarcă JSON cu variantele", on_click=lambda: ui.download(json.dumps(report, ensure_ascii=False, indent=2).encode(), "evaluare_cronologica.json") if report else ui.notify("Rulează întâi evaluarea")).props("no-caps")

        ui.separator()
        ui.label("Registru prospectiv al variantelor").classes("text-subtitle1")
        ui.label("Generarea obișnuită salvează automat variantele. Alege înregistrarea și fixează extragerea viitoare înainte de începutul zilei respective, ora României. Rezultatul se adaugă după încheierea zilei și verificarea arhivei loto.ro.").classes("text-caption")
        ui.label("Registrul local păstrează o amprentă și blochează modificările prin aplicație. Ora calculatorului nu este o certificare externă. Taxele declarate se aplică identic martorului aleator, la același număr de variante.").classes("text-caption")
        records = ui.select({}, label="Variante înregistrate").classes("w-full")
        with ui.row():
            target = ui.input("Extragere viitoare (AAAA-LL-ZZ)", value=(datetime.now().date() + timedelta(days=3)).isoformat())
            ledger_fees = ui.number("Taxe totale / strategie (lei, declarate)", value=None, min=0, precision=2)
        ledger_status = ui.label("").classes("text-caption")
        ledger_summary = ui.column().classes("w-full")

        async def load_records():
            from loto_enterprise.core.prospective_ledger import ProspectiveLedger
            from scripts.evaluate_chronological import rate, money

            try:
                ledger = ProspectiveLedger()
                exported = await asyncio.to_thread(ledger.export)
                options = {e["record_id"]: f"{e['record_id']} · {e['payload']['variants']} variante · {e['recorded_at']}"
                           for e in exported["events"] if e["kind"] == "generation"}
                records.set_options(options)
                summary = await asyncio.to_thread(ledger.summary)
                ledger_summary.clear()
                with ledger_summary:
                    ui.label(f"Generări: {summary['generations']} · Fixate: {summary['commitments']} · Cu rezultat: {summary['settled']} · În așteptare: {summary['unsettled']}")
                    ui.label("Rezultate financiare numai pentru extragerile cu rezultat oficial verificat.").classes("text-caption")
                    rows = []
                    for name in ("generated", "random_reference"):
                        s = summary[name]
                        rows.append({"strategie": name, "premiu [95%]": rate(s["any_prize"]), "4+ [95%]": rate(s["4plus"]),
                                     "recuperare [95%]": rate(s["recovered_cost"]),
                                     "cost": money(s["full_cost_bani"]), "brut": money(s["gross_bani"]), "net": money(s["net_bani"])})
                    ui.table(columns=[{"name": k, "label": k, "field": k} for k in rows[0]], rows=rows).classes("w-full")
                    ui.label("Sinteză descriptivă înainte de impozit. Eșantionul prospectiv nu demonstrează singur un avantaj; intervalele și toate evenimentele sunt în export.").classes("text-caption")
                ledger_status.set_text(f"Lanț verificat: {exported['head_sha256'][:16]}…")
            except Exception as exc:
                ledger_status.set_text(str(exc))

        async def commit_record():
            from loto_enterprise.core.prospective_ledger import ProspectiveLedger

            try:
                if not records.value or ledger_fees.value is None:
                    raise ValueError("Alege variantele și completează taxele, inclusiv 0 dacă aceasta este ipoteza")
                receipt = await asyncio.to_thread(ProspectiveLedger().commit, records.value, target.value,
                                                 ticket_fees_bani=round(ledger_fees.value * 100))
                await load_records()
                ledger_status.set_text(f"Extragere fixată; variantele sunt păstrate. Amprentă: {receipt}")
            except Exception as exc:
                ledger_status.set_text(f"Înregistrare refuzată: {exc}")

        async def settle_record():
            from loto_enterprise.core.prospective_ledger import ProspectiveLedger
            from scripts.prospective_registry import settle_from_history

            try:
                if not records.value:
                    raise ValueError("Alege înregistrarea")
                receipt = await asyncio.to_thread(settle_from_history, ProspectiveLedger(), records.value)
                await load_records()
                ledger_status.set_text(f"Rezultat oficial adăugat: {receipt}")
            except Exception as exc:
                ledger_status.set_text(f"Rezultat indisponibil: {exc}")

        async def export_records():
            from loto_enterprise.core.prospective_ledger import ProspectiveLedger

            try:
                result = await asyncio.to_thread(ProspectiveLedger().export)
                ui.download(json.dumps(result, ensure_ascii=False, indent=2).encode(), "registru_prospectiv.json")
            except Exception as exc:
                ledger_status.set_text(str(exc))

        with ui.row():
            ui.button("Încarcă registrul", on_click=load_records).props("no-caps")
            ui.button("Fixează extragerea viitoare", on_click=commit_record).props("no-caps")
            ui.button("Adaugă rezultatul oficial", on_click=settle_record).props("no-caps")
            ui.button("Exportă registrul și amprentele", on_click=export_records).props("no-caps")
