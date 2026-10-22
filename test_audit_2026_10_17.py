"""Constatările auditului pe module din 2026-10-17, fiecare cu testul care pica înainte."""

from __future__ import annotations

import app_nicegui  # noqa: F401  (importul leagă numele UI, ca aplicația)
import pandas as pd
import ui_results


def _odds(game, guarantee, variants=None, audit=None):
    pool = list(range(1, 11))
    data = {"hard_core": pool, "variants": variants or [list(range(1, 7))], "guarantee": guarantee}
    if audit is not None:
        data["audit"] = audit
    return ui_results._wheel_probability_lines(game, data)


def test_guarantee_sentence_follows_the_guarantee_actually_used():
    # Înainte: „Garanția 4 nu asigură 5” la orice garanție, și la g5, unde
    # rândul de deasupra arăta 5+ egal în pool și pe variante.
    assert "Garanția 3 nu asigură 4 pe o variantă; compară probabilitățile separate." in _odds(
        "6/49", 3
    )
    assert not any("Garanția" in line and "nu asigură" in line for line in _odds("6/49", 6))
    # Garanția efectivă din audit are întâietate față de cea cerută.
    lines = _odds("6/49", 4, audit={"wheel_guarantee_used": 5})
    assert "Garanția 5 nu asigură 6 pe o variantă; compară probabilitățile separate." in lines
    # La 5/40 și Joker biletul are 5 numere: garanția 5 nu mai are prag peste ea.
    assert not any("nu asigură" in line for line in _odds("5/40", 5, [list(range(1, 6))]))
    assert not any("nu asigură" in line for line in _odds("6/49", None))


def test_joker_odds_without_the_urna2_number_are_still_computed():
    # Coloana Urnei 2 inutilizabilă: motorul nu pune numărul Joker, varianta are 5.
    lines = _odds("joker", 4, [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]])
    assert lines and "indisponibile" not in lines[0]
    assert any("3+ numere: în pool" in line for line in lines)
    assert "Joker: procentele privesc urna 1" in " ".join(lines)


# --------------------------------------------------------------------------- #
# Joker: fiecare urnă cu nota ei de încredere; rezerva Urnei 2 se vede.
# --------------------------------------------------------------------------- #


def _holm(scorer, holm_p, candidates):
    return {
        "scorer": scorer,
        "ensemble": [{"method": scorer, "weight": 1.0}],
        "low_confidence": False,
        "multiplicity": {"candidates": candidates, "holm_p": holm_p, "alpha": 0.05, "proven": False},
    }


def _joker_panel(monkeypatch, urna2):
    from scripts.analysis.audit_output import capture_ui

    asked = []
    entries = {
        "joker_urna1": _holm("frequency", 0.12, 42),
        "joker_urna2": _holm("ridge_pooled_feats", 0.79, 38),
    }

    def _decision(key, pool):
        asked.append((key, pool))
        return entries[key]

    monkeypatch.setattr(ui_results, "_decision_entry", _decision)
    data = {
        "pool_size": 16,
        "pool_size_requested": 16,
        "hard_core": list(range(1, 17)),
        "hard_core_joker": [9],
        "variants": [],
        "guarantee": 4,
        "context": {"coverage_pct": 100.0},
        "audit": {
            "bench_winner": {
                "joker_urna1": {"method": "frequency", "pool_hint": 16},
                "joker_urna2": urna2,
            }
        },
    }
    with capture_ui() as ui:
        ui_results._render_pool_body("joker.csv", "joker", data)
    return ui.text(), asked


def test_joker_panel_shows_the_note_of_each_urn(monkeypatch):
    # Înainte: numai nota Urnei 1 (42 de candidați, p = 0.12), chiar sub rândul
    # Urnei 2, iar nota Urnei 2 (p = 0.79) lipsea; mailul le avea pe amândouă.
    text, asked = _joker_panel(
        monkeypatch, {"method": "ridge_pooled_feats", "pool_hint": 1, "single_pick": True}
    )
    assert asked == [("joker_urna1", 16), ("joker_urna2", 1)]
    assert "⚠️ Urna 1: Avantaj nedemonstrat: după corecția Holm pentru cei 42" in text
    assert "⚠️ Urna 2: Avantaj nedemonstrat: după corecția Holm pentru cei 38" in text
    assert "p = 0.79" in text


