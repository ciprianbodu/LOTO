from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import pytest
import ui_hits
import ui_results
from loto_engine import LotoEngine
from loto_enterprise.core import backtesting as btmod
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from TESTE.test_large_pool_views_wf import history

def _assert_compact_stats_and_prizes(full_rows, compact_rows, game):
    """Compare every original field, and the new variant/prize information."""
    from collections import Counter
    from loto_enterprise.core.generated_prize import estimate_generated_prize
    from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw

    full = ui_hits._wf_per_draw_stats(full_rows, game)
    compact = ui_hits._wf_per_draw_stats(compact_rows, game)
    assert full.keys() == compact.keys()
    changed = {"generated_variants", "expected_variant_count", "variant_prize_groups"}
    labels = {
        "6/49": {"I":"6/6", "II":"5/6", "III":"4/6", "IV":"3/6"},
        "5/40": {"I":"5/6*", "II":"5/6", "III":"4/6"},
        "joker": {"I":"5/5+J", "II":"5/5", "III":"4/5+J", "IV":"4/5",
                  "V":"3/5+J", "VI":"3/5", "VII":"2/5+J", "VIII":"1/5+J"},
    }[game]
    categories = tuple(PrizeCategory(k, label, 1, 12345 + i * 100, None, 12345 + i * 100)
                       for i, (k, label) in enumerate(labels.items()))
    for index in full:
        a, b = full[index], compact[index]
        assert {k: v for k, v in a.items() if k not in changed} == {k: v for k, v in b.items() if k not in changed}
        variants = a["generated_variants"]
        groups = b["variant_prize_groups"]
        assert a["expected_variant_count"] is None and a["variant_prize_groups"] is None
        assert b["expected_variant_count"] == len(variants)
        assert b["generated_variants"] == [a["best_variant_raw"]]
        assert groups["version"] == 1 and groups["total_variants"] == len(variants)
        assert sum(g["count"] for g in groups["groups"]) == len(variants)
        def signature(v):
            main = v[:5] if game == "joker" else v
            return (tuple(sorted(set(main) & set(a["drawn"]))), tuple(v[5:]) if game == "joker" else ())
        assert Counter(signature(v) for v in variants) == Counter({signature(g["variant"]): g["count"] for g in groups["groups"]})
        assert all(g["variant"] in variants for g in groups["groups"])
        joker = None
        if game == "joker":
            selected = int(a["best_variant_raw"][-1])
            flag = a["best_variant_joker_hit"]
            assert isinstance(flag, bool)
            joker = selected if flag else (1 if selected != 1 else 2)
        official = PrizeDraw({"6/49":"loto_6_49", "5/40":"loto_5_40", "joker":"joker"}[game],
                             a["label"], tuple(a["drawn"]), joker, categories, "https://www.loto.ro/test")
        kwargs = dict(target_numbers=a["drawn"], target_date=a["label"], expected_count=len(variants),
                      pool_numbers=a["pool_numbers"], joker_hit=a["best_variant_joker_hit"])
        whole = estimate_generated_prize(game, variants, official, **kwargs)
        packed = estimate_generated_prize(game, b["generated_variants"], official,
                                          variant_prize_groups=groups, **kwargs)
        assert whole.status == "ready" and packed == whole


@pytest.mark.parametrize("game",["6/49", "5/40", "joker"])
def test_compaction_preserves_full_wheel_summary_and_context(monkeypatch,game):
    bt=btmod.LotoBacktester(history(game,n=12),game_type=game)
    pick=6 if game=="6/49" else 5
    def pipeline(self,**kwargs):
        self.hard_core=list(range(1,31))
        if game=="joker":
            variants=[[1,2,3,21,22,7],[1,2,3,4,20,7],[1,2,3,4,21,7]]
        elif pick==5:
            variants=[[1,2,3,20,21],[1,2,3,4,20],[1,2,3,4,21]]
        else:
            variants=[[1,2,3,20,21,22],[1,2,3,4,20,21],[1,2,3,4,20,22]]
        return variants,None,None,None,{"coverage_pct":98.5},self.audit
    monkeypatch.setattr(LotoEngine,"run_institutional_pipeline",pipeline)
    args=(bt.df,bt.draws,bt.dates,game,10,30,4,0,100)
    full=btmod._retroactive_step_stateless(*args)
    short=btmod._retroactive_step_stateless(*args,compact_variants=True)
    assert short.variant_count==len(full.variants)==3
    assert short.variants==[full.variants[1]]
    assert short.hits==full.hits and short.hits_union==full.hits_union
    assert short.joker_hit==full.joker_hit and short.ticket_context==full.ticket_context
    a=wf.expand_predictions_to_flat([full],game);b=wf.expand_predictions_to_flat([short],game)
    assert wf.per_draw_hit_summary(a)==wf.per_draw_hit_summary(b)
    assert wf.wheel_coverage_summary(a)==wf.wheel_coverage_summary(b)
    _assert_compact_stats_and_prizes(a, b, game)
    assert ui_results._wf_summary(a,{"pool_size":30},game)==ui_results._wf_summary(b,{"pool_size":30},game)
    with capture_ui() as c: ui_hits._render_hits_4plus(b,game,{"pool_size":30})
    assert "Variante evaluate = 3" in c.text()

