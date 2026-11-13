# Audit de cod și scheme reduse — 13 noiembrie 2026

Audit efectuat pe Windows, CPython 3.15.2, asupra checkout-ului de la
`9311fd9`. Testele și probele de integrare au rulat cu runtime, coadă și
cache-uri separate de aplicația stației.

## Corecții și optimizare aplicate

1. `ui_hits._decision_target_text`: mesajul pentru o decizie păstrată pe altă
   țintă indica folosirea metodei vechi inclusiv la 5+/6+. În aceste cazuri,
   `method_selector._tail_target_fallback` folosește în realitate `frequency`
   pentru poolurile afectate. Mesajul descrie acum acest comportament; la
   țintele 3/4 păstrează explicația metodei vechi.
2. `ui_results._render_cost_foreign` și raportul: la rezultate recuperate cu
   `pool_size` diferit de `hard_core`, costul sistemului complet și numărul
   efectiv din raport puteau folosi mărimea cerută. Acum folosesc numerele
   distincte din pool; valoarea veche rămâne fallback când numerele lipsesc.
   Repro: pool cerut 12, pool efectiv 6, bilet 6 — C(12,6)=924 era afișat în
   loc de C(6,6)=1. Workerul curent normalizează deja corect acest payload;
   corecția acoperă în special rezultatele vechi sau externe.
3. `ui_prizes.PoolPrizeBinding`: sosirea premiilor unei alte pagini recalcula
   toate estimările LR deja disponibile. Rezultatele se memorează acum local
   tabelului, numai pentru LR, cu verificarea contextului istoric și a
   întregului snapshot oficial. Poolul, codul, Jokerul, numărul variantelor,
   rectificările oficiale și stările de încărcare invalidează reutilizarea.
   Variantele generale mutable folosesc traseul anterior.

Proba de performanță a actualizării tabelului, cu 300 de rânduri 5/40 Cod 48,
10 publicări asincrone și mediana a trei repetări: 1,0788879 s înainte,
0,0426121 s după (25,32× în această funcție). Textele și sumele coincid.
Aceasta este o accelerare a afișării, nu o creștere a câștigurilor.

Nu se schimbă scorurile, alegerea poolului, variantele oficiale sau algoritmul
generatorului. Aceste modificări de afișare nu cer invalidarea cache-urilor
worker/WF; memoria nouă de premii există numai pe durata tabelului.

## Verificarea calculelor

- 277 fișiere, 519 designuri/secțiuni, 232 covere complete și 33 indici LR.
  Toate hashurile corespund manifestului. Enumerarea independentă a tuturor
  intersecțiilor posibile de până la șase numere a dat 53.557 comparații
  exacte, cu diferență maximă zero.
- Calculul premiilor a fost confruntat cu un oracol separat, pe 33 de scheme,
  4.950 de cazuri și 829.050 de variante: zero diferențe. Verificarea include
  Joker corect/greșit și primele cinci numere ale extragerii 5/40.
- Cele trei scheme implicite au fost reenumerate separat pe toate cele
  622.592 de submulțimi ale poolurilor lor. Numărătorile coincid cu motorul.
- Auditul aplicației: 55 de metode, 13 istorice, 880 de verificări de paritate
  a scorurilor, 26 de fluxuri, 24 de bugete, worker real pe 13 jocuri cu
  rezultat decodat și interfață HTTP 200. Fișierele protejate ale copiei au
  rămas identice după audit.

## Ce se poate optimiza la câștiguri

Probabilități exacte la extrageri uniforme, pentru cel puțin o variantă:

| Joc și schemă | Pool | Variante | 4+ | 5+ | 6 |
|---|---:|---:|---:|---:|---:|
| 6/49 Cod 59 | 16 | 112 | 7,431390688% | 0,207439800% | 0,000800926% |
| Joker Cod 12, urna 1 | 15 | 118 | 1,579116667% | 0,009658206% | — |
| 5/40 Cod 48 | 19 | 930 | 28,274428274% | 0,819955294% | — |

Cod 59 atinge limita combinatorică la fiecare prag 3..6: o altă așezare cu
același pool și același număr de variante nu poate crește aceste șanse.
Cod 48 atinge limita la 4+. Cod 12 atinge limitele la 3+ și 5. Diferența
față de o limită superioară la celelalte praguri nu dovedește existența unui
sistem mai bun. La 5/40, 5+ pe toate cele șase numere extrase nu înseamnă
automat categoria I; la Joker, tabelul privește numai urna 1.

