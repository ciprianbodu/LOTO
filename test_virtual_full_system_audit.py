import math
import numpy as np
import pandas as pd
import pytest
from loto_engine import LotoEngine
from loto_enterprise.core.backtesting import LotoBacktester, _retroactive_step_stateless, scored_variant_numbers
from loto_enterprise.core import walk_forward_adapter as wf

def frame(game):
    n=5 if game=='joker' else 6
    maximum={'6/49':49,'5/40':40,'joker':45}[game]
    rng=np.random.default_rng(725)
    rows=[rng.choice(np.arange(1,maximum+1),n,replace=False) for _ in range(50)]
    df=pd.DataFrame(rows,columns=[f'n{i}' for i in range(1,n+1)])
    df['date']=[(pd.Timestamp('2020-01-01')+pd.Timedelta(days=int(i//2))).strftime('%d-%m-%Y') for i in range(len(df))]
    if game=='joker': df['joker']=[1+i%20 for i in range(len(df))]
    return df

def step(bt,game,i,pool,virtual):
    pick=6 if game=='6/49' else 5
    return _retroactive_step_stateless(bt.df,bt.draws,bt.dates,game,i,pool,pick,0,0,virtual_full_system=virtual)

@pytest.mark.parametrize('game',['6/49','5/40','joker'])
def test_virtual_matches_complete_enumeration(game):
    bt=LotoBacktester(frame(game),game_type=game)
    a=step(bt,game,len(bt.draws)-1,10,False)
    b=step(bt,game,len(bt.draws)-1,10,True)
    assert b.hard_core==a.hard_core
    assert b.hits==a.hits and b.hits_union==a.hits_union
    assert b.variant_count==len(a.variants)==math.comb(10,6 if game=='6/49' else 5)
    assert b.variants[0] in a.variants
    assert len(set(scored_variant_numbers(b.variants[0],game))&set(a.actual_numbers))==a.hits
    assert b.joker_hit==a.joker_hit and b.wheel_coverage==a.wheel_coverage==100
    assert b.virtual_full_system is True

@pytest.mark.parametrize('game',['6/49','5/40','joker'])
def test_virtual_30_produces_only_one_representative_and_no_wheel(game,monkeypatch):
    bt=LotoBacktester(frame(game),game_type=game)
    monkeypatch.setattr(LotoEngine,'generate_predictions',lambda *a,**k: (_ for _ in ()).throw(AssertionError('materialized')))
    b=step(bt,game,len(bt.draws)-1,30,True)
    pick=6 if game=='6/49' else 5
    assert b.variant_count==math.comb(30,pick) and len(b.variants)==1
    assert b.hits==min(b.hits_union,pick)

@pytest.mark.parametrize('game',['6/49','5/40','joker'])
def test_same_day_rows_never_train_virtual_pool(game):
    df=frame(game); bt=LotoBacktester(df,game_type=game); i=49
    a=step(bt,game,i,30,True)
    other=df.copy(); columns=[c for c in other.columns if c.startswith('n')]; other.loc[48,columns]=list(range(1,len(columns)+1))
    if game=='joker': other.loc[48,'joker']=20
    b=step(LotoBacktester(other,game_type=game),game,i,30,True)
    assert a.hard_core==b.hard_core
    assert a.ticket_context['hard_core_joker']==b.ticket_context['hard_core_joker']

@pytest.mark.parametrize('game',['6/49','5/40','joker'])
def test_virtual_end_to_end_cache_and_modes(game,monkeypatch,tmp_path):
    import loto_enterprise.core.backtesting as backtesting
    monkeypatch.setattr(wf,'CACHE_DIR',tmp_path)
    monkeypatch.setattr(backtesting,'_WF_PROBE_STEPS',1)
    monkeypatch.setattr(backtesting,'_WF_SERIAL_MAX_MS',1e9)
    pick=6 if game=='6/49' else 5
    kwargs=dict(df_source=frame(game),game_type=game,pool_size=30,backtest_depth_percent=10,lookback_percent=0,guarantee=pick,max_variants=0,virtual_full_system=True)
    first,m=wf.run_honest_walk_forward(**kwargs,should_cancel=lambda:True)
    assert len(first)==1 and m['partial'] and m['virtual_full_system']
    allrows,m2=wf.run_honest_walk_forward(**kwargs)
    assert not m2['partial'] and len(allrows)==m2['n_expected']
    assert all(r.virtual_full_system and r.variant_count==math.comb(30,pick) for r in allrows)
    again,m3=wf.run_honest_walk_forward(**kwargs,cache_only=True)
    assert m3['from_cache'] and m3['virtual_full_system'] and len(again)==len(allrows)
    with pytest.raises(ValueError): wf.run_honest_walk_forward(**{**kwargs,'max_variants':1})