def test_urna2_fallback_is_named_even_on_the_single_pick_row(monkeypatch):
    text, asked = _joker_panel(
        monkeypatch,
        {
            "method": "frequency",
            "pool_hint": 1,
            "single_pick": True,
            "fallback": True,
            "attempted": "markov_self_state",
            "reason": "metodă interzisă în producție",
        },
    )
    assert "fallback: metodă interzisă în producție" in text
    assert ("joker_urna2", 1) not in asked  # la rezervă nota nu descrie pool-ul
    assert "Urna 2: Avantaj nedemonstrat" not in text


# --------------------------------------------------------------------------- #
# Walk-forward: peste hazard nu înseamnă validat; raportul spune la fel.
# --------------------------------------------------------------------------- #


def _wf_flat(pool_hits: list[int]):
    from loto_enterprise.core.walk_forward_adapter import WalkForwardResult

    pool = list(range(1, 17))
    return [
        WalkForwardResult(
            draw_index=i,
            draw_date=f"{1 + i % 28:02d}-0{1 + i // 28}-2026",
            variant=pool[:6],
            hits=h,
            hits_union=h,
            wheel_coverage=100.0,
            ticket_context={"hard_core": pool, "actual": pool[:h] + [40, 41, 42, 43, 44, 45][: 6 - h]},
        )
        for i, h in enumerate(pool_hits)
    ]


def test_hits_panel_qualifies_a_rate_above_chance(monkeypatch):
    from scripts.analysis.audit_output import capture_ui

    import ui_hits

    monkeypatch.setattr(ui_hits, "_bench_target", lambda: 4)
    flat = _wf_flat([4] * 10 + [1] * 90)  # 10% la 4+, față de 7.96% la întâmplare
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "6/49", {"pool_size": 16, "wheel_guarantee": 4}, pool_n=16)
    text = ui.text()
    assert "ℹ️ Peste hazard (≥4): Pool: 10.00% față de random 7.96%" in text
    assert "test binomial unilateral" in text and "nu e o validare externă" in text
    assert "fără lookahead" not in text
    assert "aleasă pe ferestre bench care cuprind și aceste extrageri" in text
    assert "Garanția 4 nu asigură 5 pe o variantă" in text


def test_report_walk_forward_line_has_the_chance_rate_and_the_selection_caveat():
    line = ui_results._wf_summary(_wf_flat([4] * 10 + [1] * 90), {"pool_size": 16}, "6/49")
    assert "la întâmplare (pool 16) 3+/4+/5+: 29.8%/7.96%/1.088% pe extragere" in line
    assert "nu e o validare externă" in line


# --------------------------------------------------------------------------- #
# Mailul: „CEL MAI BUN REZULTAT” are rata la întâmplare și spune că nu validează.
# --------------------------------------------------------------------------- #


def test_mail_best_draw_line_compares_with_any_pool_of_the_same_size():
    from math import comb

    import pandas as pd

    from loto_enterprise.core.lotteries import lottery_by_id

    spec = lottery_by_id("6/49")
    rows = [[1, 2, 3, 4, 5, 6]] * 2 + [[30, 31, 32, 33, 34, 35]] * 23
    df = pd.DataFrame(
        {
            "date": [f"{1 + i:02d}-01-2026" for i in range(25)],
            **{f"n{j + 1}": [r[j] for r in rows] for j in range(6)},
        }
    )
    pool = list(range(1, 17))
    line = app_nicegui._mail_best_draw_line(spec, df, pool)
    expected = 25 * comb(16, 6) / comb(49, 6)
    assert "(25 de extrageri, pool-ul de azi): 6 numere din pool" in line
    assert f"de 2 ori în total, față de ~{expected:.2g} pentru un pool oarecare de 16 numere" in line
    assert "nu validare: pool-ul e ales pe aceleași extrageri" in line
    one = app_nicegui._mail_best_draw_line(spec, df.iloc[1:], pool)
    assert "; o dată în total" in one


# --------------------------------------------------------------------------- #
# Mărunte: recomandarea de pool, sumele în lei, meniul pe ecran îngust, etichete.
# --------------------------------------------------------------------------- #


def test_hit_forecast_recommends_the_smallest_pool_not_the_current_one():
    from covering.hypergeo import hypergeometric_hit_forecast

    rec = hypergeometric_hit_forecast(16, 6, 49, 130)["recommendations"]
    # 3+ la 6/49: ~3 evenimente în 130 de extrageri apar deja de la pool 8.
    assert rec["pool_for_3_events_3+"] < 16
    small = hypergeometric_hit_forecast(8, 6, 49, 130)["recommendations"]
    assert small == rec  # recomandarea nu mai depinde de pool-ul curent


