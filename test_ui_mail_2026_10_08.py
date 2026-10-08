"""Regresii din auditul 2026-10-08: mailul de rezultate, nota de încredere din
panou, data extragerii, ora rezultatului recuperat și etichetele butoanelor."""

from __future__ import annotations

import ast
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import app_nicegui as app  # importul rulează _sync_ui_namespace, ca aplicația
import ui_results
from loto_enterprise.core.lotteries import lottery_by_id

ROOT = Path(__file__).resolve().parent


def _entry(scorer: str, *, proven: bool, holm_p: float, low_confidence=False) -> dict:
    """Intrarea deciziei, în forma scrisă de decision.py (pool 16, ținta 4+)."""
    rationale = (
        f"{scorer}: rată 4+ @ k16 = 0.091 (Wilson_lb=0.085), beat random "
        "(hipergeometric 0.0796) in 3/4 windows on the same 4+ target (lift +0.0114)"
    )
    if not proven:
        rationale += (
            f" [după corecția Holm pentru 40 candidați: p={holm_p:.3f} ≥ 0.05 — "
            "avantaj nedemonstrat]"
        )
    return {
        "scorer": scorer,
        "ensemble": [{"method": scorer, "weight": 1.0}],
        "rationale": rationale,
        "baseline_rate": 0.0796,
        "target_label": "4+",
        "low_confidence": low_confidence,
        "multiplicity": {
            "candidates": 40,
            "holm_p": holm_p,
            "alpha": 0.05,
            "proven": proven,
        },
    }


def _result(method: str, *, pool: int = 16, requested: int | None = None) -> dict:
    return {
        "pool_size": pool,
        "pool_size_requested": requested or pool,
        "hard_core": list(range(1, pool + 1)),
        "variants": [],
        "guarantee": 4,
        "context": {"coverage_pct": 100.0},
        "audit": {
            "bench_winner": {
                "loto_6_49": {"method": method, "pool_hint": requested or pool}
            }
        },
    }


# --------------------------------------------------------------------------- #
# Constatarea 7: mailul spune „avantaj nedemonstrat”, ca panoul
# --------------------------------------------------------------------------- #


def test_mail_rating_says_when_holm_does_not_confirm_the_advantage(monkeypatch):
    spec = lottery_by_id("6/49")
    entry = _entry("markov_lag2", proven=False, holm_p=0.435)
    monkeypatch.setattr(app, "_decision_entry", lambda key, pool: entry)
    lines = app._mail_method_lines(spec, _result("markov_lag2"))
    assert lines[0] == "METODĂ: markov_lag2 (câștigătoarea bench-ului la pool 16)"
    assert "9.10%" in lines[1] and "7.96%" in lines[1] and "3/4" in lines[1]
    assert len(lines) == 3
    assert "Avantaj nedemonstrat" in lines[2] and "p = 0.43" in lines[2]
    assert lines[2] == app._multiplicity_note(entry)

    # Avantaj confirmat: fără avertisment.
    entry = _entry("markov_lag2", proven=True, holm_p=0.01)
    assert len(app._mail_method_lines(spec, _result("markov_lag2"))) == 2
    # low_confidence: mesajul de încredere redusă ține locul notei Holm (ca panoul).
    entry = _entry("markov_lag2", proven=False, holm_p=0.9, low_confidence=True)
    assert len(app._mail_method_lines(spec, _result("markov_lag2"))) == 2


# --------------------------------------------------------------------------- #
# Constatarea 10: nota și ratingul numai pentru metoda care a produs pool-ul
# --------------------------------------------------------------------------- #


