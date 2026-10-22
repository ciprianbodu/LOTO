"""Schemele reduse LR ca variante ale motorului, la același plafon (2026-10-20).

Cerința: mai multe hituri. Pe aceleași numere și cu tot atâtea variante, unele
scheme LR prind mai des decât construcția motorului (de pildă 6/49 Cod 50, 10
numere, 30 de variante, la garanția 5; 5/40 Cod 37, 16 și 348, și Joker Cod 15,
11 și 22, la garanția 4). Motorul le încearcă la plafonul egal cu mărimea lor,
de la orice joc cu același bilet, și le adoptă numai certificate. Cod 59 e deja
optim la fiecare prag: rămâne ce era. Tot aici, revizia adversarială: pool peste
16, coverul completat la garanția egală cu biletul, panoul.
"""

from __future__ import annotations

import pytest

from covering import lr_schemes as lr
from covering.dispatch import generate_wheel
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from loto_enterprise.core import walk_forward_adapter as wfa

GEOMETRY = {"6/49": (6, 6, 49), "5/40": (5, 6, 40), "joker": (5, 5, 45)}


def _wheel(game, n, guarantee, cap):
    pick, draws, universe = GEOMETRY[game]
    pool = list(range(1, n + 1))
    scores = {p: float(n + 1 - p) for p in pool}
    wheel, cov = generate_wheel(
        "hitcover", pool, pick, guarantee, cap, scores, draw_n=draws, max_num=universe
    )
    odds = wheel_hit_probabilities(pool, [t[:pick] for t in wheel], draws, universe)
    return wheel, cov, odds["ticket"]


def _scheme(game, code):
    return next(s for s in lr.lr_schemes(game) if s.code == code)


@pytest.mark.parametrize(
    ("game", "code", "guarantee", "top"),
    [("6/49", 50, 5, 5), ("5/40", 37, 4, 4), ("joker", 15, 4, 4)],
)
def test_the_engine_adopts_a_better_lr_scheme_at_its_size(game, code, guarantee, top):
    s = _scheme(game, code)
    wheel, _cov, odds = _wheel(game, s.n, guarantee, s.size)
    lr_odds = lr.scheme_odds(s)
    assert len(wheel) == s.size
    assert odds[top] == pytest.approx(lr_odds[top], rel=1e-12)
    for t, p in lr_odds.items():
        assert odds[t] >= p - 1e-15
    # Profilul exact al schemei, pe pozițiile 1..n ale pool-ului.
    pool = list(range(1, s.n + 1))
    expected = wheel_hit_profile(pool, [list(v) for v in s.variants])
    assert wheel_hit_profile(pool, wheel) == expected


def test_the_gain_over_the_previous_construction(monkeypatch):
    """Construcția motorului fără etapa LR, la aceeași geometrie: 14,58% la 4+."""
    from covering import dispatch

    pool = list(range(1, 17))
    scores = {p: float(17 - p) for p in pool}
    monkeypatch.setattr(
        dispatch,
        "improve_with_lr_scheme",
        lambda pool_, wheel, *a: (wheel, {"applied": False}),
    )
    old, _ = generate_wheel("hitcover", pool, 5, 4, 348, scores, draw_n=6, max_num=40)
    before = wheel_hit_probabilities(pool, old, 6, 40)["ticket"]
    after = lr.scheme_odds(_scheme("5/40", 37))
    assert after[4] > before[4] * 1.02  # 14,91% față de 14,58%


@pytest.mark.parametrize(
    ("game", "n", "guarantee", "cap"),
    [("6/49", 16, 4, 112), ("joker", 9, 4, 9), ("6/49", 10, 4, 15)],
)
def test_a_scheme_that_does_not_dominate_is_left_out(game, n, guarantee, cap):
    """Cod 59 egal; Joker Cod 34 sub aplicație; Cod 49 compromis."""
    _pick, draws, universe = GEOMETRY[game]
    wheel, _cov, _odds = _wheel(game, n, guarantee, cap)
    pool = list(range(1, n + 1))
    order = sorted(pool, key=lambda p: -float(n + 1 - p))
    _w, audit = lr.improve_with_lr_scheme(order, wheel, guarantee, draws, universe)
    assert not audit["applied"]


def test_the_stage_needs_the_same_number_of_variants():
    s = _scheme("joker", 15)
    pool = list(range(1, 12))
    wheel = [sorted(pool[p - 1] for p in v) for v in s.variants][:21]
    _w, audit = lr.improve_with_lr_scheme(pool, wheel, 4, 5, 45)
    assert not audit["applied"] and audit["reason"] == "no scheme of this size"


