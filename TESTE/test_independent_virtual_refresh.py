import asyncio
from nicegui import core, ui
import app_nicegui as app
import ui_results
from TESTE.test_bench_status_ui import sidebar_context


def test_user_closed_analysis_stays_closed_after_refresh(sidebar_context):
    client, settings, state = sidebar_context
    state["virtual_wf_status"] = "Analiză virtuală încheiată."
    with client:
        ui_results.results_panel()
    def section():
        return next(e for e in client.elements.values() if isinstance(e, ui.expansion)
                    and e._props.get("label") == "📊 Analiză & Clasament")
    old = section()
    assert old.value is True
    old.value = False
    ui_results.results_panel.refresh()
    core.loop.run_until_complete(asyncio.sleep(0))
    new = section()
    print("same:", old is new, "old:", old.value, "after refresh:", new.value)
    assert new.value is False
