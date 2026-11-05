# Scheme LR și eliminarea variantelor simple — 5 noiembrie 2026

Referință: `778c92c1e3581aff72cd2132362db8fc03b3f9a2`. Poolurile implicite
rămân 16 la 6/49 (Cod 59, 112 variante), 15 la Joker (Cod 12, 118 variante)
și 19 la 5/40 (Cod 48, 930 variante).

## Funcționalitatea eliminată

Au fost eliminate câmpurile pentru numărul variantelor simple și al biletelor,
butonul și dialogul „Bilet complet”, lista Top-N, butoanele „Arată toate” și
„Ascunde”, enumerarea separată din raport și reluarea „Bilet complet” din
istoric. Nu mai există generatorul, firele de calcul, memoria temporară,
funcțiile de dispersie sau ramurile de recoacere folosite exclusiv de această
funcție. Scripturile de analiză care depindeau de modulele șterse au fost
retrase; rapoartele lor rămân documente istorice.

Setările vechi sunt ignorate la încărcare și dispar la următoarea salvare.
Poolurile, schemele oficiale și desfășurarea lor, acoperirea, costurile și
premiile rămân disponibile. Sistemele păstrează variantele interne necesare
acestor calcule. Câmpul serializat `ticket_context` rămâne compatibil cu
istoricul salvat și cu reluarea schemelor LR; nu se invalidează cache-urile.

Testele funcției retrase au fost eliminate. Verificările poolului istoric,
ale refacerii cache-urilor vechi și ale proceselor WF au fost păstrate în
`test_wf_history_context.py`; testele algoritmilor generali de covering rămân.

## Optimizarea grupării LR

La cel puțin 256 de variante și cel mult șase numere nimerite în pool,
`scheme_prize_groups` folosește, pentru fiecare poziție, un întreg ai cărui
biți indică variantele care conțin poziția. Împărțirea după pozițiile nimerite
produce cel mult 64 de grupe. Numărul biților dă multiplicitatea, iar primul
bit indică reprezentantul real. Ordonarea după acești reprezentanți păstrează
exact ordinea primei apariții din fișierul oficial.

Indicii sunt calculați o dată pe instanța schemei. Schemele mai mici și
intersecțiile cu peste șase numere păstrează parcurgerea liniară. Rezultatele
returnate nu sunt memorate și nu împart liste mutabile între apeluri.

Loaderul sortează numerele fiecărei variante înainte de verificarea
duplicatelor: aceeași variantă cu numerele permutate nu mai poate umfla
numărul de variante sau premiile. Ordinea variantelor distincte se păstrează.

## Măsurători

Comparația folosește gruparea compactă din commitul de referință, șapte
repetări în ordine alternată și date sintetice reproductibile. Au rulat și
alte verificări CPU, deci timpii sunt orientativi. Pentru Cod 48:

| Operație | Înainte | După | Raport |
|---|---:|---:|---:|
| Gruparea a 1.200 de rânduri, cu indicii deja calculați | 0,27265 s | 0,10053 s | 2,71× |
| Grupare și validarea premiilor pentru 120 de rânduri | 0,09534 s | 0,07571 s | 1,26× |
| Primul apel pe o instanță nouă | 0,836 ms | 2,738 ms | cost inițial +1,902 ms |

Cod 59 și Cod 12 păstrează algoritmul liniar; fluctuațiile măsurate pentru
ele nu sunt atribuite unei optimizări. Raportul privește aceste funcții,
nu durata întregii aplicații sau rata de câștig. Detalii, hashuri și rezultate
identice: `lr_groups_2026_11_05.json`.

Reproducere din rădăcina proiectului, cu runtime separat:

```bash
git show 778c92c:covering/lr_schemes.py > /tmp/lr_schemes_before.py
python scripts/analysis/bench_lr_groups_2026_11_05.py \
  --baseline-source /tmp/lr_schemes_before.py \
  --output /tmp/lr_groups.json --repeats 7
```

## Dovezi de echivalență

Verificarea independentă a comparat integral grupurile pentru toate cele
63.633 de intersecții posibile cu cel mult numărul de bile extrase: 14.893
la Cod 59, 4.944 la Cod 12 și 43.796 la Cod 48. Zero diferențe de conținut,
ordine, reprezentanți sau multiplicități. Alte 59 de cazuri verifică instanțe
independente, rezultate modificate de apelant, intersecții mari și scheme
construite manual. Cele 309 teste noi acoperă toate cele 33 de scheme oficiale.

Eliminarea ramurilor de recoacere nefolosite a fost comparată cu sursa
anterioară în 119 cazuri; rezultatele și excepțiile sunt identice.

Interfața izolată a răspuns HTTP 200 și a afișat poolurile 16/15/19 și
Cod 59/12/48, fără controalele eliminate. Fluxul real configurare UI →
SQLite → worker într-un proces separat → rezultat a trecut pentru toate
cele trei jocuri, cu câte 300 de extrageri istorice. Verificarea nu a trimis
mail și nu a modificat coada sau setările unei instanțe active.

## Compatibilitatea mediului

Manifestul existent semnează 271 de fișiere de design cu CRLF și șase cu LF.
Checkoutul Linux avea LF pentru toate, deși blocurile erau identice cu HEAD.
`.gitattributes` fixează acum CRLF pentru designuri, cu cele șase excepții LF
deja declarate. Nu se schimbă blocurile, numărul variantelor sau manifestul.

Suita a expus și o problemă existentă în oprirea proceselor: o rădăcină
protejată era exclusă abia după selectarea descendenților. Un apel cu PID-ul
procesului curent putea astfel opri procesele lui auxiliare. Rădăcinile
protejate sunt acum refuzate înainte de extindere; regresia folosește un
arbore simulat. Testul unui arbore real distinge pe Linux un proces zombie
de un copil încă viu și verifică identitatea procesului înainte de curățare.

## Validare finală

CPython 3.14.8, trei copii independente, runtime/cache/coadă separate și
PowerShell 7.5.2 pentru testele lansatoarelor: **6.787 teste trecute,
16 omise, zero eșecuri**, în aproximativ 5 minute și 15 secunde.
Au fost incluse toate cele 204 fișiere de teste, cu 6.803 identități pytest
distincte și fără suprapunere între loturi. Omiterile sunt detaliate în
`lr_validation_2026_11_05.json`. Același fișier păstrează verificările
exhaustive, amprentele surselor și proba UI/worker.

Compilarea fișierelor Python modificate și `git diff --check` au trecut.
Istoricele, deciziile, blocurile oficiale și datele benchmarkului sunt
nemodificate în Git. Nicio instanță activă a utilizatorului nu a fost repornită.