def test_money_amounts_use_a_narrow_space_for_thousands():
    assert ui_results._thousands(1216) == "1 216"
    assert ui_results._thousands(64064.0) == "64 064"
    assert ui_results._thousands(1234.5, 2) == "1 234.50"
    assert ui_results._thousands(152) == "152"


def test_narrow_screens_have_a_button_that_opens_the_controls():
    from pathlib import Path

    src = Path(app_nicegui.__file__).read_text(encoding="utf-8")
    assert "as drawer:" in src and "on_click=drawer.toggle" in src
    # Vizibil numai sub 1024 px, unde Quasar scoate sertarul din ecran.
    assert 'ui.page_sticky(position="top-left", x_offset=8, y_offset=8).classes("lt-md")' in src


def test_bench_labels_only_random_as_a_realisation_of_chance():
    from pathlib import Path

    src = Path(ui_results.__file__).with_name("ui_bench.py").read_text(encoding="utf-8")
    assert "realizare empirică a hazardului" in src
    assert "martor «{_brec[0]}» (filtru exclus din producție" in src
    assert "Wilson și brut sunt aceleași procente" not in src
    assert "istoricul listează +3 și +4;" not in src


def test_unplayed_numbers_reason_depends_on_the_coverage():
    full = {"context": {"coverage_pct": 100.0}}
    capped = {"context": {"coverage_pct": 0.82}}
    assert ui_results._unplayed_reason(full) == "designul își ține garanția fără ele"
    assert "nu au încăput în limita de variante" in ui_results._unplayed_reason(capped)
    assert "nu au încăput" in ui_results._unplayed_reason({})


def test_romanian_counts_in_the_hits_panel():
    import ui_hits

    assert ui_hits._n_extrageri(777) == "777 de extrageri"
    assert ui_hits._n_extrageri(1) == "1 extragere"
    assert ui_hits._n_extrageri(15) == "15 extrageri"


def test_recovery_with_a_moved_decision_says_why_the_history_is_missing(monkeypatch):
    # Înainte: secțiunea lipsea fără aviz; calea live scria „Walk-forward sărit”.
    import loto_enterprise.core.walk_forward_adapter as wfa
    import pandas as pd

    calls = []
    monkeypatch.setattr(wfa, "run_honest_walk_forward", lambda **kw: calls.append(kw))
    monkeypatch.setattr(app_nicegui, "_result_scorers_match_decision", lambda *_: False)
    data = {
        "pool_size": 16,
        "guarantee": 4,
        "audit": {"bench_winner": {"loto_6_49": {"method": "markov_lag2", "pool_hint": 16}}},
    }
    for key, value in {
        "results": ([("loto_6_49.csv", {"6/49": data})], 1),
        "result_sources": {"loto_6_49.csv": pd.DataFrame({"n1": [1]})},
        "retro": {},
        "retro_meta": {},
    }.items():
        monkeypatch.setitem(app_nicegui.STATE, key, value)
    assert app_nicegui._load_cached_walk_forward() == 0
    assert calls == []
    assert app_nicegui.STATE["retro_meta"]["loto_6_49.csv_6/49"] == {"decision_moved": True}


def test_new_draws_count_agrees_in_romanian():
    assert app_nicegui._new_draws_text(1) == "1 extragere nouă"
    assert app_nicegui._new_draws_text(3) == "3 extrageri noi"
    assert app_nicegui._new_draws_text(25) == "25 de extrageri noi"


def test_leaderboard_names_a_generation_made_before_the_current_decision(monkeypatch):
    # Înainte: 🎯 rămânea pe metoda veche, nota Holm era a câștigătorului nou,
    # iar motivul invocat era un ensemble imposibil cu ENSEMBLE_MAX_METHODS = 1.
    import ui_bench
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui
    from test_output_consistency import _folds

    df = _folds()
    extra = df[df["method"] == "frequency"].copy()
    extra["method"] = "croston_interval"
    extra["rate_3plus_k11"] = 0.095
    df = pd.concat([df, extra], ignore_index=True)
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    monkeypatch.setattr(app_nicegui, "_BENCH_FOLDS_CACHE", ui_bench._BENCH_FOLDS_CACHE)
    monkeypatch.setattr(
        ui_bench, "_last_generation_bench_info", lambda *_a, **_k: {"method": "croston_interval"}
    )
    saved = {"scorer": "frequency", "ensemble": [{"method": "frequency", "weight": 1.0}]}
    monkeypatch.setattr(ui_bench, "_decision_entry", lambda *_a, **_k: saved)
    with capture_ui() as ui:
        ui_bench._render_bench_leaderboard_slice(df, "joker_urna1", 11, "Joker", 20)
    text = ui.text()
    assert "generat înaintea deciziei de acum" in text and "care alege frequency" in text
    assert "ensemble-ul de scoring a rămas pe alt cap de listă" not in text
    for line in text.splitlines():
        if "Avantaj nedemonstrat" in line:
            assert line.startswith("⚠️ frequency (clasamentul de acum): ")


