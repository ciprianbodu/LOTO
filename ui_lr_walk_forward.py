"""Validate the LR ticket selection and inspect its historical tickets by draw."""
import logging
from datetime import date, datetime

from nicegui import ui

from loto_enterprise.core.ro_text import lei_text
from covering.lr_budget import ANY_PRIZE, SINGLE_SCHEME_MODE
from covering.lr_refinement_status import refinement_complete
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS
from lr_walk_forward_run import cancel_lr_walk_forward, lr_walk_forward_snapshot
from ui_shared import report_restricted_range

logger = logging.getLogger(__name__)


def _date(raw):
    return date.fromisoformat(str(raw)[:10]).strftime("%d.%m.%Y") if raw else "—"


def _range_text(restricted, bounds):
    if bounds is None:
        return "Interval neconfirmat"
    if restricted is None:
        return "Bază aleasă automat"
    return f"Pool restrâns între {bounds[0]} și {bounds[1]}" if restricted else "Baza completă"


def _choices(settings, result=None):
    bounds = report_restricted_range(settings, result)
    mode = "Metoda inversă" if settings.get("inverse") else "Metoda normală"
    goal = "orice premiu" if settings.get("objective") == ANY_PRIZE else "4+ pe o variantă"
    policy = " · Fixed Share pe bilete" if settings.get("selection_policy") == ONLINE_TICKET_EXPERTS else ""
    amount = lei_text(settings["budget_lei"])
    if settings.get("budget_mode") == SINGLE_SCHEME_MODE:
        amount = f"sumă orientativă {amount}"
    return (f"{mode} · {_range_text(settings.get('restricted'), bounds)} · {amount} · "
            f"{goal} · fereastră de test {settings['depth']:g}%{policy}")


def _numbers(values):
    return ", ".join(map(str, values or ())) or "—"


def _yes_no(value):
    if value is None:
        return "—"
    return "Da" if value else "Nu"


def _draw_table(draws, inverse, joker):
    fields = [("date", "Extragere"), ("training_rows", "Extrageri anterioare"), ("numbers", "Numere extrase")]
    if inverse:
        fields.append(("excluded_pool", "Pool direct exclus"))
    fields.extend([("pool", "Pool testat"), ("pool_hits", "Potriviri în pool"),
                   ("ticket_hits", "Maxim pe o variantă LR")])
    if joker:
        fields.extend([("joker_drawn", "Bila Joker extrasă"), ("jokers", "Pool Urna 2")])
        if inverse:
            fields.append(("excluded_jokers", "Bile Joker excluse"))
        fields.extend([("joker_hit", "Bila în pool"), ("ticket_joker_hit", "Joker și cel puțin un număr pe aceeași variantă")])
    fields.append(("objective_hit", "Obiectiv atins"))
    if any(draw.get("selected_method") for draw in draws):
        fields.append(("selected_method", "Metodă folosită"))
    if any(draw.get("error") for draw in draws):
        fields.append(("error", "Motivul omiterii"))
    columns = [{"name": key, "field": key, "label": label, "align": "left"} for key, label in fields]
    page_size = 10

    def rows(page):
        start = len(draws) - (page - 1) * page_size
        output = []
        for index in range(start - 1, max(0, start - page_size) - 1, -1):
            draw = draws[index]
            row = {key: draw.get(key) if draw.get(key) is not None else "—" for key, _ in fields}
            row.update(id=index, date=_date(draw.get("date")))
            if "selected_method" in row:
                row["selected_method"] = (draw.get("selected_method") or {}).get("method", "—")
            for key in ("numbers", "pool", "excluded_pool", "jokers", "excluded_jokers"):
                if key in row:
                    row[key] = _numbers(draw.get(key))
            for key in ("objective_hit", "joker_hit", "ticket_joker_hit"):
                if key in row:
                    row[key] = _yes_no(draw.get(key))
            output.append(row)
        return output

    table = ui.table(columns=columns, rows=rows(1), row_key="id", pagination={"rowsPerPage": 0})
    table.props("dense flat hide-bottom").classes("w-full max-w-full")
    pages = (len(draws) + page_size - 1) // page_size
    if pages > 1:
        def change_page(event):
            table.rows = rows(int(event.value))

        ui.pagination(1, pages, value=1, direction_links=True, on_change=change_page).props(
            'max-pages=6 aria-label="Pagina extragerilor validate"'
        )


