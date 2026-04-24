"""
Script de rulare predicție deterministă bazată pe frecvență și Wheeling Combinatorial (Set Cover).
Fără componente de Machine Learning.
"""

import sys
import argparse
import logging
import json
import os
from datetime import datetime
from loto_engine import LotoEngine

# Rezolvare encoding Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(
        description="Loto: Predicție matematică deterministă (Frecvență + Combinatorial Wheeling)."
    )
    parser.add_argument(
        "csv",
        nargs="?",
        default=None,
        help="Fișier CSV (implicit: input.csv / istoric_loto.csv)",
    )
    parser.add_argument(
        "--game",
        choices=("6/49", "5/40", "joker"),
        default="6/49",
        help="Tipul jocului (implicit: 6/49).",
    )
    args = parser.parse_args()

    csv_path = args.csv or "istoric_loto.csv"
    if not os.path.exists(csv_path):
        print(f"Nu s-a găsit niciun fișier CSV la calea: {csv_path}")
        return

    print("=" * 60)
    print("  SISTEM DE PREDICȚIE LOTO DETERMINIST (WHEELING)")
    print("=" * 60)
    print(f"Fișier date: {csv_path}")
    print(f"Joc: {args.game.upper()}")

    engine = LotoEngine(game_type=args.game)
    if not engine.load_data(csv_path):
        print("Eroare la încărcarea datelor.")
        return

    print(f"Extrageri încărcate: {engine.audit.get('rows_loaded', 0)}")

    def progress_cb(msg, pct):
        print(f"[{pct}%] {msg}")

    lines, p10, p90, g_range = engine.run_institutional_pipeline(progress_cb=progress_cb)

    print("\n" + "=" * 60)
    print("           REZULTATE FINALE - SISTEM COMBINATORIAL")
    print("=" * 60)

    print(f"\n🎯 NUCLEU DUR (Selectat pe baza frecvenței celei mai mari):")
    print(f"   {engine.hard_core}")

    print(f"\n🎲 VARIANTE OPTIME (Set Cover Guarantee=4):")
    for i, line in enumerate(lines, 1):
        print(f"   V{i}: {line}")

    out_name = f"predictie_{args.game.replace('/', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "game": args.game,
        "csv_path": csv_path,
        "hard_core": engine.hard_core,
        "variants": lines
    }
    try:
        with open(out_name, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Rezultate salvate în: {out_name}")
    except OSError as e:
        print(f"\n⚠️ Nu s-a putut salva JSON: {e}")

if __name__ == "__main__":
    main()
