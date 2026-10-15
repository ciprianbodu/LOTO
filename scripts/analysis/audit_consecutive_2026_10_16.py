"""Descriptive audit of consecutive runs in Romanian 6/49; not a holdout test.

Run from the repository root. Only the named JSON report is written. The current
method is fixed retrospectively, and every historical score uses earlier days.
Global relabelling preserves temporal overlap; it tests numeric clustering,
not prediction. No scorer, benchmark, decision or history is changed.
"""
from __future__ import annotations
import os

import argparse
import sys,json,hashlib,io,math,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding="utf-8")
import numpy as np
import pandas as pd
from collections import Counter
from functools import lru_cache
from loto_enterprise.core.history import chronological_history,training_cutoffs
from loto_enterprise.core.method_selector import get_ensemble_for_game,combine_ensemble_scores,get_winner_name
from loto_enterprise.core.ranking import rank_by_score,longest_consecutive_run
from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.benchmark.methods import METHODS,call_method
def main():
    root=Path(__file__).parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=root/'audit_consecutive_2026_10_16.json',help='Destination JSON; history and local decision remain read-only.')
    args=parser.parse_args()
    project=root.parent.parent
    history_path=project/'_ISTORIC/loto_6_49.csv'
    decision_path=project/'best_methods.json'
    decision_raw=decision_path.read_bytes()
    raw=history_path.read_bytes()
    df=chronological_history(pd.read_csv(io.BytesIO(raw)))
    draws=df[[f'n{i}' for i in range(1,7)]].to_numpy(np.int64)
    cutoffs=training_cutoffs(df)
    @lru_cache(None)
    def count_safe(n,k,cap,run=0):
        if k<0 or k>n:return 0
        if n==0:return int(k==0)
        ans=count_safe(n-1,k,cap,0)
        if run<cap:ans+=count_safe(n-1,k-1,cap,run+1)
        return ans
    out={'history_sha256':hashlib.sha256(raw).hexdigest(),'decision_sha256':hashlib.sha256(decision_raw).hexdigest(),'rows':len(draws),'last_date':str(df.iloc[-1]['date']),'uniform':{},'current':{},'replay':{}}
    for k in (6,12,16):
        total=math.comb(49,k)
        out['uniform'][str(k)]={'total':total,'p_run_ge2':1-count_safe(49,k,1)/total,'p_run_ge3':1-count_safe(49,k,2)/total,'p_run_ge4':1-count_safe(49,k,3)/total,'p_run_ge5':1-count_safe(49,k,4)/total,'expected_adjacent_pairs':k*(k-1)/49}
    for k in range(6,17):
        ens=get_ensemble_for_game('loto_6_49',k,config_path=str(decision_path),max_methods=1)
        scores=combine_ensemble_scores([(n,fn(draws,49),w) for n,fn,w in ens])
        ranked=rank_by_score(scores,49)
        audit={}; limited=apply_consecutive_limit(ranked,k,2,audit)
        out['current'][str(k)]={'method':[(n,w) for n,fn,w in ens],'pool':sorted(ranked[:k]),'max_run':longest_consecutive_run(ranked[:k]),'unique_scores':len(set(scores.values())),'boundary_tie':scores[ranked[k-1]]==scores[ranked[k]],'limited_pool':sorted(limited),'limit_audit':audit}
    print('Current',json.dumps(out['current']['16']),flush=True)
    # Fixed current scorer over earlier-day prefixes: descriptive audit, not selection validation.
    name=get_winner_name('loto_6_49',16,config_path=str(decision_path)); fn=METHODS[name][0]
    indices=list(range(max(200,len(draws)-800),len(draws)))
    rows=[]; pools=[]
    for j,i in enumerate(indices):
        scores,_=call_method(name,draws[:cutoffs[i]],49)
        ranked=rank_by_score(scores,49)
        if len(ranked)<16:continue
        p=ranked[:16]; limited=apply_consecutive_limit(ranked,16,2)
        actual=set(draws[i]); pools.append(p)
        rows.append({'i':i,'date':str(df.iloc[i]['date']),'run':longest_consecutive_run(p),'tie':scores[ranked[15]]==scores[ranked[16]],'hits_raw':len(actual.intersection(p)),'hits_limited':len(actual.intersection(limited)),'replaced':len(set(p)-set(limited))})
        if j%200==0:print('Replay',j,flush=True)
    pools=np.asarray(pools)
    runs=Counter(r['run'] for r in rows)
    rec={'method':name,'n':len(rows),'first_date':rows[0]['date'],'last_date':rows[-1]['date'],'runs':dict(sorted(runs.items())),'p_run_ge3':sum(r['run']>=3 for r in rows)/len(rows),'p_run_ge4':sum(r['run']>=4 for r in rows)/len(rows),'boundary_ties':sum(r['tie'] for r in rows),'hits_raw':dict(Counter(r['hits_raw'] for r in rows)),'hits_limited':dict(Counter(r['hits_limited'] for r in rows)),'note':'Current scorer selected on all available history and fixed retrospectively; descriptive only, not a holdout. Temporal pools strongly overlap. The relabelling p-value concerns numeric clustering, not predictive advantage.'}
    # Random relabelling preserves overlaps/temporal dependence, tests numeric clustering specifically.
    rng=np.random.default_rng(20261016); null=[]; null4=[]
    for rep in range(5000):
        labels=rng.permutation(49)+1
        relabel=np.sort(labels[pools-1],axis=1)
        adjacent=np.diff(relabel,axis=1)==1
        null.append(float(np.mean(np.any(adjacent[:,:-1]&adjacent[:,1:],axis=1))))
        null4.append(float(np.mean(np.any(adjacent[:,:-2]&adjacent[:,1:-1]&adjacent[:,2:],axis=1))))
    rec['relabel_null']={'replicates':len(null),'seed':20261016,'run3_mean':float(np.mean(null)),'run3_95pct':np.quantile(null,[.025,.975]).tolist(),'run3_upper_p':(1+sum(x>=rec['p_run_ge3'] for x in null))/(1+len(null)),'run4_mean':float(np.mean(null4)),'run4_95pct':np.quantile(null4,[.025,.975]).tolist(),'run4_upper_p':(1+sum(x>=rec['p_run_ge4'] for x in null4))/(1+len(null4))}
    out['replay']=rec
    out['draw_runs']={'all':dict(Counter(longest_consecutive_run(x) for x in draws)),'last800':dict(Counter(longest_consecutive_run(x) for x in draws[-800:]))}

    # Independently verify the geometry counter, observed runs, and scorer formula.
    import itertools

    def independent_run(nums):
        values=set(map(int,nums));best=0
        for start in values:
            if start-1 not in values:
                length=1
                while start+length in values:length+=1
                best=max(best,length)
        return best

    for k in range(1,6):
        for cap in range(1,5):
            brute=sum(independent_run(c)<=cap for c in itertools.combinations(range(10),k))
            assert brute==count_safe(10,k,cap)
    assert all(independent_run(row)==longest_consecutive_run(row) for row in draws)
    before=draws.copy();base=fn(draws,49);rng=np.random.default_rng(16492026)
    worst=0.
    for rep in range(300):
        labels=rng.permutation(49)+1
        changed=fn(labels[draws-1],49)
        worst=max(worst,max(abs(changed[int(labels[n-1])]-v) for n,v in base.items()))
        expected={int(labels[n-1]) for n in rank_by_score(base,16)}
        assert set(rank_by_score(changed,16))==expected
    formula_cases=0
    if name=='markov_lag2':
        for stop in np.linspace(400,len(draws),40,dtype=int):
            hist=draws[:stop];counts=np.zeros(50,dtype=int);transition=np.zeros((50,50),dtype=int)
            for src,dst in zip(hist[:-2],hist[2:]):
                for x in src:
                    counts[x]+=1
                    for y in dst:transition[x,y]+=1
            direct={n:sum(transition[int(x),n]/max(counts[int(x)],1) for x in hist[-2]) for n in range(1,50)}
            assert rank_by_score(direct,49)==rank_by_score(fn(hist,49),49)
            formula_cases+=1
    assert np.array_equal(draws,before)
    out['verification']={'exact_dp_brute_force_cases':20,'historical_run_checks':len(draws),'relabel_cases':300,'max_score_error':worst,'independent_markov_formula_cases':formula_cases,'input_unchanged':True}
    if raw!=history_path.read_bytes() or decision_raw!=decision_path.read_bytes():
        raise RuntimeError('History or decision changed during audit; result not written.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'replay':out['replay'],'verification':out['verification']},indent=2,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
