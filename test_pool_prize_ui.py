"""Retry controls for hypothetical pool prizes."""
from types import SimpleNamespace
import app_nicegui as app
import ui_hits
import ui_prizes
from scripts.analysis.audit_output import capture_ui
from test_history_official_prizes_ui import _entries


def test_pool_retry_forces_visible_page_reload(monkeypatch):
    instances=[]
    class Binding:
        def __init__(self,*args,**kwargs): self.calls=[]; instances.append(self)
        def request(self,**kwargs): self.calls.append(kwargs.get("retry_missing",False))
        def refresh(self): pass
    monkeypatch.setattr(ui_prizes,"PoolPrizeBinding",Binding)
    with capture_ui() as view:
        ui_hits._render_hits_4plus(_entries(1),"6/49",{"pool_size":16})
    assert len(instances)==1 and instances[0].calls==[False]
    button=next(n for n in view.walk() if n["kind"]=="button" and "Reîncearcă premiile" in n["args"][0])
    button["kwargs"]["on_click"]()
    assert instances[0].calls==[False,True]


def test_virtual_retry_forces_visible_page_reload(monkeypatch):
    instances=[]
    class Binding:
        def __init__(self,*args,**kwargs): self.calls=[]; instances.append(self)
        def request(self,**kwargs): self.calls.append(kwargs.get("retry_missing",False))
        def refresh(self): pass
    monkeypatch.setattr(ui_prizes,"PoolPrizeBinding",Binding)
    entry=SimpleNamespace(draw_index=1,draw_date="2026-10-25",variant=[1,2,3,4,5,6],hits=4,hits_union=4,variant_count=1,ticket_context={"actual":[1,2,3,4,20,21],"hard_core":list(range(1,11))})
    record={"name":"6/49","game":"6/49","pool":10,"guarantee":6,"depth":30,"flat":[entry],"meta":{"n_test_draws":1,"n_expected":1},"simulated":False}
    monkeypatch.setitem(app.STATE,"virtual_wf_results",[record])
    monkeypatch.setitem(app.STATE,"virtual_wf_status","gata")
    with capture_ui() as view:
        app._render_virtual_history()
    assert len(instances)==1 and instances[0].calls==[False]
    button=next(n for n in view.walk() if n["kind"]=="button" and "Reîncearcă premiile" in n["args"][0])
    button["kwargs"]["on_click"]()
    assert instances[0].calls==[False,True]


def test_historical_currency_is_not_presented_as_current_when_other_category_is_reported():
    from loto_enterprise.core.official_prizes import PrizeDraw, PrizeCategory
    draw = PrizeDraw("loto_6_49", "2004-01-01", (1, 2, 3, 4, 5, 6), None, (
        PrizeCategory("II", "5/6", 0, None, 10000, 10000),
        PrizeCategory("III", "4/6", 10, 10000, None, 10000),
    ), "https://www.loto.ro/test")
    row = {"label": "2004-01-01", "drawn": [1,2,3,4,5,6], "pool_numbers": [1,2,3,4,5,7,8,9],
           "generated_variants": [[1,2,3,4,5,7], [1,2,3,4,7,8]]}
    shown = ui_prizes.pool_prize_text("6/49", row, ui_prizes.PrizeView("ready", draw))
    assert "indisponibil" in shown.text
    assert "monedă istorică neconfirmată" in shown.detail
    assert " lei" not in shown.detail
