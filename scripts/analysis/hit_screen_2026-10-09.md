# Teste pentru o rată de hit mai mare, 2026-10-09

## Concluzie

1. **Nicio metodă nouă nu se promovează.** Am testat 492 de combinații
   metodă × pool, în trei familii netestate până acum:
   - semnale între jocurile românești;
   - „urmărește liderul” peste cei ~490 de scoreri din 5 octombrie;
   - frecvența pe era curentă.

   Pe fiecare joc, cel mai bun rezultat din dezvoltare rămâne sub ce găsește
   aceeași căutare pe istorii pur aleatoare (p-ul căutării între 0,28 și 0,92).
   Niciun candidat înghețat nu trece Holm pe confirmare (minim 0,099 pe hituri).
2. **Extragerile nu au nicio abatere detectabilă de la aleator.** Am rulat 131
   de teste pe 14 serii (România și 10 loterii străine). 7 au p < 0,05, cât se
   așteaptă din întâmplare (6,6). Cel mai mic p e 0,0145, iar după Holm niciun
   test nu rămâne semnificativ (BH minim 0,71).
3. **Un dezechilibru de bile ar fi vizibil.** Dacă 16 numere ar ieși cu 10%
   mai des, rata 4+ a unui pool de 16 la 6/49 ar urca de la 7,96% la 10,04%.
   Testul de frecvență l-ar fi prins cu probabilitate de 82% pe istoricul
   actual. Nu l-a prins. Un avantaj de mărimea care ar conta practic ar fi
   apărut deci în baterie.
4. **Excepția de urmărit: Joker Urna 1.** Cele mai frecvente 16 numere pe tot
   istoricul prind 4+ în 7,34% din extrageri. Aceeași alegere pe istorii
   aleatoare dă în medie 5,93% (p = 0,002; Holm pe 13 serii 0,026).
   Efectul nu se comportă însă ca un avantaj stabil:
   - pool-ul ales pe prima jumătate a istoricului pierde pe a doua;
   - frecvența aplicată numai pe trecut stă la nivelul hazardului până în 2023;
   - urcă abia în 2024–2026.

   Aplicația joacă deja `frequency` la Joker, pool 16, 4+. Dacă efectul e
   real, aplicația îl folosește deja.

Rata pool-ului rămâne cea hipergeometrică: 7,96% la 6/49, 16,03% la 5/40 și
4,68% la Joker, pentru pool 16 și 4+. Nicio metodă testată până acum nu o
depășește demonstrabil.

## Date

- Istoricul de la commit-ul `38cab1e`, cu extragerile din 08-10-2026.
- Amprentele sha256 ale celor 13 CSV-uri sunt în
  `hit_screen_2026-10-09.json`, câmpul `data_sha256`. Scriptul refuză să
  scrie rezultatul dacă istoricul se schimbă în timpul rulării.
- O primă rulare a amestecat istoricul fără și cu extragerile din 08-10. Nu e
  folosită aici.

## Partea A. Bateria de aleatorism

Script: `hit_screen_2026-10-09.py`. Fiecare statistică se compară cu 2.000
de istorii sintetice uniforme, cu aceeași structură de zile.

| Test | Ce ar prinde |
|---|---|
| frecvența numerelor (χ²) | bile favorizate pe tot istoricul |
| deriva între jumătăți | un set de bile schimbat |
| număr × ziua săptămânii | aparate sau seturi diferite joia și duminica |
| număr × poziția bilei | ordinea de extragere dependentă de număr |
| repetări la 1, 2, 3 extrageri | numere care „rămân” sau „fug” |
| perechi (χ² pe C(N,2)) | numere care ies împreună |
| serii pe fiecare număr | grupări în timp |
| între jocuri (România) | legături între 6/49, 5/40 și Joker, în aceeași zi și în zile succesive |

Rezultat: 131 de teste. 7 au p < 0,05, față de 6,6 așteptate din întâmplare.

| Serie | Test | p |
|---|---|---:|
| Belgia 6/45 | repetări la 3 extrageri | 0,015 |
| 6/49 după Joker | numere comune cu extragerea Joker anterioară | 0,019 (mai puține decât la întâmplare) |
| Austria 6/45 | frecvență | 0,026 |
| Slovacia 6/49 | repetări la 2 extrageri | 0,028 |
| Slovacia 6/49 | poziția bilei | 0,035 |
| Bulgaria 6/49 | repetări la 2 extrageri | 0,049 |
| Spania 6/49 | ziua săptămânii | 0,050 |

Niciunul nu trece corecția Holm (toate 1,00) sau Benjamini-Hochberg (minim
0,71). Pe un singur joc românesc, cel mai mic p e 0,09 (Joker, ziua
săptămânii). Între jocuri, singurul p sub 0,05 arată mai puține numere comune
decât la întâmplare, deci nicio legătură de folosit.

