"""Keep test pipeline runs away from the user's live variation history."""

import pytest


@pytest.fixture(autouse=True)
def isolated_pool_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTO_POOL_HISTORY_FILE", str(tmp_path / "pool_history.json"))
