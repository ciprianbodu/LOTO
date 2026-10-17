"""Constatările auditului pe module din 2026-10-17, fiecare cu testul care pica înainte."""

from __future__ import annotations

import app_nicegui  # noqa: F401  (importul leagă numele UI, ca aplicația)
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
    assert f"de 2 ori în total, față de ~{expected:.1f} pentru un pool oarecare de 16 numere" in line
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
