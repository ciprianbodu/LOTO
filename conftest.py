"""Keep tests away from the user's live pool history and UI settings."""

import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_pool_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTO_POOL_HISTORY_FILE", str(tmp_path / "pool_history.json"))


@pytest.fixture(autouse=True)
def isolated_ui_settings(tmp_path, monkeypatch):
    # UI functions resolve globals in their defining modules; a facade-only
    # patch can still write the persisted last_finalized_job_id via ui_runtime.
    state_file = tmp_path / "ui_state.json"
    for name in ("ui_runtime", "app_nicegui", "ui_bench", "ui_hits", "ui_results"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "UI_STATE_FILE"):
            monkeypatch.setattr(module, "UI_STATE_FILE", state_file)
