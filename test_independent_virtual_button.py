from nicegui import ui
import app_nicegui as app
import ui_results
from test_bench_status_ui import sidebar_context

def test_button_handler_with_no_physical_job(sidebar_context, monkeypatch):
    client, settings, state = sidebar_context
    starts=[]
    monkeypatch.setattr(app,"_start_virtual_history", lambda:starts.append(True))
    state["results"]=None
    with client:
        ui_results.results_panel()
    button=next(e for e in client.elements.values() if isinstance(e,ui.button)
                and "Calculează istoric virtual" in e.text)
    print("listeners",repr(button._event_listeners))
    handler=next(listener.handler for listener in button._event_listeners.values())
    handler(None)
    assert starts==[True]