def test_autopilot_notification_names_the_joker_number_method(monkeypatch):
    # AGENTS §5.10: „avantaj nedemonstrat” și în notificarea Auto-Pilot; Urna 2 lipsea.
    import loto_enterprise.core.method_selector as ms
    from loto_enterprise.core.lotteries import lottery_by_id

    asked, notes = [], []

    def _rec(key, pool, **_kw):
        asked.append((key, pool))
        proven = key != "joker_urna2"
        return {"scorer": f"m_{key}", "multiplicity": {"proven": proven}, "sim_depth_pct": 30}

    monkeypatch.setattr(ms, "recommend_optimal_config", _rec)
    monkeypatch.setattr(app_nicegui, "_selected_games", lambda: [lottery_by_id("joker")])
    monkeypatch.setattr(app_nicegui, "submit_generation", lambda **_kw: None)
    monkeypatch.setattr(app_nicegui.ui, "notify", lambda msg, **_kw: notes.append(msg))
    monkeypatch.setitem(app_nicegui.STATE, "datasets", [("joker.csv", None)])
    app_nicegui.apply_autopilot_and_generate()
    assert ("joker_urna2", 1) in asked
    assert "joker_urna1: m_joker_urna1 | joker_urna2: m_joker_urna2 · avantaj nedemonstrat (Holm)" in notes[0]


def test_interval_table_ranks_by_the_games_target(monkeypatch):
    # Înainte: mereu 3+, și la 5/40, unde 3 numere nu aduc premiu (ținta 4+).
    from scripts.analysis.audit_output import capture_ui

    seen = []
    real = app_nicegui._base_interval_rows

    def _spy(csv_name, draw_n, max_num, pool, target=3):
        seen.append((csv_name, target))
        return real(csv_name, draw_n, max_num, pool, target)

    monkeypatch.setattr(app_nicegui, "_base_interval_rows", _spy)
    monkeypatch.setitem(app_nicegui._BASE_TABLE_OPENED, "value", True)
    for _key in app_nicegui._POOL_KEYS.values():
        monkeypatch.setitem(app_nicegui.SETTINGS, _key, 12)
    monkeypatch.setitem(app_nicegui.SETTINGS, "bench_hit_target", 3)
    with capture_ui() as ui:
        app_nicegui._render_base_interval_tables.func()
    targets = dict(seen)
    assert targets["loto_5_40.csv"] == 4 and targets["loto_6_49.csv"] == 3
    assert any("ținta 4+" in str(n["args"][0]) for n in ui.walk() if n["kind"] == "expansion")


def test_hits_header_says_which_pool_and_settings_are_counted(monkeypatch):
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    flat = _wf_flat([3, 1, 4])
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "6/49", {"pool_size": 16}, pool_n=16)
    text = ui.text()
    assert "numărată pe pool-ul ales ATUNCI, numai din extragerile de dinainte, nu pe pool-ul de azi" in text
    assert "Fără limită de consecutive. Fără restrângerea bazei." in text
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            flat, "6/49", {"pool_size": 16}, pool_n=16, consecutive_limit_text="limita 2"
        )
    assert "Fără limită de consecutive" not in ui.text()
    summary = next(n for n in ui.walk() if n["kind"] == "table")
    assert summary["kwargs"]["columns"][1]["label"] == "🎯 Pool de atunci (din 16)"


# --------------------------------------------------------------------------- #
# Revizia adversarială a diff-ului (2026-10-17).
# --------------------------------------------------------------------------- #


