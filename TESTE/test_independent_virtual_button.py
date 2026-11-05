from nicegui import ui
import app_nicegui as app
import ui_results
from TESTE.test_bench_status_ui import sidebar_context

def test_no_manual_history_button_with_no_physical_job(sidebar_context, monkeypatch):
    client, settings, state = sidebar_context
    starts=[]
    monkeypatch.setattr(app,"_start_virtual_history", lambda:starts.append(True))
    state["results"]=None
    with client:
        ui_results.results_panel()
    assert not any(isinstance(e,ui.button) and "Calculează istoric virtual" in e.text
                   for e in client.elements.values())
    assert not starts
