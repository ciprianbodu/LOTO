from types import SimpleNamespace as NS
import pytest
from loto_enterprise.core.generated_prize import estimate_generated_prize as estimate
from loto_enterprise.core.official_prizes import PrizeDraw, PrizeCategory

URL = 'https://www.loto.ro/loto-new/archive'
def cat(name, label, amount=10000, winners=1):
    return PrizeCategory(name,label,winners,amount if winners else None,None,amount)
def draw(game='loto_6_49',nums=(4,5,7,8,17,29),joker=None,order=True):
    if game=='loto_6_49':
        cats=(cat('I','6/6',100000),cat('II','5/6',1451681),cat('III','4/6',23846),cat('IV','3/6',5000))
    elif game=='loto_5_40':
        cats=(cat('I','5/6*',100000),cat('II','5/6',20000),cat('III','4/6',3000))
    else:
        cats=(cat('I','5/5+J',100000),cat('II','5/5',90000),cat('III','4/5+J',80000),cat('IV','4/5',70000),cat('V','3/5+J',60000),cat('VI','3/5',50000),cat('VII','2/5+J',40000),cat('VIII','1/5+J',30000))
    return PrizeDraw(game,'2026-10-22',nums,joker,cats,URL,order_known=order)

def calc(d, variants, **kw):
    return estimate(d.game,variants,d,target_numbers=d.numbers,target_date=d.draw_date,**kw)

def test_649_evaluates_actual_variants_not_full_pool():
    d=draw(); variants=[[4,5,7,8,17,2],[4,5,7,8,2,3],[4,5,7,2,3,6],[1,2,3,6,9,10]]
    result=calc(d,variants,expected_count=4)
    assert result.status=='ready' and result.category_counts=={'II':1,'III':1,'IV':1}
    assert result.total_bani==1451681+23846+5000

def test_compact_groups_have_same_total_as_the_full_variants():
    d=draw(); variants=[[4,5,7,8,17,2],[4,5,7,8,17,3],[4,5,7,8,2,3],[4,5,7,8,3,6],[4,5,7,2,3,6]]
    groups={'version':1,'total_variants':5,'groups':[{'variant':variants[0],'count':2},{'variant':variants[2],'count':2},{'variant':variants[4],'count':1}]}
    a=calc(d,variants,expected_count=5); b=calc(d,[variants[0]],expected_count=5,variant_prize_groups=groups)
    assert a==b and b.category_counts=={'II':2,'III':2,'IV':1}

def test_report_category_makes_total_unavailable():
    d=draw(); categories=list(d.categories);categories[1]=cat('II','5/6',1451681,0)
    d=PrizeDraw(d.game,d.draw_date,d.numbers,d.joker,tuple(categories),d.source_url)
    r=calc(d,[[4,5,7,8,17,2],[4,5,7,8,2,3]])
    assert r.status=='missing_prizes' and r.total_bani is None and r.missing_categories==('II',)

def test_540_respects_first_five_official_numbers():
    d=draw('loto_5_40',(1,2,3,4,5,6))
    v=[[1,2,3,4,5],[1,2,3,4,6],[1,2,3,4,7]]
    r=calc(d,v); assert r.status=='ready' and r.category_counts=={'I':1,'II':1,'III':1} and r.total_bani==123000
    unknown=draw('loto_5_40',(1,2,3,4,5,6),order=False)
    assert calc(unknown,v).status=='ambiguous_category'
    # No 5-hit variant: order is not needed.
    assert calc(unknown,[[1,2,3,4,7]]).category_counts=={'III':1}

def test_joker_uses_each_variant_joker_ball():
    d=draw('joker',(1,2,3,4,5),joker=12)
    v=[[1,2,3,4,9,12],[1,2,3,4,9,13],[1,9,10,11,14,12],[1,9,10,11,14,13]]
    r=calc(d,v)
    assert r.status=='ready' and r.category_counts=={'III':1,'IV':1,'VIII':1}
    assert r.total_bani==80000+70000+30000

def test_joker_five_main_numbers_require_known_ball_or_flag():
    d=draw('joker',(1,2,3,4,5),joker=12)
    assert calc(d,[[1,2,3,4,9]]).status=='missing_joker'
    assert calc(d,[[1,2,3,4,9]],joker_hit=True).status=='missing_joker'
    assert calc(d,[[1,2,3,4,9]],joker_hit=False).status=='missing_joker'
    unconfirmed=draw('joker',(1,2,3,4,5),joker=None)
    assert calc(unconfirmed,[[1,2,3,4,9,12]],joker_hit=True).status=='missing_joker'
    assert calc(d,[[1,2,3,4,9,13]],joker_hit=True).status=='invalid_variant'

