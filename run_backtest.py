"""
Script de rulare backtesting pentru variantele LOTO generate.

Utilizare:
    python run_backtest.py --csv istoric_loto.csv --variants "5,12,23,34,41,48|3,15,27,38,44,49"
    python run_backtest.py --csv istoric_loto.csv --file variante.json
"""

import argparse
import json
import sys
from pathlib import Path

from loto_enterprise.core.backtesting import quick_backtest, LotoBacktester


def parse_variants_string(s: str) -> list:
    """Parsează string de variante: '1,2,3,4,5,6|7,8,9,10,11,12'"""
    variants = []
    for v in s.split("|"):
        nums = [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        if nums:
            variants.append(nums)
    return variants


def load_variants_from_file(path: str) -> list:
    """Încarcă variante din JSON sau text."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Fișierul {path} nu există")
    
    content = p.read_text(encoding="utf-8")
    
    # Încercăm JSON mai întâi
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Căutăm chei comune care ar conține variante
            for key in ["variants", "variante", "lines", "linii", "alpha_lines", "results"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
    except json.JSONDecodeError:
        pass
    
    # Fallback: parsează ca text (câte o variantă pe linie)
    variants = []
    for line in content.strip().split("\n"):
        nums = [int(x.strip()) for x in line.replace(",", " ").split() if x.strip().isdigit()]
        if len(nums) >= 5:
            variants.append(nums)
    
    return variants


def main():
    parser = argparse.ArgumentParser(
        description="Backtesting pentru strategii LOTO",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemple:
  %(prog)s --csv istoric.csv --variants "1,2,3,4,5,6|10,20,30,40,45,49"
  %(prog)s --csv istoric.csv --file variante_generate.json --game 5/40
  %(prog)s --csv istoric.csv --file variante.txt --percentile 10
        """
    )
    
    parser.add_argument(
        "--csv",
        required=True,
        help="Calea către fișierul CSV cu istoricul extragerilor"
    )
    parser.add_argument(
        "--variants",
        help="Variante direct ca string (ex: '1,2,3,4,5,6|7,8,9,10,11,12')"
    )
    parser.add_argument(
        "--file",
        help="Fișier cu variante (JSON sau text)"
    )
    parser.add_argument(
        "--game",
        choices=["6/49", "5/40", "joker"],
        default="6/49",
        help="Tipul jocului (default: 6/49)"
    )
    parser.add_argument(
        "--percentile",
        type=float,
        default=20.0,
        help="Procentul din ultimele extrageri pentru evaluare (default: 20)"
    )
    parser.add_argument(
        "--output",
        help="Salvează rezultatele în fișier JSON"
    )
    
    args = parser.parse_args()
    
    # Validăm inputurile
    if not args.variants and not args.file:
        print("Eroare: Trebuie să specifici --variants sau --file")
        sys.exit(1)
    
    # Încărcăm variantele
    try:
        if args.file:
            variants = load_variants_from_file(args.file)
            print(f"Încărcate {len(variants)} variante din {args.file}")
        else:
            variants = parse_variants_string(args.variants)
            print(f"Parseate {len(variants)} variante din linia de comandă")
    except Exception as e:
        print(f"Eroare la încărcarea variantelor: {e}")
        sys.exit(1)
    
    if not variants:
        print("Eroare: Nu s-au putut încărca variante")
        sys.exit(1)
    
    # Rulăm backtesting
    try:
        print(f"\nRulare backtesting pe ultimele {args.percentile}% din extrageri...")
        print(f"Fișier CSV: {args.csv}")
        print(f"Tip joc: {args.game}")
        print(f"Variante de testat: {len(variants)}")
        
        backtester = LotoBacktester(args.csv, args.game)
        summary = backtester.evaluate_variants(variants, args.percentile)
        backtester.print_summary(summary)
        
        # Salvăm rezultatele dacă e cerut
        if args.output:
            output_data = {
                "csv_file": args.csv,
                "game_type": args.game,
                "percentile": args.percentile,
                "variants_tested": summary.variants_tested,
                "total_draws_evaluated": summary.total_draws_evaluated,
                "avg_hits_per_draw": summary.avg_hits_per_draw,
                "avg_hit_rate": summary.avg_hit_rate,
                "best_draw_hits": summary.best_draw_hits,
                "worst_draw_hits": summary.worst_draw_hits,
                "median_hits": summary.median_hits,
                "std_hits": summary.std_hits,
                "distribution": summary.distribution,
            }
            
            Path(args.output).write_text(
                json.dumps(output_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            print(f"\nRezultate salvate în: {args.output}")
        
        # Return code bazat pe performanță
        if summary.avg_hits_per_draw >= 3:
            print("✅ Performanță BUNĂ: Media >= 3 numere ghicite")
            return 0
        elif summary.avg_hits_per_draw >= 2:
            print("⚠️ Performanță MODERATĂ: Media >= 2 numere ghicite")
            return 0
        else:
            print("❌ Performanță SLABĂ: Media < 2 numere ghicite")
            return 1
            
    except FileNotFoundError as e:
        print(f"Eroare: Fișier negăsit - {e}")
        sys.exit(2)
    except Exception as e:
        print(f"Eroare la rularea backtesting: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    sys.exit(main())