### Dezechilibru comun între jocurile românești

Script separat: `shared_bias_2026-10-09.py`, definit înainte de calcul. Dacă
jocurile românești ar folosi aceleași bile dezechilibrate, frecvențele pe
număr ale lor ar fi corelate.

| Pereche | Zile comune | Corelație pe tot istoricul (p) | Medie pe blocuri de 100 de zile (p) |
|---|---:|---|---|
| 6/49 – 5/40 | 1.065 | 0,21 (0,11) | 0,03 (0,26) |
| 6/49 – Joker | 1.678 | 0,24 (0,06) | −0,03 (0,82) |
| 5/40 – Joker | 1.432 | 0,17 (0,14) | 0,01 (0,45) |

Nicio corelație nu e semnificativă. Pe blocuri de timp, acolo unde un
dezechilibru schimbător ar trebui să apară, media e practic zero.

### Cât de mare ar trebui să fie un dezechilibru ca să conteze

Model: 16 numere au fiecare șansa de extragere înmulțită cu (1 + e), cu
extragere ponderată fără întoarcere. Rata 4+ a acelui pool, puterea testului
de frecvență pe istoricul real și puterea testului cel mai bun posibil (pool-ul
favorizat cunoscut dinainte):

| Joc | e | 4+ pool 16 | Aleator | Câștig relativ | Puterea testului de frecvență | Puterea cu pool cunoscut |
|---|---:|---:|---:|---:|---:|---:|
| 6/49 | 0,05 | 8,88% | 7,96% | +11,6% | 0,20 | 0,51 |
| 6/49 | 0,10 | 10,04% | 7,96% | +26,2% | 0,82 | 0,98 |
| 6/49 | 0,20 | 11,90% | 7,96% | +49,5% | 1,00 | 1,00 |
| 5/40 | 0,05 | 17,51% | 16,03% | +9,2% | 0,13 | 0,48 |
| 5/40 | 0,10 | 19,16% | 16,03% | +19,6% | 0,65 | 0,96 |
| Joker | 0,05 | 5,21% | 4,68% | +11,3% | 0,18 | 0,30 |
| Joker | 0,10 | 5,81% | 4,68% | +24,1% | 0,68 | 0,76 |
| Joker | 0,20 | 7,17% | 4,68% | +53,3% | 1,00 | 1,00 |

Un avantaj de 20–26% relativ (e = 0,10) ar fi fost detectat cu probabilitate
de 65–82%, iar unul de 40–53% (e = 0,20) sigur; nu a fost. Un avantaj de ~10%
relativ (e = 0,05) poate trece neobservat. Ca să-l exploateze, o metodă ar
trebui însă să ghicească exact numerele favorizate. Chiar cu ele cunoscute
dinainte, istoricul îl confirmă în cel mult jumătate din cazuri (30–51%).

## Partea B. Familii noi de metode

Protocolul din 5 octombrie, neschimbat:
- scorul unei zile vede numai zilele anterioare;
- primele 200 de zile sunt warmup;
- dezvoltarea sunt primele 70% din zilele rămase, confirmarea ultimele 30%;
- un candidat la care locul K se decide prin egalitate în peste 50% din
  rânduri iese din selecție;
- nulul căutării: 200 de istorii sintetice pe fiecare joc, pe care se reface
  tot ecranul nou;
- pool-uri: 6/49 (6, 11, 16), 5/40 (8, 11, 16), Joker Urna 1 (8, 11, 16),
  Urna 2 (1).

Evenimentul țintă e 4+ la pool 11 și 16, respectiv 3+ sub 11. La 5/40 e
mereu 4+, iar la Urna 2 e numărul exact.

Familiile:
- **între jocuri** (24–36 de scoreri pe joc): ultima extragere, EWMA 0,02 /
  0,1 / 0,3 și ferestrele de 50 și 200 de zile ale celorlalte jocuri
  românești, numai din zilele anterioare; plus opusul fiecăruia;
- **urmărește liderul** (12): în fiecare zi, pool-ul scorerului cu cele mai
  multe hituri în ultimele 50 / 100 / 200 / 400 de zile sau cu discount
  0,99 / 0,995. Alegerea se face fie dintre frecvențe și EWMA, fie dintre toți
  cei ~490 de scoreri din 5 octombrie;
- **era curentă** (12): frecvența de la ultima schimbare detectată (χ² între
  ultimele W zile și cele 4W de dinainte; W = 50 / 100 / 200, prag 0,01 /
  0,001); plus opusul.

Testul de scurgere verifică că fiecare scor și fiecare alegere „urmărește
liderul” rămân identice când se schimbă o zi viitoare, a jocului sau a
sursei. L-am verificat injectând câte o scurgere în fiecare familie: testul
le-a prins pe toate.

### Dezvoltare față de nulul căutării

