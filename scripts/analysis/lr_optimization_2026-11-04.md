# Optimizarea pe schemele LR cerute — 4 noiembrie 2026

Lucrarea urmărește exact poolurile 16 la 6/49, 15 la Joker și 19 la 5/40,
pe variantele schemelor oficiale corespunzătoare. Referința este commitul
`ee3ed90b624603f03cbfa36041d78f158f86c94c`.

| Joc | Pool | Cod LR | Variante |
|---|---:|---:|---:|
| 6/49 | 16 | 59 | 112 |
| Joker, Urna 1 | 15 | 12 | 118 |
| 5/40 | 19 | 48 | 930 |

## Ce intră în aplicație

Calculul istoricului LR folosește măști poziționale memorate pe instanța
schemei, fără hash repetat al sutelor de variante. Căutarea primei variante
maxime se oprește când atinge limita matematică a intersecției. Păstrează
ordinea oficială a variantelor și maparea terminalului pe poolul crescător.

Premiile se calculează din reprezentanți reali grupați după mulțimea exactă
a numerelor nimerite, cu toate multiplicitățile, inclusiv variantele fără
premiu. Această grupare păstrează categoriile I/II de la 5/40, determinate
de primele cinci numere extrase, și bila aleasă la Joker. Identitatea
extragerii, datele incomplete, reporturile și valorile invalide sunt în
continuare verificate de validatorul existent. Dacă ordinea extragerii
5/40 nu este confirmată, se păstrează evaluarea listei integrale, inclusiv
numărătorile parțiale ale mesajului de indisponibilitate.

La pornire, implicitul Joker devine 15; celelalte două pooluri rămân 16 și
19. Celelalte setări salvate se păstrează. Istoricele salvate își păstrează
poolul propriu; nu sunt prezentate drept rezultate ale unei alte configurații.
Aplicația activă nu este repornită de această intervenție.

Nu se modifică blocurile schemelor, scorurile, deciziile din
`best_methods.json` sau listele de eligibilitate. Generatorul general de
covering design își păstrează contractul; plafoanele de variante nu devin
implicit o comandă pentru un anumit cod LR. Optimizarea de față privește
reluarea schemelor oficiale din istoricul LR și evaluarea lor separată.
Rezultatele identice nu necesită invalidarea cache-urilor existente.

## Câștigul de timp măsurat

În benchmarkul izolat, cinci repetări alternante pe 120 de rânduri de premii
pentru fiecare schemă au dat următoarele mediane:

| Schemă | Înainte | După | Accelerare |
|---|---:|---:|---:|
| Cod 59 | 0,4912 s | 0,1844 s | 2,66× |
| Cod 12 | 0,6997 s | 0,1846 s | 3,79× |
| Cod 48 | 2,9203 s | 0,1914 s | 15,26× |

Sunt măsurări ale acestor funcții, cu alte verificări rulând în paralel,
nu accelerări pretinse ale întregii aplicații. Fiecare rezultat monetar și
fiecare numărătoare au fost comparate integral cu implementarea veche.
Datele de premiu ale benchmarkului sunt sintetice, marcate ca atare.
Detalii: `lr_replay_2026_11_04.md`, `.json` și scriptul reproductibil
`bench_lr_replay_2026_11_04.py`.

## Limitele geometrice și hiturile

Probabilități exacte, pentru extrageri uniforme, ca cel puțin o variantă
a schemei să atingă pragul:

| Schemă | 4+ | 5+ | 6 |
|---|---:|---:|---:|
| Cod 59 | 7,431390688% | 0,207439800% | 0,000800926% |
| Cod 12, Urna 1 | 1,579116667% | 0,009658206% | — |
| Cod 48 | 28,274428274% | 0,819955294% | — |

La Joker sunt hiturile Urnei 1, fără condiția bilei Joker. La 5/40 sunt
șase numere extrase și cinci numere pe variantă; categoria I nu este
echivalentă cu orice intersecție de cinci.

Cod 59 atinge limitele superioare combinatorice la toate pragurile 3..6,
la același pool și același număr de variante. Cod 48 acoperă toate grupele
de patru, deci este deja optim pentru 4+. La 5+ pentru Cod 48 și 4+ pentru
Cod 12 există o distanță față de limita superioară, dar aceasta nu dovedește
existența unei scheme care să o atingă. Modificarea blocurilor ar produce
un alt sistem. Permutarea etichetelor nu schimbă probabilitățile uniforme.

Aceste rezultate provin din enumerare independentă a tuturor celor 622.592
de submulțimi poziționale ale celor trei pooluri, cu numărători întregi și
ponderi combinatorice exacte pentru completarea extragerii din afara poolului.
Raport: `lr_requested_geometry_2026_11_04.json` și scriptul cu același nume.

## Studiul selecției poolului

`lr_pool_study_2026_11_04.py` evaluează toate cele 53 de metode înregistrate,
direct pe cele trei scheme oficiale. Protocolul este fixat în script înainte
de rezultate. Scorarea fiecărei ținte folosește numai extragerile anterioare
zilei sale; nu folosește alte extrageri din aceeași zi. Rezerva `frequency`
este metoda efectiv înregistrată, cu ponderile ei, iar cazurile în care o
metodă produce scoruri inutilizabile sunt numărate explicit.

