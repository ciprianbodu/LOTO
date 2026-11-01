"""Sumele UI folosesc variantele generate, nu toate combinatiile poolului."""
from types import SimpleNamespace
import ui_hits
import ui_prizes
from loto_enterprise.core.official_prizes import PrizeDraw, PrizeCategory
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult


def draw():
    return PrizeDraw("loto_6_49", "2026-10-22", (1,2,3,4,5,6), None, (
        PrizeCategory("I", "6/6", 0, None, 1000, 1000),
        PrizeCategory("II", "5/6", 10, 20000, None, 20000),
        PrizeCategory("III", "4/6", 10, 1000, None, 1000),
        PrizeCategory("IV", "3/6", 10, 5000, None, 5000),
    ), "https://www.loto.ro/test")


def entry(variant, hits, **kwargs):
    return WalkForwardResult(draw_index=1, draw_date="2026-10-22", variant=variant, hits=hits, hits_union=5,
        ticket_context={"hard_core": list(range(1,11)), "actual": [1,2,3,4,5,6]}, **kwargs)


def test_per_draw_sums_all_generated_wheel_variants():
    flat=[entry([1,2,3,4,5,7],5),entry([1,2,3,4,7,8],4),entry([1,2,3,7,8,9],3)]
    row=ui_hits._wf_per_draw_stats(flat,"6/49")[1]
    assert len(row["generated_variants"])==3
    shown=ui_prizes.pool_prize_text("6/49",row,ui_prizes.PrizeView("ready",draw()))
    assert shown.text=="260,00 lei"
    assert "cat. II: 1 × 200,00 lei" in shown.detail
    assert "cat. III: 1 × 10,00 lei" in shown.detail
    assert "cat. IV: 1 × 50,00 lei" in shown.detail


def test_compact_old_and_virtual_do_not_generate_an_amount_or_request():
    old=ui_hits._wf_per_draw_stats([entry([1,2,3,4,5,7],5,variant_count=3750)],"6/49")[1]
    virtual=ui_hits._wf_per_draw_stats([entry([1,2,3,4,5,7],5,variant_count=593775,virtual_full_system=True)],"6/49")[1]
    assert "recalculează" in ui_prizes.pool_prize_text("6/49",old,ui_prizes.PrizeView()).text
    assert "virtual" in ui_prizes.pool_prize_text("6/49",virtual,ui_prizes.PrizeView()).text
    requested=[]
    lookup=SimpleNamespace(request=lambda rows,**kw: requested.extend(rows))
    binding=ui_prizes.PoolPrizeBinding("6/49",None,[{"_draw_index":1},{"_draw_index":2}],{1:old,2:virtual},lookup=lookup)
    binding.request()
    assert requested==[]


def test_compact_groups_use_all_counted_wheel_variants():
    p=entry([1,2,3,4,5,7],5,variant_count=3)
    p.variant_prize_groups={"version":1,"total_variants":3,"groups":[
        {"variant":[1,2,3,4,5,7],"count":1},
        {"variant":[1,2,3,4,7,8],"count":1},
        {"variant":[1,2,3,7,8,9],"count":1}]}
    row=ui_hits._wf_per_draw_stats([p],"6/49")[1]
    shown=ui_prizes.pool_prize_text("6/49",row,ui_prizes.PrizeView("ready",draw()))
    assert shown.text=="260,00 lei"


def test_observed_22_october_3750_wheel_variants_are_9519_22():
    pool=[2,3,4,5,6,7,8,9,10,12,13,16,17,18,19,20,21,23,24,25,26,27,30,32,33,36,39,40,44,46]
    actual=[4,5,7,8,17,29]
    official=PrizeDraw("loto_6_49","2026-10-22",tuple(actual),None,(
        PrizeCategory("I","6/6",1,1776075440,None,1776075440),
        PrizeCategory("II","5/6",16,1451681,None,1451681),
        PrizeCategory("III","4/6",974,23846,None,23846),
        PrizeCategory("IV","3/6",15569,5000,None,5000),
    ),"https://www.loto.ro/test")
    first=[4,5,7,8,2,3]
    row={"label":"2026-10-22","drawn":actual,"pool_numbers":pool,
         "generated_variants":[first],"expected_variant_count":3750,
         "variant_prize_groups":{"version":1,"total_variants":3750,"groups":[
             {"variant":first,"count":7},
             {"variant":[4,5,7,2,3,6],"count":157},
             {"variant":[2,3,6,9,10,12],"count":3586}]}}
    shown=ui_prizes.pool_prize_text("6/49",row,ui_prizes.PrizeView("ready",official))
    assert shown.text=="9.519,22 lei"
    assert "cat. III: 7 × 238,46 lei" in shown.detail
    assert "cat. IV: 157 × 50,00 lei" in shown.detail
