# Optimizare verificată a acoperirii și a calculelor — 4 noiembrie 2026

Schimbarea îmbunătățește șansele exacte ale variantelor în anumite configurații
cu pool 20, la același număr de variante. Ea nu demonstrează un avantaj predictiv
al numerelor selectate. Dimensiunile implicite 16/19/12, schemele oficiale LR,
metodele de scoring, decizia și costurile unitare nu sunt modificate.

## Câștigul demonstrat

Comparația folosește algoritmul anterior din commitul
`5b5624050e8c66625b032b12c3fb4f6c646d9379`, nu un generator artificial mai slab.
Probabilitățile sunt calculate prin numărători întregi ale tuturor rezultatelor
posibile ale jocului, pe modelul extragerii uniforme. Nu provin din potrivirea
unor variante pe extrageri istorice.

Exemple, toate cu pool 20 și garanție solicitată 4:

| Joc | Variante | P(4+) înainte → după | P(5+) înainte → după |
|---|---:|---:|---:|
| 6/49 | 250 | 12,530793% → 12,826313% | 0,443527% → 0,446151% |
| 5/40 | 500 | 20,811332% → 21,148453% | 0,453785% → 0,455427% |
| Joker Urna 1 | 500 | 6,183216% → 6,312292% | neschimbată |

Acestea sunt exemple, nu recomandări de a mări poolul sau bugetul. La același
buget, noul algoritm rămâne la rezultatul anterior dacă nu obține certificarea.
Se optimizează probabilitatea de a avea cel puțin o variantă peste prag.
Numărul mediu total de variante peste acel prag rămâne, în modelul uniform,
`B × P(o variantă peste prag)` la același B; acoperirea schimbă suprapunerea
evenimentelor, nu probabilitatea marginală a fiecărei variante.
Matricea completă conține 24 de geometrii, 36 de comparații pe joc și 144 de
comparații de praguri. Opt geometrii adoptă alte variante; zece comparații pe
joc au un câștig. Nu există scăderi la pragurile 3..6 din matrice.

Pentru B variante distincte de șase numere, P(6/6) = B / C(49,6): la B fix nu
poate crește prin rearanjare. La 5/40, cele șase numere extrase sunt comparate
cu variante de cinci numere; maximul pe variantă este cinci. Joker este evaluat
aici numai pe Urna 1, separat de bila Joker și de premii.

Date și reproducere: [matricea exactă](large_pool_certified_2026-11-04.json),
[scriptul de comparație](bench_large_pool_certified.py).

## Ce se schimbă în cod

Calculul vechi al câștigului unui schimb scădea și grupele care rămâneau pe
biletul nou. Noua căutare scade numai grupele acoperite exclusiv de biletul
eliminat și absente din înlocuitor. Subgrupele candidaților sunt calculate o
singură dată pe trecere, iar pierderile biletului o singură dată înaintea
comparării cu acei candidați.

Pentru compatibilitate, căutarea anterioară rămâne punctul de plecare și este
accelerată fără schimbarea variantelor. Căutarea exactă suplimentară este
eligibilă numai la pool 20, garanție sub mărimea biletului, bilet de cel mult
șase numere, plafon 2..4096 și acoperire clasică parțială. Designurile
condiționale, sistemele complete și celelalte pooluri păstrează comportamentul
anterior.

Un candidat este adoptat numai dacă numărătorile exacte nu scad la niciun prag
3..6 și la nicio mărime posibilă a intersecției pool–extragere, până la șase
numere. Numărul de variante, distinctivitatea, numerele jucate și acoperirea
grupelor g și g+1 se păstrează. Ponderile completărilor din afara poolului sunt
nenegative; de aceea comparația pe numărători dovedește nedescendența
probabilităților pentru toate jocurile înregistrate. Un test verifică limita
de șase numere extrase. Nu se afirmă aceeași proprietate pentru o geometrie
viitoare cu mai mult de șase numere extrase.

Poolul 20 permite certificarea prin `2**20` submulțimi; această alocare nu este
extinsă la pooluri mai mari. Semnătura WF trece selectiv la `lp3` în domeniul
eligibil; hash-ul workerului folosește aceeași semnătură. Cheile configurațiilor
neafectate sunt păstrate, iar cache-urile vechi nu sunt șterse.

## Timp și memorie

Calculul profilurilor exacte din `covering.probability` folosește formula
combinatorică pentru zero/un bilet. Pentru mai multe bilete, păstrează NumPy
și folosește temporare cu întregi mai mici, fără pierdere de precizie.
Numărătorile și probabilitățile rezultate sunt identice.