def test_virtual_incomplete_and_missing_are_never_estimated():
    d=draw(); v=[[4,5,7,8,17,2]]
    assert calc(d,v,virtual_full_system=True).status=='variants_unavailable'
    assert calc(d,v,expected_count=100).status=='variants_unavailable'
    assert calc(d,[]).status=='variants_unavailable'
    assert calc(d,None).status=='variants_unavailable'

def test_group_sum_and_signature_are_validated():
    d=draw();v=[[4,5,7,8,17,2],[4,5,7,8,17,3]]
    bad={'version':1,'total_variants':3,'groups':[{'variant':v[0],'count':1}]}
    assert calc(d,v,expected_count=3,variant_prize_groups=bad).status=='invalid_context'
    bad={'version':1,'total_variants':2,'groups':[{'variant':x,'count':1} for x in v]}
    assert calc(d,v,expected_count=2,variant_prize_groups=bad).status=='invalid_context'

def test_wrong_draw_game_date_pool_or_duplicate_numbers_fail_closed():
    d=draw()
    assert estimate('6/49',[[4,5,7,8,17,2]],d,target_numbers=d.numbers,target_date='2026-10-21').status=='draw_mismatch'
    assert estimate('5/40',[[4,5,7,8,17]],d,target_numbers=d.numbers,target_date=d.draw_date).status=='draw_mismatch'
    assert calc(d,[[4,5,7,8,17,2]],pool_numbers=[4,5,7,8,17,3]).status=='invalid_context'
    assert calc(d,[[4,5,7,8,17,17]]).status=='invalid_context'

def test_old_draw_has_no_category_4_before_2010():
    d=draw();d=PrizeDraw(d.game,'2009-01-01',d.numbers,d.joker,d.categories,d.source_url)
    assert calc(d,[[4,5,7,2,3,6]]).total_bani==0

def test_historical_currency_remains_unconverted():
    d=draw();d=PrizeDraw(d.game,'2004-01-01',d.numbers,d.joker,d.categories,d.source_url)
    r=calc(d,[[4,5,7,8,2,3]])
    assert r.status=='historical_currency' and r.total_bani==23846

def test_no_winning_variants_returns_zero():
    r=calc(draw(),[[1,2,3,6,9,10]])
    assert r.status=='ready' and r.total_bani==0 and not r.category_counts


@pytest.mark.parametrize('game,maximum', [('loto_6_49', 6), ('loto_5_40', 5), ('joker', 5)])
def test_enumerated_subset_matches_grouped_signature_counts(game,maximum):
    from itertools import combinations
    from collections import Counter
    from loto_enterprise.core.prize_categories import resolve_variant_category
    pool=list(range(1,10))
    nums=(1,2,3,4,5) if game=='joker' else (1,2,3,4,5,6)
    d=draw(game,nums,joker=12 if game=='joker' else None)
    variants=[list(v)+([12] if game=='joker' else []) for i,v in enumerate(combinations(pool,maximum)) if i%3!=1]
    groups={}
    for v in variants:
        key=(tuple(sorted(set(v[:maximum])&set(nums))),v[-1] if game=='joker' else None)
        if key not in groups: groups[key]={'variant':v,'count':0}
        groups[key]['count']+=1
    summary={'version':1,'total_variants':len(variants),'groups':list(groups.values())}
    results=[resolve_variant_category(game,v,nums,official_joker=d.joker,draw_date=d.draw_date) for v in variants]
    expected=Counter(r.category for r in results if r.category)
    full=calc(d,variants,expected_count=len(variants),pool_numbers=pool)
    compact=calc(d,[variants[0]],expected_count=len(variants),pool_numbers=pool,variant_prize_groups=summary)
    assert full.status=='ready' and full.category_counts==dict(expected) and compact==full


def test_22_oct_2026_synthetic_groups_match_reconstructed_counts():
    d=draw()
    pool=[2,3,4,5,6,7,8,9,10,12,13,16,17,18,19,20,21,23,24,25,26,27,30,32,33,36,39,40,44,46]
    summary={'version':1,'total_variants':3750,'groups':[
        {'variant':[4,5,7,8,2,3],'count':7},
        {'variant':[4,5,7,2,3,6],'count':157},
        {'variant':[2,3,6,9,10,12],'count':3586},
    ]}
    r=calc(d,None,expected_count=3750,variant_prize_groups=summary,pool_numbers=pool)
    assert r.status=='ready' and r.category_counts=={'III':7,'IV':157}
    assert r.total_bani==951922
