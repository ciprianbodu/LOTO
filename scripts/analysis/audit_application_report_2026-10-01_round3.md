# Audit aplicatie si hituri — 1 octombrie 2026, runda 3

## Rezultat

Corectiile elimina explicatii ambigue din UI si adauga numararea explicita a
hiturilor de 5+ pe pool si pe cel putin un bilet. Coverele clasice complete de
4 sunt optimizate automat pentru 5+, fara cresterea numarului de variante sau
pierderea vreunei componente a profilului exact de hituri. Worker v10 si WF
v29 invalideaza geometria veche; o pornire normala si o generare noua sunt
suficiente. Setarea tinta 3/4 si pool-ul utilizatorului sunt pastrate.

Auditul logicii de scoring si replay-ul temporal nu au identificat un avantaj
predictiv stabil care sa justifice promovarea unei metode noi. Scorurile sunt
ranking-uri empirice, nu probabilitati ale numerelor urmatoarei extrageri.

## De ce 4 si mai ales 5 sunt rare

Pentru un pool fix de 16, probabilitatea unei extrageri uniforme de a contine
cel putin t numere din pool se calculeaza hipergeometric:

`P(H >= t) = sum(C(16,h) * C(N-16,d-h), h=t..d) / C(N,d)`.

Probabilitatea pe bilete se calculeaza exact prin toate intersectiile posibile
cu pool-ul, numarand evenimentul «cel putin un bilet», fara a considera biletele
independente. Garantiile si numarul de bilete din tabel sunt ale jobului local
2: pool 16, garantie clasica 4, fara plafon de variante.

| Joc | P(4+ in pool / pe bilet) | P(5+ in pool) | P(5+ pe bilet), dupa optimizare |
|---|---:|---:|---:|
| 6/49 | 7,96000% | 1,08806% | 0,32487% |
| 5/40: 6 extrase, 5 jucate | 16,02655% | 2,93978% | 0,39970% |
| Joker Urna 1 | 4,67752% | 0,35752% | 0,03765% |

Garantie 4 cu acoperire completa inseamna: daca pool-ul contine cel putin 4
numere extrase, exista un bilet cu cel putin 4. Nu inseamna ca cinci numere
prinse in pool ajung obligatoriu impreuna pe un bilet. La 6/49, cele 195
variante acopereau 1151 din cele 4368 submultimi de cinci; acum acopera 1158.
Pentru cinci numere in pool, restul submultimilor nu sunt garantate pe un bilet.
O garantie clasica completa de 5 pe pool 16 cu bilete de 6 cere macar
`ceil(C(16,5) / C(6,5)) = 728` variante, ca limita inferioara; aceasta nu este
o constructie si nu dovedeste ca 728 sunt suficiente.

In modelul uniform, sansa noua de 5+ la 6/49 corespunde unei medii de aproximativ
308 extrageri intre evenimente. Media nu este o perioada fixa, un termen de
asteptare sau o predictie. Istoricul nu schimba sansa urmatoarei extrageri daca
extragerile sunt uniforme si independente. Jokerul din Urna 2 este separat;
tabelul nu estimeaza probabilitatea unui premiu Joker comun.

Cache-urile WF v28 ale rezultatului analizat confirma diferenta:

| Joc | Extrageri evaluate | 4+ in pool / pe bilet | 5+ in pool | 5+ pe bilet |
|---|---:|---:|---:|---:|
| 6/49 | 776 | 72 / 72 | 12 | 2 |
| 5/40 | 522 | 85 / 85 | 27 | 3 |
| Joker Urna 1 | 657 | 29 / 29 | 2 | 0 |

Sunt rezultate ale geometriei vechi si ale unei decizii fixe curente, citite
fara rescrierea cache-ului. Nu sunt o validare externa a alegerii scorerului.
Joker Urna 2 avea 46 potriviri din 657. Ultima extragere, 01-10-2026, nu a
produs hit predictiv Urna 2; comparatia dupa antrenare pe acelasi rand avea o
potrivire si era etichetata gresit ca hit predictiv.

## Corectii UI si raport

- Regula tintei este descrisa per joc: 3+/4+ la 6/49 si Joker Urna 1, minimum
  4+ la 5/40, top-1 exact la Urna 2. Selectia dupa 3+ nu optimizeaza direct
  4+/5+; selectia dupa 4+ nu optimizeaza direct 5+.
- 5/40 are sase numere extrase si cinci pe varianta. Plafonul garantiei este
  explicat prin cele cinci numere jucate.
- Comparatia Urna 2 cu ultima extragere este descrisa explicit ca potrivire
  dupa antrenare; termenii «hit predictiv» si «prezis» au fost eliminati.
