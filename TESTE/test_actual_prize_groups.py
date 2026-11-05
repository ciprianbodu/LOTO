from collections import Counter
from pathlib import Path
import pytest
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as bt
from loto_enterprise.core import walk_forward_adapter as wf
from TESTE.test_large_pool_views_wf import history


@pytest.mark.parametrize("game", ["6/49", "5/40", "joker"])
def test_compact_prize_groups_preserve_all_actual_variants(monkeypatch, game):
    backtester = bt.LotoBacktester(history(game, n=12), game_type=game)
    if game == "6/49":
        tickets = [[1,2,3,4,5,7], [1,2,3,4,5,8], [1,2,3,4,6,9], [1,2,3,10,11,12]]
    else:
        tickets = [[1,2,3,4,5], [1,2,3,4,7], [1,2,3,4,8], [1,2,3,6,9]]
    if game == "joker":
        tickets = [v + [7] for v in tickets]
    def fake_pipeline(engine, **kwargs):
        engine.hard_core = list(range(1,31))
        return tickets, None, None, None, {"coverage_pct": 99.0}, engine.audit
    monkeypatch.setattr(LotoEngine, "run_institutional_pipeline", fake_pipeline)
    args = (backtester.df,backtester.draws,backtester.dates,game,10,30,4,0,100)
    p=bt._retroactive_step_stateless(*args,compact_variants=True)
    groups=p.variant_prize_groups
    assert groups["version"]==1 and groups["total_variants"]==len(tickets)
    assert sum(x["count"] for x in groups["groups"])==len(tickets)
    actual=set(p.actual_numbers)
    def signature(v):
        return (tuple(sorted(set(bt.scored_variant_numbers(v,game)) & actual)),tuple(v[5:]) if game=="joker" else ())
    original=Counter(map(signature,tickets))
    restored=Counter({signature(g["variant"]):g["count"] for g in groups["groups"]})
    assert original==restored
    assert all(g["variant"] in tickets for g in groups["groups"])
    expanded=wf.expand_predictions_to_flat([p],game)
    assert len(expanded)==1 and expanded[0].variant_prize_groups==groups
    # Premiile calculate din toate biletele și din grupurile compacte coincid.
    from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw
    from loto_enterprise.core.generated_prize import estimate_generated_prize
    labels = {"6/49": {"I":"6/6", "II":"5/6", "III":"4/6", "IV":"3/6"},
              "5/40": {"I":"5/6*", "II":"5/6", "III":"4/6"},
              "joker": {"I":"5/5+J", "II":"5/5", "III":"4/5+J", "IV":"4/5", "V":"3/5+J", "VI":"3/5", "VII":"2/5+J", "VIII":"1/5+J"}}[game]
    categories=tuple(PrizeCategory(k, label, 1, 12345 + i*100, None, 12345+i*100) for i,(k,label) in enumerate(labels.items()))
    joker=int(backtester.df.iloc[10]["joker"]) if game=="joker" else None
    draw=PrizeDraw({"6/49":"loto_6_49","5/40":"loto_5_40","joker":"joker"}[game], str(p.target_draw_date), tuple(p.actual_numbers), joker, categories, "https://www.loto.ro/test")
    kw=dict(target_numbers=p.actual_numbers,target_date=p.target_draw_date,expected_count=len(tickets),pool_numbers=list(range(1,31)))
    plain=estimate_generated_prize(game,tickets,draw,**kw)
    compressed=estimate_generated_prize(game,None,draw,variant_prize_groups=groups,**kw)
    assert plain.status==compressed.status=="ready"
    assert plain.category_counts==compressed.category_counts and plain.total_bani==compressed.total_bani


def test_old_cached_records_backfill_as_unavailable():
    row=wf.WalkForwardResult(1,"2026-01-01",[1,2,3,4,5,6],4,4,variant_count=30)
    del row.__dict__["variant_prize_groups"]
    wf._backfill_new_fields([row])
    assert row.variant_prize_groups is None