def test_physical_tickets_do_not_take_the_stage():
    """„Bilet complet” (fără `max_num`) rămâne pe construcția de dinainte."""
    s = _scheme("joker", 15)
    pool = list(range(1, 12))
    scores = {p: float(12 - p) for p in pool}
    wheel, _ = generate_wheel("hitcover", pool, 5, 4, 22, scores, draw_n=5)
    assert wheel_hit_profile(pool, wheel) != wheel_hit_profile(
        pool, [list(v) for v in s.variants]
    )


def test_the_wf_key_signs_the_scheme_only_at_its_size():
    sig = wfa._wheel_sig(16, "5/40", 4, None, 348)
    assert f"|lr{lr.lr_scheme_signature(16, 5, 348)}" in sig
    assert "|lr" not in wfa._wheel_sig(16, "5/40", 4, None, 347)
    assert "|lr" not in wfa._wheel_sig(16, "5/40", 4, None, 0)
    # Joker și 5/40 au același bilet de 5: la 11 numere, 22 de variante, Cod 15.
    assert "|lr" in wfa._wheel_sig(11, "joker", 4, None, 22)


def test_the_signature_follows_the_scheme_rows(monkeypatch, tmp_path):
    for f in lr.LR_DIR.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    monkeypatch.setattr(lr, "LR_DIR", tmp_path)
    before = lr.lr_scheme_signature(11, 5, 22)
    assert before is not None
    s = _scheme("joker", 15)
    swap = {10: 11, 11: 10}  # pozițiile 10 și 11 schimbate pe fiecare variantă
    text = [
        "# numere: 11",
        f"# variante: {s.size}",
        *(" ".join(map(str, sorted(swap.get(p, p) for p in v))) for v in s.variants),
    ]
    (tmp_path / "LR_joker_cod15.txt").write_text(
        "\n".join(text) + "\n", encoding="utf-8"
    )
    lr._load.cache_clear()
    assert lr.lr_scheme_signature(11, 5, 22) not in (None, before)


def test_cod59_is_optimal_at_every_threshold():
    """Împachetare perfectă: nicio așezare de 112 variante pe 16 numere nu prinde
    mai des la 3+, 4+, 5+ sau 6."""
    s = _scheme("6/49", 59)
    assert lr.optimal_thresholds(s) == (3, 4, 5, 6)
    # Marginea e strânsă și pentru o așezare oarecare: cea a aplicației la 112.
    _w, _c, odds = _wheel("6/49", 16, 4, 112)
    for t in (3, 4, 5, 6):
        assert odds[t] <= lr._upper_bound(16, 112, 6, t, 6, 49) * (1 + 1e-12)


def test_the_upper_bound_is_never_beaten_by_the_engine():
    """Marginea e o margine: niciun wheel al motorului nu o depășește."""
    for game, n, g, cap in [
        ("6/49", 12, 4, 22),
        ("5/40", 12, 4, 40),
        ("joker", 9, 3, 9),
    ]:
        pick, draws, universe = GEOMETRY[game]
        _w, _c, odds = _wheel(game, n, g, cap)
        for t, p in odds.items():
            if 3 <= t <= min(pick, draws):
                assert p <= lr._upper_bound(n, cap, pick, t, draws, universe) * (
                    1 + 1e-12
                )


def test_joker_codes_below_the_bound_are_not_called_optimal():
    assert 4 not in lr.optimal_thresholds(_scheme("joker", 34))
    assert 4 not in lr.optimal_thresholds(_scheme("joker", 12))
    assert 4 in lr.optimal_thresholds(_scheme("joker", 15))


# --------------------------------------------------------------------------- #
# Panoul: optimalitatea și aplicația la același cost
# --------------------------------------------------------------------------- #
def _panel_lines(game, n):
    import app_nicegui  # noqa: F401  (leagă numele UI, ca aplicația)
    import ui_results

    data = {"hard_core": list(range(1, n + 1)), "pool_size": n, "variants": []}
    return ui_results._lr_scheme_lines(game, data)


def test_panel_says_cod59_is_optimal():
    lines = _panel_lines("6/49", 16)
    assert (
        "Schema Cod 59 e optimă: nicio altă așezare de 112 variante pe 16 numere nu "
        "prinde mai des, la niciun prag."
    ) in lines
    assert not any(line.startswith("La același cost") for line in lines)


def test_panel_points_to_the_app_where_it_beats_the_joker_code():
    lines = _panel_lines("joker", 9)
    better = next(line for line in lines if line.startswith("La același cost"))
    assert "aplicația prinde mai des decât Cod 34: cu plafonul 9 și garanția 5" in better
    assert "3+ 4.094% față de 3.940%" in better
    assert "Variantele aplicației se joacă la agenție ca variante simple." in better
    # Ce pierde jucătorul: 4 sigur pe o variantă când ies 5 dintre cele 9.
    assert (
        "Codul dă însă sigur 4 pe o variantă când ies 5 dintre cele 9 numere jucate; "
        "wheel-ul aplicației, în 98.41% din cazuri."
    ) in lines