- Wilson z=1 este descris ca scor euristic de clasare, nu interval de incredere
  de 95% sau dovada de avantaj predictiv. Algoritmul deciziei ramane acelasi.
- Probabilitatile exacte 5+ apar separat pentru pool si variante. Sumarul WF
  numara extragerile distincte, nu toate biletele castigatoare de la aceeasi
  extragere. Un panou pliabil arata date si intervale distincte 4+/5+ pe bilet.

## Optimizarea geometrica la acelasi cost

`covering/higher_hits.py` inlocuieste bilete numai cand toate submultimile de
patru acoperite exclusiv de biletul vechi raman acoperite. Candidatul trebuie
sa imbunatateasca strict profilul 5+ la o intersectie posibila cu extragerea,
fara scadere la niciun prag sau marime a intersectiei. Dupa maparea canonica,
se recalculeaza si verifica intregul profil exact, printr-un algoritm diferit.
Numarul de variante distincte si pool-ul raman identice.

Limite: pool <=16, pick 5/6, draw_n=6, maximum 512 bilete, doua treceri, cel mult
16384 inlocuiri eligibile evaluate, cache-uri de geometrie in memorie limitate.
Ordinea pool-ului este scor descrescator si, la egalitate, numarul mai mare.
Nu se foloseste istoricul in cautare. Ruta automata clasica fara plafon si
celelalte metode explicite complete primesc optimizarea; explicit greedy si
lotto conditional raman neschimbate. Bugetele pozitive pastreaza hitcover-ul
optimizat din rundele precedente.

La draw_n=pick=5, P(5) este exact numarul de bilete distincte / C(N,5).
Rearanjarea unui numar fix de astfel de bilete nu o poate creste; Joker si
Euromillions sunt exceptate din noua cautare.

Masurare pe variantele reale ale jobului 2, cu ranking-ul lor canonic:

| Joc | Variante inainte / dupa | P(5+ pe bilet) inainte → dupa | Crestere relativa | Rece / cache |
|---|---:|---:|---:|---:|
| 6/49 | 195 / 195 | 0,3228375% → 0,3248684% | 0,6291% | 0,386 s / 0,0021 s |
| 5/40 | 452 / 452 | 0,3991528% → 0,3996999% | 0,1371% | 0,814 s / 0,0046 s |
| Joker Urna 1 | 460 / 460 | 0,0376506% → identic | 0% | cautare omisa |

6/49: noua constructie are 9 schimburi, profile[5][5] 1151→1158 si
profile[6][5] 7162→7215. 5/40: 15 schimburi, profile[6][5] 4473→4494.
3+, 4+ si 6+ sunt identice. Cresterea este mica, exacta in modelul uniform;
nu este o dovada de predictivitate sau de profit. Nu se afirma optimalitatea.
Conservarea numaratorilor pe fiecare h,t pastreaza probabilitatile uniforme;
nu garanteaza rezultate cel putin egale pe fiecare extragere concreta.

O enumerare independenta a tuturor celor 65536 submultimi ale pool-ului 16,
cu intersectii directe intre multimi, a confirmat zero componente pierdute.
Acest control separat a folosit fixture-uri cu 196/457 variante, distincte
de cele 195/452 ale jobului utilizatorului.

## Auditul logicii predictive si al selectiei

Metodele invatate construiesc trasaturile strict din I[:t], cu frecvente,
EWMA si ultima aparitie inchise la t-1. Etichetele sunt aliniate la randul t;
trasaturile urmatoarei extrageri sunt F[n]. Testele modifica tinta si viitorul
si verifica invarianta trasaturilor disponibile inaintea tintei. Benchmarkul
si WF exclud toate extragerile din ziua tintei. Ranking-ul, validarea scorului
si excluderile de productie sunt comune; nu s-au reintrodus filtre structurale.

Noul script `scripts/analysis/audit_selection_replay.py` evalueaza 52 metode
pe 180 predictii pre-extragere pentru fiecare joc. Primele 120 rezultate
anterioare sunt folosite pentru selectie, ultimele 60 pentru evaluare
ulterioara. Selectia foloseste ferestre 10/30/60/100 si fie ramane fixa pentru
60 de pasi, fie se reface la fiecare 20. Nici selectia nu poate vedea outcome-uri
din ziua primei tinte a blocului. Sunt comparate top-N brut si limita explicita
de maximum doua consecutive, fara penalizare recenta sau feedback.

Rularea finala a cerut 37.440 evaluari (52 x 180 x 4) in 71,0 secunde.
Cele 1.260 scoruri inutilizabile sunt exact cele 7 metode plate de Urna 2 x 180;
nu lipseste nicio predictie la metodele efectiv selectate.

