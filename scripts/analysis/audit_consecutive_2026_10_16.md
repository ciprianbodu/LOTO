# Audit consecutive — Loto 6/49, 16 octombrie 2026

Sursa: 2.593 extrageri până la 15-10-2026. Metoda efectivă pentru pool 16:
`markov_lag2`. Amprentele exacte ale istoricului și deciziei sunt în
`audit_consecutive_2026_10_16.json`. Scorarea istorică folosește numai zile
anterioare țintei, dar **metoda a fost selectată pe istoricul disponibil acum**:
reluarea este descriptivă, nu o validare independentă și nu dovedește un avantaj
predictiv. Nu s-au modificat scoreri, benchmarkuri, decizii sau istorice.

## Ce înseamnă „prea multe consecutive”

| Set uniform din 49 | Conține minimum 3 consecutive | Minimum 4 | Minimum 5 |
|---|---:|---:|---:|
| 6 numere | 4,7696% | 0,3115% | 0,0138% |
| 12 numere | 40,8207% | 8,8007% | 1,5777% |
| 16 numere | 74,4580% | 27,6166% | 7,7335% |

Probabilități exacte, prin numărare dinamică, verificată separat prin enumerare
pe 20 de geometrii mici. Un pool de 16 trebuie comparat cu alte pool-uri de 16,
nu cu biletele sau extragerile de 6 numere. În istoricul real, 123/2.593 extrageri
(4,7435%) conțin minimum 3 consecutive; reperul exact este 4,7696%.
Lungimile seriilor din toate extragerile au fost recalculate independent.

Pool-ul brut actual este `2, 3, 4, 5, 6, 8, 12, 14, 15, 16, 20, 25, 32, 33,
36, 48`. Seria maximă are 5 numere. Există 49 de scoruri distincte, iar scorurile
locurilor 16 și 17 sunt diferite: secvența nu este produsă de departajarea
numerică la egalitate. Limita activă înlocuiește 4 și 15 cu 9 și 18.

Pe 800 de prefixe (20-12-2018–15-10-2026), minimum 3 consecutive apar în 705
pool-uri (88,125%), iar minimum 4 în 321 (40,125%). Nu există nicio egalitate la
granița 16/17. Ratele depășesc media uniformă, dar pool-urile succesive se
suprapun puternic; tratarea lor ca 800 de observații independente ar exagera
evidența statistică.

Controlul descriptiv folosește 5.000 de permutări globale ale etichetelor
1–49 (seed 20261016). Fiecare permutare se aplică întregii traiectorii,
păstrând toate suprapunerile dintre pool-uri. Intervalul central 95% pentru
rata seriilor de minimum 3 este 56,25%–90,38%; p unilateral = 0,0596.
Pentru minimum 4, p = 0,1096. Acest control privește aglomerarea etichetelor,
nu capacitatea de a anticipa extrageri. Nu demonstrează un defect al scorerului
și nici un avantaj. P-ul nu este o confirmare externă și nu corectează
căutările istorice prin care a fost aleasă metoda.

## Efectul filtrului, exclusiv descriptiv

| Eveniment în pool | Fără limită | Cu maximum 2 consecutive |
|---|---:|---:|
| 4+ | 77/800 | 67/800 |
| 5+ | 12/800 | 8/800 |

Aceste rezultate nu justifică promovarea filtrului și nici alegerea inversă
ca metodă predictivă. Filtrul exprimă preferința pentru compoziția pool-ului.
În modelul extragerilor uniforme, orice pool fix de 16 are aceeași probabilitate
hipergeometrică de hit, indiferent de consecutivele sale. Hiturile din acest
tabel sunt de **pool**, nu numărul variantelor câștigătoare.

## Auditul formulei și al biletelor

- 300 de reetichetări ale istoricului păstrează exact apartenența top-16 după
  aplicarea aceleiași reetichetări. Formula nu folosește vecinătatea numerică;
  diferența maximă dintre scorurile corespunzătoare este 8,9 × 10⁻¹⁶.
