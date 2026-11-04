# Optimizare certificată a variantelor pentru pool 20 — 2026-11-04

Optimizarea îmbunătățește probabilitatea exactă de cel puțin un bilet cu 4+
sau 5+ numere la același pool și același număr de variante, în configurațiile
din tabel. Nu schimbă scorurile sau selecția poolului. Poolurile implicite ale
aplicației (16/19/12) nu intră în noua etapă; peste 20 se păstrează exact
variantele vechi, cu evaluare mai rapidă a aceleiași căutări.

## Schimbarea și certificarea

Vechea estimare de swap scădea grupele unice ale biletului înlocuit inclusiv
când acestea se regăseau pe noul bilet. Astfel putea rata mutări favorabile.
Calculul exact folosește `grupe noi neacoperite − grupe unice vechi absente
din înlocuitor`. Subgrupele candidaților se construiesc o singură dată pe
pas, iar pierderile biletului vechi o singură dată pe bilet.

Căutarea veche este păstrată, cu rezultat identic și aceleași actualizări în
aceeași ordine. Rezultatul ei este punctul de plecare și etalonul noii căutări.
Un candidat este adoptat numai la pool 20, garanție sub mărimea biletului,
plafon 2..4096 și fără condiție de tip „g dacă c” peste garanție, după:

- păstrarea numărului de bilete distincte și a numerelor jucate;
- acoperire nedescrescătoare a grupelor de g și g+1 numere;
- număr nedescrescător de intersecții favorabile pentru fiecare mărime
  `j=3..6` și fiecare prag `t=3..min(j,pick)`, cu cel puțin o creștere strictă.

Pentru o extragere uniformă de `d≤6` numere din universul `N`, numărul exact
al extragerilor favorabile este
`Σ_j counts[j][t] × C(N−20,d−j)`. Toate ponderile sunt nenegative, deci
certificarea dovedește că P(3+), P(4+), P(5+) și P(6+) nu scad pentru jocurile
acceptate. Nu depinde de alegerea numerelor, scoruri sau istoric. Un test
verifică faptul că fiecare geometrie din registru are cel mult 6 numere extrase.

La pool 20 evaluatorul existent folosește cel mult `2**20` stări. Nu se
extinde această alocare exponențială la poolurile mai mari. Noul namespace de
cache se aplică numai configurațiilor care pot intra în această etapă.

## Rezultate matematice

S-au comparat ambele dimensiuni de bilet, fiecare cu plafoanele
10/32/64/100/125/152/200/250/300/404/500/750: 24 de geometrii și 36 de
comparații pe joc (biletul de 5 se verifică atât la 5/40, cât și la Joker).
Toate cele 144 de comparații de prag 3..6 sunt nedescrescătoare. Opt geometrii
schimbă biletele; zece comparații pe joc obțin o creștere. Tabelul include
toate creșterile; cazurile neschimbate și numărătorii întregi sunt în JSON.

Pool 20 și garanție 4 în fiecare rând. Procentele sunt probabilități per
extragere, pentru cel puțin o variantă, nu frecvențe simulate sau premii.
Coloana „creștere 4+” este relativă, nu puncte procentuale.

| Joc | Variante | P(4+) înainte → după | Creștere 4+ | P(5+) înainte → după |
|---|---:|---:|---:|---:|
| 6/49 | 152 | 9.569148% → 9.583042% | +0.1452% | 0.277356% → 0.277414% |
| 6/49 | 200 | 11.123080% → 11.336476% | +1.9185% | 0.359702% → 0.361389% |
| 6/49 | 250 | 12.530793% → 12.826313% | +2.3584% | 0.443527% → 0.446151% |
| 6/49 | 300 | 13.906569% → 14.039294% | +0.9544% | 0.526380% → 0.527767% |
| 6/49 | 404 | 15.564593% → 15.915899% | +2.2571% | 0.686014% → 0.689776% |
| 6/49 | 500 | 16.833359% → 17.051111% | +1.2936% | 0.829015% → 0.832026% |
| 5/40 | 500 | 20.811332% → 21.148453% | +1.6199% | 0.453785% → 0.455427% |
| Joker Urna 1 | 500 | 6.183216% → 6.312292% | +2.0875% | 0.040925% → 0.040925% |
| 5/40 | 750 | 25.865339% → 26.168852% | +1.1734% | 0.673670% → 0.675545% |
| Joker Urna 1 | 750 | 8.211521% → 8.334050% | +1.4922% | 0.061387% → 0.061387% |

