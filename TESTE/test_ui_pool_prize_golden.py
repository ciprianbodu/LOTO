"""Real isolated SQLite + background fetch + NiceGUI pool pagination."""
from types import SimpleNamespace
from threading import Event
from nicegui import ui as nicegui_ui
import ui_hits
import ui_prizes
from scripts.analysis.audit_output import CaptureUI
from loto_enterprise.core.official_prizes import PrizeCache
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult

POOL=[2,3,4,5,6,7,8,9,10,12,13,16,17,18,19,20,21,23,24,25,26,27,30,32,33,36,39,40,44,46]
DRAWN=[10,27,28,29,33,39]


def official_html():
    # Exact published numbers and values for 25.10.2026, and a synthetic second
    # block to check paging. The HTTP response never leaves this test process.
    def block(day):
        balls=''.join(f'<img src="/bundles/lotofrontend/images/bile/{n}.png"/>' for n in DRAWN)
        return f'''<div class="rezultate-extrageri-content"><div class="numere-extrase">{balls}</div>
        <div class="button-open-details">Detalii castiguri la 6/49 din {day}.10.2026</div>
        <table class="results-table"><tr><td>I (6/6)</td><td>REPORT</td><td>826.505,28</td><td>1.326.505,28</td></tr>
        <tr><td>II (5/6)</td><td>4</td><td>68.875,44</td><td>-</td></tr>
        <tr><td>III (4/6)</td><td>466</td><td>591,20</td><td>-</td></tr>
        <tr><td>IV (3/6)</td><td>10.468</td><td>50,00</td><td>-</td></tr></table></div>'''
    return '''<select name="select-year"><option value="2026" selected>2026</option></select>
    <select name="select-month"><option value="10" selected>10</option></select>'''+block('26')+block('25')


class RealTablesUI(CaptureUI):
    def __init__(self):
        super().__init__()
        self.tables=[]
    def table(self, **kwargs):
        table=nicegui_ui.table(**kwargs)
        # retain event callback because the NiceGUI element does not expose it
        table._test_kwargs=kwargs
        self.tables.append(table)
        return table


def test_offline_real_prize_cache_ui_async_and_page_change(tmp_path,monkeypatch):
    started, release=Event(), Event()
    fetches=[]
    def fetch(game, year, month):
        fetches.append((game,year,month))
        started.set()
        assert release.wait(5)
        return official_html()
    cache=PrizeCache(tmp_path/'isolated_official_prizes.db', fetcher=fetch)
    instances=[]
    Base=ui_prizes.PoolPrizeHistoryLookup
    def create_lookup(game):
        lookup=Base(game, cache_factory=lambda: cache)
        instances.append(lookup)
        return lookup
    monkeypatch.setattr(ui_prizes,'PoolPrizeHistoryLookup',create_lookup)
    captured=RealTablesUI()
    monkeypatch.setattr(ui_hits,'ui',captured)
    def wf(index, day):
        return WalkForwardResult(draw_index=index, draw_date=day, variant=[10,27,33,39,2,3],
            hits=4, hits_union=4, ticket_context={'hard_core':POOL,'actual':DRAWN})
    entries=[wf(0,'2026-10-25')]+[wf(i,'2026-10-26') for i in range(1,16)]
    try:
        ui_hits._render_hits_4plus(entries,'6/49',{'pool_size':30})
        lookup,=instances
        assert started.wait(5)
        pool=next(t for t in captured.tables if any(c['name']=='pool_prize' for c in t.columns))
        assert len(pool.rows)==16
        before=next(r for r in pool.rows if r['draw']=='2026-10-25')
        assert '591,20' not in before['pool_prize']
        assert 'se încarcă' in before['pool_prize']
        assert pool.rows[-1]['draw']=='2026-10-25'
        # Move to page 2 before the first HTTP call returns; it must be queued.
        pool._test_kwargs['on_pagination_change'](SimpleNamespace(value={'page':2,'rowsPerPage':15}))
        release.set()
        lookup.wait_idle(timeout=10)
        for node in captured.walk():
            if node['kind']=='timer':
                node['args'][1]()
        after=next(r for r in pool.rows if r['draw']=='2026-10-25')
        assert after['pool_prize']=='591,20 lei'
        assert '1' in after['pool_cost'] and '8' in after['pool_cost']
        assert after['prize_source'].startswith('https://www.loto.ro/')
        assert 'cat. III: 1 × 591,20 lei' in after['prize_detail']
        assert '10/2026' in after['prize_detail']
        assert 'nu include taxa pe bilet' in captured.text()
        assert 'simularea' in captured.text()
        assert fetches==[('loto_6_49',2026,10)]
        assert (tmp_path/'isolated_official_prizes.db').is_file()
    finally:
        release.set()
        for lookup in instances:
            lookup.close()
        for table in captured.tables:
            table.delete()


def test_offline_virtual_history_shows_the_same_official_estimate(tmp_path,monkeypatch):
    import app_nicegui as app
    started,release=Event(),Event()
    def fetch(game,year,month):
        started.set()
        assert release.wait(5)
        return official_html()
    cache=PrizeCache(tmp_path/'virtual_isolated.db',fetcher=fetch)
    lookups=[]
    Base=ui_prizes.PoolPrizeHistoryLookup
    def factory(game):
        obj=Base(game,cache_factory=lambda:cache)
        lookups.append(obj)
        return obj
    monkeypatch.setattr(ui_prizes,'PoolPrizeHistoryLookup',factory)
    captured=RealTablesUI()
    monkeypatch.setattr(app,'ui',captured)
    entry=WalkForwardResult(draw_index=25,draw_date='2026-10-25',variant=[10,27,33,39,2,3],
        hits=4,hits_union=4,ticket_context={'hard_core':POOL,'actual':DRAWN},
        variant_count=593775,virtual_full_system=True)
    record={'name':'6/49','game':'6/49','pool':30,'guarantee':6,'depth':30,'flat':[entry],
        'meta':{'n_test_draws':1,'n_expected':1,'partial':False},'simulated':False}
    monkeypatch.setitem(app.STATE,'virtual_wf_results',[record])
    monkeypatch.setitem(app.STATE,'virtual_wf_status','Analiză virtuală încheiată.')
    try:
        app._render_virtual_history()
        table=next(t for t in captured.tables if any(c['name']=='pool_prize' for c in t.columns))
        for node in captured.walk():
            if node['kind']=='timer':
                node['args'][1]()
        assert 'istoricul virtual nu conține variante generate' in table.rows[0]['pool_prize']
        assert table.rows[0]['pool_cost'] == '—'
        assert not started.is_set()
        assert 'simularea' in captured.text() or 'estimare' in captured.text() or 'estimat' in captured.text()
    finally:
        release.set()
        for lookup in lookups:
            lookup.close()
        for table in captured.tables:
            table.delete()