@pytest.mark.parametrize("cap",[0,100000])
def test_pool_30_preserves_requested_cap_and_uses_compact_representation(monkeypatch,tmp_path,cap):
    calls=[]
    def simulate(self,**kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(btmod.LotoBacktester,"run_retroactive_backtest",simulate)
    monkeypatch.setattr(wf,"CACHE_DIR",tmp_path)
    flat,meta=wf.run_honest_walk_forward(history(n=10),"6/49",30,10,use_cache=False,guarantee=4,max_variants=cap)
    assert not flat and "large_pool_wf_skipped" not in meta
    assert calls[0]["max_variants"]==cap and calls[0]["compact_variants"] is True


def test_impossible_generator_request_is_refused_explicitly():
    flat,meta=wf.run_honest_walk_forward(history(n=10),"6/49",49,10,use_cache=False,guarantee=6,max_variants=0)
    assert flat==[] and meta["skip_reason"]=="resource_limit"
    assert "plafon" in meta["skip_detail"].lower()
    assert "plafon" in ui_results._large_pool_wf_note(meta).lower()


def test_compact_partial_cache_is_reused_and_completed(monkeypatch,tmp_path):
    calls=[]
    def simulate(self,**kw):
        calls.append(kw)
        idx=9 if len(calls)==1 else 8
        context={"hard_core":list(range(1,31)),"actual":[1,2,3,4,5,6],"audit":{}}
        return [btmod.RetroactivePrediction("a",str(self.dates[idx]),[[1,2,3,4,5,6]],set(range(1,31)),[1,2,3,4,5,6],6,30,4,"6/49",draw_index=idx,hits_union=6,wheel_coverage=100,ticket_context=context,variant_count=3750)]
    monkeypatch.setattr(btmod.LotoBacktester,"run_retroactive_backtest",simulate)
    monkeypatch.setattr(wf,"CACHE_DIR",tmp_path)
    first,meta1=wf.run_honest_walk_forward(history(n=10),"6/49",30,20,use_cache=True,guarantee=4)
    assert meta1["partial"] and len(first)==1
    second,meta2=wf.run_honest_walk_forward(history(n=10),"6/49",30,20,use_cache=True,guarantee=4)
    assert calls[1]["skip_indices"]=={9}
    assert {x.draw_index for x in second}=={8,9} and all(x.variant_count==3750 for x in second)
    assert not meta2["partial"]
    third,meta3=wf.run_honest_walk_forward(history(n=10),"6/49",30,20,use_cache=True,guarantee=4)
    assert meta3["from_cache"] and len(calls)==2 and len(third)==2


@pytest.mark.parametrize("broken",[False,True])
def test_compact_cache_key_is_selective_and_includes_fallback(monkeypatch,broken):
    from loto_enterprise.core import method_selector
    def choose(*args,**kwargs):
        if broken: raise RuntimeError("decision temporarily unreadable")
        return {"scorer":"frequency", "ensemble":[{"method":"frequency","weight":1.0}]}
    monkeypatch.setattr(method_selector,"recommend_optimal_config",choose)
    monkeypatch.setattr(wf,"_wheel_sig",lambda *a,**kw:"same-wheel")
    small_now=wf._decision_sig("6/49",16)
    big_now=wf._decision_sig("6/49",30)
    monkeypatch.setattr(wf,"_LEGACY_WF_POOL_MAX",{"6/49":100})
    assert wf._decision_sig("6/49",16)==small_now
    assert wf._decision_sig("6/49",30)!=big_now


@pytest.mark.parametrize("game,g,count", [("6/49",6,"593,775"),("5/40",5,"142,506"),("joker",5,"142,506")])
def test_maximum_guarantee_pool_30_unlimited_refuses_actual_resource_limit(game,g,count):
    flat,meta=wf.run_honest_walk_forward(history(game,n=10),game,30,10,use_cache=False,guarantee=g,max_variants=0)
    assert flat == [] and meta["skip_reason"] == "resource_limit"
    assert meta["max_variants"] == 0 and count in meta["skip_detail"]
    assert "plafon" in ui_results._large_pool_wf_note(meta).lower()
