import json
from types import SimpleNamespace
import app_nicegui as app
import bench_outcome
import ui_hits
import ui_prizes
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from TESTE.test_rebench_selected_sources import session


def test_full_rebench_to_analysis_with_changed_settings(session, monkeypatch, tmp_path):
    monkeypatch.setattr(bench_outcome, 'RUNTIME_ROOT', tmp_path / 'runtime')
    monkeypatch.setattr(app, 'BENCH_PID_FILE', tmp_path / 'bench.pid')
    monkeypatch.setattr(app, '_refresh_status', lambda: None)
    monkeypatch.setattr(app, '_virtual_decision_fingerprint', lambda *_: (3, ('verified',)))
    monkeypatch.setattr(app._bench_freshness_panel, 'refresh', lambda: None)
    monkeypatch.setitem(app.SETTINGS, 'bench_hit_target', 3)
    monkeypatch.setitem(app.STATE, 'virtual_wf_depth', 30)
    monkeypatch.setitem(app.STATE, 'virtual_wf_results', [])
    monkeypatch.setitem(app.STATE, 'virtual_wf_status', None)
    monkeypatch.setitem(app.STATE, '_bench_process', None)
    monkeypatch.setitem(app.STATE, '_bench_run_identity', None)
    monkeypatch.setitem(app.STATE, '_bench_context_required', None)
    monkeypatch.setitem(app.STATE, 'bench_was_running', False)
    monkeypatch.setitem(app.STATE, 'bench_cancelled', False)
    monkeypatch.setattr(app.subprocess, 'Popen', lambda *_a, **_kw: SimpleNamespace(pid=9876, poll=lambda: 0))
    class ImmediateThread:
        def __init__(self, *, target, **_kwargs): self.target=target
        def start(self): self.target()
    monkeypatch.setattr(app.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(app.results_panel, 'refresh', lambda: None)
    calls=[]
    def simulated(**kw):
        calls.append(kw)
        size=kw['pool_size']; game=kw['game_type']; pick=6 if game=='6/49' else 5
        nums=list(range(1, size+1)); variant=nums[:pick]+([1] if game=='joker' else [])
        row=SimpleNamespace(draw_index=1,draw_date='29-10-2026',variant=variant,hits=pick,
            hits_union=pick,variant_count=1,virtual_full_system=False,joker_hit=(True if game=='joker' else None),
            variant_prize_groups={'version':1,'total_variants':1,'groups':[{'variant':variant,'count':1}]},
            ticket_context={'actual':nums[:6 if game=='5/40' else pick], 'hard_core':nums})
        return [row], {'n_test_draws':1,'n_expected':1,'partial':False,'country':'RO'}
    monkeypatch.setattr(wf, 'run_honest_walk_forward', simulated)
    app.run_rebench()
    pid, run_id, _digest=app.STATE['_bench_run_identity']
    bench_outcome.record_exit(run_id,pid,0)
    for name in ('pool_size_649_val','pool_size_joker_val','pool_size_540_val'):
        monkeypatch.setitem(app.SETTINGS,name,30)
    app._observe_bench_activity(False)
    assert [(c['game_type'],c['pool_size']) for c in calls]==[('6/49',16),('joker',12),('5/40',19)]
    assert all(c.get('compact_results') is True and not c.get('virtual_full_system') for c in calls)
    assert [(r['game_id'],r['pool']) for r in app.STATE['virtual_wf_results']]==[('6/49',16),('joker',12),('5/40',19)]
    monkeypatch.setattr(ui_prizes.PoolPrizeBinding, 'request', lambda *_a, **_kw: None)
    monkeypatch.setattr(ui_hits,'_render_bench_leaderboard',lambda *_a, **_kw: None)
    monkeypatch.setattr(ui_hits,'_render_virtual_controls',lambda: None)
    monkeypatch.setattr(ui_hits,'_render_game_analysis',lambda *_a, **_kw: ui_hits.ui.label('OLD HISTORY'))
    old={'pool_size':30,'hard_core':list(range(1,31))}
    with capture_ui() as view:
        ui_hits._render_analysis_menu([('custom_649.csv',{'6/49':old})])
    text=view.text()
    assert all(f'pool cerut {n}' in text for n in (16,12,19))
    assert 'Rezultatul recuperat · pool 30' in text and 'OLD HISTORY' in text
    tables=[n for n in view.walk() if n['kind']=='table' and any(c['name']=='drawn' for c in n['kwargs'].get('columns',[]))]
    displayed=[len(row['pool'].split()) for table in tables for row in table['kwargs'].get('rows',[]) if isinstance(row.get('pool'),str) and ' ' in row['pool']]
    assert sorted(displayed)==[12,16,19]