def test_mail_rating_is_not_another_methods_after_the_decision_changed(monkeypatch):
    """Pool generat la 4+ de markov_lag2; ținta trecută pe 3+ alege acum
    ridge_pooled_feats. Ratingul și p-ul Holm ale acesteia nu descriu pool-ul."""
    spec = lottery_by_id("6/49")
    other = _entry("ridge_pooled_feats", proven=False, holm_p=0.415)
    monkeypatch.setattr(app, "_decision_entry", lambda key, pool: other)
    lines = app._mail_method_lines(spec, _result("markov_lag2"))
    assert lines[0].startswith("METODĂ: markov_lag2")
    assert len(lines) == 2
    assert "s-a schimbat după generare" in lines[1]
    assert "ridge_pooled_feats" in lines[1]
    assert "9.10%" not in lines[1] and "p = 0.41" not in lines[1]


def test_mail_reads_the_decision_for_the_pool_the_method_was_chosen_for(monkeypatch):
    """Restrângerea bazei micșorează pool-ul efectiv (16 → 11); scorerul a fost
    ales pentru pool 16 (`pool_hint`), deci ratingul e cel de la k16."""
    spec = lottery_by_id("6/49")
    asked = []
    entry = _entry("markov_lag2", proven=False, holm_p=0.435)

    def _decision(key, pool):
        asked.append(pool)
        return entry

    monkeypatch.setattr(app, "_decision_entry", _decision)
    lines = app._mail_method_lines(spec, _result("markov_lag2", pool=11, requested=16))
    assert asked == [16]
    assert lines[0] == "METODĂ: markov_lag2 (câștigătoarea bench-ului la pool 16)"


def _panel_text(monkeypatch, data, entry, asked=None) -> str:
    from scripts.analysis.audit_output import capture_ui

    def _decision(key, pool):
        if asked is not None:
            asked.append(pool)
        return entry

    monkeypatch.setattr(ui_results, "_decision_entry", _decision)
    with capture_ui() as ui:
        ui_results._render_pool_body("loto_6_49.csv", "6/49", data)
    return ui.text()


def test_panel_note_belongs_to_the_method_that_produced_the_pool(monkeypatch):
    own = _entry("markov_lag2", proven=False, holm_p=0.435)
    text = _panel_text(monkeypatch, _result("markov_lag2"), own)
    assert "Avantaj nedemonstrat" in text and "p = 0.43" in text
    assert "s-a schimbat după generare" not in text

    # Decizia de acum alege altă metodă: nici p-ul ei, nici lipsa avertismentului.
    other = _entry("ridge_pooled_feats", proven=False, holm_p=0.415)
    text = _panel_text(monkeypatch, _result("markov_lag2"), other)
    assert "p = 0.41" not in text and "Avantaj nedemonstrat" not in text
    assert "s-a schimbat după generare" in text and "ridge_pooled_feats" in text
    proven = _entry("croston_interval", proven=True, holm_p=0.029)
    text = _panel_text(monkeypatch, _result("markov_self_state", pool=11), proven)
    assert "s-a schimbat după generare" in text

    # Rezerva motorului (frequency) nu e o schimbare a deciziei: fără notă.
    data = _result("frequency")
    data["audit"]["bench_winner"]["loto_6_49"].update(
        fallback=True, attempted="markov_lag2", reason="scoruri inutilizabile"
    )
    text = _panel_text(monkeypatch, data, own)
    assert "Avantaj nedemonstrat" not in text and "s-a schimbat" not in text


def test_panel_note_uses_the_pool_the_method_was_chosen_for(monkeypatch):
    asked: list = []
    own = _entry("markov_lag2", proven=False, holm_p=0.435)
    _panel_text(monkeypatch, _result("markov_lag2", pool=11, requested=16), own, asked)
    assert asked == [16]


def test_decision_entry_method_resolves_aliases_like_production():
    renamed = {"scorer": "alternating_parity"}
    assert app._decision_entry_method(renamed, "loto_6_49") == "season_period2"
    assert app._decision_entry_method({"scorer": "random"}, "loto_6_49") is None
    assert app._decision_entry_method({}, "loto_6_49") is None


# --------------------------------------------------------------------------- #
# Constatarea 9: extragerea din mail e după ultima extragere din istoric
# --------------------------------------------------------------------------- #


