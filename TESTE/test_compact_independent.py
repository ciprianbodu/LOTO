from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from loto_enterprise.core.backtesting import LotoBacktester, _retroactive_step_stateless, scored_variant_numbers
from loto_enterprise.core.walk_forward_adapter import expand_predictions_to_flat, per_draw_hit_summary, wheel_coverage_summary, _merge_partial_coverage
from ui_hits import _wf_per_draw_stats

def history(game):
    rng=np.random.default_rng(1323)
    pick,upper=(6,49) if game=='6/49' else (5,45)
    rows=[rng.choice(np.arange(1,upper+1), pick, replace=False) for _ in range(36)]
    df=pd.DataFrame(rows,columns=[f'n{i}' for i in range(1,pick+1)])
    df['date']=[(pd.Timestamp('2020-01-01') + pd.Timedelta(days=i//2)).strftime('%d-%m-%Y') for i in range(len(df))]
    if game=='joker': df['joker']=[i%20+1 for i in range(len(df))]
    return df

def step(bt,game,index,compact):
    return _retroactive_step_stateless(bt.df,bt.draws,bt.dates,game,index,30,4,0,0,compact_variants=compact)

@pytest.mark.parametrize('game', ['6/49','joker'])
def test_compact_exactly_matches_every_ticket_reference(game):
    bt=LotoBacktester(history(game),game_type=game)
    i=len(bt.draws)-1
    full=step(bt,game,i,False)
    compact=step(bt,game,i,True)
    actual=set(full.actual_numbers)
    hits=[len(set(scored_variant_numbers(v,game)) & actual) for v in full.variants]
    assert len(full.variants)>1
    best=full.variants[hits.index(max(hits))]
    assert compact.variants==[best]
    assert compact.variant_count==len(full.variants)
    assert compact.hits==max(hits)==full.hits
    assert compact.hits_union==full.hits_union
    assert compact.predicted_numbers==full.predicted_numbers
    assert compact.hard_core==full.hard_core
    assert compact.actual_numbers==full.actual_numbers
    assert compact.wheel_coverage==full.wheel_coverage
    assert compact.joker_hit==full.joker_hit
    assert compact.ticket_context==full.ticket_context
    legacy=expand_predictions_to_flat([full],game)
    small=expand_predictions_to_flat([compact],game)
    assert len(small)==1
    assert small[0].variant_count==len(full.variants)
    assert per_draw_hit_summary(small)==per_draw_hit_summary(legacy)
    assert wheel_coverage_summary(small)==wheel_coverage_summary(legacy)
    from TESTE.test_compact_large_wf import _assert_compact_stats_and_prizes
    _assert_compact_stats_and_prizes(legacy, small, game)

@pytest.mark.parametrize('game', ['6/49','joker'])
def test_partial_cache_merge_keeps_counts_and_draws(game):
    bt=LotoBacktester(history(game),game_type=game)
    newest=step(bt,game,len(bt.draws)-1,True)
    older=step(bt,game,len(bt.draws)-3,True)
    newer_flat=expand_predictions_to_flat([newest],game)
    older_flat=expand_predictions_to_flat([older],game)
    meta={'n_predictions':1,'n_test_draws':1,'n_expected':3}
    merged,meta=_merge_partial_coverage({'flat':older_flat},newer_flat,meta)
    assert {r.draw_index for r in merged}=={older.draw_index,newest.draw_index}
    assert {r.draw_index:r.variant_count for r in merged}=={older.draw_index:older.variant_count,newest.draw_index:newest.variant_count}
    assert meta['partial'] is True and meta['n_test_draws']==2
    assert sum(r.variant_count for r in merged)==older.variant_count+newest.variant_count

@pytest.mark.parametrize('game',['6/49','joker'])
def test_real_unlimited_wf_partial_cache_resumes_without_losing_counts(game, monkeypatch, tmp_path):
    import loto_enterprise.core.walk_forward_adapter as wf
    import loto_enterprise.core.backtesting as backtesting
    from loto_enterprise.core.history import simulation_indices
    monkeypatch.setattr(wf,'CACHE_DIR',tmp_path)
    monkeypatch.setattr(backtesting,'_WF_PROBE_STEPS',1)
    monkeypatch.setattr(backtesting,'_WF_SERIAL_MAX_MS',1e9)
    frame=history(game)
    kw=dict(df_source=frame,game_type=game,pool_size=30,backtest_depth_percent=10,lookback_percent=0,guarantee=4,max_variants=0,use_cache=True)
    first,m1=wf.run_honest_walk_forward(**kw,should_cancel=lambda:True)
    assert len(first)==1 and m1['partial']
    assert first[0].variant_count and first[0].variant_count>1
    later,m2=wf.run_honest_walk_forward(**kw)
    assert not m2['partial'] and len(later)==m2['n_expected']
    assert {r.draw_index for r in first}<={r.draw_index for r in later}
    again,m3=wf.run_honest_walk_forward(**kw,cache_only=True)
    assert m3['from_cache'] and [(r.draw_index,r.variant_count,r.variant) for r in again]==[(r.draw_index,r.variant_count,r.variant) for r in later]
    bt=LotoBacktester(frame,game_type=game)
    assert {r.draw_index for r in later}==set(simulation_indices(bt.df.attrs['training_cutoffs'],10))
    for record in later:
        full=step(bt,game,record.draw_index,False)
        actual=set(full.actual_numbers)
        hit=[len(set(scored_variant_numbers(v,game)) & actual) for v in full.variants]
        assert record.variant_count==len(full.variants)
        assert record.variant==full.variants[hit.index(max(hit))]
