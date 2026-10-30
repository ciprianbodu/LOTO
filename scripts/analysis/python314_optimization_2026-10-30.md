# Optimizari masurate pe Python 3.14 — 30.10.2026

Referinta surselor: `44a2c6ed5a62234e44e99b472e5084e6d44659a5`.
Mediu: Windows x64, CPython 3.14.10, NumPy 2.5.3,
`D:\_BUILD\_LOTO\.venv\Scripts\python.exe`.
Lucrul si testele se fac intr-un snapshot local separat de aplicatia care ruleaza
din Google Drive. Nu se modifica metodele de selectie, datele sau setarile de joc.

## Modificari si masuratori

| Functie / caz | Referinta | Optimizat | Observatii |
| --- | ---: | ---: | --- |
| `_hit_counts`, pool 12, profil fara cache | 2,362 ms | 0,128 ms | Ruta NumPy existenta, aceleasi numaratori intregi |
| `_hit_counts`, pool 16, profil fara cache | 52,601 ms | 1,339 ms | Aproximativ 39 ori mai rapid pentru acest profil |
| `last_seen_index`, matrice 3.000 x 49 | 367,664 us | 85,7 us | Masca booleana in locul matricei de indici int64 |
| `score_gap_current`, istoricul real 6/49 | 1.106 us | 447 us | Scoruri identice cu referinta |
| `_backfill_new_fields`, 93.000 de bilete | 93,566 ms | 30,922 ms | Schema dataclass citita o data pe clasa si pe apel |

Acestea sunt mediane locale ale functiilor, nu accelerarea intregii aplicatii si
nici un efect asupra sanselor de castig. Benchmarkurile contin implementarile
anterioare ca referinte si alterneaza ordinea masuratorilor unde este cazul.
Profilul combinatoric masoara apeluri fara LRU; aplicatia refoloseste in continuare
rezultatele deja memorate.

`vector_to_scores` converteste vectorul float64 in valori Python prin `tolist()`
inainte de construirea dictionarului. Scorurile, tipurile si ordinea raman identice.
Castigul intregii functii pe vectori de 40..49 de numere este aproximativ 1,15..1,29x.

Limite si costuri:

- La `last_seen_index`, 50 x 49, mediana creste de la 6,266 la 7,611 us; se
  pastreaza o singura implementare, fara prag suplimentar pentru acest cost mic.
- Pentru 3.000 x 49, alocarile maxime urmarite de `tracemalloc` scad de la
  1.414.336 la 294.824 de octeti.
- Profilul combinatoric pe 16 numere foloseste temporar aproximativ 1,77 MB,
  fata de aproximativ 69 KB pentru bucla veche. LRU retine numai tabelul de
  numaratori, nu aceste tablouri temporare. Pool-urile de cel mult 8 numere
  pastreaza bucla Python.
- JIT si modul fara GIL nu sunt activate. Castigurile provin din reducerea
  lucrului Python repetat, masurata pe interpretul de productie 3.14.10.

## Reproducere

Din radacina proiectului, cu interpretul mediului virtual:

```powershell
python -m scripts.analysis.bench_py314_scoring --rounds 9 --loops 250
python -m scripts.analysis.bench_probability_py314 --repeats 9
python -m scripts.analysis.benchmark_py314_wf_backfill --draws 100 --variants 930 --repeats 9
python -m pytest -q --basetemp "$env:TEMP\loto314-tests" -p no:cacheprovider
python -m scripts.analysis.audit_application --output scratch/py314-application-audit.json
```

Pe Windows, directorul temporar scurt evita limita Git de lungime a caii in
testele care creeaza clone si rebase-uri imbricate.

Testele noi confrunta scorurile cu referinta, enumera independent intersectiile
fiecarei submultimi a pool-ului si verifica migrarea cache-urilor vechi, inclusiv
fabricile de valori si contextul comun al biletelor. Reviziile independente ale
celor trei modificari nu au identificat diferente in rezultatele de productie.

Auditul aplicatiei a trecut: 13 istorice, 848 comparatii de scoruri, 154 de
designuri, 26 de fluxuri de generare, 13 profile de cover complet, worker separat
pe 13 jocuri si UI HTTP 200. Fisierele protejate ale copiei testate au ramas
intacte.

Suita completa a incheiat cu 3683 teste trecute, 20 sarite si 12 esecuri,
toate in `test_launcher_git.py`, provocate de limita Windows de lungime a
caii temporare din copia izolata (`Filename too long`). Doua cazuri reproduse
pe o cale scurta au trecut fara schimbarea codului; apoi intregul modul Git
a fost reluat in `%TEMP%\l314-launch-...`: 111 trecute, 2 sarite. Prin urmare,
validarea cumulata acopera 3695 teste distincte trecute si 20 sarite, fara
esecuri ramase. Nu s-au eliminat teste si nu s-a schimbat codul lansatorului
pentru a ocoli eroarea de mediu.

Sintaxa fisierelor Python modificate si `git diff --check` trec.
Formatele persistente si cheile cache raman aceleasi deoarece rezultatele sunt
identice. Sursele se copiaza o singura data in proiect, fara rezultatele locale
Re-Bench; aplicatia activa nu se reporneste in timpul lucrului.
