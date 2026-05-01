"""Smoke test: rulează 1 backtest iterație + 1 predicție live cu adaptive_feedback v2
(reset recalibrat + hard_inversion). Verifică integritatea pipeline-ului end-to-end.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

POOL_HISTORY = ROOT / "pool_history.json"
ADAPTIVE_STATE = ROOT / "adaptive_state.json"


def backup() -> dict:
    out = {}
    for src in [POOL_HISTORY, ADAPTIVE_STATE]:
        if src.exists():
            dst = src.with_suffix(src.suffix + ".smoke_backup")
            shutil.copy(src, dst)
            out[str(src)] = str(dst)
    return out


def restore(b: dict) -> None:
    for s, bk in b.items():
        Path(bk).replace(Path(s))


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST — adaptive_feedback v2")
    print("=" * 60)

    bak = backup()
    try:
        from loto_enterprise.core.backtesting import LotoBacktester
        from loto_engine import LotoEngine
        from loto_enterprise.core.adaptive_feedback import (
            classify_event,
            compute_post_draw_feedback,
            compute_temp_blacklist,
            detect_regime_mismatch,
        )

        # Test 1: clasificare evenimente
        assert classify_event(0) == "catastrophe"
        assert classify_event(1) == "underperf"
        assert classify_event(3) == "normal"
        print("[OK] classify_event")

        # Test 2: feedback diferențiat catastrophe + temp blacklist
        pool = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        actual = [40, 41, 42, 43, 44, 45]
        new_map, ev, info = compute_post_draw_feedback(
            pool, actual, {}, history=[],
            game_type="6/49", pool_size=12, streak_zero=0,
            prev_mode="normal", reset_duration=0,
        )
        assert ev == "catastrophe"
        assert info["evaluated_pool"] == sorted(pool)
        assert info["streak_zero"] == 1  # noul threshold = 3, deci NU e reset încă
        assert info["active_mode"] == "normal"
        print(f"[OK] compute_post_draw_feedback: ev={ev}, streak={info['streak_zero']}, mode={info['active_mode']}")

        # Test 3: temp blacklist după catastrofă
        bl = compute_temp_blacklist(pool, ev, universe_size=49, pool_size=12, enable_full_inversion=True)
        assert bl == set(pool)
        print(f"[OK] compute_temp_blacklist (full inversion): n={len(bl)}")

        # Test 4: detect_regime_mismatch — nu mai trigger pe varianță naturală
        hist = [{"pool_hits": h} for h in [1, 2, 1, 1, 1]]  # avg 1.2, sub baseline 1.47 dar peste cutoff 1.10
        is_mm, avg = detect_regime_mismatch(hist, "6/49", 12)
        assert is_mm is False
        print(f"[OK] regime_mismatch=False pentru avg={avg:.2f} (baseline=1.47, cutoff=1.10)")

        # Test 5: backtest 1 iterație
        print("\n[Test 5] Backtest 1 iterație (fast)...")
        t0 = time.time()
        bt = LotoBacktester(str(ROOT / "input.csv"), game_type="joker")
        n_total = len(bt.draws)
        depth = (1 / n_total) * 100.0
        preds = bt.run_retroactive_backtest(
            pool_size=12, guarantee=4, lookback_percent=20.0,
            backtest_depth_percent=depth, filter_consecutives=True,
            simulation_step=1, use_feedback=True,
        )
        elapsed = time.time() - t0
        assert len(preds) >= 1
        p = preds[0]
        print(f"[OK] Backtest 1 iter: {elapsed:.1f}s | hits_union={p.hits_union} | best_var={p.hits} | pool={sorted(p.predicted_numbers)}")

        # Test 6: live predict (engine standalone) — verifică că temp_blacklist nu pune pipeline-ul jos
        print("\n[Test 6] Live engine standalone...")
        eng = LotoEngine("joker")
        eng.load_data(str(ROOT / "input.csv"))
        # Setăm manual ca pipeline-ul să intre pe ramura "catastrophe → temp_blacklist"
        eng._adaptive_event = "catastrophe"
        eng._adaptive_mode = "normal"
        eng._temp_blacklist = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}  # 10 numere excluse
        from loto_enterprise.core.adaptive_feedback import compute_temp_blacklist as _ctb
        # Activăm `enable_adaptive_persistence=False` ca să NU se scrie state
        lines, _, _, _, _, audit = eng.run_institutional_pipeline(
            pool_size=12, guarantee=4, max_variants=5,
            smart_reduction=False, ultra_hit_optimization=False,
            enable_adaptive_persistence=False,
        )
        assert len(lines) >= 1
        hard_inv = audit.get("hard_inversion")
        if hard_inv:
            print(f"[OK] hard_inversion în audit: excluded={hard_inv['n_excluded']}")
        else:
            print("[INFO] hard_inversion absent — temp_blacklist a fost respins ca prea agresiv (safety check)")
        print(f"[OK] Live engine: variants={len(lines)} | pool size={len(eng.hard_core)} | sample={eng.hard_core[:5]}...")

        # Verificăm că pool-ul NU conține numerele blocate (dacă temp_blacklist a fost aplicat)
        if hard_inv:
            excluded = set(hard_inv["excluded"])
            overlap = set(eng.hard_core) & excluded
            assert not overlap, f"BUG: numerele blocate ({excluded}) au apărut în pool: {overlap}"
            print(f"[OK] Pool nu include niciunul din {len(excluded)} numere temp-blocate")

        print("\n" + "=" * 60)
        print("SMOKE TEST TRECUT CU SUCCES")
        print("=" * 60)
    finally:
        restore(bak)


if __name__ == "__main__":
    main()