| Joc | Teste | Excluși la egalități | Cel mai bun z (hituri) | q95 nul | p căutare | Cel mai bun z la pool 16, 4+ | q95 nul | p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 6/49 | 144 | 0 | 1,89 | 3,15 | 0,846 | 1,62 | 3,07 | 0,672 |
| 5/40 | 144 | 6 | 2,61 | 3,22 | 0,279 | 2,43 | 2,94 | 0,234 |
| Joker Urna 1 | 144 | 9 | 1,94 | 3,30 | 0,816 | 1,27 | 2,93 | 0,876 |
| Joker Urna 2 | 60 | 0 | 1,55 | 3,15 | 0,905 | — | — | — |

Media z-urilor pe datele reale e între −0,28 și 0,15, cu abaterea 0,73–0,95.
Pe istoriile sintetice e 0,02, cu abaterea 0,96–0,98. Un semnal real ar
împinge distribuția reală spre dreapta; nu o face.

### Confirmare pe ultimele 30% românești

Holm pe toate cele 21 de teste înghețate.

| Joc | Candidat | Pool | Confirmare | Aleator | z hituri | Holm hituri | Holm eveniment | Extern (p) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 6/49 | x540_win200 | 6 | 20/731 = 2,74% | 1,86% | −1,07 | 1 | 1 | — |
| | ftl_bank_L100 | 11 | 19/731 = 2,60% | 1,79% | 1,81 | 0,700 | 1 | 0,574 |
| | x540_ewma0.02 | 16 | 79/731 = 10,81% | 7,96% | 1,63 | 0,889 | 0,077 | — |
| | x540_win50 | 16 | 80/731 = 10,94% | 7,96% | 1,73 | 0,795 | 0,056 | — |
| | x540_ewma0.1 | 16 | 63/731 = 8,62% | 7,96% | 0,81 | 1 | 1 | — |
| 5/40 | x649_ewma0.3 | 16 | 70/476 = 14,71% | 16,03% | −1,08 | 1 | 1 | — |
| | anti_x649_ewma0.02 | 8 | 6/476 = 1,26% | 0,95% | 0,14 | 1 | 1 | — |
| | x649_ewma0.02 | 16 | 75/476 = 15,76% | 16,03% | −0,63 | 1 | 1 | — |
| | anti_x649_ewma0.1 | 8 | 8/476 = 1,68% | 0,95% | 2,60 | 0,099 | 1 | — |
| | anti_x649_win200 | 11 | 21/476 = 4,41% | 3,85% | −0,92 | 1 | 1 | — |
| Joker U1 | ftl_freq_d0.995 | 16 | 28/605 = 4,63% | 4,68% | 0,22 | 1 | 1 | 0,080 |
| | ftl_freq_L200 | 16 | 33/605 = 5,45% | 4,68% | 1,57 | 0,929 | 1 | 0,133 |
| | ftl_freq_L50 | 16 | 30/605 = 4,96% | 4,68% | −0,18 | 1 | 1 | 0,329 |
| | x649_ewma0.02 | 8 | 24/605 = 3,97% | 3,27% | 1,56 | 0,929 | 1 | — |
| | ftl_bank_d0.99 | 11 | 2/605 = 0,33% | 0,96% | −2,24 | 1 | 1 | 0,195 |
| | x649_win200 | 16 | 30/605 = 4,96% | 4,68% | 1,65 | 0,889 | 1 | — |
| Joker U2 | xjk1_last | 1 | 32/605 = 5,29% | 5,00% | 0,33 | 1 | 1 | — |
| | ftl_freq_d0.995 | 1 | 38/605 = 6,28% | 5,00% | 1,45 | 1 | 1 | — |
| | anti_x649_ewma0.3 | 1 | 31/605 = 5,12% | 5,00% | 0,14 | 1 | 1 | — |
| | anti_xjk1_ewma0.1 | 1 | 24/605 = 3,97% | 5,00% | −1,17 | 1 | 1 | — |
| | anti_xjk1_ewma0.3 | 1 | 21/605 = 3,47% | 5,00% | −1,73 | 1 | 1 | — |

Replicarea externă folosește 6/49 din Germania, Cehia, Slovacia, Polonia,
Spania și Bulgaria, respectiv 6/45 și EuroMillions pentru Joker. Semnalele
între jocuri nu au istorice paralele în străinătate.

Rezultatul cel mai apropiat de prag este 5/40 → 6/49 la pool 16:
10,94% față de 7,96% la 4+ (Holm pe eveniment 0,056). Criteriul fixat
dinainte cere însă Holm < 0,05 pe hituri, iar acolo valoarea e 0,795. Analiza
post-hoc arată de unde vine (`hit_screen_posthoc_2026-10-09.py`):
- pool-ul stă practic numai în 1..40, iar un pool aleator de 16 numere din 1..40 ar fi
  dat 8,55% pe aceeași perioadă;