def test_panel_names_the_cheaper_complete_cover_at_540():
    lines = _panel_lines("5/40", 16)
    assert (
        "La 4+ și 5 nu se știe dacă se poate mai bine: schema dă 14.912% și 0.314%, "
        "iar marginea superioară pentru orice așezare de 348 de variante pe 16 numere "
        "e 15.451% și 0.317%, în aceeași ordine; marginea nu e mereu atinsă."
    ) in lines
    cheaper = next(line for line in lines if line.startswith("Aplicația dă la fel"))
    assert "cu 404 variante, 195 Lei mai puțin (garanția 4, fără plafon)" in cheaper
    assert "la 5: 0.363% față de 0.393%" in cheaper
    # Cod 37: după etapa nouă aplicația dă exact schema, deci nicio comparație.
    assert not any("decât Cod 37" in line for line in lines)


def test_a_missed_bound_is_not_called_below_the_maximum():
    """Revizia 2026-10-20: „sub maximul posibil” la o margine neatinsă era fals la
    Joker Cod 45, optimă la fiecare prag (căutare exhaustivă)."""
    import app_nicegui  # noqa: F401
    import ui_results

    # Toate schemele, și cele 5/40 de 17-19 numere (șansa exactă merge până la 20).
    for s in lr.lr_schemes():
        for line in ui_results._lr_quality_lines(s, lr.scheme_odds(s), 5.0):
            assert "sub maximul" not in line, (s.game, s.code, line)
    assert (
        "Schema Cod 45 e optimă: nicio altă așezare de 5 variante pe 7 numere nu "
        "prinde mai des, la niciun prag."
    ) in _panel_lines("joker", 7)


def test_cod49_is_optimal_at_4_and_improvable_at_5():
    lines = _panel_lines("6/49", 10)
    assert (
        "Schema Cod 49 e optimă la 3+ și 4+: nicio altă așezare de 15 variante pe "
        "10 numere nu prinde mai des la aceste praguri."
    ) in lines
    assert (
        "Cu tot atâtea variante se poate prinde mai des: 5+ 0.027% față de 0.026% "
        "(wheel-ul aplicației cu garanția 6)."
    ) in lines


def test_a_gain_under_one_percent_is_not_advice():
    lines = _panel_lines("5/40", 12)
    assert (
        "Cu plafonul 113 și garanția 4, aplicația prinde abia mai des decât Cod 26: "
        "5 0.102% față de 0.101%. Sub 1% în plus nu merită 113 variante simple în "
        "locul codului."
    ) in lines
    assert not any(line.startswith("La același cost") for line in lines)


def test_two_values_that_print_alike_get_more_digits():
    import app_nicegui  # noqa: F401
    import ui_results

    assert ui_results._lr_pct_pair(0.000452274, 0.000453316) == ("0.0452%", "0.0453%")
    assert ui_results._lr_pct_pair(0.0016, 0.0016) == ("0.160%", "0.160%")
    lines = _panel_lines("5/40", 10)
    assert any("5 0.0453% față de 0.0452%" in line for line in lines)


