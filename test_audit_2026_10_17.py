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