def test_report_has_each_urns_method_and_note_and_the_joker_penalty(monkeypatch):
    entries = {
        "joker_urna1": _holm("frequency", 0.12, 42),
        "joker_urna2": _holm("ridge_pooled_feats", 0.79, 38),
    }
    monkeypatch.setattr(ui_results, "_decision_entry", lambda key, pool: entries[key])
    data = {
        "pool_size": 16,
        "pool_size_requested": 16,
        "hard_core": list(range(1, 17)),
        "hard_core_joker": [9],
        "variants": [],
        "guarantee": 4,
        "context": {"coverage_pct": 100.0},
        "audit": {
            "bench_winner": {
                "joker_urna1": {"method": "frequency", "pool_hint": 16},
                "joker_urna2": {"method": "ridge_pooled_feats", "pool_hint": 1, "single_pick": True},
            },
            "recent_penalty": {"draws": 3, "factor": 0.5, "penalized": {"4": 0.5}, "penalized_urna2": {"9": 0.5}},
        },
    }
    monkeypatch.setitem(ui_results.STATE, "results", ([("joker.csv", {"joker": data})], {}))
    monkeypatch.setitem(ui_results.STATE, "retro", {})
    monkeypatch.setitem(ui_results.STATE, "retro_meta", {})
    report = ui_results._build_report()
    assert "Metodă (joker_urna2): ridge_pooled_feats" in report
    assert "⚠️ Urna 1: Avantaj nedemonstrat: după corecția Holm pentru cei 42 de candidați" in report
    assert "⚠️ Urna 2: Avantaj nedemonstrat" in report
    assert "numărul Joker penalizat: 9" in report

    from scripts.analysis.audit_output import capture_ui

    with capture_ui() as ui:
        ui_results._render_pool_body("joker.csv", "joker", data)
    assert "Urna 2 (numărul Joker): 9" in ui.text()

    # Urna 2 pe rezervă: motivul în raport, fără nota ei.
    data["audit"]["bench_winner"]["joker_urna2"].update(fallback=True, reason="metodă interzisă")
    report = ui_results._build_report()
    assert "Metodă (joker_urna2): ridge_pooled_feats — rezervă: metodă interzisă" in report
    assert "Urna 2: Avantaj nedemonstrat" not in report


def test_old_cache_variant_is_not_shown_unmarked():
    import ui_hits

    flat = _wf_flat([5, 4])
    for p in flat:
        p.ticket_context = None  # cache vechi: varianta există, extragerea nu
    stats = ui_hits._wf_per_draw_stats(flat, "6/49")
    assert all(d["best_variant"] == [] and d["drawn"] == [] for d in stats.values())


def test_counts_and_wording_in_the_new_texts(monkeypatch):
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    flat = [p for p in _wf_flat([1] * 25)]
    for i, p in enumerate(flat):
        p.joker_hit = i == 0
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "joker", None)
    text = ui.text()
    assert "nimerit la 1 din 25 de extrageri" in text
    assert "cele numerele" not in text
    assert app_nicegui._weaker_text(0) == "niciunul n-a ieșit mai slab"
    assert app_nicegui._weaker_text(1) == "unul a ieșit mai slab"
    assert app_nicegui._weaker_text(25) == "25 au ieșit mai slabe"
    assert app_nicegui._workers_text(1) == "într-un singur proces"
    assert app_nicegui._workers_text(21) == "pe 21 de procese în paralel"
    assert ui_results._wf_summary(_wf_flat([1]), {"pool_size": 16}, "6/49").startswith("1 extragere |")


def test_fallback_pool_is_not_called_a_bench_selection():
    import ui_hits
    from scripts.analysis.audit_output import capture_ui

    fb = {"audit": {"bench_winner": {"loto_6_49": {"method": "frequency", "fallback": True}}}}
    assert ui_results._method_from_bench(fb) is False
    assert ui_results._method_from_bench({}) is True
    line = ui_results._wf_summary(_wf_flat([4] * 10 + [1] * 90), dict(fb, pool_size=16), "6/49")
    assert "rezerva frequency (fără decizie bench)" in line and "decizia de azi" not in line
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            _wf_flat([4] * 10 + [1] * 90), "6/49", {"pool_size": 16}, pool_n=16,
            method_from_bench=False,
        )
    text = ui.text()
    assert "metoda e rezerva frequency, fără decizie bench" in text
    assert "aleasă pe ferestre bench" not in text


