"""Cache invalidation follows the actual, bounded change in wheel geometry."""
import copy

import pytest

from covering.large_pool import certified_refinement_applies
from loto_enterprise.core import walk_forward_adapter as wf


@pytest.mark.parametrize("game,pick", [("6/49", 6), ("5/40", 5), ("joker", 5)])
@pytest.mark.parametrize("pool", [19, 20, 21, 30])
@pytest.mark.parametrize("cap", [0, 1, 2, 152, 4096, 4097])
@pytest.mark.parametrize("mode", ["classical", "conditional", "full"])
def test_wf_separates_only_potentially_changed_wheels(game, pick, pool, cap, mode):
    guarantee = pick if mode == "full" else 4
    condition = guarantee + 1 if mode == "conditional" else guarantee
    signature = wf._wheel_sig(pool, game, guarantee, condition, cap)
    expected = pool == 20 and 1 < cap <= 4096 and mode == "classical"
    assert signature.endswith("|lp3") == expected
    if pool > 19 and not expected:
        assert signature.endswith("|lp2")
    assert certified_refinement_applies(pool, pick, guarantee, cap, condition) == expected


@pytest.mark.parametrize("game", ["6/49", "5/40", "joker"])
@pytest.mark.parametrize("restrict_max", [0, 19, 20])
def test_worker_hash_tracks_effective_pool_revision(tmp_path, monkeypatch, game, restrict_max):
    import worker

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    config = {"datasets": [{"tasks": [{
        "game_label": game, "pool_size": 20, "guarantee": 4,
        "max_variants": 152, "restrict_base_max": restrict_max,
    }]}]}
    original = copy.deepcopy(config)
    signature = wf._wheel_sig
    current = worker._pipeline_cache_key("same-history", config)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: signature(*args).replace("|lp3", "|lp2"))
    previous = worker._pipeline_cache_key("same-history", config)
    assert (current != previous) == (restrict_max != 19)
    assert config == original