Exemple de POOL pe aceleasi 60 extrageri, 12-03..01-10-2026. Nu sunt hituri de
bilet si nu sunt premii:

| Joc / selectie facuta numai pe trecut | 4+ top-N brut | 5+ top-N brut | 4+ cu consecutive<=2 | 5+ cu consecutive<=2 |
|---|---:|---:|---:|---:|
| 6/49 tinta3, metoda fixa | 3 | 0 | 5 | 0 |
| 6/49 tinta3, reselectie20 | 4 | 2 | 4 | 2 |
| 6/49 tinta4, fixa sau reselectie20 | 7 | 1 | 7 | 1 |
| 6/49 frequency, martor | 8 | 4 | 6 | 2 |
| 5/40 tinta4, metoda fixa | 7 | 1 | 9 | 2 |
| 5/40 tinta4, reselectie20 | 8 | 0 | 8 | 0 |
| Joker Urna1 tinta3, metoda fixa | 4 | 1 | 2 | 0 |
| Joker Urna1 tinta3, reselectie20 | 5 | 1 | 2 | 0 |
| Joker Urna1 tinta4, fixa sau reselectie20 | 4 | 1 | 2 | 0 |

Metodele fixe selectate: pair_lift_last (6/49 tinta3), dmd_forecast (6/49
tinta4), hurst_persistence (5/40), markov_lag2 (Joker Urna1). La Urna2,
selectia fixa freq_window_200 a dat 4/60 =6,67%, fata de 3 potriviri asteptate
la 5%; limita Wilson de 95% este 2,62%, sub baseline. Reselectia20 a dat
2/60 =3,33%. Aceste esantioane mici nu sustin un avantaj stabil.

Limita de consecutive poate imbunatati sau reduce hiturile pe un esantion;
nu este un mecanism predictiv. Tinta4 urmareste mai direct obiectivul de patru,
dar simpla schimbare a tintei nu dovedeste performanta viitoare superioara.
Nu am schimbat setarea salvata si nu am ales retrospectiv frequency sau alt
scorer fiindca a castigat tocmai in cele 60 extrageri evaluate.

Formulele curente au fost dezvoltate folosind istoricul disponibil. Chiar cu
separare temporala corecta a selectiei, acest replay este retrospectiv, nu un
holdout extern neatins al formulelor. WF cu fixed_current_decision are aceeasi
limita a selectiei. Nicio metoda sau blend nou nu este promovat.

## Verificari si reproducere

Snapshot izolat de la commit d83dea96f48a747374b54f3b1ea90712c3c94b8a, cu
istorice si benchmark stabile. Codul Python din snapshot este identic cu cel
integrat. Re-Bench-ul local al utilizatorului (folds.csv/report.json modificate)
este pastrat si exclus din commit, la fel ca toate starea si cache-urile.

- Python 3.14.7, Windows: 52 metode, curated active 52, per-game 51, random
  adaugat de runner.
- 13 istorice, 832 verificari scoring/ranking, 151 designuri validate exhaustiv.
- 26 pipeline-uri, 24 comparatii de bugete si 13 profile complete de patru.
- Worker separat: 13 rezultate COMPLETED din SQLite temporar, payload valid;
  UI pe port liber HTTP 200. Hash-urile surselor/starii din snapshot sunt intacte.
- 14 regresii UI, 52 regresii geometrie, 10 regresii replay temporal si o
  regresie reala de reset al markerului UI noi.
- Salvarea setarilor UI din teste este izolata per test in toate modulele;
  scriptul reset_jobs din teste isi rezolva de asemenea calea in tmp_path.
  Regresia de reset verifica markerul 0 si pastrarea celorlalte setari.
- Prima rulare pe Google Drive s-a intrerupt la 81% prin eroare de dispozitiv.
  Rularea finala completa foloseste o copie identica pe discul temporar local.
- Suita completa finala: **1.932 trecute, 17 omise, zero esecuri**, 290,44 s.
  Cele 272 avertismente sunt deprecieri NumPy din generatoare de date de test.
- Compilare surse si git diff --check: trecute.

```powershell
python scripts/analysis/audit_application.py --output scratch/application_audit.json
python scripts/analysis/audit_selection_replay.py --workers 2 --output scratch/selection_replay.json
python scripts/analysis/audit_output.py --report scratch/rendered_report.txt
python -m pytest -q
```

Cand un Re-Bench ruleaza in paralel, foloseste o copie stabila a surselor si
datelor. Instrumentele noi nu scriu benchmarkul sau decizia de productie.
Validarea hiturilor si matematicii interne nu reverifica fiecare extragere
istorica la sursa oficiala. Categoria I de la 5/40 (primele cinci extrase) si
premiile/ROI nu sunt modelate separat.
