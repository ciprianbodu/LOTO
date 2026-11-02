"""Run a declared chronological ticket experiment without production writes.

python -m scripts.evaluate_chronological --protocol protocol.json --output report.json
Protocol: dataclass fields of core.chronological_evaluation.Protocol; parameters
is an explicit list of pool/lookback/filter/wheel settings. All METHODS enter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from loto_enterprise.core.chronological_evaluation import ChronologicalEvaluator, Parameters, Protocol
from loto_enterprise.core.official_prizes import PrizeCache
from loto_enterprise.core.prospective_ledger import code_identity


def protocol_from_dict(value):
    value = dict(value)
    value["parameters"] = tuple(Parameters(**p) for p in value["parameters"])
    protocol = Protocol(**value)
    protocol.validate()
    return protocol


def lottery_for_experiment(game):
    from loto_enterprise.core.lotteries import require_lottery

    return require_lottery({"loto_6_49": "6/49", "loto_5_40": "5/40", "joker": "joker"}[game])


def money(value):
    return "indisponibil" if value is None else f"{value / 100:.2f} lei"


def rate(value):
    if value["probability"] is None:
        return "indisponibil"
    lo, hi = value["ci95"]
    return f"{100 * value['probability']:.3f}% [{100*lo:.3f}–{100*hi:.3f}]"


def markdown_report(report):
    p = report["protocol"]
    lines = ["# Evaluare cronologică a selecției", "",
        f"Joc: {p['game']}; {p['budget']} variante distincte/extragere/strategie; obiectiv variante: {p['objective']}; ținta selecției metodelor: {p['hit_target']}+ în pool (minimum 4+ la 5/40, 1/1 la Urna 2).",
        f"Metode păstrate: {len(report['registry'])}. Configurația și criteriile au fost fixate înaintea evaluării exterioare.",
        "", "Replay retrospectiv al procedurii, nu holdout prospectiv. Formulele metodelor au fost dezvoltate pe istoricul disponibil; rezultatul nu demonstrează avantaj predictiv.",
        "", "## Rezultate observate, pe toate extragerile evaluate", "",
        "| Strategie | Premiu: frecvență [95%] | 4+: frecvență [95%] | Recuperare cost, cazuri cunoscute [95%] | Cost complet | Brut estimat | Brut minus cost | Finanțe cunoscute |",
        "|---|---|---|---|---|---|---|---|"]
    for name, s in report["summary"].items():
        lines.append(f"| {name} | {rate(s['any_prize_frequency'])} | {rate(s['4plus_frequency'])} | {rate(s['cost_recovery_frequency_known_only'])} | {money(s['full_cost_bani'])} | {money(s['gross_bani'])} | {money(s['net_bani'])} | {s['financially_resolved_draws']}/{len(report['rows'])} |")
    lines += ["", "Intervalele frecvențelor sunt descriptive (extrageri puține, selecție adaptivă și posibilă dependență); nu reprezintă teste de superioritate. Toate cele trei strategii au același cost declarat pe extragere.",
        "Premiile lipsă și taxele pe bilete necunoscute rămân indisponibile. Netul este înainte de impozit, pe valorile unitare oficiale publicate; adăugarea unor câștigători ar putea schimba aceste valori. Biletele nu sunt dovedite ca jucate.",
        "", "## Geometria variantelor pentru ultimul pas (model uniform)", "",
        "| Strategie | P(cel puțin un premiu) [eroare MC 95%] | P(4+) [eroare MC 95%] | P(categoria I), exact | Cost |",
        "|---|---|---|---|---|"]
    if report["rows"]:
        last = report["rows"][-1]
        for name, s in last["probabilities"].items():
            lines.append(f"| {name} | {rate(s['any_prize'])} | {rate(s['4plus'])} | {s['jackpot_probability_exact']:.10g} | {money(last['outcomes'][name]['full_cost_bani'])} |")
    lines += ["", "Aceste probabilități descriu acoperirea pe extrageri uniforme și regulile actuale. Estimările Monte Carlo folosesc aceleași extrageri pentru strategii și un eșantion separat de cel al optimizării. Nu măsoară puterea predictivă a scorerului.",
        "Optimizarea pe universul întreg este o căutare limitată, cu obiectiv explicit, fără scoruri istorice și fără pretenție de optim global. Probabilitatea recuperării costului viitor nu este calculabilă fără distribuția premiilor viitoare; frecvența istorică a recuperării costului apare separat în JSON.",
        "", "## Urma cronologică", "", "| Țintă | Ultima dată folosită la selecție | Metodă | Pool | Cost pool | Brut pool | Net pool |", "|---|---|---|---|---|---|---|"]
    for row in report["rows"]:
        s, o = row["selection"], row["outcomes"]["pool"]
        lines.append(f"| {row['target_date']} | {s['selection_last_date']} | {s['method']} | {s['parameters']['pool_size']} | {money(o['full_cost_bani'])} | {money(o['gross_bani'])} | {money(o['net_bani'])} |")
    lines += ["", f"Protocol SHA-256: `{report['protocol_sha256']}`.",
              "JSON-ul însoțitor păstrează toate variantele, deciziile (inclusiv Holm/low_confidence), parametrii respinși și erorile metodelor.",
              "Bugetul rămâne fix; nicio pierdere nu declanșează o creștere a mizei.", ""]
    return "\n".join(lines)


def run_experiment(protocol, *, history=None, outer_draws=10, progress=None, prize_cache=None, cancel_check=None):
    root = Path(__file__).resolve().parents[1]
    source = Path(history) if history else root / lottery_for_experiment(protocol.game).csv
    cache = prize_cache or PrizeCache()
    # Exact identity lookup, local cache only. No bulk automatic network fetch.
    lookup = lambda game, day, numbers, joker: cache.get_draw(game, day, numbers, joker, fetch=False)
    evaluator = ChronologicalEvaluator(pd.read_csv(source), protocol, prize_lookup=lookup, cancel_check=cancel_check)
    report = evaluator.run(outer_draws=outer_draws, progress=progress)
    report["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    report["code"] = code_identity(root)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--outer-draws", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = protocol_from_dict(json.loads(args.protocol.read_text(encoding="utf-8-sig")))
    report = run_experiment(protocol, history=args.history, outer_draws=args.outer_draws,
        progress=lambda done, total, day: print(f"{done}/{total}: selecție înainte de {day}", flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(f"Raport: {args.output}")


if __name__ == "__main__":
    main()