def test_mail_chance_baseline_keeps_significant_digits():
    from loto_enterprise.core.lotteries import lottery_by_id

    spec = lottery_by_id("6/49")
    rows = [[1, 2, 3, 4, 5, 40]] + [[30, 31, 32, 33, 34, 35]] * 9
    df = pd.DataFrame(
        {
            "date": [f"{1 + i:02d}-01-2026" for i in range(10)],
            **{f"n{j + 1}": [r[j] for r in rows] for j in range(6)},
        }
    )
    line = app_nicegui._mail_best_draw_line(spec, df, [1, 2, 3, 4, 5, 6])
    assert "~0.0 " not in line and "~0.00018 " in line  # 10 · C(6,5) · 43 / C(49,6)


def test_eta_is_wired_to_the_runner_and_divides_the_foreign_fallback(monkeypatch, tmp_path):
    import loto_enterprise.benchmark.runner as runner

    monkeypatch.setattr(runner, "bench_worker_count", lambda: 4)
    assert app_nicegui._bench_workers() == 4
    src = open(app_nicegui.__file__, encoding="utf-8").read()
    assert "scaled * target * 1.25 / workers" in src


def test_leaderboard_labels_render_random_and_witnesses_differently(monkeypatch):
    import ui_bench
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui
    from test_output_consistency import _folds

    df = _folds()
    witness = df[df["method"] == "random"].copy()
    witness["method"] = "neighbor_adjacent"
    witness["rate_3plus_k11"] = 0.30
    witness["tiebreak_k11"] = 0.1
    df = pd.concat([df, witness], ignore_index=True)
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    monkeypatch.setattr(app_nicegui, "_BENCH_FOLDS_CACHE", ui_bench._BENCH_FOLDS_CACHE)
    with capture_ui() as ui:
        ui_bench._render_bench_leaderboard_slice(df, "joker_urna1", 11, "Joker", 1)
    text = ui.text()
    assert "🎲 baseline «random» (realizare empirică a hazardului" in text
    assert "🚫 martor «neighbor_adjacent» (filtru exclus din producție" in text
    assert "baseline «neighbor_adjacent»" not in text


def test_leaderboard_reason_uses_the_saved_decision_not_the_recomputed_one(monkeypatch):
    # Generarea a folosit decizia salvată (croston_interval); folds.csv recalculat
    # alege frequency. Regenerarea ar juca tot croston_interval: nu „generat
    # înaintea deciziei de acum”.
    import ui_bench
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui
    from test_output_consistency import _folds

    df = _folds()
    extra = df[df["method"] == "frequency"].copy()
    extra["method"] = "croston_interval"
    extra["rate_3plus_k11"] = 0.095
    df = pd.concat([df, extra], ignore_index=True)
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    monkeypatch.setattr(app_nicegui, "_BENCH_FOLDS_CACHE", ui_bench._BENCH_FOLDS_CACHE)
    monkeypatch.setattr(
        ui_bench, "_last_generation_bench_info", lambda *_a, **_k: {"method": "croston_interval"}
    )
    saved = {"scorer": "croston_interval", "ensemble": [{"method": "croston_interval", "weight": 1.0}]}
    monkeypatch.setattr(ui_bench, "_decision_entry", lambda *_a, **_k: saved)
    with capture_ui() as ui:
        ui_bench._render_bench_leaderboard_slice(df, "joker_urna1", 11, "Joker", 20)
    assert "generat înaintea deciziei de acum" not in ui.text()


def test_mail_rating_says_it_measures_the_raw_top_k_when_the_pool_was_adjusted(monkeypatch):
    # Criticul auditului: RATING-ul din mail e al top-K brut, sub pool-ul jucat cu
    # limita de consecutive, fără nicio precizare (panoul o avea).
    from loto_enterprise.core.lotteries import lottery_by_id
    from test_ui_mail_2026_10_08 import _entry, _result

    spec = lottery_by_id("6/49")
    entry = _entry("markov_lag2", proven=True, holm_p=0.01)
    monkeypatch.setattr(app_nicegui, "_decision_entry", lambda key, pool: entry)
    plain = app_nicegui._mail_method_lines(spec, _result("markov_lag2"))
    assert len(plain) == 2 and not any("Clasamentul bench" in x for x in plain)
    data = _result("markov_lag2")
    data["audit"]["consecutive_limit"] = {"requested": 2, "applied": 2, "removed": [], "added": []}
    lines = app_nicegui._mail_method_lines(spec, data)
    assert lines[-1].startswith("Configurația generată include limita de consecutive")
    assert "Clasamentul bench măsoară scorerul fără aceste ajustări" in lines[-1]
