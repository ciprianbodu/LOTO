"""Keep tests away from the user's live pool history, UI settings and job queue."""

import functools
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_pool_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTO_POOL_HISTORY_FILE", str(tmp_path / "pool_history.json"))


@pytest.fixture(autouse=True)
def isolated_ui_settings(tmp_path, monkeypatch):
    # UI functions resolve globals in their defining modules; a facade-only
    # patch can still write the settings file via ui_runtime.
    state_file = tmp_path / "ui_state.json"
    for name in ("ui_runtime", "app_nicegui", "ui_bench", "ui_hits", "ui_results"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "UI_STATE_FILE"):
            monkeypatch.setattr(module, "UI_STATE_FILE", state_file)


@pytest.fixture(autouse=True)
def isolated_job_marks(tmp_path, monkeypatch):
    # The UI marks a finished job on its row in the station's queue database;
    # the default path is bound at import, so redirect the facade's writer.
    module = sys.modules.get("app_nicegui")
    if module is not None and hasattr(module, "mark_job_finalized"):
        import job_queue

        monkeypatch.setattr(
            module,
            "mark_job_finalized",
            functools.partial(
                job_queue.mark_job_finalized, db_path=str(tmp_path / "ui_marks.db")
            ),
        )