La pool 20 cu multe bilete, mediana măsurată a funcției a trecut de la
69,170 ms la 51,860 ms, iar vârful alocărilor urmărite a scăzut de la circa
28,31 MB la 13,64 MB. La pool 16 și 19 s-au măsurat accelerări mai mici.
Aceste măsurători ocolesc cache-ul rezultatului și folosesc 21 de repetări cu
ordine alternată. Au rulat sub încărcare concurentă; nu reprezintă accelerarea
întregii aplicații. Noua certificare a variantelor poate adăuga timp în unele
geometrii, chiar dacă funcțiile individuale sunt mai rapide.

Reproducere și toate cazurile:
[script](probability_opt_2026_11_04.py), [măsurători](probability_opt_2026_11_04.json).

## Cercetarea statistică

Grila de 31 de ipoteze include frecvențe, recență, perioade, gap-uri și
controale structurale/pseudomatematice. Cele 1.488 de celule produc 2.976 de
teste binomiale și pereche, cu corecție Holm comună. Niciun avantaj nu trece
corecția. Evaluarea folosește numai informația anterioară zilei țintă.
Arhivele au fost deja folosite în alte cercetări, deci cele două perioade
cronologice nu sunt prezentate drept loturi nevăzute. Nu s-a promovat nicio
metodă și nu s-au modificat excluderile sau decizia de producție.

[Protocol, limite și rezultate](hit_evidence_2026_11_04.md),
[toate numărătorile](hit_evidence_2026_11_04.json).

Comparația descriptivă a variantelor vechi/noi a fost făcută ulterior pe cele
trei exemple deja fixate în tabel, fără alegerea configurațiilor după rezultate.
Poolurile sunt identice la fiecare pas: top-20 canonic după frecvența anterioară,
fără limită de consecutive, cu întreaga zi țintă exclusă. Se folosesc ultimele
30% dintre extragerile eligibile după cele 250 inițiale; sunt tot date reutilizate.

| Joc, variante | Extrageri evaluate | Extrageri cu 4+ înainte → după | Câștiguri / pierderi pereche |
|---|---:|---:|---:|
| 6/49, 250 | 705 | **99 → 98** | 15 / 16 |
| 5/40, 500 | 450 | 79 → 86 | 8 / 1 |
| Joker Urna 1, 500 | 586 | 46 → 48 | 6 / 4 |

La 5+ rămân 1, 0 și respectiv 0 extrageri favorabile; la 6 sunt zero.
Scăderea retrospectivă la 6/49 este explicită: avantajul geometric pe modelul
uniform nu implică o creștere pe fiecare arhivă sau pe următoarele extrageri.
Acest tabel nu demonstrează un avantaj predictiv și nu a influențat promovarea
variantelor. [Script](compare_certified_history_2026_11_04.py),
[numărători și fiecare pas](compare_certified_history_2026_11_04.json).

## Verificare

Pe Python 3.14.10, suita completă, împărțită în trei copii izolate: **5.716
teste trecute, 22 sărite, zero eșecuri**, în 19 min 15 s timp total. Sunt
202 module de teste, inclusiv 1.505 cazuri noi: 543 statistice/combinatorice,
419 pentru profilurile exacte, 318 pentru schimburi și certificare, 225 pentru
invalidarea selectivă a cache-ului. Numărătoarea finală vine din cele trei
fișiere JUnit, fără dublarea testelor reluate separat.

Au trecut și compilarea fișierelor Python modificate, șase comparații prin
generatorul public (toate jocurile românești, scoruri egale și variate), precum
și auditul aplicației: 13 istorice, 848 comparații bench/producție, 26 fluxuri,
worker separat pentru toate cele 13 jocuri cu rezultat decodat, UI HTTP 200.
Amprentele fișierelor protejate au rămas neschimbate.

Auditul independent al tuturor designurilor a verificat **277 de fișiere,
519 designuri/secțiuni și 53.557 de numărători exacte**, fără diferențe, pentru
intersecțiile de 0..6 numere (toate extragerile posibile ale jocurilor
înregistrate). Cele 232 de covere complete au acoperire 100%; cei 33 de indici
oficiali LR și toate amprentele manifestului corespund. Designurile parțiale
nu sunt prezentate artificial drept complete.

Primele încercări de rulare în sandbox au fost incomplete: restricții MSYS,
dispozitiv SSLKEYLOGFILE injectat în mediu și pornirea serviciului UI. Nu sunt
incluse în numărătoarea finală. Rularea completă reușită și auditul UI au folosit
copii și baze temporare izolate în afara sandbox-ului, cu SSLKEYLOGFILE eliminat
numai din mediul proceselor de test. Avertismentele de depreciere existente
sunt păstrate în logurile testelor. Aplicația activă nu a fost repornită.

Dovezi: [suita completă](optimization_validation_2026-11-04.json),
[auditul designurilor](audit_all_designs_2026_11_04.json),
[scriptul auditului](audit_all_designs_2026_11_04.py),
[auditul aplicației](application_audit_20261104.json).
