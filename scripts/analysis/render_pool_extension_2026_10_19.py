"""Present recorded extension results only; no scoring, ranking or retuning."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEM = "pool_extension_2026-10-19"


def render():
    report = json.loads((HERE / f"{STEM}.json").read_bytes())
    protocol = json.loads((HERE / f"{STEM}_protocol.json").read_bytes())
    lines = [
        "# Cautare extinsa: Loto 5/40 pool 9-15 si Joker Urna 1 pool 9-19",
        "",
        "32 de definitii noi si combinatii cu Markov pentru fiecare joc, evaluate separat. Sunt hituri in pool, nu castiguri sau hituri garantate pe bilet. Istoricele sunt reutilizate retrospectiv; rezultatele nu sunt dovezi prospective.",
        "",
        "## Selectorul ales numai din trecut",
        "",
        "Ratele de mai jos sunt media indicatorilor 4+ peste toate poolurile cerute in joc, pe ultimele 20% din zile, cu limita de consecutive 2. Fiecare joc are propria familie de pooluri; nu sunt observatii independente.",
        "",
        "| Joc | Extrageri | Selector 4+ | Markov 4+ | Diferenta pp | p unilateral | Holm2 | CI95 diferenta pp |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for game, part in report["games"].items():
        primary = part["primary"]
        test = primary["paired_vs_markov"]
        low, high = test["ci95_day_cluster_bootstrap"]
        lines.append(
            f"| {part['geometry']['label']} | {primary['selector']['n']} | {primary['selector']['primary']:.6%} | {primary['markov']['primary']:.6%} | {100 * test['mean_difference']:+.6f} | {test['p_value']:.6f} | {primary['holm_across_games']:.6f} | [{100 * low:+.6f}; {100 * high:+.6f}] |"
        )
    lines += [
        "",
        "Holm2 corecteaza cele doua comparatii primare. Un prag trecut aici este un semnal retrospectiv conditionat pe traseele observate; nu promoveaza automat metoda si nu demonstreaza avantaj viitor.",
        "",
    ]
    for game, part in report["games"].items():
        meta = part["geometry"]
        primary = part["primary"]
        ks = meta["ks"]
        n = primary["selector"]["n"]
        fixed = part["candidates"]
        survivors = [
            name
            for name, row in fixed.items()
            if row["vs_markov"]["mean_difference"] > 0
            and row["holm_across_games"] < 0.05
            and row["invalid_primary_rows"] == 0
        ]
        gate = primary["selector_gate_passed"]
        lines += [
            f"## {meta['label']}",
            "",
            f"Interval: {part['first_primary']}..{part['last_primary']}. Geometrie: {meta['draw_n']} numere extrase din {meta['max_num']}, bilet de {meta['play_n']}. La 5/40 se folosesc TOATE cele sase numere extrase; la Joker numai Urna 1.",
            "",
            f"Poarta selectorului fata de Markov, dupa Holm2: **{'trecuta' if gate else 'netrecuta'}**. Definitii fixe cu diferenta pozitiva, Holm64<0,05 si fara fallback primar: **{len(survivors)}**. Configuratia scorarii in productie ramane neschimbata.",
            "",
            "| Pool | Selector 4+ | Markov 4+ | Selector% | Markov% | Referinta aleatoare 4+ | Metoda din configuratia inghetata | Acea metoda 4+ |",
            "|---:|---:|---:|---:|---:|---:|---|---:|",
        ]
        for j, k in enumerate(ks):
            a, b, c = [
                primary[name]["events4"][j]
                for name in ("selector", "markov", "current")
            ]
            lines.append(
                f"| {k} | {a}/{n} | {b}/{n} | {a / n:.4%} | {b / n:.4%} | {part['hypergeometric'][str(k)]['p_ge_4']:.4%} | {part['current_baselines'][f'k{k}']} | {c}/{n} ({c / n:.4%}) |"
            )
        lines += [
            "",
            "Metodele configuratiei au fost alese pe istoricul complet reutilizat. Coloana lor este DOAR diagnostic: aceleasi reguli topN/consecutive 2, fara penalizari sau restrangerea bazei. Nu este o reluare a selectiei istorice de productie si nici o validare independenta. Referinta aleatoare este hipergeometrica; aceasta runda testeaza primar fata de Markov.",
            "",
            "### Indicatori secundari pe acelasi traseu",
            "",
            "| Selectie | Metoda | 3+ | 4+ | 5+ | Media hiturilor | Zero hituri |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for mode, key in (("limita2", "primary"), ("topN brut", "raw")):
            for label, name in (
                ("Selector", "selector"),
                ("Markov", "markov"),
                ("Configuratie (diagnostic)", "current"),
            ):
                row = part[key][name]
                lines.append(
                    f"| {mode} | {label} | {row['rate3plus']:.6%} | {row['primary']:.6%} | {row['rate5plus']:.6%} | {row['mean_hits']:.6f} | {row['zero_rate']:.6%} |"
                )
        lines += [
            "",
            "### Alegeri pe ferestrele trecute",
            "",
            "| Origine | Interval evaluat | Definitie aleasa | Eligibile | Fallback ulterior |",
            "|---:|---|---|---:|---:|",
        ]
        for block in part["blocks"]:
            lines.append(
                f"| {block['origin']:.0%} | {block['evaluation_first']}..{block['evaluation_last']} | {block['chosen_id']} | {block['eligible_count']} | {block['selected_future_invalid_rows']} |"
            )
        lines += [
            "",
            "### Toate definitiile fixe, fara alegerea unui castigator dupa rezultat",
            "",
            "Holm32 corecteaza toate definitiile din joc, iar Holm64 toate perechile definitie-joc din aceasta extensie. P-urile sunt fata de Markov, pentru obiectivul agregat 4+.",
            "",
            "| Definitie | Rata 4+ | Diferenta pp | p brut | Holm32 | Holm64 | Fallback |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name, row in sorted(fixed.items()):
            test = row["vs_markov"]
            lines.append(
                f"| {name} | {row['limited']['primary']:.6%} | {100 * test['mean_difference']:+.6f} | {test['p_value']:.6f} | {row['holm_within_game']:.6f} | {row['holm_across_games']:.6f} | {row['invalid_primary_rows']} |"
            )
        lines += [""]
    lines += [
        "## Protocol si limite",
        "",
        "- Aceeasi banca de 16 algoritmi: patru BOCPD zilnice (hazard 1/128 sau 1/512, prior 20 sau 100), patru contraste de interactiune lag2 (shrink 20/100, amplitudine 0,25/0,5), opt calibratoare bilineare de rang (partener frequency/EWMA30, grila 3/5, memorie completa/HL256). Pentru fiecare exista un blend de rang 50% cu Markov. Media priorului este draw_n/max_num, iar rangurile se scaleaza la universul jocului.",
        "- Predictiile unei zile preced toate actualizarile acelei zile. Metodele pe istoric brut invata de la randul 0; calibratoarele invata din fluxul expertilor de la 50% din zile. Rang mediu la egalitati, fara zgomot de eticheta; fallback frequency exact si marcat. Invaliditatea se propaga numai prin parintii consumati.",
        "- Selectorul compara numai fereastra anterioara 20% la originile 70/80/90%, cu limite exacte pe zilele CSV complet. Scor 4+ agregat, egalitatile prefera Markov, apoi complexitatea mica, apoi ID. Parametrii raman fixi in blocul urmator. 70..80% este diagnostic; 80..100% evaluare retrospectiva prequentiala. Invaliditatea viitoare este numarata si nu schimba alegerea anterioara.",
        "- Pool canonic pentru FIECARE k, prin functia reala a aplicatiei. TopN brut foloseste aceleasi alegeri ca modul consecutive 2; nu exista o a doua selectie pe brut.",
        "- 99.999 de permutari paired pe zile, semn comun tuturor poolurilor si extragerilor zilei; CI95 din 2.000 de bootstrapuri pe zile. Dependenta seriala poate limita inferenta. P-urile sunt conditionate pe traseele observate, nu calibreaza toate rundele de cautare adaptiva. Nu exista holdout virgin, test prospectiv sau nul IID al intregului pipeline.",
        "- Urna 2 nu are pool 9..19 si nu face parte din aceasta cautare. Nu sunt adaugate alte state si nu sunt modificate studiile 6/49 inghetate. Nu se afirma optimalitatea globala a Markov.",
        "- Cache-ul si jurnalele raman locale in TEMP; fisierele operative protejate au amprente egale inainte/dupa. Metodele configuratiei sunt cele inregistrate la inghetarea protocolului, chiar daca ulterior configuratia statiei se schimba.",
        "",
        "Surse de inspiratie pentru BOCPD si context de calibrare: "
        + ", ".join(
            f"[sursa primara {i}]({url})"
            for i, url in enumerate(protocol["sources"], 1)
        )
        + ". Calibratorul bilinear este o ipoteza proprie, nu implementarea exacta BBQ; sursele nu valideaza predictia loto.",
        "",
        "## Verificare si reproducere",
        "",
        f"Calcul pana la JSON: {report['elapsed_seconds']:.2f}s; nu include validarea sau intreaga aplicatie.",
    ]
    validation = HERE / f"{STEM}_validation.json"
    if validation.exists():
        checked = json.loads(validation.read_bytes())
        lines += [
            "",
            f"Validator separat: {checked['checks']} de controale, {len(checked['failures'])} esecuri; CSV verificate integral: {checked['csv_rows']['candidates']} de randuri de candidati si {checked['csv_rows']['trace']} din traseu. Planul de verificari pentru scoruri/prefixe: {'executat' if checked['full_prediction_audit'] else 'omis'}. [Dovezi]({STEM}_validation.json).",
            "",
            "Statisticile, selectorul si CSV-urile sunt refacute integral. Verificarile directe ale scorurilor si poolurilor folosesc esantionul inregistrat: noua tinte canonice si trei prefixe pentru fiecare joc, toate cele 32 de definitii pe prefix, 21 de verificari directe ale expertilor si perturbarea tintei. Nu este o recalculare independenta a fiecarui scor din intregul istoric.",
        ]
    checks = HERE / f"{STEM}_checks.json"
    if checks.exists():
        verified = json.loads(checks.read_bytes())
        lines += [
            "",
            "Suita completa in copia izolata: "
            + verified["full_suite"]["summary"]
            + ".",
            "",
            "Interfata „Clasament → Istoric hits” afiseaza numai 4+ la toate jocurile, inclusiv sumarul si reluarea biletelor. Procentele pastreaza toate extragerile la numitor. Schimbarea intra la repornirea normala a aplicatiei. [Verificari]("
            + f"{STEM}_checks.json"
            + ").",
        ]
        if verified.get("station_post_research_changes_preserved"):
            lines += [
                "",
                "Amprentele operative au ramas egale pe durata cercetarii. Raportul local raport_complet.txt are ulterior alta amprenta, cu data dinaintea suitei finale, si este pastrat fara rescriere sau atribuirea cauzei. Decizia, setarile, istoricul poolurilor si Re-Bench-ul statiei coincid in continuare cu amprentele inregistrate.",
            ]
    lines += [
        "",
        "Comenzile se ruleaza din radacina proiectului, cu versiunile semnate in protocol:",
        "",
        "```powershell",
        "python scripts/analysis/pool_extension_2026_10_19.py --register",
        "python scripts/analysis/pool_extension_2026_10_19.py --run --workers 4",
        f"python -m scripts.analysis.validate_pool_extension_2026_10_19 --verify scripts/analysis/{STEM}.json --output scripts/analysis/{STEM}_validation.json",
        "python scripts/analysis/render_pool_extension_2026_10_19.py",
        "```",
        "",
    ]
    (HERE / f"{STEM}.md").write_bytes("\n".join(lines).encode("utf-8"))


if __name__ == "__main__":
    render()
