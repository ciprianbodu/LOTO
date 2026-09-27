"""Biletul complet: 3 variante la 6/49, 4 la 5/40, 2 la Joker, din pool."""

from __future__ import annotations

from itertools import combinations

from loto_enterprise.core.full_ticket import build_full_ticket
from loto_enterprise.core.pool_selection import select_pool_from_scores


def _cov(variants, pool, g):
    targets = list(combinations(pool, g))
    hit = sum(any(set(t) <= set(v) for v in variants) for t in targets)
    return hit / len(targets) * 100


def test_variant_counts_and_sizes_per_game():
    pool = [3, 7, 11, 19, 24, 31, 38, 40]
    for game, n, pick in (("6/49", 3, 6), ("5/40", 4, 5), ("joker", 2, 5)):
        data = {"hard_core": pool, "guarantee": 4, "hard_core_joker": [12]}
        t = build_full_ticket(game, data)
        assert t["error"] is None
        assert len(t["variants"]) == n
        for v in t["variants"]:
            main = v[:pick]
            assert len(main) == pick and set(main) <= set(pool)
        assert abs(t["coverage"] - round(_cov([v[:pick] for v in t["variants"]], pool, 4), 2)) < 0.02


def test_joker_ball_appended_to_every_variant():
    t = build_full_ticket(
        "joker", {"hard_core": [1, 6, 9, 23, 24, 29, 42, 43], "guarantee": 4, "hard_core_joker": [20]}
    )
    assert t["joker"] == 20
    assert all(len(v) == 6 and v[-1] == 20 for v in t["variants"])


def test_every_pool_number_is_played():
    pool = [1, 6, 9, 23, 24, 29, 42, 43]
    t = build_full_ticket("5/40", {"hard_core": pool, "guarantee": 4})
    assert set(pool) <= {n for v in t["variants"] for n in v}


def test_pool_smaller_than_a_ticket_is_refused():
    t = build_full_ticket("6/49", {"hard_core": [1, 2, 3], "guarantee": 3})
    assert t["error"] and "variants" not in t


_RANK = {n: float(26 - n) for n in range(1, 26)}  # 1 = cel mai bine clasat


def _data(pool, scores=_RANK, **extra):
    d = {"hard_core": pool, "guarantee": 3, "audit": {"timesfm_predictions": scores}}
    d.update(extra)
    return d


def test_6_49_pool_of_six_gets_the_next_ranked_number():
    """Din 6 numere iese o singura varianta de 6; biletul cere 3 distincte."""
    t = build_full_ticket("6/49", _data([1, 2, 3, 4, 5, 6]))
    assert t["pool"] == [1, 2, 3, 4, 5, 6, 7]
    assert len({tuple(v) for v in t["variants"]}) == 3
    assert "am adăugat 7" in t["note"]


def _pipeline_data(scores, size, max_num=49, **extra):
    """Pool-ul si audit-ul scrise de productie pentru aceste scoruri."""
    audit: dict = {}
    pool = select_pool_from_scores(scores, size, set(), audit, max_num=max_num)
    d = {"hard_core": pool, "guarantee": 3, "audit": audit}
    d.update(extra)
    return d


def test_extension_follows_the_canonical_tie_break():
    """La scor egal castiga numarul mai mare, ca la pool-ul afisat."""
    scores = {n: 10.0 for n in range(1, 7)} | {20: 1.0, 30: 1.0}
    t = build_full_ticket("6/49", _pipeline_data(scores, 6))
    assert t["pool"] == [1, 2, 3, 4, 5, 6, 30]


def test_extension_ignores_ties_created_by_the_audit_rounding():
    """22 e peste 34 pe scorul exact; rotunjite la 6 zecimale sunt egale."""
    scores = {n: 10.0 for n in range(1, 7)} | {22: 0.6489827, 34: 0.6489826}
    data = _pipeline_data(scores, 6)
    assert data["audit"]["timesfm_predictions"][22] == data["audit"]["timesfm_predictions"][34]
    t = build_full_ticket("6/49", data)
    assert t["pool"] == sorted(select_pool_from_scores(scores, 7, set(), {}, max_num=49))
    assert t["pool"] == [1, 2, 3, 4, 5, 6, 22]
    assert "am adăugat 22" in t["note"]


def test_joker_trim_keeps_the_method_ranking_not_the_smallest_numbers():
    """1 e ultimul in clasament; 40 bate 45 doar pe scorul exact."""
    scores = {n: 30.0 - n for n in range(20, 29)} | {40: 0.5000004, 45: 0.5000003}
    scores |= {1: 0.1}
    data = _pipeline_data(scores, 12, max_num=45, hard_core_joker=[7])
    assert sorted(data["hard_core"]) == [1, *range(20, 29), 40, 45]
    t = build_full_ticket("joker", data)
    assert t["pool"] == sorted(select_pool_from_scores(scores, 10, set(), {}, max_num=45))
    assert t["pool"] == [*range(20, 29), 40]
    assert "În afara biletului: 1, 45" in t["note"]