- `markov_lag2` a fost recalculat independent, prin contoare întregi ale
  tranzițiilor, pe 40 de prefixe; toate clasamentele coincid.
- Departajarea numerică este deterministă, dar nu poate preveni consecutivele
  la scoruri egale. Docstringurile care afirmau evitarea degenerării au fost
  corectate; scorurile și regula de clasare rămân aceleași.
- Variantele formate dintr-un pool fără 3 consecutive nu pot introduce singure
  o astfel de secvență. Completarea „Bilet complet” păstrează pool-ul și ordinea
  clasamentului; limita se relaxează numai dacă nu există un superset admis.
  Nu a fost reprodus un defect în această logică.

## Optimizarea verificării limitei

În `_max_addable`, verificarea de capacitate folosește acum intervalele de
numere disponibile: la prima secvență prea lungă, se elimină opționalul cel
mai din dreapta. Orice completare validă trebuie să elimine un opțional din
acea secvență; alegerea din dreapta intersectează cel puțin aceleași secvențe
viitoare și nu introduce un conflict în prefixul deja rezolvat. Numerele
obligatorii sunt păstrate, iar conflictele dintre ele sunt raportate.

| Pool din 49 | Filtrul anterior | Filtrul optimizat | Accelerare |
|---|---:|---:|---:|
| 6 | 0,660 ms | 0,094 ms | 7,04× |
| 10 | 0,897 ms | 0,157 ms | 5,72× |
| 16 | 1,537 ms | 0,235 ms | 6,53× |

Aceste măsurători privesc numai filtrul, nu durata întregii aplicații. Rezultatul
selecției nu se schimbă: 78.732 cazuri de capacitate și 1.500 selecții comparate
cu implementarea anterioară sunt identice. Testele independente adaugă 19.683
capacități prin enumerare exhaustivă și 2.000 selecții lexicografice, inclusiv
relaxarea, valoarea aplicată, numerele sărite, golurile și conflictele.

Verificare țintită: 93 de teste trecute în 87,81 s, zero eșecuri, în
`test_consecutive_capacity.py`, `test_max_consecutive_run.py`,
`test_full_ticket.py` și `test_wf_generation_settings.py`.

## Reproducere

`python scripts/analysis/audit_consecutive_2026_10_16.py` reface măsurătorile
scorerului, probabilitățile exacte și verificările independente, apoi scrie
numai `audit_consecutive_2026_10_16.json` lângă script; `--output` permite un
alt fișier de destinație. Numărul prefixelor este
800, permutările sunt 5.000, iar semințele sunt fixe. Istoricul și decizia sunt
recitite la final; dacă s-au schimbat în timpul rulării, rezultatul nu se scrie.
Metoda și datele viitoare pot schimba rezultatul unei rerulări: amprentele din
JSON identifică exact această măsurătoare. `best_methods.json` este decizia
locală ignorată de Git, nu un fixture versionat: reproducerea acestei rulări
cere aceleași amprente ale deciziei și istoricului. Dacă decizia locală se
schimbă, scriptul auditează noua decizie și nu reproduce retroactiv rezultatul
vechi. Importul modulului nu rulează calcule și nu scrie raportul.

Performanța este o măsurătoare locală pe Python 3.14.8 / Windows, față de
`ranking.py` din commitul `6a158c4a06b88ddb171a2d541a6e4880bd25aa20`.
Parametri: `random.Random(1601026)`, 20 de clasamente amestecate pentru fiecare
geometrie, 20 de repetări ale lotului prin `timeit`, maximum 2 consecutive;
valoarea din tabel este timpul total împărțit la 400 de apeluri. Generatorul
aleator este avansat întâi de cele 1.500 de comparații de selecție (100 de
clasamente pentru fiecare univers 6, 10, 16, 20, 49; fiecare evaluat la pool
6, 10, 16, plafonat la univers), apoi de măsurătorile universurilor 49, 20, 16,
în această ordine. Scriptul de probă a fost temporar, în `scratch`, și nu este
livrat ca utilitar de producție; timpii nu sunt praguri garantate. Protecția
permanentă împotriva regresiilor este în `test_consecutive_capacity.py`.
