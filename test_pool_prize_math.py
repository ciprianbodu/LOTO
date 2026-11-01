from collections import Counter
from datetime import date
from itertools import combinations
import pytest

from loto_enterprise.core.pool_prize import estimate_pool_prize
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw
from loto_enterprise.core.prize_categories import resolve_variant_category

LABELS = {
    '6/49': [('I','6/6'),('II','5/6'),('III','4/6'),('IV','3/6')],
    '5/40': [('I','5/6*'),('II','5/6'),('III','4/6')],
    'joker': [('I','5/5+J'),('II','5/5'),('III','4/5+J'),('IV','4/5'),('V','3/5+J'),('VI','3/5'),('VII','2/5+J'),('VIII','1/5+J')],
}
CANON={'6/49':'loto_6_49','5/40':'loto_5_40','joker':'joker'}

def draw_for(game,numbers, *, day='2026-10-25', joker=7, order=True, overrides=None):
    cats=[]
    overrides=overrides or {}
    for i,(cat,label) in enumerate(LABELS[game]):
        kw={'category':cat,'matches_label':label,'winners':4,'amount_bani':100+i,'report_bani':None,'raw_value_bani':100+i}
        kw.update(overrides.get(cat, {}))
        cats.append(PrizeCategory(**kw))
    return PrizeDraw(CANON[game],day,tuple(numbers), joker if game=='joker' else None, tuple(cats), 'https://www.loto.ro/', order_known=order)


def enumerate_expected(game,pool,draw,joker):
    expected=Counter()
    k=6 if game=='6/49' else 5
    for nums in combinations(pool,k):
        variant=list(nums)+([joker] if game=='joker' else [])
        match=resolve_variant_category(game,variant,draw.numbers,official_joker=draw.joker,draw_date=draw.draw_date,draw_order_known=draw.order_known)
        if match.category:
            expected[match.category]+=1
    return dict(expected)


@pytest.mark.parametrize('game',['6/49','5/40','joker'])
def test_exhaustive_small_pool_matches_actual_variant_classification(game):
    k=6 if game=='6/49' else 5
    draw_n=5 if game=='joker' else 6
    for v in range(k,k+5):
        pool=list(range(1,v+1))
        for m in range(min(draw_n, v)+1):
            numbers=pool[:m]+list(range(20,20+draw_n-m))
            for offset in (0,1) if game=='5/40' else (0,):
                ordered=numbers[offset:]+numbers[:offset]
                record=draw_for(game,ordered)
                for joker in (7,8) if game=='joker' else (None,):
                    result=estimate_pool_prize(game,pool,record,target_numbers=ordered,target_date=record.draw_date,selected_joker=joker)
                    expected=enumerate_expected(game,pool,record,joker)
                    assert result.status=='ready', (game,v,m,offset,joker,result)
                    assert result.category_counts==expected
                    assert result.total_bani==sum(expected.get(c.category,0)*c.amount_bani for c in record.categories)


def test_pool30_4_hits_counts_every_prize_category():
    pool=range(1,31)
    numbers=[1,2,3,4,35,36]
    draw=draw_for('6/49',numbers)
    result=estimate_pool_prize('6/49',pool,draw,target_numbers=numbers,target_date=draw.draw_date)
    assert result.category_counts=={'III':325,'IV':10400}
    assert result.total_bani==325*102+10400*103


def test_report_means_total_unavailable_not_zero_or_jackpot():
    numbers=[1,2,3,4,5,6]
    draw=draw_for('6/49',numbers,overrides={'I':{'winners':0,'amount_bani':None,'raw_value_bani':999999}})
    result=estimate_pool_prize('6/49',range(1,7),draw,target_numbers=numbers,target_date=draw.draw_date)
    assert result.status=='missing_prizes' and result.total_bani is None and result.missing_categories==('I',)


def test_report_in_nonwinning_category_does_not_block_total():
    numbers=[1,2,3,4,35,36]
    draw=draw_for('6/49',numbers,overrides={'I':{'winners':0,'amount_bani':None}})
    result=estimate_pool_prize('6/49',range(1,7),draw,target_numbers=numbers,target_date=draw.draw_date)
    assert result.status=='ready' and result.category_counts=={'III':1}