Selecția metodei este refăcută în blocuri cronologice de 100 de extrageri,
din minimum 400 de evaluări trecute. Alegerea între memoria de 200 de pași
și întregul trecut folosește patru subferestre anterioare de câte 50.
Obiectivul este lexicografic: 4+, apoi 5+ și 6, pe variantele LR; separat
se compară același selector construit pe hiturile poolului, `frequency` și
`random`. Se evaluează limita implicită de două numere consecutive și
sensibilitatea fără această limită. Celelalte restricții opționale sunt
oprite, explicit în protocol.

Istoricele au mai fost analizate în proiect. Separarea cronologică evită
folosirea viitorului în fiecare alegere simulată, dar nu transformă această
cercetare într-un test prospectiv pe date neatinse. Testele binomiale și
corecția Holm sunt diagnostice ale acestui studiu exploratoriu. Rezultatele
nu rescriu decizia de producție și nu justifică promisiuni de câștig.

Studiul complet a evaluat 5.798 de ținte istorice × 53 de metode, în ambele
moduri de consecutive, fără nicio utilizare a rezervei pentru scoruri
inutilizabile. A produs 1.650 de celule și 3.300 de teste statistice.
Minimul p ajustat Holm este 0,38287 pentru comparația binomială și 1,0
pentru comparația pereche cu `frequency`. Niciun avantaj nu este confirmat
după această corecție.

Pe ultimele 30% din ținte, cu limita implicită de două consecutive, numărul
extragerilor cu cel puțin o variantă LR la pragul indicat este:

| Joc | Extrageri | 4+ `frequency` → selecție după LR | 5+ `frequency` → selecție după LR |
|---|---:|---:|---:|
| 6/49 | 705 | 54 → 42 | 2 → 0 |
| Joker, Urna 1 | 586 | 7 → 9 | 0 → 0 |
| 5/40 | 450 | 132 → 133 | 7 → 3 |

La 6/49 nu apare niciun 6/6 pentru aceste două reguli în intervalul evaluat.
Selecția orientată direct spre LR produce rezultate mixte și pierde față
de `frequency` la 6/49 și la unele praguri 5+. Prin urmare, nu este promovată
în producție. Nici `frequency` nu este promovată prin acest studiu: este
referința fixă de comparație, iar decizia existentă rămâne intactă. Aceste
numărători sunt descriptive, nu câștiguri garantate sau bilete dovedite ca
jucate. Raportul complet publică toate metodele, ambele moduri și fiecare
alegere cronologică: `lr_pool_study_2026_11_04.json` și `.md`.

O verificare independentă a recalculat numărătorile, ferestrele, intervalele
și corecția Holm din matricele salvate, fără a relansa scorerii. A verificat
188 de blocuri de selecție și 18.392 de alegeri istorice. Rezultatul coincide
cu raportul: `lr_study_independent_review_20261104.json`.

## Validare finală

CPython 3.14.10, patru copii izolate, runtime/cache/coadă separate:
**6.561 teste trecute, 22 sărite, zero eșecuri**, în aproximativ 19 minute
și 35 de secunde. Cele 6.583 de identități pytest sunt distincte între loturi.
Au fost repartizate toate cele 205 fișiere `test_*.py`; 203 conțin cazuri
pytest, iar două sunt utilitare vechi fără funcții colectabile:
`test_integration.py` execută aserțiuni la import, iar `test_tfm_hits.py`
are un `main` manual, neexecutat de pytest. Nu sunt numărate drept teste
pytest suplimentare. Verificatorul compară identitățile și contoarele JUnit,
nu adună rulări repetate: `lr_validation_20261104.json`.

Față de referință, suita conține 845 de cazuri noi. Cele trei module noi
verifică reluarea exactă a schemelor (442), integrarea premiilor compacte
(262) și studiul cronologic (140); încă un caz verifică noul implicit Joker.
Cele 140 de teste ale studiului au trecut și pe forma finală a scriptului,
după corectarea spațierii în textul raportului. Codul aplicației și testele
livrate sunt identice octet cu octet cu cele din copiile suitei complete.

Auditul aplicației a trecut pentru 13 istorice, 848 de comparații de scoruri,
154 de designuri C/L, 26 de fluxuri și 13 profile complete. Workerul separat
a terminat toate cele 13 jocuri, iar UI a răspuns HTTP 200. Verificarea
suplimentară a paginii confirmă textul „15 la Joker”. Fișierele protejate
au rămas identice. Rapoarte: `lr_application_audit_20261104.json` și
`lr_ui_defaults_verified_20261104.json`.

Auditul integral al designurilor a verificat 277 de fișiere, 519 secțiuni,
232 de covere complete la 100%, 33 de indici LR și 53.557 de comparații
exacte fără diferențe: `lr_all_designs_audit_20261104.json`.

Validarea finală include compilarea celor 13 fișiere Python livrate și
verificarea diferențelor Git. Nu se publică baze de runtime, cache-urile
cercetării, setări locale sau modificările preexistente din `bench_results`.
