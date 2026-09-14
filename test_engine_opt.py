import time

from loto_engine import LotoEngine


def test_engine():
    engine = LotoEngine(game_type="5/40")
    success = engine.load_data("_ISTORIC/loto_5_40.csv")  # sursa reală 5/40
    if not success:
        print("Nu pot încărca _ISTORIC/loto_5_40.csv (lipsește?)")
        return

    print("Running Institutional Pipeline...")
    start_time = time.time()

    lines, p10, p90, g_range, context, audit = engine.run_institutional_pipeline(
        pool_size=12,
        guarantee=3,
        max_variants=50,
        lookback=0,
        filter_consecutives=True,
        smart_reduction=True,
    )

    print(f"Elapsed time: {time.time() - start_time:.2f}s")
    print(f"Hard Core (Nucleul Dur): {engine.hard_core}")
    print(f"Variante generate: {len(lines)}")

    # Cronologia pool-ului prin etapele pipeline-ului (NQI → Smart → POST-HOC)
    stages = audit.get("pipeline_stages", {})
    if stages:
        print("\nPipeline Stages (cronologia pool-ului):")
        for stage, pool in stages.items():
            print(f"  {stage}: {pool}")
    else:
        print("\n[EROARE] audit['pipeline_stages'] lipsește!")
        raise SystemExit(1)

    assert lines, "Pipeline-ul nu a generat nicio variantă!"
    assert engine.hard_core, "Pool-ul (hard_core) e gol!"
    assert len(engine.hard_core) <= 12, "Pool-ul depășește pool_size cerut!"
    print("\n[OK] test_engine_opt: pipeline complet, pool + variante generate.")


if __name__ == "__main__":
    test_engine()