- restul vine din a doua jumătate a confirmării (8,22%, apoi 13,66%);
- dezechilibrul comun al bilelor nu se confirmă (tabelul de mai sus).

Cu o extragere mai puțin în istoric, Holm pe eveniment ieșea 0,038. Faptul
că o singură extragere mută rezultatul peste prag arată cât de fragil e.

## Partea C. Plafonul retrospectiv

Se iau cele mai frecvente 16 numere pe **tot** istoricul, deci privind înainte,
și se măsoară rata 4+ a acestui pool pe același istoric. Rezultatul se compară
cu același calcul pe 2.000 de istorii aleatoare. Câștigul aparent vine
aproape în întregime din faptul că pool-ul e ales după rezultat.

| Serie | Extrageri | Retrospectiv 4+ | Aleator (fără selecție) | Medie pe sintetice | q95 | p |
|---|---:|---:|---:|---:|---:|---:|
| România 6/49 | 2.591 | 9,96% | 7,96% | 9,73% | 10,54% | 0,343 |
| România 5/40 | 1.742 | 19,63% | 16,03% | 19,23% | 20,49% | 0,308 |
| **România Joker** | 2.194 | **7,34%** | 4,68% | 5,93% | 6,70% | **0,002** |
| Germania 6/49 | 6.565 | 9,25% | 7,96% | 9,05% | 9,58% | 0,269 |
| Cehia 6/49 | 7.624 | 8,88% | 7,96% | 8,98% | 9,46% | 0,635 |
| Slovacia 6/49 | 5.168 | 9,21% | 7,96% | 9,20% | 9,75% | 0,504 |
| Polonia 6/49 | 7.412 | 8,96% | 7,96% | 8,98% | 9,44% | 0,540 |
| Spania 6/49 | 4.195 | 9,15% | 7,96% | 9,36% | 10,01% | 0,708 |
| Bulgaria 6/49 | 703 | 10,53% | 7,96% | 11,61% | 13,24% | 0,870 |
| Austria 6/45 | 3.687 | 13,18% | 10,73% | 12,46% | 13,24% | 0,065 |
| Belgia 6/45 | 1.566 | 13,92% | 10,73% | 13,46% | 14,69% | 0,283 |
| Ungaria 6/45 | 1.852 | 13,39% | 10,73% | 13,24% | 14,31% | 0,423 |
| EuroMillions 5/50 | 1.985 | 4,89% | 3,13% | 4,18% | 4,89% | 0,059 |

Joker Urna 1 iese din șir (p = 0,002; Holm pe cele 13 serii 0,026). Pool-ul
retrospectiv este 1, 3, 5, 6, 8, 10, 17, 18, 19, 20, 24, 28, 29, 37, 40, 45.
Analiza post-hoc arată că efectul nu e stabil în timp:

| Verificare | 4+ pool 16 | Aleator |
|---|---:|---:|
| Pool ales pe primele 50%, jucat pe restul | 47/1.097 = 4,28% (p = 0,75) | 4,68% |
| Pool ales pe primele 70%, jucat pe restul | 44/659 = 6,68% (p = 0,013) | 4,68% |
| Frecvență pe tot trecutul, aplicată zi de zi, 2003–2023 | 3,2–5,5% pe perioade de 3 ani | 4,68% |
| Aceeași, 2024–2026 | 8,84% (294 de extrageri) | 4,68% |

Cele mai frecvente numere din prima jumătate a istoricului nu sunt cele din a
doua. Testul de frecvență pe tot istoricul nu vede nimic (p = 0,18). Un
dezechilibru fizic persistent al bilelor ar fi arătat altfel: același pool ar
fi câștigat pe orice tăiere. Datele se potrivesc mai bine cu o grupare
recentă, din 2024 încoace, care poate fi și întâmplătoare.

Pentru aplicație, rezultatul nu cere nicio schimbare. Decizia bench alege deja
`frequency` la Joker, pool 16, 4+ (6,2% față de 4,68% pe folds, Holm 0,125).
Dacă gruparea continuă, aplicația o prinde deja; dacă dispare, nu pierde
nimic față de alte metode. Singura cale de a afla e un test pe extrageri
viitoare, fixat dinainte, ca cel pentru `dmd_forecast`.

## Reproducere

```powershell
$env:PYTHONPATH="."
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\hit_screen_2026-10-09.py --leak
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\hit_screen_2026-10-09.py
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\shared_bias_2026-10-09.py
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\hit_screen_posthoc_2026-10-09.py
```

Ecranul complet a durat 56 de minute pe 4 nuclee. `HIT_SCREEN_M`,
`HIT_SCREEN_NULL` și `HIT_SCREEN_WORKERS` scad numărul de simulări pentru o
probă rapidă.
