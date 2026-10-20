"""Schemele reduse predefinite ale Loteriei Române (2026-10-17).

Utilizatorul joacă la 6/49 schemele reduse LR (Cod 59 pe 16 numere). Aplicația
le are acum ca date: desfășurările din broșurile loto.ro, verificate exact pe
indexul de câștiguri tipărit. Testele refac indexul din fișiere, fixează
așezarea pe pool (crescător, ca terminalul) și verifică panoul, Istoric hits,
raportul și mailul.
"""

from __future__ import annotations

import itertools
import json
from math import comb
from pathlib import Path

import pandas as pd
import pytest

import app_nicegui as app  # importul leagă numele UI, ca aplicația
import ui_hits
import ui_results
from covering import lr_schemes as lr
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from scripts.analysis import import_lr_schemes as importer
from scripts.analysis.audit_output import capture_ui

MANIFEST = json.loads((lr.LR_DIR / "index_oficial.json").read_text(encoding="utf-8"))
LABEL = {"649": "6/49", "540": "5/40", "joker": "joker"}
POOL16 = [2, 5, 9, 13, 17, 21, 22, 26, 30, 33, 35, 38, 41, 44, 46, 49]


def _scheme(game: str, code: int):
    return next(s for s in lr.lr_schemes(game) if s.code == code)


# --------------------------------------------------------------------------- #
# Datele
# --------------------------------------------------------------------------- #


def test_every_printed_scheme_is_loaded_with_its_size():
    loaded = {(s.game, s.code): (s.n, s.size) for s in lr.lr_schemes()}
    assert len(MANIFEST) == 33
    assert loaded == {
        (LABEL[e["game"]], e["code"]): (e["n"], e["variants"]) for e in MANIFEST
    }
    # Codurile pe care le juca utilizatorul și cele vechi din panou.
    assert loaded[("6/49", 59)] == (16, 112)
    assert loaded[("5/40", 37)] == (16, 348) and loaded[("5/40", 38)] == (16, 443)
    assert loaded[("joker", 12)] == (15, 118)


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: f"{e['game']}_cod{e['code']}")
def test_the_printed_win_index_is_recomputed_from_the_file(entry):
    """Indexul de câștiguri al broșurii, refăcut din variantele salvate."""
    scheme = _scheme(LABEL[entry["game"]], entry["code"])
    assert (
        importer.index_errors(entry["game"], scheme.n, scheme.variants, entry["index"])
        == []
    )


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: f"{e['game']}_cod{e['code']}")
def test_the_variants_are_the_ones_verified_at_import(entry):
    """Amprenta scrisă la import, după grilă, lista din stânga și index."""
    scheme = _scheme(LABEL[entry["game"]], entry["code"])
    assert importer.rows_sha256(scheme.variants) == entry["rows_sha256"]


def test_the_row_hash_catches_what_the_index_cannot():
    """O reetichetare a pozițiilor păstrează indexul tipărit; amprenta, nu."""
    entry = next(e for e in MANIFEST if e["game"] == "649" and e["code"] == 59)
    swap = {15: 16, 16: 15}
    variants = [sorted(swap.get(p, p) for p in v) for v in _scheme("6/49", 59).variants]
    assert importer.index_errors("649", 16, variants, entry["index"]) == []
    assert importer.rows_sha256(variants) != entry["rows_sha256"]
    joker = next(e for e in MANIFEST if e["game"] == "joker" and e["code"] == 35)
    variants = [list(v) for v in _scheme("joker", 35).variants]
    assert variants[0] == [1, 2, 3, 4, 6]
    variants[0] = [1, 3, 4, 6, 7]
    assert importer.joker_index_errors(8, variants, joker["index"]) == []
    assert importer.rows_sha256(variants) != joker["rows_sha256"]


def test_a_changed_variant_breaks_the_printed_index():
    entry = next(e for e in MANIFEST if e["game"] == "649" and e["code"] == 59)
    variants = [list(v) for v in _scheme("6/49", 59).variants]
    variants[0] = [1, 2, 3, 4, 5, 7]
    assert importer.index_errors("649", 16, variants, entry["index"])
    joker = next(e for e in MANIFEST if e["game"] == "joker" and e["code"] == 34)
    variants = [list(v) for v in _scheme("joker", 34).variants]
    variants[0] = [1, 2, 3, 4, 9] if variants[0] != [1, 2, 3, 4, 9] else [1, 2, 3, 5, 9]
    assert importer.joker_index_errors(9, variants, joker["index"])


