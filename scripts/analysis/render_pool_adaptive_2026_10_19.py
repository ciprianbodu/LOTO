"""Render the recorded adaptive-method study; no fitting, scoring or retuning.

Reads report/protocol/optional validation JSON only. This presentation helper is
outside the scientific source hash set and may be rerun after validation. It
never imports candidate generators, reads histories or changes production.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEM = "pool_adaptive_649_2026-10-19"
BLEND_FAMILY = "markov_new_method_rank_blend"


def _ci_text(test):
    low, high = test["ci95_day_cluster_bootstrap"]
    return f"[{100 * low:+.6f}; {100 * high:+.6f}]"


def build_markdown(
    report: dict,
    protocol: dict,
    validation: dict | None = None,
    checks: dict | None = None,
) -> str:
    """Build prose/tables from recorded numbers, with no outcome-based choices."""
    fixed = report["static_combinations"]
    if len(fixed) != report["candidate_count"]:
        raise ValueError("candidate_count does not match recorded fixed definitions")
    pure = [
        row["spec"] for row in fixed.values() if row["spec"]["family"] != BLEND_FAMILY
    ]
    blends = [
        row["spec"] for row in fixed.values() if row["spec"]["family"] == BLEND_FAMILY
    ]
    families = {spec["family"] for spec in pure}
    gate = report["selector_superiority_gate_passed"]
    conclusion = (
        "Selectorul a trecut poarta comparatiei fixate in Romania si Bulgaria. "
        "Aceasta este o constatare retrospectiva conditionata pe traseele observate; "
        "nu demonstreaza un avantaj viitor si nu promoveaza automat o metoda."
        if gate
        else "Selectorul nu a trecut poarta comparatiei fixate in ambele tari. "
        "Selectorul nu si-a demonstrat superioritatea fata de markov_lag2 "
        "pe obiectivul agregat stabilit."
    )
    survivors = [
        name
        for name, row in fixed.items()
        if all(
            row[region]["difference"] > 0
            and row[region]["holm"] < 0.05
            and row[region]["invalid_primary_rows"] == 0
            for region in ("romania", "external")
        )
    ]
    lines = [
        "# Metode adaptive noi pentru poolurile 9-16 la 6/49 - 2026-10-19",
        "",
        conclusion,
        "",
        (
            f"Banca fixata: **{report['candidate_count']} algoritmi** = "
            f"{len(pure)} definitii in {len(families)} familii noi + {len(blends)} "
            "combinatii de rang 50% cu Markov. Markov si frequency sunt controale separate. "
            f"Definitii cu diferenta pozitiva, Holm < 0,05 in ambele tari si fara "
            f"fallback pe intervalul final: **{len(survivors)}**."
        ),
        "",
        (
            "Obiectivul primar este media celor opt indicatori 4+ pentru poolurile "
            "9..16, cu limita de consecutive 2, pe ultimele 20% din zile. Sunt hituri "
            "in pool, nu pe bilet. Cele opt pooluri suprapuse nu sunt opt probe independente."
        ),
        "",
        "## Selectorul cauzal, intervalul primar",
        "",
        "| Tara | Interval | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | CI95 diferenta pp |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for country, label in (("romania", "Romania"), ("bulgaria", "Bulgaria")):
        part = report["countries"][country]
        primary = part["primary"]
        test = primary["paired"]
        lines.append(
            f"| {label} | {part['first_primary']}..{part['last_primary']} | "
            f"{primary['selector']['n']} | {primary['selector']['primary']:.6%} | "
            f"{primary['baseline']['primary']:.6%} | {test['mean_difference'] * 100:+.6f} | "
            f"{test['p_value']:.6f} | {_ci_text(test)} |"
        )
    for country, label in (("romania", "Romania"), ("bulgaria", "Bulgaria")):
        primary = report["countries"][country]["primary"]
        n = primary["selector"]["n"]
        lines += [
            "",
            f"## {label}: fiecare dimensiune de pool, limita 2",
            "",
            "Aceste descompuneri sunt descriptive; nu selecteaza alta formula sau alt pool.",
            "",
            "| Pool | Selector 4+ | Markov 4+ | Selector % | Markov % | Diferenta pp | Hazard 4+ |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for j, k in enumerate(range(9, 17)):
            x = primary["selector"]["events4"][j]
            y = primary["baseline"]["events4"][j]
            random_rate = report["hypergeometric"][str(k)]["p_ge_4"]
            lines.append(
                f"| {k} | {x}/{n} | {y}/{n} | {x / n:.4%} | {y / n:.4%} | "
                f"{(x - y) / n * 100:+.4f} | {random_rate:.4%} |"
            )
    lines += [
        "",
        "## Indicatori secundari pe acelasi traseu",
        "",
        (
            "Mediile sunt peste cele opt pooluri. Top-N brut foloseste exact aceleasi "
            "formule alese cu limita 2; nu exista o a doua selectie pe rezultatele brute."
        ),
        "",
        "| Tara | Selectie | Metoda | 4+ | 3+ | Media hiturilor | Zero hituri |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for country, label in (("romania", "Romania"), ("bulgaria", "Bulgaria")):
        part = report["countries"][country]
        for mode, key in (("limita 2", "primary"), ("top-N brut", "raw")):
            for method, kind in (("Selector", "selector"), ("Markov", "baseline")):
                row = part[key][kind]
                lines.append(
                    f"| {label} | {mode} | {method} | {row['primary']:.6%} | "
                    f"{row['rate3plus']:.6%} | {row['mean_hits']:.6f} | {row['zero_rate']:.6%} |"
                )
    lines += [
        "",
        "## Alegerile fixate din fiecare fereastra trecuta",
        "",
        (
            "Originea 70% produce numai diagnosticul 70..80%; originile 80% si 90% "
            "produc intervalul primar 80..100%. Alegerea se tine fixa pana la originea urmatoare."
        ),
        "",
        "| Tara | Origine | Interval evaluat | Formula aleasa | Eligibile | Fallback ulterior |",
        "|---|---:|---|---|---:|---:|",
    ]
    for country, label in (("romania", "Romania"), ("bulgaria", "Bulgaria")):
        for block in report["countries"][country]["blocks"]:
            lines.append(
                f"| {label} | {block['origin']:.0%} | "
                f"{block['evaluation_first']}..{block['evaluation_last']} | "
                f"{block['chosen_id']} | {block['eligible_count']} | "
                f"{block['selected_future_invalid_rows']} |"
            )
    lines += [
        "",
        "## Algoritmii cu definitie fixa: diagnostic retrospectiv",
        "",
        (
            "Tabelul pastreaza ordinea ID-urilor, fara a proclama castigatorul dupa "
            "rezultatul final. Scorurile algoritmilor online se pot schimba folosind "
            "trecutul; definitiile si parametrii lor au fost fixati in manifest. "
            f"Holm include toti cei {len(fixed)} algoritmi separat in Romania si Bulgaria; "
            "controalele sunt excluse. Diferentele sunt fata de Markov, in puncte procentuale."
        ),
        "",
        "| Algoritm | RO 4+ | Delta RO pp | Holm RO | BG 4+ | Delta BG pp | Holm BG | Fallback RO/BG |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in sorted(fixed.items()):
        ro, bg = row["romania"], row["external"]
        lines.append(
            f"| {name} | {ro['limited']['primary']:.6%} | {ro['difference'] * 100:+.6f} | "
            f"{ro['holm']:.6f} | {bg['limited']['primary']:.6%} | "
            f"{bg['difference'] * 100:+.6f} | {bg['holm']:.6f} | "
            f"{ro['invalid_primary_rows']}/{bg['invalid_primary_rows']} |"
        )
    lines += [
        "",
        "## Familii, protocol si limite",
        "",
        (
            "- Patru BOCPD: posteriori separati pe bila, hazard zilnic 1/128 sau "
            "1/512, prior beta de concentratie 20 sau 100 si medie 6/49. Sunt pastrate "
            "toate lungimile de regim, fara cap sau pruning. Doua extrageri in aceeasi "
            "zi folosesc o singura actualizare Beta-Binomial dupa ambele predictii."
        ),
        (
            "- Patru contraste lag2: interactiunea a doi antecedenti din cele 15 "
            "perechi ale extragerii sursa, diferenta simetrica (q11+q00)-(q10+q01), "
            "shrinkage 20/100 spre marginala trecuta a raspunsului, amplitudine de "
            "rang 0,25/0,5 peste Markov. Lag2 numara randuri; tinta oricarei tranzitii "
            "folosite la antrenare are data strict anterioara prognozei."
        ),
        (
            "- Opt calibratoare bilineare: rang Markov x rang frequency/EWMA30, "
            "grila 3/5 noduri, memorie totala sau half-life 256 de extrageri tinta, "
            "prior 100 observatii la fiecare nod spre 6/49. Sunt modele proprii de "
            "scoring, nu implementari ale algoritmului BBQ si nu probabilitati "
            "comune calibrate pentru sase bile extrase fara inlocuire."
        ),
        (
            "- BOCPD si contrastele invata din istoricul complet de la randul zero. "
            "Calibratoarele invata numai din fluxul de scoruri disponibil de la 50% "
            "din zile (si minimum 256 de randuri anterioare). Toata ziua este prezisa "
            "inainte de actualizarea cu oricare extragere a ei. Cele 16 blenduri "
            "aditionale dau ponderi egale rangului noii metode si rangului Markov."
        ),
        (
            "- In fiecare origine 70/80/90%, selectorul compara numai ultimele 20% "
            "din zilele CSV-ului complet. Egalitatile prefera Markov, apoi complexitatea "
            "declarata mai mica, apoi ID. O formula este exclusa daca fereastra "
            "trecuta contine fallback propriu sau al unui parinte efectiv consumat "
            "ori egalitati in cel putin 50% din pasi la oricare taietura top-K BRUTA. "
            "Frequency folosit numai ca rezerva nu invalideaza un BOCPD utilizabil. "
            "Poarta egalitatilor nu diagnosticheaza toate pozitiile accesate dupa "
            "limita de consecutive. Invaliditatea viitoare se numara, fara reselectie."
        ),
        (
            "- Scorurile sunt echivariante la relabeling, fara label jitter; "
            "departajarea top-K canonica si limita numerica de consecutive apartin "
            "evaluatorului si nu sunt invariante la o permutare arbitrara a etichetelor."
        ),
        (
            "- 99.999 de permutari paired pe zile, cu acelasi semn pentru toate "
            "poolurile si extragerile zilei; intervale bootstrap pe zile. Aceste teste "
            "presupun interschimbabilitatea etichetelor metodelor la nivelul zilei. "
            "Dependenta seriala si selectia adaptiva pot limita inferenta. P-urile "
            "sunt conditionate pe traseele observate, fara reluarea intregului "
            "selector si a cautarii sub nul."
        ),
        (
            "- Romania si Bulgaria au fost cercetate in runde anterioare. Nu exista "
            "holdout virgin, validare prospectiva sau calibrare IID a intregii cautari. "
            "Rejucarea unor tinte IID cu scorurile dependente de istoric tinute fixe "
            "nu ar fi un nul valid pentru intregul pipeline si nu a fost folosita. "
            "Bulgaria aplica acelasi algoritm de selectie pe propriul trecut; nu "
            "transfera o singura formula romaneasca fixa."
        ),
        (
            "- Referinta hipergeometrica este sansa unui pool uniform de aceeasi "
            "marime. Orice clasament retrospectiv al acestei banci nu demonstreaza "
            "optimalitatea globala a Markov, performanta viitoare sau necesitatea "
            "unei schimbari in productie."
        ),
        (
            "- Scorurile parintilor sunt reutilizate numai cu provenienta si "
            "semnaturi identice de date/cod/versiuni si aliniere exacta a tintelor. "
            "Cache-urile studiilor vechi si fisierele protejate ale aplicatiei raman intacte."
        ),
    ]
    sources = protocol.get("sources", [])
    if sources:
        links = ", ".join(
            f"[sursa primara {i}]({url})" for i, url in enumerate(sources, 1)
        )
        lines += [
            "",
            (
                f"Sursele primare inregistrate: {links}. Sunt inspiratie pentru "
                "BOCPD si context pentru binning/calibrare; nu valideaza predictia "
                "loto sau formulele proprii de contrast si calibrare bilineara."
            ),
        ]
    lines += [
        "",
        "## Verificare si reproducere",
        "",
        (
            f"Calculul pana la JSON: {report['elapsed_seconds']:.2f} s cu scoruri "
            "parinte deja in cache. Timpul nu include cautarile anterioare, "
            "validarea, exporturile ulterioare sau pornirea intregii aplicatii."
        ),
        "",
    ]
    if validation is not None:
        lines.append(
            f"Validatorul independent: {validation['checks']} verificari, "
            f"{len(validation['failures'])} esecuri, {validation['country_banks']} "
            f"arhive; verdict **{'trece' if validation['passed'] else 'esec'}**. "
            f"Detalii: [{STEM}_validation.json]({STEM}_validation.json)."
        )
    else:
        lines.append(
            "Validarea independenta nu este inca inclusa in acest raport randat."
        )
    if checks is not None:
        lines += [
            "",
            "Suita completa in copia locala izolata: "
            + checks["full_suite"]["summary"]
            + ".",
            "",
            (
                "Auditul aplicatiei: worker separat pentru toate cele 13 jocuri, "
                "UI HTTP 200, fisiere protejate intacte. CSV-urile sunt verificate "
                f"integral: {checks['csv_parity']['candidate_rows']} de randuri ale "
                f"candidatilor si {checks['csv_parity']['trace_rows']} de randuri din traseu. "
                f"Dovezi: [{STEM}_checks.json]({STEM}_checks.json), "
                f"[auditul aplicatiei]({STEM}_application_audit.json)."
            ),
        ]
    lines += [
        "",
        "Comenzile se ruleaza din radacina proiectului, cu versiunile din protocol. "
        "Rularea foloseste cache-urile vechi de scoruri, semnate si pastrate local; "
        "acestea nu sunt incluse in Git.",
        "",
        "```powershell",
        "python scripts/analysis/pool_adaptive_649_2026_10_19.py --register",
        "python scripts/analysis/pool_adaptive_649_2026_10_19.py --run --workers 4",
        (
            "python -m scripts.analysis.validate_pool_adaptive_2026_10_19 --verify "
            f"scripts/analysis/{STEM}.json --output scripts/analysis/{STEM}_validation.json"
        ),
        "python scripts/analysis/render_pool_adaptive_2026_10_19.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def render(report_path: Path | None = None, output_path: Path | None = None):
    report_path = (
        Path(report_path) if report_path is not None else HERE / f"{STEM}.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    protocol_path = report_path.with_name(report_path.stem + "_protocol.json")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    validation_path = report_path.with_name(report_path.stem + "_validation.json")
    validation = (
        json.loads(validation_path.read_text(encoding="utf-8"))
        if validation_path.exists()
        else None
    )
    checks_path = report_path.with_name(report_path.stem + "_checks.json")
    checks = (
        json.loads(checks_path.read_text(encoding="utf-8"))
        if checks_path.exists()
        else None
    )
    text = build_markdown(report, protocol, validation, checks)
    destination = (
        Path(output_path) if output_path is not None else report_path.with_suffix(".md")
    )
    destination.write_bytes(text.encode("utf-8"))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    render(args.report, args.output)
