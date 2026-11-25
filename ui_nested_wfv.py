"""Display the chronological comparison saved by Re-Bench."""

import json

from nicegui import ui

from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN
from loto_enterprise.benchmark import methods as registry
from loto_enterprise.benchmark.chronological_validation import (
    selection_metric,
    validation_path,
)
from loto_enterprise.core.lotteries import games_for_country, lottery_by_bench_key

_POLICIES = (
    ("frequency", "frequency", "Fixă"),
    ("random", "random · martor aleator", "Fixă"),
    ("pool_frozen", "Hituri în poolul selectat", "La începutul testului"),
    ("pool_block26", "Hituri în poolul selectat", "La fiecare 26 de zile de extragere"),
    ("pool_daily", "Hituri în poolul selectat", "Înaintea fiecărei zile"),
    ("decision_frozen", "Decizie pe pool", "La începutul testului"),
    ("decision_block26", "Decizie pe pool", "La fiecare 26 de zile de extragere"),
    ("decision_hindsight", "Decizie pe pool", "La final, retrospectiv"),
    ("ticket_frozen", "Hituri pe variantă", "La începutul testului"),
    ("ticket_block26", "Hituri pe variantă", "La fiecare 26 de zile de extragere"),
    ("ticket_daily", "Hituri pe variantă", "Înaintea fiecărei zile"),
    ("dynamic_lr", "Selecția dinamică LR din generator", "Înaintea fiecărei zile"),
    ("ticket_hindsight", "Hituri pe variantă", "La final, retrospectiv"),
)


def _nested_results(result, *, game_key=None):
    key = result.get("key") or game_key
    game = lottery_by_bench_key(key)
    if game is None:
        ui.label("Raportul salvat nu identifică jocul.").classes("text-warning")
        return
    single = key == game.bench_key_urna2
    metric = selection_metric(second_urn=single, official_scheme=game.is_romanian)
    title = f"{game.display} · Urna 2" if single else game.display
    if scheme := result["scheme"]:
        kind = "Cod" if scheme["official_for_game"] else "Cod simulat"
        title += f" · {kind} {scheme['code']} · {scheme['variants']} variante · pool {result['k']}"
    ui.label(title).classes("text-subtitle1")
    ui.label(
        f"{result['n']} extrageri, între {result['start']} și {result['end']}, "
        f"în {result['n_dates']} zile de extragere."
    ).classes("text-caption")
    saved_catalog = result.get("catalog_version")
    candidates = result.get("candidate_methods") or list(
        result["arms"].get("frequency", {}).get("method_draws", {})
    )
    if candidates:
        catalog_label = saved_catalog or "istoric"
        comparator = f" · martor separat {result['comparator']}" if result.get("comparator") else ""
        ui.label(
            f"Catalog {catalog_label} · {len(candidates)} metode eligibile · "
            + ", ".join(candidates) + comparator
        ).classes("text-caption")
    fallbacks = [
        f"{method} ({days})" for method, days in result.get("fallback_days", {}).items() if days
    ]
    if fallbacks:
        ui.label(
            f"Zile evaluate în total · {result['scored_days']}. Zile în care frequency "
            "a înlocuit o metodă fără clasament utilizabil · " + ", ".join(fallbacks)
        ).classes("text-caption")
    current = (
        saved_catalog == registry.catalog_version_for_game(key)
        and result.get("hit_target") == (1 if single else 4)
        and result.get("selection_metric") == metric
        and (single or result.get("max_consecutive_run") == (LR_MAX_CONSECUTIVE_RUN if game.is_romanian else 0))
    )
    best = result.get("best_policy") if current else None
    labels = [("policy", "Criteriu"), ("origin", "Când s-a ales metoda")]
    thresholds = ((4, "four", "4+"), (5, "five", "5+"), (6, "six", "6"))
    thresholds = (
        [(t, name, label) for t, name, label in thresholds if t <= game.draw_n]
        if not single
        else []
    )
    if single:
        labels.append(("primary", "Bile ghicite"))
    labels.extend((name, f"{label} · pool / variantă") for _, name, label in thresholds)
    labels.append(("selected", "Alegere automată"))
    rows = []
    for key, policy, origin in _POLICIES:
        if key not in result["arms"]:
            continue
        counts = result["arms"][key]
        rows.append(
            {
                "id": key,
                "policy": "Potrivirea bilei"
                if single and key.startswith("ticket")
                else policy,
                "origin": origin,
                "primary": counts["primary"],
                "selected": "✓ Selectată" if key == best else "",
                **{
                    name: f"{counts.get(f'pool_{t}plus', '—')} / "
                    f"{counts.get(f'ticket_{t}plus', '—') if t <= game.pick_n else '—'}"
                    for t, name, _ in thresholds
                },
            }
        )
    if winner := next((row for row in rows if row["id"] == best), None):
        count = result["arms"][best]["primary" if single else metric]
        measure = {
            "top1": "potriviri ale bilei",
            "ticket_4plus": "extrageri cu 4+ pe o variantă LR",
            "pool_4plus": "extrageri cu 4+ în pool",
        }[metric]
        ui.label(
            f"Cea mai bună în perioada testată · {winner['policy']} · {winner['origin']} · "
            f"{count} {measure} din {result['n']}"
        ).classes("text-weight-bold text-positive")
        usage = [
            f"{method} ({draws})" for method, draws in result["arms"][best].get("method_draws", {}).items()
            if draws
        ]
        if usage:
            ui.label("Extrageri evaluate cu fiecare metodă a regulii selectate · " + ", ".join(usage)).classes("text-caption")
    else:
        ui.label(
            "Rezultat anterior. Următorul Re-Bench va recalcula alegerea cu metodele și criteriul curent."
        ).classes("text-caption text-warning")
    columns = [
        {"name": name, "label": label, "field": name, "align": "left"}
        for name, label in labels
    ]
    ui.table(
        columns=columns, rows=rows, row_key="id", pagination={"rowsPerPage": 0}
    ).props("dense flat hide-bottom").classes("w-full")