Ținta 5+ din benchmark privește poolul. Căutarea variantelor prioritizează
garanția aleasă; aceasta nu devine automat o căutare prioritară de 5+ pe
bilet. La pragul maxim, când biletul are cât extragerea (6/49 și Joker),
șansa este fixată de numărul variantelor distincte, nu de așezarea acestora.

S-a testat extinderea acceptării locale `wheel_hitcover` la îmbunătățiri
exclusiv de 5+. Ea produce unele câștiguri în constructor, însă motorul
complet le recuperează deja în mai multe cazuri. Schimbarea constructorului
a produs și o regresie finală la pool 10, garanție 5, plafon 48: P5+
0,0006955183 → 0,0006898689 și acoperire 96,83% → 96,03%, la același număr
de variante. Modificarea nu a fost adoptată. Încercarea a 48 de candidați ca
ultimă etapă, fără schimbarea traiectoriei, nu a găsit o dominanță strictă.
Acesta este rezultatul căutării limitate, nu o dovadă generală de optimalitate.

## Compromisul Joker identificat

Certificarea probabilistică actuală protejează pragurile 3+ și mai mari,
conform contractului proiectului. Aceasta permite un compromis la premiile
mici. La pool 8, garanție 5, plafon 4, schimbarea ultimei variante
`[1,2,3,4,8]` în `[1,2,3,5,8]` crește șansele de 3+/4+, dar reduce P2+
de la 0,21076333385 la 0,20440365080.

Enumerarea separată a evenimentului „cel puțin un bilet cu exact două numere
și Joker” confirmă reducerea de la 1,044342624% la 1,015425301% (−2,77%
relativ). Evenimentul cu exact un număr și Joker crește în acest exemplu.
Interfața și raportul explică acum explicit acest compromis. Ținta de
evaluare 5+ cerută anterior în proiect rămâne neschimbată.

Pe poolul Joker implicit 15, verificările cu garanția 4/5 și plafoanele
4/10/32/64/118 nu au găsit pierderi P2 rămase în rezultatul final. Aceste
zece probe nu justifică o afirmație despre toate configurațiile Joker.
Schemele LR oficiale nu au fost modificate.

## Validare și dovezi

Regresiile noi sunt locale în `_TESTE/`. Două teste vechi ale porții
presupuneau absența oricărei extrageri după 09.11.2026. Acum folosesc istorii
sintetice și verifică ambele situații: fără extragere nouă și cu o extragere
eligibilă, urmate de apelul idempotent. Un al treilea test păstra minimul UI
vechi, fixat la 6; acum verifică eticheta cu minimul din registrul LR.
Protocolul preînregistrat, istoricul real și regulile de producție nu au
fost schimbate pentru a satisface aceste teste.

Validarea finală pe CPython 3.15.2 cuprinde toate cele 214 fișiere locale de
teste, în trei copii independente: **7.170 trecute, 25 omise, zero eșecuri**.
S-au verificat identitatea codului UI testat cu cel livrat și acoperirea
fiecărui fișier de teste exact o dată. Compilarea celor trei module UI și
`git diff --check` trec. Ultima pornire UI pe port separat a răspuns HTTP 200.
Cele 16 fișiere protejate ale stației (13 istorice, decizie și cele două
rezultate benchmark) sunt identice octet cu octet la final.

Dovezi locale:

- `scratch/audit_20261113_designs/all_designs.json` și probele Python/JSON
  pentru candidații 5+ și compromisul Joker din același director.
- `scratch/audit_2026_11_13/requested_geometry.json`.
- `scratch/audit_2026_11_13/application_integration.json`, cu logurile UI și
  worker și hashurile codului verificat.
- `scratch/audit_2026_11_13/final_verification.json`, cele trei loguri
  `verified_suite_*.log`, `production_integrity.json` și `ui_http_final.json`.
- `scratch/audit_2026_11_13/prize_oracle_20261113.py` și rezultatul JSON;
  `lr_refresh_benchmark_20261113.py` și rezultatul JSON din același director.
  Scriptul de performanță fixează blobul implementării de referință, astfel
  încât comparația să poată fi reluată și după publicarea corecției.

Aplicația activă de pe portul 8000 nu a fost repornită. Rezultatele Re-Bench,
deciziile de producție și istoricele stației nu au fost rescrise de audit.