def _game_result(entry):
    settings = entry["settings"]
    result = entry.get("result") or {}
    bounds = report_restricted_range(settings, result)
    if bounds is None:
        ui.label(f"{entry['name']} · Intervalul din validarea salvată este nevalid sau contradictoriu.").classes("text-warning")
        return
    with ui.column().classes("w-full min-w-0 gap-1"):
        ui.label(entry["name"]).classes("text-subtitle1 text-weight-bold")
        ui.label(f"{_choices(settings, result)} · {entry['source']}").classes("text-body2")
        selector = "dinamic" if settings["dynamic"] else "fix"
        lookback = settings["lookback"] or 100
        code = f"Cod LR {settings['scheme']}"
        if "variants" in result and "price_bani" in result:
            price = f"{result['price_bani'] / 100:.2f}".replace(".", ",")
            variants = "1 variantă" if result["variants"] == 1 else f"{result['variants']} variante"
            code += f" · {variants} · cost total {price} lei"
        if settings.get("selection_policy") == ONLINE_TICKET_EXPERTS:
            ui.label(f"{code} · Fixed Share · istoric pentru scoruri {lookback:g}%").classes("text-caption")
        else:
            ui.label(f"{code} · selector {selector} · istoric {lookback:g}% · "
                     f"ținta Re-Bench {settings['hit_target']}+ în pool").classes("text-caption")
        if error := entry.get("error") or result.get("error"):
            ui.label(error).classes("text-warning").props('role="status"')
            return
        if not refinement_complete(result):
            count = result.get("summary", {}).get("provisional_draws", 0)
            ui.label(f"Validare provizorie. Extrageri evaluate folosind o rafinare incompletă: {count}.").classes("text-warning")
        if result.get("max_consecutive_run") != LR_MAX_CONSECUTIVE_RUN:
            ui.label("Raport anterior regulii de maximum două numere consecutive. "
                     "Pornește o nouă validare pentru selecția actuală.").classes("text-warning")
        if (result.get("inverse") and result.get("replay_policy") != "daily"
                and not result.get("inverse_references_checked")):
            ui.label("Raport anterior verificării referinței directe. Reia validarea inversă pentru selecția actuală.").classes(
                "text-warning"
            )
        lot = require_lottery(entry["game"], "RO")
        summary = result["summary"]
        if result.get("requested_restricted") is None:
            base_label = "Baza inițială" if result.get("replay_policy") == "daily" else "Baza folosită în test"
            ui.label(f"{base_label} · {_range_text(result['restricted'], bounds)}").classes("text-body2")
        ui.label(
            f"Antrenare inițială până la {_date(result['training_end'])} · {summary['training_rows']} extrageri. "
            f"Test de la {_date(summary['first_date'])} până la {_date(summary['last_date'])} · "
            f"{summary['rows']} extrageri în {summary['days']} zile."
        ).classes("text-caption")
        low, high = bounds
        if (in_range := summary.get(f"all_main_in_{low}_{high}")) is not None:
            total = summary["rows"]
            share = f"{100 * in_range / total:.2f}%".replace(".", ",")
            ui.label(f"Toate cele {lot.draw_n} numere din urna principală au fost între {low} și {high} "
                     f"în {share} dintre extragerile testate ({in_range} din {total}).").classes("text-body2")
        evaluated = summary["objective_evaluated"]
        hits = summary["objective_hits"]
        if evaluated:
            rate = f"{100 * hits / evaluated:.2f}%".replace(".", ",")
            ui.label(f"Obiectiv atins la {hits} din {evaluated} extrageri evaluate ({rate}).").classes("text-weight-bold")
        else:
            ui.label("Obiectivul nu a putut fi evaluat la nicio extragere.").classes("text-warning")
        if summary["infeasible"]:
            ui.label(f"Extrageri fără bilet evaluabil · {summary['infeasible']}.").classes("text-warning")
        pool_counts = [f"{k}+ · {summary[f'pool_{k}plus']}" for k in range(3, lot.draw_n + 1)]
        n_played = summary["evaluated"]
        ticket_counts = []
        for threshold in range(3, lot.pick_n + 1):
            hits = summary[f"ticket_{threshold}plus"]
            ratio = f" ({100 * hits / n_played:.2f}%)".replace(".", ",") if n_played else ""
            ticket_counts.append(f"{threshold}+ · {hits}/{n_played}{ratio}")
        ui.label("Extrageri cu potriviri în pool · " + " / ".join(pool_counts)).classes("text-body2")
        ui.label("Extrageri cu potriviri pe aceeași variantă LR · " + " / ".join(ticket_counts)).classes("text-body2")
        if "any_prize_evaluated" in summary:
            unknown = n_played - summary["any_prize_evaluated"]
            status = f" · {unknown} rezultate nedeterminate" if unknown else ""
            ui.label(f"Orice premiu · {summary['any_prize_hits']} din {summary['any_prize_evaluated']} "
                     f"extrageri evaluabile{status}.").classes("text-body2")
            wins = summary["paid_winning_variants"]
            if wins is None:
                wins = f"{summary['paid_winning_variants_min']}–{summary['paid_winning_variants_max']}"
            cost = f"{summary['total_cost_bani'] / 100:.2f}".replace(".", ",")
            ui.label(f"Variante câștigătoare · {wins}. Cost în perioada testată · {cost} lei. "
                     f"Variante plătite · {summary['paid_variants']}, dintre care "
                     f"{summary['duplicate_variants']} repetări în aceeași extragere.").classes("text-body2")
        if summary.get("fallback_by_role") is not None:
            ui.label(f"Metodă de rezervă în selecția biletului sau a pool-ului direct exclus · "
                     f"{summary['fallback_draws']} din {n_played} extrageri jucate.").classes("text-body2")
        if lot.bench_key_urna2:
            if summary.get("ticket_joker_evaluated") is not None:
                ui.label(f"Urna 2 · bila extrasă în pool la {summary['joker_hits']} din {summary['joker_evaluated']} "
                         f"extrageri, pe aceeași variantă cu cel puțin un număr principal corect la {summary['ticket_joker_hits']} din "
                         f"{summary['ticket_joker_evaluated']}.").classes("text-body2")
            else:
                ui.label(f"Urna 2 · bila extrasă în pool la {summary['joker_hits']} din "
                         f"{summary['joker_evaluated']} extrageri evaluate.").classes("text-body2")
        if isinstance(result.get("protocol"), str) and result["protocol"]:
            ui.label(result["protocol"]).classes("text-caption")
        rules = result.get("methods") or {}
        for key, label in ((lot.bench_key, "Urna principală"), (lot.bench_key_urna2, "Urna 2")):
            for role, title in (("tested", label), ("direct", f"{label} · pool direct exclus")):
                if role == "direct" and not result["inverse"]:
                    continue
                decision = (rules.get(role) or {}).get(key) or {}
                members = decision.get("members", [])
                if members:
                    names = ", ".join(
                        f"{m['method']} ({m['weight'] * 100:.0f}%)" if len(members) > 1 else m["method"]
                        for m in members
                    )
                    fallback = " · metodă de rezervă" if decision.get("fallback") else ""
                    ui.label(f"{title} · {names}{fallback}").classes("text-caption")
        if draws := result.get("draws"):
            with ui.expansion("Extrageri și bilete verificate").classes("w-full min-w-0"):
                _draw_table(draws, result["inverse"], bool(lot.bench_key_urna2))


