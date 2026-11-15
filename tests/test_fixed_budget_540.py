"""Finite-budget geometry, independently counted over every 5/40 drawing."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from covering.common import _sorted_pool
from covering.dispatch import _generate_wheel, generate_wheel
from covering.fixed_budget_540 import (
    REVISION,
    fixed_budget_540_certificate,
    fixed_budget_540_signature,
    improve_fixed_budget_540,
)

ROOT = Path(__file__).resolve().parents[1]


def _wheel(g=5, v=40, cap=21, drawn=6, maximum=40, method="hitcover", condition=None):
    pool = list(range(1, v + 1))
    scores = {n: float(v + 1 - n) for n in pool}
    return generate_wheel(method, pool, 5, g, cap, scores, condition, drawn, maximum)


def test_every_draw_confirms_global_bound_and_all_tail_improvements():
    """All 3,838,380 drawings: frozen candidate versus both real incumbents."""
    pool = list(range(1, 41))
    scores = {n: float(41 - n) for n in pool}
    packed, _ = _wheel()
    old4, _ = _generate_wheel("hitcover", pool, 5, 4, 21, scores, None, 6, 40)
    old5, _ = _generate_wheel("hitcover", pool, 5, 5, 21, scores, None, 6, 40)
    masks = [np.array([sum(1 << (n - 1) for n in t) for t in w], dtype=np.uint64)
             for w in (packed, old4, old5)]
    histograms = [np.zeros(7, dtype=np.int64) for _ in masks]
    draws = itertools.combinations(range(40), 6)
    while batch := list(itertools.islice(draws, 16384)):
        values = np.array(batch, dtype=np.uint64)
        draw_masks = np.bitwise_or.reduce(np.left_shift(np.uint64(1), values), axis=1)
        for histogram, ticket_masks in zip(histograms, masks):
            best = np.bitwise_count(draw_masks[:, None] & ticket_masks[None, :]).max(axis=1)
            histogram += np.bincount(best, minlength=7)
    certificate = fixed_budget_540_certificate(pool, packed)
    assert certificate is not None
    assert histograms[0].tolist() == certificate["histogram"]
    assert histograms[1].tolist() == [0, 161800, 2723237, 898720, 53888, 735, 0]
    assert histograms[2].tolist() == [0, 180000, 2851840, 762440, 43456, 644, 0]
    assert all(int(h.sum()) == certificate["total_draws"] for h in histograms)
    for h in histograms[1:]:
        assert all(histograms[0][t:].sum() >= h[t:].sum() for t in range(1, 7))
    assert max(len(set(a) & set(b)) for a, b in itertools.combinations(packed, 2)) == 1
    assert sum(histograms[0][4:]) == 21 * (5 * 595 + 35)
    assert histograms[0][5] == 21 * 35


@pytest.mark.parametrize("g", [4, 5])
def test_certified_incumbent_replacement_keeps_classical_coverage(g):
    pool = list(range(1, 41))
    scores = {n: float(41 - n) for n in pool}
    old, old_cov = _generate_wheel("hitcover", pool, 5, g, 21, scores, None, 6, 40)
    improved, new_cov = improve_fixed_budget_540(pool, old, g)
    assert new_cov == old_cov
    assert len(improved) == len(old) == 21
    old_targets = {s for t in old for s in itertools.combinations(t, g)}
    new_targets = {s for t in improved for s in itertools.combinations(t, g)}
    assert len(new_targets) == len(old_targets)
    assert fixed_budget_540_certificate(pool, improved) is not None


def test_certificate_checks_complete_mapped_wheel_instead_of_its_size():
    packed, _ = _wheel()
    pool = list(range(1, 41))
    reversed_pool = list(reversed(pool))
    relabel = dict(zip(pool, reversed_pool))
    mapped = [[relabel[n] for n in block] for block in reversed(packed)]
    cert = fixed_budget_540_certificate(reversed_pool, mapped)
    assert cert and cert["revision"] == REVISION
    assert fixed_budget_540_certificate(pool, mapped) is None
    assert fixed_budget_540_certificate(pool, packed[:-1]) is None
    damaged = [list(t) for t in packed]
    damaged[0][0] = next(n for n in pool if n not in damaged[0])
    assert fixed_budget_540_certificate(pool, damaged) is None
    assert improve_fixed_budget_540(pool, damaged, 4) == (damaged, None)
    assert fixed_budget_540_certificate(pool, packed, draw_n=5, max_num=45) is None


@pytest.mark.parametrize("v,k,g,cap,c,d,n", [
    (39, 5, 5, 21, 0, 6, 40), (40, 5, 3, 21, 0, 6, 40),
    (40, 5, 5, 20, 0, 6, 40), (40, 5, 5, 22, 0, 6, 40),
    (40, 5, 4, 21, 5, 6, 40), (40, 5, 5, 21, 0, 5, 45),
    (40, 6, 5, 21, 0, 6, 49), (True, 5, 5, 21, 0, 6, 40),
])
def test_other_geometry_has_no_revision(v, k, g, cap, c, d, n):
    assert fixed_budget_540_signature(v, k, g, cap, c, d, n) is None


@pytest.mark.parametrize("g", [4, 5])
def test_real_full_history_pipeline_uses_the_same_certificate(g):
    from loto_engine import LotoEngine

    engine = LotoEngine("5/40")
    assert engine.load_data(str(ROOT / "_ISTORIC/loto_5_40.csv"))
    assert len(engine.data) == len(pd.read_csv(ROOT / "_ISTORIC/loto_5_40.csv"))
    lines, _, _, _, context, audit = engine.run_institutional_pipeline(
        pool_size=40, guarantee=g, max_variants=21, track_pool_variation=False,
    )
    cert = audit["fixed_budget_540"]
    assert fixed_budget_540_certificate(cert["position_pool"], lines) == cert
    assert len(lines) == 21 and set(engine.hard_core) == set(range(1, 41))
    assert context["coverage_pct"] in (0.11, 0.0)


@pytest.mark.parametrize("game,v,g,cap,c,changed", [
    ("5/40", 40, 4, 21, 0, True), ("5/40", 40, 5, 21, 0, True),
    ("5/40", 40, 3, 21, 0, False), ("5/40", 40, 4, 21, 5, False),
    ("5/40", 39, 5, 21, 0, False), ("5/40", 40, 5, 22, 0, False),
    ("joker", 40, 5, 21, 0, False), ("6/49", 40, 5, 21, 0, False),
])
def test_wf_and_worker_invalidate_only_the_changed_configuration(
    monkeypatch, game, v, g, cap, c, changed
):
    import covering.fixed_budget_540 as fixed
    from loto_enterprise.core.walk_forward_adapter import _wheel_sig
    import worker

    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    cfg = {"datasets": [{"tasks": [{"game_label": game, "pool_size": v,
            "guarantee": g, "max_variants": cap, "wheel_condition": c}]}]}
    new_sig = _wheel_sig(v, game, g, c, cap)
    new_key = worker._pipeline_cache_key("stable-input", cfg)
    monkeypatch.setattr(fixed, "fixed_budget_540_signature", lambda *_a, **_k: None)
    old_sig = _wheel_sig(v, game, g, c, cap)
    old_key = worker._pipeline_cache_key("stable-input", cfg)
    assert (new_sig != old_sig) is changed
    assert (new_key != old_key) is changed


def test_explicit_greedy_keeps_its_previous_cache_namespace(monkeypatch):
    from loto_enterprise.core.walk_forward_adapter import _wheel_sig

    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    assert _wheel_sig(40, "5/40", 5, 0, 21) == "constructive_cover|g5|c5|cap21|rt1|lp2"
    old, cov = _generate_wheel("greedy", list(range(1, 41)), 5, 5, 21, None, None, 6, 40)
    assert generate_wheel("greedy", list(range(1, 41)), 5, 5, 21, None, None, 6, 40) == (old, cov)


@pytest.mark.parametrize("g", [4, 5])
def test_real_walk_forward_generates_the_certified_wheel(monkeypatch, tmp_path, g):
    import wheeling_methods
    from loto_enterprise.core import walk_forward_adapter as wf

    original = wheeling_methods.generate_wheel
    generated = []

    def observe(*args, **kwargs):
        wheel, coverage = original(*args, **kwargs)
        pool = kwargs.get("pool", args[1] if len(args) > 1 else None)
        scores = kwargs.get("scores", args[5] if len(args) > 5 else None)
        generated.append(fixed_budget_540_certificate(_sorted_pool(pool, scores), wheel))
        return wheel, coverage

    monkeypatch.setattr(wheeling_methods, "generate_wheel", observe)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    frame = pd.read_csv(ROOT / "_ISTORIC/loto_5_40.csv")
    records, meta = wf.run_honest_walk_forward(
        frame, "5/40", 40, 0.1, use_cache=False,
        guarantee=g, max_variants=21,
    )
    assert generated and all(cert is not None for cert in generated)
    assert records and all(record.variant_count == 21 for record in records)
    assert len(records) == meta["n_expected"]


def test_actual_worker_process_roundtrips_both_guarantees_and_cache(tmp_path):
    output = tmp_path / "worker_result.json"
    script = r'''
import json,sys
from pathlib import Path
import pandas as pd
import job_queue as jq
import worker
from ui_shared import decode_queue_result
from covering.fixed_budget_540 import fixed_budget_540_certificate
jq.init_job_queue()
df=pd.read_csv('_ISTORIC/loto_5_40.csv')
results=[]
for g in [4,5,5]:
    cfg={'input_hash':'fb540-worker', 'use_cache':True,'datasets':[{'fname':'loto_5_40.csv','df_json':df.to_json(orient='split'),'tasks':[{'game_label':'5/40','pool_size':40,'guarantee':g,'max_variants':21,'wheel_condition':0,'bench_hit_target':5}]}]}
    jid=jq.submit_job('pipeline',json.dumps(cfg))
    job=jq.fetch_pending_job(worker_token=worker.WORKER_TOKEN)
    assert job and job['id']==jid
    worker._process_one_job(job)
    status=jq.get_job_status(jid)
    assert status['status']=='COMPLETED',status
    bundle,n=decode_queue_result(status['result_json'])
    data=bundle[0][1]['5/40']
    cert=data['audit']['fixed_budget_540']
    assert fixed_budget_540_certificate(cert['position_pool'],data['variants'])==cert
    assert data['total_draws']==len(df) and len(data['variants'])==21
    results.append({'guarantee':g,'status':status['status'],'variants':len(data['variants']),'certificate':cert['revision'],'cache_hit':'Cache hit' in (status.get('log_tail') or '')})
Path(sys.argv[1]).write_text(json.dumps(results,indent=2))
'''
    env = dict(os.environ)
    env.update(LOTO_RUNTIME_DIR=str(tmp_path / "runtime"),
               LOTO_JOBS_DB=str(tmp_path / "jobs.db"),
               LOTO_WF_CACHE_DIR=str(tmp_path / "wf"),
               LOTO_POOL_HISTORY_FILE=str(tmp_path / "pool_history.json"))
    env.pop("LOTO_WHEEL_METHOD", None)
    completed = subprocess.run([sys.executable, "-c", script, str(output)], cwd=ROOT,
                               env=env, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(output.read_text())
    assert len(report) == 3 and all(r["status"] == "COMPLETED" for r in report)
    assert report[-1]["cache_hit"] is True