def nested_wfv_panel(country):
    lots = games_for_country(country)
    games = {
        key: (f"{g.display} · Urna 2" if key == g.bench_key_urna2 else g.display)
        for g in lots
        for key in g.bench_keys
    }
    romanian = any(lot.is_romanian for lot in lots)
    path = validation_path(country)
    with ui.expansion("Validare cronologică după Re-Bench", value=False).classes(
        "w-full"
    ):
        criterion = (
            "cel puțin 4 numere pe aceeași variantă a codului LR ales la pornire"
            if romanian else "cel puțin 4 numere în pool"
        )
        ui.label(
            "Pe ultimele 30% din zilele eligibile, este evidențiată regula cu cele mai multe "
            f"extrageri cu {criterion}. Rândurile retrospective servesc comparației."
        ).classes("text-body2")
        if romanian:
            ui.label("Raportul Re-Bench general. La Joker Urna 2 se numără bilele ghicite.").classes("text-caption")
            ui.link("Validează metoda și baza alese pentru biletele LR", "#lr-walk-forward")
        else:
            ui.label("Variantele se simulează cu o schemă pe 10 numere.").classes("text-caption")
        game = ui.select(games, value=next(iter(games)), label="Joc sau urnă").classes(
            "w-full sm:w-96"
        )
        output = ui.column().classes("w-full gap-1")
        document = {}
        last_version = object()

        def show_result():
            output.clear()
            key = game.value
            with output:
                if result := document.get("results", {}).get(key):
                    ui.label(f"Sursa · {lottery_by_bench_key(key).csv}").classes(
                        "text-caption"
                    )
                    _nested_results(result, game_key=key)
                elif error := document.get("errors", {}).get(key):
                    ui.label(f"Validarea nu a putut fi finalizată. {error}").classes(
                        "text-warning"
                    )
                else:
                    ui.label("Rezultatul va apărea aici după Re-Bench.").classes(
                        "text-caption"
                    )

        def refresh():
            nonlocal document, last_version
            try:
                version = path.stat().st_mtime_ns
            except OSError:
                version = None
            if version == last_version:
                return
            last_version = version
            try:
                document = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if version is not None
                    else {}
                )
            except OSError, ValueError:
                document = {
                    "errors": dict.fromkeys(
                        games, "Rezultatul salvat nu poate fi citit."
                    )
                }
            show_result()

        game.on_value_change(lambda _: show_result())
        refresh()
        ui.timer(2.0, refresh)