La 6/49, P(6) este exact `număr variante distincte / C(49,6)` și rămâne
neschimbată la același număr de bilete. Similar, la Joker Urna 1 P(5) rămâne
`număr variante / C(45,5)`. În 5/40 se extrag 6 numere pentru un bilet de 5,
deci reorganizarea variantelor poate îmbunătăți și P(5+). Urna 2 Joker și
premiile nu sunt incluse în acest calcul.

Certificarea respinge explicit un candidat la 6/49, plafon 100, care crește
numărul grupelor de 4 acoperite dar pierde trei grupe de 3: aplicația păstrează
biletele vechi. La plafon 152 creșterea este mică, dar exactă; la plafon 250
P(4+) crește cu 2,3584% relativ și P(5+) cu 0,5917% relativ.

## Cod și validare locală

`test_large_pool_refine_exact.py` conține 318 teste: 240 cazuri cu numărare
independentă integrală înainte/după swap (toate mărimile subgrupelor),
60 geometrii cu amprente înghețate ale rezultatului vechi și verificări ale
certificatului, probabilităților, limitelor și reproductibilității.
Împreună cu cele 12 teste existente din `test_large_pool_refine.py`:
**330 trecute în 26,97 s**, Python 3.14.10, runtime/coadă/cache izolate.
Aceasta este verificarea locală a modulului, nu raportul suitei întregi.

Măsurarea timpilor a folosit trei repetări cu ordinea înainte/după alternată,
cu cache-ul construcției golit înainte de fiecare repetare. În paralel rulau
suite izolate, deci timpii sunt orientativi. Pentru 6/49/152 s-au măsurat
1,291 s → 0,616 s; pentru 5/40/404, 2,025 s → 0,923 s. Nu toate configurațiile
sunt mai rapide: căutarea și certificarea suplimentară la 6/49/250 au dus la
1,604 s → 2,146 s. Această etapă se memorează per geometrie în proces;
nu reprezintă o accelerare măsurată a întregii aplicații.

## Reproducere

Referință: commit `5b5624050e8c66625b032b12c3fb4f6c646d9379`,
`covering/large_pool.py`. Fișierul măsurat are SHA-256
`a9e5b9a6f69cfe259d869416c0a097163b2a9c464f1368854b6a437f417445eb`.
Hash-ul identifică octeții efectiv măsurați; o conversie LF/CRLF îl schimbă.
Scriptul primește explicit referința și îi salvează hash-ul.

În PowerShell, dintr-o copie izolată a proiectului:

```powershell
$old = Join-Path $env:TEMP 'large_pool_before_20261104.py'
git show 5b5624050e8c66625b032b12c3fb4f6c646d9379:covering/large_pool.py |
    Set-Content -LiteralPath $old -Encoding utf8
python scripts/analysis/bench_large_pool_certified.py --baseline-source $old `
    --output scripts/analysis/large_pool_certified_2026-11-04.json --repeats 3
python -m pytest -q test_large_pool_refine_exact.py test_large_pool_refine.py
```

Experimentul nu încarcă istorice, nu schimbă decizia benchmarkului și nu
folosește căutarea retrospectivă de tipare. Avantajul raportat privește
geometria biletelor în modelul extragerilor uniforme, fără promisiunea unui
număr mai mare de câștiguri în următoarele extrageri.
