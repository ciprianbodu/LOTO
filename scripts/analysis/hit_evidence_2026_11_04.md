# Dovezi statistice pentru hituri în pool — 4 noiembrie 2026

Pe această baterie exploratorie nu există dovadă statistică ajustată pentru promovarea unei metode noi. Cele 31 de ipoteze au produs 1.488 de celule de evaluare și 2.976 de teste; niciun rezultat nu trece corecția Holm la 5%. Cel mai mic p ajustat este 1. Înaintea corecției, 25 de teste binomiale și 3 comparații pereche au p sub 0,05. Aceste selecții retrospective nu constituie dovadă de predicție.

Protocolul și grila au fost fixate în cod înaintea rulării. Scorerii includ frecvențe cumulative și pe ferestre 10/25/50/100/250/500, frecvențe inverse, EWMA pe șase timpi de înjumătățire, vechimea apariției, repetarea/evitarea ultimei extrageri, vecini numerici și controale structurale: paritate, prime/compuse, numere mici/mari, ultima cifră și centrul ultimei extrageri. Controalele „pseudomatematice” rămân exclusiv în analiza offline. Nicio metodă, excludere sau decizie de producție nu a fost modificată.

Se folosesc numai extragerile anterioare datei țintă; toate extragerile din aceeași zi sunt excluse împreună. Rankingul este cel canonic al aplicației. După 250 de extrageri inițiale, s-au evaluat 2.348 de extrageri 6/49, 1.499 la 5/40 și 1.951 Joker Urna 1. Poolurile sunt 6/12/16, 5/12/19 și respectiv 5/12/15, deci includ toate cele trei dimensiuni implicite curente: 16/19/12. Sunt evaluate pragurile 4+, 5+ și, numai unde se extrag șase bile, 6. Fișierul JSON păstrează amprentele SHA-256 ale surselor, fiecare numărătoare, ratele, intervalele de încredere și toate valorile p.

Cele două perioade cronologice (primele 70% și ultimele 30%) sunt arhive deja reutilizate în cercetarea anterioară, nu loturi externe nevăzute. Familia Holm cuprinde toate metodele, jocurile, poolurile, pragurile, perioadele și ambele teste. Nu corectează încercările din studiile istorice anterioare. Intervalele Clopper–Pearson sunt marginale, nu simultane și nu corectează selecția câștigătorului. Comparația pereche cu `frequency` este un diagnostic exploratoriu bazat pe ipoteza schimbabilității perechilor discordante; rezultatul principal este cel față de nulul uniform independent.

Baseline-ul combinatoric este suma hipergeometrică exactă. Testul binomial unilateral măsoară incompatibilitatea numărătorii cu acel nul, fără a transforma un avantaj retrospectiv în promisiune. Formulele și API-urile sunt descrise de [SciPy hypergeom](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.hypergeom.html) și [SciPy binomtest](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html).

Există două limite matematice ferme. La 6/49, pentru B variante DISTINCTE de șase numere, probabilitatea ca cel puțin una să aibă șase hituri este exact B / C(49,6), pe extrageri uniforme. Cele B evenimente jackpot sunt disjuncte; reorganizarea acelorași B variante distincte nu poate mări această probabilitate. Pentru un pool fix, sistemul complet atinge `min(hituri_pool, numere_pe_variantă)` la fiecare extragere și saturează plafonul posibil al variantelor din acel pool. La 5/40 se extrag șase bile, dar varianta conține cinci: șase hituri în pool nu înseamnă șase pe o variantă. La Joker, analiza de aici privește doar Urna 1, nu premiul care depinde de bila Joker.

Validare: 543 de teste automate au trecut pe Python 3.14.10. Formula hipergeometrică este comparată cu enumerarea independentă a tuturor extragerilor pe sute de geometrii mici; sunt verificate corecția Holm, intervalele exacte și lipsa informației din ziua țintă sau din viitor pentru fiecare dintre cele 31 de metode. Ultima rulare statistică a durat 23,37 s pe această stație, în paralel cu alte teste; nu reprezintă un benchmark general al aplicației.

Reproducere în copie izolată:

```powershell
python scripts/analysis/hit_evidence_2026_11_04.py
python -m pytest TESTE/test_hit_evidence_2026_11_04.py -q
```