def test_virtual_summary_never_claims_generated_groups(monkeypatch):
    backtester=bt.LotoBacktester(history("6/49",n=12),game_type="6/49")
    def virtual(engine, **kwargs):
        engine.hard_core=list(range(1,31))
        return [],None,None,None,{},engine.audit
    monkeypatch.setattr(LotoEngine,"run_institutional_pipeline",virtual)
    p=bt._retroactive_step_stateless(backtester.df,backtester.draws,backtester.dates,"6/49",10,30,6,0,100,compact_variants=True,virtual_full_system=True)
    assert p.virtual_full_system and p.variant_prize_groups is None
    assert wf.expand_predictions_to_flat([p],"6/49")[0].variant_prize_groups is None


def test_compact_cache_with_prize_groups_never_loads_old_summary(monkeypatch,tmp_path):
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    def simulate(self, **kwargs):
        idx=9
        groups={"version":1,"total_variants":3,"groups":[{"variant":[1,2,3,4,5,6],"count":3}]}
        return [bt.RetroactivePrediction("2025-01-01",str(self.dates[idx]),[[1,2,3,4,5,6]],set(range(1,31)),[1,2,3,4,5,6],6,30,4,"6/49",draw_index=idx,hits_union=6,variant_count=3,ticket_context={"hard_core":list(range(1,31)),"actual":[1,2,3,4,5,6]},variant_prize_groups=groups)]
    monkeypatch.setattr(bt.LotoBacktester,"run_retroactive_backtest",simulate)
    kw=dict(df_source=history(n=10),game_type="6/49",pool_size=30,backtest_depth_percent=10,guarantee=4,use_cache=True)
    first,meta=wf.run_honest_walk_forward(**kw)
    assert first and first[0].variant_prize_groups
    new=Path(meta["cache_file"])
    assert new.is_file() and new.name.endswith("pg1.pkl")
    old=new.with_name(new.name[:-len("pg1.pkl")]+".pkl")
    new.rename(old)
    missing, old_meta=wf.run_honest_walk_forward(**kw,cache_only=True)
    assert missing==[] and old_meta.get("cache_miss")
    latest, fresh=wf.run_honest_walk_forward(**kw)
    assert latest and not fresh["from_cache"] and latest[0].variant_prize_groups
    again, saved=wf.run_honest_walk_forward(**kw,cache_only=True)
    assert saved["from_cache"] and again[0].variant_prize_groups==latest[0].variant_prize_groups


@pytest.mark.parametrize("explicit_compact",[False,True])
def test_foreign_compact_cache_key_stays_unchanged(monkeypatch,tmp_path,explicit_compact):
    monkeypatch.setattr(wf,"CACHE_DIR",tmp_path)
    frame=history(n=10)
    kw=dict(df_source=frame,game_type="6/49",pool_size=30,backtest_depth_percent=10,guarantee=4,game_key="de_lotto",country="DE",compact_results=explicit_compact)
    _,meta=wf.run_honest_walk_forward(**kw,cache_only=True)
    old=wf._decision_sig("6/49",30,guarantee=4,game_key="de_lotto",country="DE")
    assert meta["decision_sig"] == old + ("csim1" if explicit_compact else "")


def test_foreign_compact_step_does_not_save_romanian_prize_groups(monkeypatch):
    b=bt.LotoBacktester(history(n=12),game_type="6/49",game_key="de_lotto",country="DE")
    def pipeline(engine,**kwargs):
        engine.hard_core=list(range(1,31))
        return [[1,2,3,4,5,6]],None,None,None,{},engine.audit
    monkeypatch.setattr(LotoEngine,"run_institutional_pipeline",pipeline)
    p=bt._retroactive_step_stateless(b.df,b.draws,b.dates,"6/49",10,30,4,0,100,game_key="de_lotto",country="DE",compact_variants=True)
    assert p.variant_prize_groups is None