def test_the_offline_proofs_reproduce():
    """Joker Cod 45 la 4+: maximul pe toate cele 4.845 de așezări (primul bloc fixat)
    e chiar numărătorul schemei; modelul ILP dă același maxim pe aceeași geometrie."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent / "scripts" / "analysis" / "prove_lr_optimality.py"
    spec = importlib.util.spec_from_file_location("prove_lr_optimality", path)
    prove = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prove)
    s = _scheme("joker", 45)
    best, _rows = prove.exhaustive(7, 5, 5, 4, 5, 45)
    assert best == lr.scheme_numerators(s)[4] == 895
    num, _chosen, dual, proved = prove.ilp(7, 5, 5, 4, 5, 45, 60)
    assert proved and num == 895 and dual < 896
    for (game, code), rows in lr._offline(str(lr.LR_DIR)).items():
        for t in rows:
            assert lr._offline_entry(_scheme(game, code), t) is not None, (game, code, t)


def test_a_stale_offline_proof_is_ignored(monkeypatch, tmp_path):
    import json

    for f in lr.LR_DIR.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    entries = json.loads((tmp_path / "optimal_offline.json").read_text("utf-8"))
    for e in entries:
        e["scheme"] += 1  # numărătorul altei desfășurări
    (tmp_path / "optimal_offline.json").write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(lr, "LR_DIR", tmp_path)
    lr._offline.cache_clear()
    lr.optimal_thresholds.cache_clear()
    try:
        assert 4 not in lr.optimal_thresholds(_scheme("joker", 45))
    finally:
        lr._offline.cache_clear()
        lr.optimal_thresholds.cache_clear()


def test_same_size_data_matches_the_engine():
    """Intrările ieftine din `app_same_size.json`, refăcute cu motorul de acum."""
    for game, code in [("joker", 34), ("joker", 15), ("6/49", 49), ("6/49", 50)]:
        s = _scheme(game, code)
        for g, variants, stored in lr.app_same_size(s):
            if g < 3:
                continue
            wheel, _cov, odds = _wheel(game, s.n, g, s.size)
            assert len(wheel) == variants
            for t, p in stored.items():
                assert odds[t] == pytest.approx(p, rel=1e-9), (game, code, g, t)


# --------------------------------------------------------------------------- #
# Revizia adversarială 2026-10-20
# --------------------------------------------------------------------------- #
def test_cod45_is_adopted_at_17_numbers_and_signed_in_the_key():
    """2026-10-22: Loto 5/40 se joacă până la 19 numere, iar șansa exactă merge
    până la 20. La 17 numere cu plafonul 488, Cod 45 bate greedy-ul (acoperire 4
    94,96% față de ~91%) și intră certificat; cheia WF îl semnează. Înainte, pool
    peste 16 sărea etapa și cheia."""
    pool = list(range(1, 18))
    scores = {p: float(18 - p) for p in pool}
    wheel, _ = generate_wheel("hitcover", pool, 5, 4, 488, scores, None, 6, 40)
    cod45 = _scheme("5/40", 45)
    assert len(wheel) == 488
    assert wheel_hit_profile(pool, wheel) == wheel_hit_profile(
        pool, [list(v) for v in cod45.variants]
    )
    sig = lr.lr_scheme_signature(17, 5, 488)
    assert sig and f"|lr{sig}" in wfa._wheel_sig(17, "5/40", 4, None, 488)
    # Peste cel mai mare cod nu există candidați.
    assert lr.lr_candidates(20, 5, 930) == []


def test_adoption_at_guarantee_equal_to_ticket():
    """5/40 pool 16, garanția 5, plafonul 443: Cod 38 bate wheel-ul completat."""
    s = _scheme("5/40", 38)
    wheel, _cov, _odds = _wheel("5/40", 16, 5, 443)
    pool = list(range(1, 17))
    assert wheel_hit_profile(pool, wheel) == wheel_hit_profile(
        pool, [list(v) for v in s.variants]
    )


def test_adoption_across_games_with_the_same_ticket():
    """5/40 pool 11, garanția 4, plafonul 22: schema Joker Cod 15 (bilet de 5),
    certificată pe extragerea 5/40."""
    s = _scheme("joker", 15)
    wheel, _cov, _odds = _wheel("5/40", 11, 4, 22)
    pool = list(range(1, 12))
    assert wheel_hit_profile(pool, wheel) == wheel_hit_profile(
        pool, [list(v) for v in s.variants]
    )


@pytest.mark.parametrize(
    ("game", "cap", "four"),
    [("5/40", 405, 0.1602655), ("5/40", 444, 0.1602655), ("5/40", 500, 0.1602655),
     ("joker", 450, 0.0467752)],
)
def test_guarantee_equal_to_ticket_keeps_the_lower_complete_cover(game, cap, four):
    """Garanția egală cu biletul, plafonul peste coverul complet de 4 (404 variante
    pe 16 numere): coverul, completat până la plafon, intră certificat. Înainte,
    5/40 dădea 4+ 16,03% la 404 variante și 8,30% la 405."""
    _w, _c, odds = _wheel(game, 16, 5, cap)
    _w, _c, at_cover = _wheel(game, 16, 5, 404)
    assert odds[4] == pytest.approx(four, rel=1e-6)
    for t, p in at_cover.items():
        assert odds[t] >= p - 1e-15


def test_padding_keeps_distinct_tickets_and_the_incumbent_size():
    from covering.guarantee_portfolio import _padded

    cover = [[1, 2, 3, 4, 5], [1, 2, 3, 4, 6]]
    wheel = [[1, 2, 3, 4, 5], [2, 3, 4, 5, 6], [1, 3, 4, 5, 6], [1, 2, 4, 5, 6]]
    out = _padded(cover, wheel)
    assert out == [[1, 2, 3, 4, 5], [1, 2, 3, 4, 6], [2, 3, 4, 5, 6], [1, 3, 4, 5, 6]]
    assert _padded(wheel, wheel) == wheel


def test_the_wf_key_marks_the_padded_portfolio():
    assert "|lg2:" in wfa._wheel_sig(16, "5/40", 5, None, 444)
    assert "lg1" not in wfa._wheel_sig(16, "5/40", 5, None, 444)