def test_540_unknown_order_only_blocks_if_five_hits_could_be_i_or_ii():
    numbers=[1,2,3,4,5,6]
    record=draw_for('5/40',numbers,order=False)
    amb=estimate_pool_prize('5/40',[1,2,3,4,5,7],record,target_numbers=numbers,target_date=record.draw_date)
    allsix=estimate_pool_prize('5/40',range(1,7),record,target_numbers=numbers,target_date=record.draw_date)
    assert amb.status=='ambiguous_order' and amb.total_bani is None
    assert allsix.status=='ready' and allsix.category_counts=={'I':1,'II':5}


def test_649_fourth_category_introduced_may_2010():
    numbers=[1,2,3,40,41,42]
    old=draw_for('6/49',numbers,day='2010-05-01')
    new=draw_for('6/49',numbers,day='2010-05-02')
    assert estimate_pool_prize('6/49',range(1,7),old,target_numbers=numbers,target_date=old.draw_date).total_bani==0
    assert estimate_pool_prize('6/49',range(1,7),new,target_numbers=numbers,target_date=new.draw_date).category_counts=={'IV':1}


def test_joker_unknown_or_conflicting_is_not_assumed():
    numbers=[1,2,3,4,40]
    draw=draw_for('joker',numbers)
    args=dict(target_numbers=numbers,target_date=draw.draw_date)
    assert estimate_pool_prize('joker',range(1,7),draw,**args).status=='missing_joker'
    assert estimate_pool_prize('joker',range(1,7),draw,selected_joker=7,joker_hit=False,**args).status=='invalid_context'
    assert estimate_pool_prize('joker',range(1,7),draw,joker_hit=False,**args).category_counts=={'IV':2,'VI':4}


def test_mismatched_draw_category_and_legacy_currency():
    numbers=[1,2,3,4,5,6]
    d=draw_for('6/49',numbers)
    assert estimate_pool_prize('6/49',range(1,7),d,target_numbers=[1,2,3,4,5,7],target_date=d.draw_date).status=='draw_mismatch'
    assert estimate_pool_prize('6/49',range(1,7),d,target_numbers=numbers,target_date='2026-10-26').status=='draw_mismatch'
    bad=draw_for('6/49',numbers,overrides={'I':{'matches_label':'4/6'}})
    assert estimate_pool_prize('6/49',range(1,7),bad,target_numbers=numbers,target_date=bad.draw_date).status=='unsupported_rules'
    old=draw_for('6/49',numbers,day='2000-01-01')
    assert estimate_pool_prize('6/49',range(1,7),old,target_numbers=numbers,target_date=old.draw_date).status=='historical_currency'


def test_official_october_25_2026_pool30_golden_and_report():
    pool=[2,3,4,5,6,7,8,9,10,12,13,16,17,18,19,20,21,23,24,25,26,27,30,32,33,36,39,40,44,46]
    numbers=[10,27,28,29,33,39]
    draw=draw_for('6/49',numbers,overrides={'III':{'winners':466,'amount_bani':59120}, 'IV':{'winners':10468,'amount_bani':5000}})
    kw=dict(target_numbers=numbers,target_date='25-10-2026')
    result=estimate_pool_prize('6/49',pool,draw,**kw)
    assert result.status=='ready' and result.category_counts=={'III':325,'IV':10400}
    assert result.total_bani==71214000
    assert estimate_pool_prize('6/49',pool,draw,target_numbers=[10,27,28,29,33,40],target_date='25-10-2026').status=='draw_mismatch'
    reported=draw_for('6/49',numbers,overrides={'III':{'winners':0,'amount_bani':None}, 'IV':{'winners':10468,'amount_bani':5000}})
    unknown=estimate_pool_prize('6/49',pool,reported,**kw)
    assert unknown.status=='missing_prizes' and unknown.total_bani is None and unknown.missing_categories==('III',)


def test_unconfirmed_old_currency_is_returned_only_as_unconverted_nominal_value():
    numbers=[1,2,3,4,5,6]
    draw=draw_for('6/49',numbers,day='2000-01-01')
    result=estimate_pool_prize('6/49',range(1,7),draw,target_numbers=numbers,target_date=draw.draw_date)
    assert result.status=='historical_currency' and result.total_bani==100
    assert 'fără conversie' in result.reason
