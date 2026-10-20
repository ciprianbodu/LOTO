"""Render recorded results only; never fit, rank candidates for deployment or retune."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEM = "pool_combinations_649_2026-10-19"


def render():
    report = json.loads((HERE / f"{STEM}.json").read_text(encoding="utf-8"))
    ro = report["countries"]["romania"]["primary"]
    ext = report["external_pooled"]
    survivors = [
        name
        for name, row in report["static_combinations"].items()
        if all(
            row[region]["difference"] > 0
            and row[region]["holm"] < 0.05
            and row[region]["invalid_primary_rows"] == 0
            for region in ("romania", "external")
        )
    ]
    lines = [
        "# Alte combinatii pentru poolurile 9-16 la 6/49 - 2026-10-19",
        "",
        "Selectorul are rezultate numeric mai bune decat Markov in aceasta evaluare, dar superioritatea nu este demonstrata statistic. Productia ramane neschimbata.",
        "",
        f"Banca fixata: **{report['candidate_count']} combinatii**, plus Markov si frequency. Selectorul a trecut pragul comparatiei in ambele regiuni: **{'da' if report['selector_superiority_gate_passed'] else 'nu'}**. Combinatii statice cu diferenta pozitiva, Holm < 0,05 in ambele regiuni si fara fallback pe intervalul final: **{len(survivors)}**.",
        "",
        "Ratele sunt media celor opt indicatori 4+ pentru poolurile 9..16, cu limita de consecutive 2. Sunt hituri in pool, nu pe bilet; cele opt pooluri suprapuse sunt evaluate impreuna, fara a le considera probe independente.",
        "",
        "## Selectorul pe ultimele 20% din zile",
        "",
        "| Regiune | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | CI95 diferenta pp |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for label, part in (("Romania", ro), ("Bulgaria", ext)):
        test = part["paired"]
        ci = [round(x * 100, 6) for x in test["ci95_day_cluster_bootstrap"]]
        lines.append(
            f"| {label} | {part['selector']['n']} | {part['selector']['primary']:.6%} | {part['baseline']['primary']:.6%} | {test['mean_difference'] * 100:+.6f} | {test['p_value']:.6f} | {ci} |"
        )
    lines += [
        "",
        "## Indicatori secundari, acelasi traseu de selectie",
        "",
        "Mediile sunt peste toate cele opt dimensiuni de pool; nu sunt rate pe bilet.",
        "",
        "| Regiune | Selectie | Metoda | 3+ | Media hiturilor | Zero hituri |",
        "|---|---|---|---:|---:|---:|",
    ]
    for label, limited, raw in (
        ("Romania", ro, report["countries"]["romania"]["raw"]),
        (
            "Bulgaria",
            ext,
            {"selector": ext["raw_selector"], "baseline": ext["raw_baseline"]},
        ),
    ):
        for mode, part in (("limita 2", limited), ("top-N brut", raw)):
            for method, key in (("Selector", "selector"), ("Markov", "baseline")):
                row = part[key]
                lines.append(
                    f"| {label} | {mode} | {method} | {row['rate3plus']:.6%} | "
                    f"{row['mean_hits']:.6f} | {row['zero_rate']:.6%} |"
                )
    lines += [
        "",
        "## Formulele alese exclusiv din trecut",
        "",
        "| Tara | Origine | Interval evaluat | Formula | Eligibile | Fallback ulterior |",
        "|---|---:|---|---|---:|---:|",
    ]
    for country, part in report["countries"].items():
        for block in part["blocks"]:
            lines.append(
                f"| {country} | {block['origin']:.0%} | {block['evaluation_first']}..{block['evaluation_last']} | {block['chosen_id']} | {block['eligible_count']} | {block['selected_future_invalid_rows']} |"
            )
    lines += [
        "",
        "## Romania, pe fiecare pool",
        "",
        "| Pool | Selector 4+ | Markov 4+ | Selector % | Markov % | Referinta aleatoare 4+ |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for j, k in enumerate(range(9, 17)):
        x = ro["selector"]["events4"][j]
        y = ro["baseline"]["events4"][j]
        n = ro["selector"]["n"]
        random_rate = report["hypergeometric"][str(k)]["p_ge_4"]
        lines.append(
            f"| {k} | {x}/{n} | {y}/{n} | {x / n:.4%} | {y / n:.4%} | {random_rate:.4%} |"
        )
    lines += [
        "",
        "## Statice: diagnostic retrospectiv",
        "",
        "Primele 12 dupa rata romaneasca sunt afisate DUPA rezultat. Nu inlocuiesc selectorul fixat anterior. Toate combinatiile sunt in CSV/JSON; Holm se aplica intregii banci separat in fiecare regiune. Aceste clasamente retrospective nu sunt dovezi prospective.",
        "",
        "| Formula | RO 4+ | Holm RO | Extern 4+ | Holm extern |",
        "|---|---:|---:|---:|---:|",
    ]
    ordered = sorted(
        report["static_combinations"].items(),
        key=lambda item: (-item[1]["romania"]["limited"]["primary"], item[0]),
    )
    for name, row in ordered[:12]:
        a = row["romania"]
        b = row["external"]
        lines.append(
            f"| {name} | {a['limited']['primary']:.6%} | {a['holm']:.6f} | {b['limited']['primary']:.6%} | {b['holm']:.6f} |"
        )
    lines += [
        "",
        "## Protocol si limite",
        "",
        "- Perechi ponderate Markov+expert, agregari robuste, RRF, medii geometrice ale pozitiilor si corectii de rang reziduale. Parametrii, descriptorii si expertii sunt fixati in manifest inainte de numararea hiturilor.",
        "- Scorurile folosesc numai date strict anterioare tintei. Consensul plat cade pe frequency si este marcat; selectorul exclude in fereastra trecuta orice formula cu fallback propriu/al parintilor ori egalitati in cel putin 50% din pasi la oricare taietura top-K BRUTA. Aceasta poarta nu diagnosticheaza toate straturile accesate dupa limita de consecutive.",
        "- Origini 70/80/90%, fereastra anterioara 20% din zilele complete ale CSV. Intervalul 70..80% este diagnostic; primarul 80..100% permite alegerea de la 90% folosind 80..90%, prin regula fixata anterior. Formula ramane fixa in interiorul blocului. Egalitatile prefera Markov, apoi complexitatea mica, apoi ID.",
        "- Top-N brut, 3+, media hiturilor si zero sunt secundare. Traseul brut foloseste aceleasi formule alese cu limita 2, fara a doua optimizare.",
        "- 99.999 de permutari paired pe zile, acelasi semn pentru toate poolurile/tragerile zilei. Extern: numai Bulgaria, conform restrangerii cerute de utilizator, cu cluster tara:data. Familia Holm pe tari are o singura ipoteza. Inferenta presupune interschimbabilitate pe zile; dependenta seriala poate limita concluziile.",
        "- P-urile sunt conditionate pe traseele observate; fara reluarea intregului selector sub nul, nu calibreaza toate alegerile adaptive si rundele de cercetare. Replicarea externa adapteaza acelasi algoritm la trecutul Bulgariei; nu transfera o singura combinatie romaneasca fixa.",
        "- Amendament de scop cerut explicit de utilizator in timpul rularii: Romania + doar Bulgaria. Germania terminase, Cehia era la scoruri si a fost oprita, iar Bulgaria nu fusese evaluata. Protocolul si runnerul initial sunt arhivate; rezultatele celorlalte tari nu intra in noul raport. Banca, ferestrele, pragurile si regulile de selectie au ramas identice.",
        "- Istoricele romanesti SI straine au fost explorate anterior. Nu exista holdout virgin, validare prospectiva sau reluare a intregii cautari pe istorice IID sintetice. Nu se revendica o calibrare IID a cautarii. Rezultatul nu demonstreaza optimalitatea globala a lui Markov si nu promoveaza automat o metoda in productie.",
        "- Scorurile vechi sunt reutilizate doar cu semnaturi identice de date/cod/versiuni si feliate exact. Cache-ul vechi ramane neatins; provenienta se pastreaza in noul cache local. Fisierele de productie protejate au amprentele in JSON.",
        "- RRF si agregarea robusta sunt ipoteze din alte domenii, fara garantii predictive pentru loto: [RRF, autorii](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf), [ForecastComb, R Journal](https://journal.r-project.org/articles/RJ-2018-052/).",
        "",
        "## Verificare si reproducere",
        "",
        f"Calculul pana la JSON in ultima reluare: {report['elapsed_seconds']:.2f} s, cu scoruri deja in cache. Nu include calcularile anterioare intrerupte sau exportul CSV si nu masoara viteza intregii aplicatii.",
        "",
    ]
    validation = HERE / f"{STEM}_validation.json"
    if validation.exists():
        checked = json.loads(validation.read_text(encoding="utf-8"))
        lines.append(
            f"Validare independenta: {checked['checks']} verificari, "
            f"{len(checked['failures'])} esecuri, {checked['country_banks']} arhive. "
            "Refacere integrala a statisticilor si a selectorului, plus noua prefixe directe pe tara; "
            f"detaliile sunt in [{STEM}_validation.json]({STEM}_validation.json)."
        )
    test_log = HERE / f"{STEM}_tests.log"
    if test_log.exists():
        last = [
            line
            for line in test_log.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if " passed" in line
        ]
        if last:
            lines += ["", "Suita completa: " + last[-1].strip() + "."]
    lines += [
        "",
        "```powershell",
        "python scripts/analysis/pool_combinations_649_2026_10_19.py --register",
        "python scripts/analysis/pool_combinations_649_2026_10_19.py --run --workers 4",
        "python scripts/analysis/validate_pool_combinations_2026_10_19.py --verify scripts/analysis/"
        + STEM
        + ".json",
        "python scripts/analysis/render_pool_combinations_2026_10_19.py",
        "```",
        "",
    ]
    (HERE / f"{STEM}.md").write_bytes("\n".join(lines).encode("utf-8"))


if __name__ == "__main__":
    render()