def lr_walk_forward_panel(scope_text, start=None, *, depth_control=None, on_replay_change=None):
    """Display validation started by the LR recommendation workflow."""
    with ui.card().classes("w-full min-w-0 gap-2").props('id="lr-walk-forward" tabindex="-1"') as panel:
        ui.label("Validare walk-forward pentru selecția LR").classes("text-h6 text-weight-bold")
        scope = ui.label(scope_text()).classes("text-body2")
        with ui.row().classes("w-full items-center gap-2"):
            if depth_control is not None:
                depth_control()
            if on_replay_change is not None:
                ui.select({"fixed": "Metoda fixată înainte de test", "daily": "Refă selecția la fiecare zi"},
                          value="fixed", label="Protocol de validare",
                          on_change=lambda e: on_replay_change(e.value)).classes("min-w-60")
            cancel = ui.button("Anulează validarea", on_click=cancel_lr_walk_forward).props("outline no-caps")
        status = ui.label().classes("text-body2").props('role="status" aria-live="polite"')
        results = ui.column().classes("w-full min-w-0 gap-2")
    seen = None
    showed_latest = False

    def refresh():
        nonlocal seen, showed_latest
        if panel.is_deleted:
            return
        scope.set_text(scope_text())
        snapshot = lr_walk_forward_snapshot(
            since=seen, include_active_entries=False, include_previous=not showed_latest,
        )
        if snapshot is None:
            return
        seen, active, latest, notice = snapshot
        cancel.set_visibility(bool(active and not active["cancelled"]))
        text = "După Re-Bench, validarea pornește automat. Recomandarea folosește rezultatele validării încheiate."
        if active:
            if active["cancelled"]:
                text = ("Validarea se va opri după jocul curent." if active.get("cancel_after_game")
                        else "Se oprește validarea.")
            elif current := active.get("current"):
                text = f"Se validează {current[1]} · jocul {current[0]} din {active['total']}."
                if captured := active.get("current_settings"):
                    text += " " + _choices(captured)
                progress = active.get("progress") or {}
                if progress.get("total"):
                    text += f" Progres · {progress['done']} din {progress['total']}."
            else:
                text = "Se pregătește validarea."
        elif notice:
            text = notice
        status.set_text(text)
        status.set_visibility(bool(text))
        if latest and (not active or not showed_latest):
            results.clear()
            with results:
                finished = datetime.fromisoformat(latest["finished_at"]).strftime("%d.%m.%Y, %H:%M")
                ui.label(f"Ultima validare walk-forward · {finished}").classes("text-subtitle1 text-weight-bold")
                for entry in latest["entries"]:
                    try:
                        _game_result(entry)
                    except (KeyError, TypeError, ValueError) as exc:
                        logger.warning("Raport walk-forward LR incomplet: %s", exc)
                        ui.label("Rezultatul salvat pentru acest joc nu poate fi afișat.").classes("text-warning")
            showed_latest = True

    refresh()
    ui.timer(.75, refresh)
    return panel