class _Thursday21(datetime):
    """Joi, 08-10-2026, 21:00: extragerea de azi e deja în CSV."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 8, 21, 0)


class _Wednesday22(datetime):
    """Miercuri, 07-10-2026, 22:00 (Germania trage miercurea și sâmbăta)."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 22, 0)


def _history(dates) -> pd.DataFrame:
    rows = {f"n{i}": [i + k for k in range(len(dates))] for i in range(1, 7)}
    return pd.DataFrame({"date": list(dates), **rows})


def _mail_state(monkeypatch, fname: str, df, outs: dict, now=_Thursday21) -> None:
    monkeypatch.setattr(app, "_dt", now)
    monkeypatch.setitem(app.STATE, "results", ([(fname, outs)], 0))
    monkeypatch.setitem(app.STATE, "result_sources", {fname: df})


def _ro_mail_state(monkeypatch, df) -> None:
    _mail_state(monkeypatch, "loto_6_49.csv", df, {"6/49": _result("frequency")})


def test_mail_dates_the_draw_after_the_last_one_in_the_result_history(monkeypatch):
    _ro_mail_state(monkeypatch, _history(["04-10-2026", "08-10-2026"]))
    assert app._mail_subject_date() == "11-10-2026"
    body = app._build_mail_body()
    assert "📅 Extragere (următoarea, Joi/Duminică): 11-10-2026" in body
    assert "ultima extragere CSV: 08-10-2026" in body

    # Extragerea de azi încă nu e în istoric: numerele sunt pentru azi.
    _ro_mail_state(monkeypatch, _history(["01-10-2026", "04-10-2026"]))
    assert app._mail_subject_date() == "08-10-2026"
    # Istoric fără date: de azi, ca înainte.
    _ro_mail_state(monkeypatch, _history(["x", "y"]).drop(columns="date"))
    assert app._mail_subject_date() == "08-10-2026"


def test_foreign_mail_date_follows_its_own_history(monkeypatch):
    """Germania: extragerea de miercuri 07-10 e deja în istoric → sâmbătă."""
    df = _history(["03-10-2026", "07-10-2026"])
    df.attrs.update(game_id="de_lotto", country="DE")
    data = {**_result("frequency"), "game_id": "de_lotto", "country": "DE"}
    _mail_state(monkeypatch, "germania.csv", df, {"de_lotto": data}, now=_Wednesday22)
    assert app._mail_subject_date() == "10-10-2026"
    assert "extragere: 10-10-2026 (Miercuri/Sâmbătă)" in app._build_mail_body()


# --------------------------------------------------------------------------- #
# Constatarea 11: ora rezultatului recuperat e ora locală
# --------------------------------------------------------------------------- #


@pytest.fixture
def bucharest(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset lipsește pe Windows")
    monkeypatch.setenv("TZ", "Europe/Bucharest")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    "completed_at,shown",
    [
        ("2026-10-08 04:38:03", "08-10-2026 07:38"),  # vara, UTC+3
        ("2026-10-07 22:30:00", "08-10-2026 01:30"),  # după miezul nopții, altă zi
        ("2026-12-01 10:00:00", "01-12-2026 12:00"),  # iarna, UTC+2
    ],
)
def test_recovered_result_shows_local_time(bucharest, monkeypatch, completed_at, shown):
    monkeypatch.setattr(
        app,
        "get_latest_completed_job",
        lambda: {"id": 7, "result_json": "x", "completed_at": completed_at,
                 "ui_finalized_at": "2026-10-08 05:00:00"},
    )
    monkeypatch.setattr(app, "decode_queue_result", lambda _raw: ([], 0))
    monkeypatch.setattr(app, "_load_cached_walk_forward", lambda: 0)
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    for key in ("active_job_id", "results", "result_sources", "results_recovered"):
        monkeypatch.setitem(app.STATE, key, None)
    app._recover_completed_job(allow_finalize=False)
    assert app.STATE["results_recovered"] == f"job #7 · {shown}"


