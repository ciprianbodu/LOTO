# Studiu pe schemele LR cerute — 4 noiembrie 2026

Toate cele 53 de metode înregistrate au fost evaluate pe Cod 59 / pool 16 la 6/49, Cod 12 / pool 15 Joker Urna 1 și Cod 48 / pool 19 la 5/40. Schemele au 112, 118 și 930 de variante. Nicio decizie sau metodă de producție nu a fost modificată.

Protocolul este exploratoriu: arhivele au fost reutilizate anterior. Selecția nested vede numai trecutul fiecărei extrageri; acest lucru nu transformă arhiva într-un lot extern neatins. Referința frequency este metoda efectivă din registry, cu ponderare exponențială.

Tabelul arată extragerile din ultimele 30% cu cel puțin pragul indicat pe o variantă LR; sunt numărători descriptive, la cost identic pe joc. Principal: limita de consecutive 2, ca în setările implicite. Sensibilitatea fără limită este păstrată integral în JSON.

| Joc | Regulă | Extrageri | 4+ | 5+ | 6 |
|---|---|---:|---:|---:|---:|
| loto_6_49 | frequency | 705 | 54 | 2 | 0 |
| loto_6_49 | random | 705 | 54 | 1 | 0 |
| loto_6_49 | nested_pool | 705 | 40 | 1 | 0 |
| loto_6_49 | nested_ticket | 705 | 42 | 0 | 0 |
| joker | frequency | 586 | 7 | 0 | — |
| joker | random | 586 | 8 | 0 | — |
| joker | nested_pool | 586 | 8 | 1 | — |
| joker | nested_ticket | 586 | 9 | 0 | — |
| loto_5_40 | frequency | 450 | 132 | 7 | — |
| loto_5_40 | random | 450 | 122 | 6 | — |
| loto_5_40 | nested_pool | 450 | 133 | 3 | — |
| loto_5_40 | nested_ticket | 450 | 133 | 3 | — |

Corecția Holm cuprinde toate cele 3300 teste. Celule care trec ambele comparații ajustate: 0. Se publică toate metodele, inclusiv erorile și rezervele frequency; nu se promovează câștigătorul retrospectiv.

Selecția externă se îngheață pe blocuri de 100 de extrageri după minimum 400 de predicții anterioare. Memoria 200 sau întregul trecut se alege în interiorul trecutului prin patru blocuri cronologice de 50 pe ultimele 200 de predicții. Scorul de selecție este lexicografic: 4+, apoi 5+ și 6; egalitățile au reguli fixe. Limitele respectă zilele cu extrageri multiple.

La 6/49, 112 variante distincte au probabilitate jackpot 112/C(49,6), indiferent de metodă pe extrageri uniforme. La 5/40, șase hituri în pool nu pot deveni șase pe o variantă de cinci numere. Joker Urna 2 și premiile nu sunt incluse.

Baseline-urile exacte și testele folosesc formulele din [SciPy hypergeom](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.hypergeom.html) și [SciPy binomtest](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html). JSON-ul păstrează amprentele surselor și scorerilor, configurațiile, fiecare alegere nested, ratele și intervalele marginale.

Reproducere: `python scripts/analysis/lr_pool_study_2026_11_04.py --cache-dir <runtime_izolat> --workers 3`.