def test_joker_pool_over_ten_keeps_the_best_ranked_ten():
    pool = list(range(1, 13))
    t = build_full_ticket("joker", _data(pool, hard_core_joker=[7]))
    assert t["pool"] == list(range(1, 11))
    assert {n for v in t["variants"] for n in v[:5]} == set(range(1, 11))
    assert "În afara biletului: 11, 12" in t["note"]


def test_pools_within_ticket_limits_are_untouched():
    for game, pool in (("6/49", list(range(1, 17))), ("5/40", list(range(1, 7))),
                       ("joker", list(range(1, 11)))):
        t = build_full_ticket(game, _data(pool, hard_core_joker=[7]))
        assert t["pool"] == pool and t["note"] is None


def test_without_ranking_the_pool_stays_and_the_reason_is_shown():
    t = build_full_ticket("6/49", {"hard_core": [1, 2, 3, 4, 5, 6], "guarantee": 3})
    assert t["pool"] == [1, 2, 3, 4, 5, 6] and len(t["variants"]) == 1
    assert "clasamentul metodei lipsește" in t["note"]


# ── Numărul de bilete fizice (1-10) ─────────────────────────────────────────

from loto_enterprise.core.full_ticket import MAX_TICKETS, PICK, TICKET_VARIANTS, clamp_tickets  # noqa: E402


def test_ticket_count_is_clamped_to_one_ten():
    assert [clamp_tickets(v) for v in (None, "", "x", 0, -3, 1, "4", 7.0, 10, 11, 99)] == [
        1, 1, 1, 1, 1, 1, 4, 7, 10, 10, 10,
    ]
    assert MAX_TICKETS == 10


def test_every_ticket_is_filled_with_distinct_variants():
    """N bilete = N × 3/4/2 variante distincte, toate din pool-ul biletului."""
    pools = {"6/49": list(range(1, 13)), "5/40": list(range(1, 9)), "joker": list(range(1, 11))}
    for game, pool in pools.items():
        pick, per = PICK[game], TICKET_VARIANTS[game]
        for n in range(1, MAX_TICKETS + 1):
            t = build_full_ticket(game, _data(pool, hard_core_joker=[7]), n)
            main = [tuple(v[:pick]) for v in t["variants"]]
            assert len(main) == n * per == t["requested"], (game, n)
            assert len(set(main)) == len(main)
            assert all(set(v) <= set(t["pool"]) for v in main)
            assert (t["tickets"], t["per_ticket"]) == (n, per)
            assert abs(t["coverage"] - _cov(main, t["pool"], t["guarantee"])) < 0.02


def test_tickets_beyond_the_guarantee_cover_bigger_groups():
    """Pool 9 la 6/49: garanția 3 e completă cu 7 variante; celelalte 23 din
    cele 30 (10 bilete) acoperă grupe de 4 din același pool, nu numere noi."""
    pool = list(range(1, 10))
    t = build_full_ticket("6/49", _data(pool), 10)
    main = [v[:6] for v in t["variants"]]
    assert len(main) == 30 and t["pool"] == pool
    assert t["coverage"] == 100.0
    assert t["guarantee_variants"] < 30
    level, pct = t["upper_coverage"]
    assert level == 4 and abs(pct - _cov(main, pool, 4)) < 0.02
    base_only = _cov(main[: t["guarantee_variants"]], pool, 4)
    assert pct > base_only


def test_more_tickets_extend_a_small_pool_from_the_ranking():
    """Din 6 numere iese o singură variantă de 6; 10 bilete (30 de variante)
    cer 9 numere, luate în ordinea clasamentului metodei."""
    t = build_full_ticket("6/49", _data([1, 2, 3, 4, 5, 6]), 10)
    assert t["pool"] == list(range(1, 10))
    assert len(t["variants"]) == 30
    assert "am adăugat 7, 8, 9" in t["note"]


def test_more_tickets_leave_room_for_the_whole_joker_pool():
    """Un bilet Joker are loc de 10 numere, două bilete de 20."""
    pool = list(range(1, 13))
    one = build_full_ticket("joker", _data(pool, hard_core_joker=[7]), 1)
    two = build_full_ticket("joker", _data(pool, hard_core_joker=[7]), 2)
    assert one["pool"] == list(range(1, 11)) and "În afara biletului" in one["note"]
    assert two["pool"] == pool and two["note"] is None
    assert {n for v in two["variants"] for n in v[:5]} == set(pool)


def test_one_ticket_keeps_the_previous_default():
    pool = [3, 7, 11, 19, 24, 31, 38, 40]
    data = {"hard_core": pool, "guarantee": 4, "hard_core_joker": [12]}
    assert build_full_ticket("6/49", data) == build_full_ticket("6/49", data, 1)