# --------------------------------------------------------------------------- #
# Constatarea 8: START_8000 nu mai șterge ultimul rezultat după prima afișare
# --------------------------------------------------------------------------- #


def test_last_result_reappears_after_the_launchers_queue_reset(tmp_path, monkeypatch):
    """Jobul terminat cu UI-ul deschis e marcat imediat. A doua zi START_8000
    rulează `reset_jobs.py --force`, apoi UI-ul: rezultatul reapare (afișare),
    fără un nou marcaj; joburile mai vechi și cadavrele pleacă."""
    import functools
    import json

    import job_queue as queue
    import reset_jobs
    from ui_shared import pack_queue_result

    database = str(tmp_path / "station.db")
    older = queue.submit_job("pipeline", json.dumps({"datasets": []}), db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(older, pack_queue_result(([], 0)), db_path=database)
    jid = queue.submit_job("pipeline", json.dumps({"datasets": []}), db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(jid, pack_queue_result(([], 0)), db_path=database)
    queue.mark_job_finalized(older, db_path=database)
    queue.mark_job_finalized(jid, db_path=database)  # status_panel, sesiunea de ieri
    ghost = queue.submit_job("pipeline", "{}", db_path=database)

    monkeypatch.setattr(reset_jobs, "DB", database)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    assert reset_jobs.main() == 0

    for name in ("get_latest_completed_job", "mark_job_finalized"):
        monkeypatch.setattr(
            app, name, functools.partial(getattr(queue, name), db_path=database)
        )
    monkeypatch.setattr(app, "_load_cached_walk_forward", lambda: 0)
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    keys = ("active_job_id", "results", "result_sources", "results_recovered")
    for key in (*keys, "legacy_finalized_job_id"):
        monkeypatch.setitem(app.STATE, key, None)
    app._recover_completed_job(allow_finalize=False)
    assert app.STATE["results"] == ([], 0)
    assert app.STATE["results_recovered"].startswith(f"job #{jid} · ")
    assert queue.get_job_status(older, db_path=database) is None
    assert queue.get_job_status(ghost, db_path=database) is None


# --------------------------------------------------------------------------- #
# Constatarea 12: butoanele spun diferența reală
# --------------------------------------------------------------------------- #


def _button_labels() -> dict[str, str]:
    """Eticheta fiecărui ui.button din sidebar, după handler-ul lui."""
    tree = ast.parse((ROOT / "app_nicegui.py").read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "button"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "on_click":
                out[ast.unparse(kw.value)] = node.args[0].value
    return out


def test_generate_buttons_do_not_suggest_that_one_skips_the_bench_decision():
    """Ambele butoane generează cu metoda din decizia bench; Auto-Pilot doar o
    arată întâi. „setări manuale” sugera un scorer fără decizie, care nu există."""
    labels = _button_labels()
    manual = labels["lambda: submit_generation(pure=False)"]
    auto = labels["apply_autopilot_and_generate"]
    assert manual == "🚀 Generează (metoda din decizia bench)"
    assert auto == "⚡ Auto-Pilot (arată metoda pe joc + generează)"
    assert "manual" not in manual
    assert not app.apply_autopilot_and_generate.__doc__.startswith("Aplică scorer")


# --------------------------------------------------------------------------- #
# Numărul Joker: metoda Urnei 2, cu rata și scorul Wilson
# --------------------------------------------------------------------------- #


def _urna2_entry(scorer: str) -> dict:
    """Intrarea deciziei pentru Urna 2 (top-1, 5% la întâmplare), ca decision.py."""
    return {
        "scorer": scorer,
        "ensemble": [{"method": scorer, "weight": 1.0}],
        "rationale": (
            f"{scorer}: rată top-1 (1/1) @ k1 = 0.071 (Wilson_lb=0.062), beat random "
            "(hipergeometric 0.0500) in 3/4 windows on the same top-1 (1/1) target "
            "(lift +0.0210) [după corecția Holm pentru 48 candidați: p=0.600 ≥ 0.05 "
            "— avantaj nedemonstrat]"
        ),
        "baseline_rate": 0.05,
        "target_label": "top-1 (1/1)",
        "low_confidence": False,
        "multiplicity": {"candidates": 48, "holm_p": 0.6, "alpha": 0.05, "proven": False},
    }


def _joker_result(urna2: dict | None) -> dict:
    winners = {"joker_urna1": {"method": "frequency", "pool_hint": 16}}
    if urna2 is not None:
        winners["joker_urna2"] = urna2
    return {
        **_result("frequency"),
        "hard_core_joker": [9],
        "audit": {"bench_winner": winners},
    }


def test_mail_names_the_joker_number_method_with_its_rate_and_wilson(monkeypatch):
    spec = lottery_by_id("joker")
    asked = []

    def _decision(key, pool):
        asked.append((key, pool))
        return _urna2_entry("markov_pairs")

    monkeypatch.setattr(app, "_decision_entry", _decision)
    data = _joker_result({"method": "markov_pairs", "pool_hint": 1, "single_pick": True})
    lines = app._mail_method_lines(spec, data, urna2=True)
    assert asked == [("joker_urna2", 1)]
    assert lines[0] == (
        "METODĂ JOKER: markov_pairs (câștigătoarea bench-ului pe Urna 2, top-1)"
    )
    assert lines[1] == (
        "RATING JOKER: rată top-1 7.10% față de 5.00% la întâmplare; "
        "scor Wilson (z=1) 6.20%; a bătut hazardul în 3/4 ferestre"
    )
    assert "Avantaj nedemonstrat" in lines[2] and "48" in lines[2]

    # Decizia Urnei 2 s-a mutat după generare: ratingul celeilalte metode nu apare.
    monkeypatch.setattr(app, "_decision_entry", lambda k, p: _urna2_entry("frequency"))
    lines = app._mail_method_lines(spec, data, urna2=True)
    assert len(lines) == 2 and "s-a schimbat după generare" in lines[1]
    assert "7.10%" not in lines[1]


def test_mail_joker_number_on_the_fallback_has_no_rating(monkeypatch):
    spec = lottery_by_id("joker")
    monkeypatch.setattr(app, "_decision_entry", lambda k, p: _urna2_entry("x"))
    data = _joker_result(
        {"method": "frequency", "fallback": True, "no_decision": True, "pool_hint": 1}
    )
    assert app._mail_method_lines(spec, data, urna2=True) == [
        "METODĂ JOKER: frequency (fără bench pentru România · Joker; "
        "rezervă implicită, fără rating)"
    ]


def test_mail_body_carries_the_joker_number_method_only_for_joker(monkeypatch):
    entries = {"joker_urna1": _entry("frequency", proven=False, holm_p=0.125)}
    entries["joker_urna2"] = _urna2_entry("markov_pairs")
    monkeypatch.setattr(app, "_decision_entry", lambda key, pool: entries.get(key, {}))
    data = _joker_result({"method": "markov_pairs", "pool_hint": 1, "single_pick": True})
    _mail_state(monkeypatch, "joker.csv", _history(["01-10-2026", "04-10-2026"]), {"joker": data})
    body = app._build_mail_body()
    assert "METODĂ: frequency (câștigătoarea bench-ului la pool 16)" in body
    assert "METODĂ JOKER: markov_pairs" in body
    assert "RATING JOKER: rată top-1 7.10% față de 5.00% la întâmplare" in body
    assert body.index("POOL:") < body.index("METODĂ JOKER") < body.index("CEL MAI BUN")

    _ro_mail_state(monkeypatch, _history(["01-10-2026", "04-10-2026"]))
    assert "JOKER" not in app._build_mail_body()