def test_a_broken_file_is_skipped_alone(tmp_path, caplog):
    good = (lr.LR_DIR / "LR_649_cod48.txt").read_text(encoding="utf-8")
    (tmp_path / "LR_649_cod48.txt").write_text(good, encoding="utf-8")
    (tmp_path / "LR_649_cod49.txt").write_text(
        good.replace("# variante: 12", "# variante: 13"), encoding="utf-8"
    )
    (tmp_path / "LR_joker_cod45.txt").write_text(
        "# numere: 7\n# variante: 1\n1 2 3 4 8\n", encoding="utf-8"
    )
    (tmp_path / "LR_649_cod057.txt").write_text("# numere: 0\n# variante: 0\n")
    (tmp_path / "LR_649_cod048.txt").write_text(good, encoding="utf-8")  # cod dublat
    (tmp_path / "LR_joker_cod34.txt").write_text(
        "\ufeff" + (lr.LR_DIR / "LR_joker_cod34.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )  # BOM de editor Windows
    loaded = lr._load(str(tmp_path))
    assert [(s.game, s.code) for s in loaded] == [("6/49", 48), ("joker", 34)]
    for name in ("LR_649_cod49.txt", "LR_joker_cod45.txt", "LR_649_cod057.txt"):
        assert name in caplog.text
    assert "codul apare de două ori" in caplog.text


def test_an_unreadable_file_is_not_reported_as_a_missing_code(monkeypatch, tmp_path):
    for f in lr.LR_DIR.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    (tmp_path / "LR_649_cod59.txt").write_text("stricat\n", encoding="utf-8")
    monkeypatch.setattr(lr, "LR_DIR", tmp_path)
    assert lr.lr_schemes("6/49", 16) == []
    assert lr.unloaded_schemes("6/49", 16) == ["Cod 59"]
    with capture_ui() as ui:
        ui_results._render_cost("6/49", _data())
    text = ui.text()
    assert "schema redusă LR Cod 59 pentru 16 nr. nu s-a putut citi" in text
    assert "fără schemă" not in text
    assert lr.unloaded_schemes("6/49", 14) == []


# --------------------------------------------------------------------------- #
# Așezarea pe pool și șansele
# --------------------------------------------------------------------------- #


def test_positions_follow_the_played_numbers_in_ascending_order():
    """Broșura 6/49, exemplul 3: pozițiile 1..13 = numerele jucate, crescător."""
    scheme = _scheme("6/49", 48)
    pool = [40, 3, 17, 22, 9, 1, 48, 30, 11]
    tickets = lr.scheme_tickets(scheme, pool)
    nums = sorted(pool)
    assert tickets == [[nums[p - 1] for p in v] for v in scheme.variants]
    assert tickets[0] == [1, 3, 9, 11, 17, 22]
    with pytest.raises(ValueError):
        lr.scheme_tickets(scheme, pool[:8])


def test_cod59_odds_and_guarantees_match_the_brochure():
    s = _scheme("6/49", 59)
    odds = lr.scheme_odds(s)
    assert [round(100 * odds[t], 4) for t in (3, 4, 5, 6)] == [
        29.8093,
        7.4314,
        0.2074,
        0.0008,
    ]
    g = lr.scheme_guarantees(s)
    # Indexul tipărit: 4 ieșite -> 4 pe o variantă în 92,31% din cazuri.
    assert round(100 * g[4][4], 2) == 92.31 and g[4][3] == 1.0
    assert round(100 * g[5][5], 2) == 15.38 and g[5][4] == 1.0
    assert round(100 * g[6][5], 2) == 85.31 and round(100 * g[6][6], 2) == 1.40
    # 3+ pe o variantă = 3+ în pool: trei numere ieșite sunt mereu pe o variantă.
    pool3 = sum(comb(16, k) * comb(33, 6 - k) for k in range(3, 7)) / comb(49, 6)
    assert odds[3] == pytest.approx(pool3, abs=1e-12)


def test_540_and_joker_odds_use_their_own_geometry():
    """5/40: 6 extrase din 40, bilet de 5; Joker: 5 din 45 (urna 1)."""
    s = _scheme("5/40", 37)
    odds, g = lr.scheme_odds(s), lr.scheme_guarantees(s)
    assert [round(100 * odds[t], 4) for t in (3, 4, 5)] == [45.5557, 14.912, 0.3142]
    assert sorted(g) == [3, 4, 5, 6]
    assert round(100 * g[4][4], 2) == 91.48 and round(100 * g[6][5], 2) == 46.30
    j = _scheme("joker", 12)
    odds, g = lr.scheme_odds(j), lr.scheme_guarantees(j)
    assert [round(100 * odds[t], 4) for t in (3, 4, 5)] == [19.7975, 1.5791, 0.0097]
    assert sorted(g) == [3, 4, 5] and g[5][4] == 1.0
    assert round(100 * g[4][4], 2) == 39.78 and round(100 * g[5][5], 2) == 3.93


def test_small_odds_print_without_an_exponent():
    assert ui_results._five_plus_pct(12 / 13983816) == "0.000086%"
    assert ui_results._five_plus_pct(0.00005) == "0.005%"
    lines = ui_results._lr_scheme_lines("6/49", _data(POOL16[:9]))
    assert "6 0.000086%" in lines[1] and "e-" not in " ".join(lines)


def test_best_hits_counts_the_best_variant_by_brute_force():
    s = _scheme("5/40", 37)
    drawn = [2, 9, 13, 26, 41, 40]
    tickets = lr.scheme_tickets(s, POOL16)
    assert lr.best_hits(s, POOL16, drawn) == max(
        len(set(t) & set(drawn)) for t in tickets
    )


# --------------------------------------------------------------------------- #
# Panoul, raportul, mailul
# --------------------------------------------------------------------------- #


def _cover_152():
    from covering.dispatch import generate_wheel

    wheel, _ = generate_wheel(
        "lajolla", POOL16, 6, 4, 0, {p: 1.0 for p in POOL16}, draw_n=6, max_num=49
    )
    return [list(t) for t in wheel]


def _data(pool=POOL16, variants=None):
    return {
        "pool_size": len(pool),
        "hard_core": list(reversed(pool)),
        "variants": variants or [],
        "guarantee": 4,
        "context": {"coverage_pct": 100.0},
        "audit": {},
    }


def test_panel_shows_what_cod59_guarantees_and_how_it_compares():
    data = _data(variants=_cover_152())
    with capture_ui() as ui:
        ui_results._render_cost("6/49", data)
    text = ui.text()
    assert "nu e documentată" not in text
    assert "**Cod 59** (112 var. ≈ 896 Lei în variante)" in text
    assert (
        "Cod 59, cele mai multe numere pe o variantă, după câte ies din cele 16: "
        "3 ieșite → 3 sigur; 4 → 3 sigur, 4 în 92.31%; 5 → 4 sigur, 5 în 15.38%; "
        "6 → 4 sigur, 5+ în 85.31%, 6 în 1.40%"
    ) in text
    assert (
        "Cod 59, șansa ca cel puțin o variantă să prindă, la o extragere uniformă: "
        "3+ 29.809% · 4+ 7.431% · 5+ 0.207% · 6 0.0008%"
    ) in text
    assert "(broșura 6/49 loto.ro, exemplul 3)" in text
    assert (
        "Cod 59 față de wheel-ul nostru (152 var.): cu 40 de variante mai puține "
        "(320 Lei); 4+ 7.431% față de 7.960%; 5+ 0.207% față de 0.251%"
    ) in text
    assert "ordine crescătoare" in text
    # Desfășurarea pe pool-ul de azi: 112 rânduri, prima pe cele mai mici numere.
    layout = next(
        n for n in ui.walk() if n["kind"] == "label" and "V  1:" in str(n["args"][0])
    )
    lines = str(layout["args"][0]).split("\n")
    assert len(lines) == 112 and lines[0] == "V  1: 2, 5, 9, 13, 17, 21"


def test_pool_without_a_code_lists_the_sizes_that_have_one():
    pool = [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40]
    with capture_ui() as ui:
        ui_results._render_cost("6/49", _data(pool))
    text = ui.text()
    assert "fără schemă redusă oficială cu variante întregi pentru 14 nr." in text
    assert (
        "Loteria Română are scheme reduse cu variante întregi pentru "
        "9, 10, 11, 12, 16 numere."
    ) in text
    assert "Sistem complet C(14,6) = 3003 var." in text


def test_empty_or_restricted_pool_sizes_agree():
    with capture_ui() as ui:
        ui_results._render_cost("6/49", {**_data([]), "pool_size": 16})
    text = ui.text()
    assert "pool gol" in text and "are scheme reduse" not in text
    # Bază restrânsă: 12 numere jucate, pool_size cerut 16.
    pool = POOL16[:12]
    with capture_ui() as ui:
        ui_results._render_cost("6/49", {**_data(pool), "pool_size": 16})
    text = ui.text()
    assert "(12 nr.)" in text and "Sistem complet C(12,6) = 924 var." in text


def test_joker_layout_carries_the_joker_number_on_every_variant():
    pool = list(range(3, 48, 3))[:15]
    data = {**_data(pool), "hard_core_joker": [7]}
    with capture_ui() as ui:
        ui_results._render_cost("joker", data)
    layout = next(
        n for n in ui.walk() if n["kind"] == "label" and "V  1:" in str(n["args"][0])
    )
    lines = str(layout["args"][0]).split("\n")
    assert len(lines) == 118 and all(line.endswith("  +7") for line in lines)
    text = ui.text()
    assert "Numărul Joker (câmpul 2) intră pe toate variantele schemei." in text
    assert (
        "șansa ca cel puțin o variantă să prindă (urna 1, fără numărul Joker)" in text
    )
    assert "broșura acestui joc nu spune ordinea" in text


def test_foreign_results_get_no_lr_scheme():
    data = {**_data(), "country": "DE", "game_id": "de_lotto"}
    assert ui_results._lr_schemes_for("de_lotto", data) == []
    assert ui_results._lr_scheme_lines("de_lotto", data) == []


def test_report_lines_carry_the_scheme():
    lines = ui_results._lr_scheme_lines("5/40", _data())
    assert lines[0].startswith("Cod 37, cele mai multe numere pe o variantă")
    assert any(line.startswith("Cod 38, ") for line in lines)


def test_mail_names_the_lr_code_of_the_pool(monkeypatch):
    df = pd.DataFrame({"date": ["04-10-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    monkeypatch.setitem(
        app.STATE, "results", ([("loto_6_49.csv", {"6/49": _data()})], 0)
    )
    monkeypatch.setitem(app.STATE, "result_sources", {"loto_6_49.csv": df})
    body = app._build_mail_body()
    assert "SCHEMĂ LR: Cod 59 (112 var. ≈ 896 lei)" in body


# --------------------------------------------------------------------------- #
# Istoric hits
# --------------------------------------------------------------------------- #


def _wf(pool, draws, game="6/49"):
    flat = []
    for di, (date, drawn) in enumerate(draws, start=10):
        ctx = {"hard_core": list(reversed(pool)), "actual": sorted(drawn)}
        tickets = [pool[:6], pool[6:12]] if game == "6/49" else [pool[:5], pool[5:10]]
        for t in tickets:
            flat.append(
                WalkForwardResult(
                    draw_index=di,
                    draw_date=date,
                    variant=list(t) + ([7] if game == "joker" else []),
                    hits=len(set(t) & set(drawn)),
                    hits_union=len(set(pool) & set(drawn)),
                    wheel_coverage=100.0,
                    ticket_context=ctx,
                )
            )
    return flat


def test_history_replays_cod59_on_the_pool_of_each_draw():
    draws = [
        ("06-08-2026", [2, 5, 9, 13, 17, 1]),  # 5 din pool
        ("09-08-2026", [2, 9, 30, 41, 3, 4]),  # 4 din pool
        ("13-08-2026", [2, 3, 4, 6, 7, 8]),  # 1 din pool
    ]
    flat = _wf(POOL16, draws)
    s = _scheme("6/49", 59)
    expected = [lr.best_hits(s, POOL16, d) for _, d in draws]
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "6/49", {"pool_size": 16}, pool_n=16)
    nodes = list(ui.walk())
    title = next(
        i
        for i, n in enumerate(nodes)
        if n["kind"] == "label" and str(n["args"][0]).startswith("📋 Cod 59 (112 var.)")
    )
    table = next(n for n in nodes[title:] if n["kind"] == "table")
    rows = {r["prag"]: r for r in table["kwargs"]["rows"]}
    wf_best = [
        max(len(set(t) & set(d)) for t in (POOL16[:6], POOL16[6:12])) for _, d in draws
    ]
    assert "+3" not in rows  # istoricul arată numai 4+
    for t in (4, 5):
        k = sum(1 for h in expected if h >= t)
        assert rows[f"+{t}"]["lr"] == f"{k} ({k / 3 * 100:.2f}%)"
        k = sum(1 for h in wf_best if h >= t)
        assert rows[f"+{t}"]["tick"] == f"{k} ({k / 3 * 100:.2f}%)"
    assert rows["6"]["rnd"] == "0.0008%"
    cols = {c["name"]: c["label"] for c in table["kwargs"]["columns"]}
    assert cols["tick"] == "🎟️ Variantele WF (2 var.)"
    assert "pusă pe pool-ul de atunci în ordine crescătoare" in ui.text()


def test_history_540_counts_all_six_drawn_numbers():
    pool = [1, 3, 4, 7, 9, 12, 15, 18, 20, 23, 26, 29, 31, 34, 37, 40]
    s37 = _scheme("5/40", 37)
    first = lr.scheme_tickets(s37, pool)[0]
    draws = [
        ("06-08-2026", first + [2]),  # o variantă întreagă + un număr din afară
        ("09-08-2026", [1, 3, 4, 7, 9, 12]),  # 6 din pool
        ("13-08-2026", [2, 5, 6, 8, 10, 11]),  # 0 din pool
    ]
    flat = _wf(pool, draws, game="5/40")
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "5/40", {"pool_size": 16}, pool_n=16)
    nodes = list(ui.walk())
    for s in lr.lr_schemes("5/40", 16):
        title = next(
            i
            for i, n in enumerate(nodes)
            if n["kind"] == "label"
            and str(n["args"][0]).startswith(f"📋 {s.label} ({s.size} var.)")
        )
        rows = {
            r["prag"]: r
            for r in next(n for n in nodes[title:] if n["kind"] == "table")["kwargs"][
                "rows"
            ]
        }
        tickets = lr.scheme_tickets(s, pool)
        best = [max(len(set(t) & set(d)) for t in tickets) for _, d in draws]
        if s.code == 37:
            assert best[0] == 5
        for t, label in ((4, "+4"), (5, "5")):
            k = sum(1 for h in best if h >= t)
            assert rows[label]["lr"] == f"{k} ({k / 3 * 100:.2f}%)"


def test_history_without_stored_pools_does_not_deny_the_code():
    flat = _wf(POOL16, [("06-08-2026", [2, 5, 9, 13, 17, 1])])
    for row in flat:
        row.ticket_context = None
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "6/49", {"pool_size": 16}, pool_n=16)
    text = ui.text()
    assert "Cod 59: reluarea cere pool-ul fiecărei extrageri" in text
    assert "nu are schemă" not in text


def test_history_says_when_the_pool_has_no_code():
    pool = list(range(1, 46, 2))[:16]
    flat = _wf(pool, [("06-08-2026", [1, 4, 7, 2, 3])], game="joker")
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(flat, "joker", {"pool_size": 16}, pool_n=16)
    assert (
        "Loteria Română nu are schemă cu variante întregi pentru 16 numere la acest "
        "joc (are pentru 7, 8, 9, 10, 11, 12, 13, 15)"
    ) in ui.text()


def test_replay_matches_brute_force_over_many_draws():
    s = _scheme("6/49", 59)
    draws = list(
        itertools.islice(itertools.combinations(range(1, 50, 2), 6), 0, 400, 7)
    )
    per = {
        i: {
            "pool_numbers": sorted(POOL16),
            "drawn": list(d),
            "best_ticket": 0,
            "label": str(i),
        }
        for i, d in enumerate(draws)
    }
    res = ui_hits._lr_replay(per, "6/49")[0]
    tickets = lr.scheme_tickets(s, POOL16)
    best = [max(len(set(t) & set(d)) for t in tickets) for d in draws]
    assert res["observed"] == {t: sum(1 for h in best if h >= t) for t in (3, 4, 5, 6)}
    assert Path(lr.LR_DIR).is_dir()