def test_ticket_count_is_a_persisted_setting_with_one_as_default():
    import ui_runtime

    assert ui_runtime.DEFAULTS["full_ticket_count_val"] == 1
    assert "full_ticket_count_val" in ui_runtime.UI_PERSIST_KEYS


def test_summary_names_the_tickets_and_the_extra_groups():
    import ui_results

    t = build_full_ticket("6/49", _data(list(range(1, 10))), 10)
    text = ui_results._full_ticket_summary(t, "6/49")
    assert text.startswith("30/30 de variante (10 × 3)")
    assert "ale biletelor" in text
    assert f"garanția e completă cu {t['guarantee_variants']} variante" in text
    assert "cu toate cele 30, grupele de 4 sunt acoperite" in text
    assert text.endswith("≈ 240 Lei")
    one = build_full_ticket("6/49", _data(list(range(1, 13))), 1)
    assert "grupele" not in ui_results._full_ticket_summary(one, "6/49")


def test_every_ticket_names_its_country_and_game():
    from loto_enterprise.core.lotteries import LOTTERIES, display_name

    assert display_name("6/49") == "România · Loto 6/49"
    assert display_name("5/40") == "România · Loto 5/40"
    assert display_name("joker") == "România · Joker"
    assert set(LOTTERIES) == set(TICKET_VARIANTS)
    assert display_name("necunoscut") == "necunoscut"


def _ranked_data(ranking, pool, limit=0):
    scores = {n: float(100 - i) for i, n in enumerate(ranking)}
    audit = {"timesfm_predictions": scores}
    if limit:
        audit["consecutive_limit"] = {"requested": limit, "applied": limit}
    return {"hard_core": pool, "guarantee": 3, "audit": audit}


def test_narrow_base_extends_as_far_as_the_ranking_goes():
    """Interval 42..49: zece bilete cer 9 numere, clasamentul are 8. Înainte,
    pool-ul rămânea de 6 și ieșea o singură variantă; acum ies toate C(8,6) = 28."""
    ranking = [45, 42, 49, 46, 48, 47, 43, 44]
    t = build_full_ticket("6/49", _ranked_data(ranking, [42, 45, 46, 47, 48, 49]), 10)
    assert t["pool"] == list(range(42, 50))
    assert len(t["variants"]) == 28
    assert "Clasamentul are numai 8 numere" in t["note"]


def test_extension_keeps_the_displayed_pool_and_respects_the_limit():
    """Parcurgerea simplă lua 43 și bloca 42 și 44; completarea verificată găsește
    singurul superset valid cu cel mult 2 consecutive."""
    ranking = [38, 49, 47, 39, 45, 40, 46, 48, 41, 43, 42, 44]
    pool = [38, 39, 41, 45, 47, 49]
    t = build_full_ticket("6/49", _ranked_data(ranking, pool, limit=2), 3)
    assert t["pool"] == [38, 39, 41, 42, 44, 45, 47, 49]
    assert len(t["variants"]) == 9
    from loto_enterprise.core.ranking import longest_consecutive_run

    assert longest_consecutive_run(t["pool"]) <= 2


def test_extension_relaxes_the_limit_rather_than_losing_the_pool():
    ranking = [37, 38, 40, 42, 44, 45, 36, 39, 41, 43, 46, 47, 48, 49]
    pool = [37, 38, 40, 42, 44, 45]
    t = build_full_ticket("6/49", _ranked_data(ranking, pool, limit=2), 10)
    assert set(pool) <= set(t["pool"]) and len(t["pool"]) == 9
    assert len(t["variants"]) == 30
    assert "Limita de consecutive a crescut" in t["note"]


def test_fractional_ticket_count_is_rounded_like_the_field():
    assert [clamp_tickets(v) for v in (2.6, 3.4, 9.9, "2.5", float("inf"))] == [3, 3, 10, 2, 1]


def test_trim_note_speaks_of_all_the_slips():
    pool = list(range(1, 25))
    two = build_full_ticket("joker", _data(pool, hard_core_joker=[7]), 2)
    assert "În afara biletelor" in two["note"]


def test_result_headings_name_country_and_game():
    import ui_runtime

    assert ui_runtime._game_title("6/49") == "România · Loto 6/49"
    assert ui_runtime._game_title("joker") == "România · Joker"
    assert ui_runtime._game_title("5/40") == "România · Loto 5/40"


def test_romanian_numeral_agreement():
    from loto_enterprise.core.ro_text import count

    assert [count(n, "variante") for n in (1, 19, 20, 23, 100, 101, 119, 120)] == [
        "1 variante", "19 variante", "20 de variante", "23 de variante",
        "100 de variante", "101 variante", "119 variante", "120 de variante",
    ]
    assert count(1, "variante", "variantă") == "1 variantă"
