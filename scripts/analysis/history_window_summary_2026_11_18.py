"""Build the compact NiceGUI snapshot from the two chronological experiments."""
import argparse
import json
from datetime import date
from pathlib import Path

from loto_enterprise.core.lotteries import GAMES


def build_summary(validation, fit, as_of):
    windows = sorted(validation["protocol"]["fractions_desc"])
    if windows != sorted(fit["protocol"]["fit_fractions_percent"]):
        raise ValueError("The two experiments must cover the same windows")
    urns = {key: lot for lot in GAMES for key in lot.bench_keys}
    val = {r["game"]: r for r in validation["games"]}
    train = {r["urn"]: r for r in fit["results"]}
    if set(val) != set(train) or set(val) != set(urns):
        raise ValueError("Both studies must cover every modelled urn")
    modes = {
        "tickets": {
            "title": "Validare după hiturile pe variantă",
            "description": "Studiul compară zilnic 9–10 metode din registru după 5+ pe o variantă, "
                           "apoi după 4+ și media hiturilor. În România s-au folosit codurile oficiale. "
                           "În străinătate s-a simulat aceeași schemă la toate cele patru procente.",
        },
        "pool": {
            "title": "Validare după hiturile în pool",
            "description": "Studiul compară zilnic 9–10 metode din registru după 5+ în pool, apoi după 4+ și media hiturilor. "
                           "Tabelul măsoară numerele extrase care au intrat în pool.",
        },
        "frequency": {
            "title": "Antrenare cu metoda frequency",
            "description": "Metoda frequency a primit doar procentul indicat din zilele anterioare. "
                           "Tabelul măsoară cea mai bună variantă oficială în România și poolul de 10 numere în străinătate.",
        },
        "mail_scorers": {
            "title": "Antrenare cu metodele din recomandarea din 16 noiembrie",
            "description": "Cele patru metode din recomandarea românească au fost fixate înaintea comparației. "
                           "Procentul stabilește cât trecut a primit fiecare metodă.",
        },
    }
    for mode in modes.values():
        mode["games"] = []
    sources = {}
    for key, lot in urns.items():
        result, fitted = val[key], train[key]
        if result["history_sha256"] != fitted["history_sha256"]:
            raise ValueError(f"Different source data for {key}")
        sources[lot.csv] = result["history_sha256"]
        single = key == lot.bench_key_urna2
        name = f"{lot.country} · {lot.name}"
        if single:
            name += " · Urna 2"
        primary, secondary = (1, 1) if single else (5, 4)
        for mode, surface in (("tickets", "ticket"), ("pool", "pool")):
            obj = result["objectives"]["pool" if single else surface]
            score = obj["holdout"]
            if single:
                measure = "top-1"
            elif mode == "tickets":
                code = result["scheme"]["code"]
                measure = f"Cod {code}" if lot.is_romanian else f"Cod {code} simulat"
            else:
                measure = f"pool {result['pool_size']}"
            modes[mode]["games"].append({
                "label": f"{name} · {measure}", "n": result["holdout_n"], "selected": obj["frozen_pct"],
                "primary": [score[str(pct)][f"{surface}_{primary}plus"] for pct in windows],
                "secondary": [score[str(pct)][f"{surface}_{secondary}plus"] for pct in windows],
            })
        for scorer, scores in fitted["methods"].items():
            mode = "frequency" if scorer == "frequency" else "mail_scorers"
            score = scores["fractions"]
            measure = "top-1" if single else (f"Cod {fitted['scheme']}" if lot.is_romanian else "pool 10")
            label = f"{name} · {measure}"
            if mode == "mail_scorers":
                label += f" · {scorer}"
            modes[mode]["games"].append({
                "label": label, "n": fitted["test_rows"], "selected": scores["selected_percent"],
                "primary": [score[str(pct)]["test_primary"] for pct in windows],
                "secondary": [score[str(pct)]["test_secondary"] for pct in windows],
            })
    return {"date": as_of.strftime("%d.%m.%Y"), "percentages": windows, "source_sha256": sources, "experiments": modes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("validation", "fit", "output"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    summary = build_summary(json.loads(args.validation.read_text(encoding="utf-8")),
                            json.loads(args.fit.read_text(encoding="utf-8")), args.as_of)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
