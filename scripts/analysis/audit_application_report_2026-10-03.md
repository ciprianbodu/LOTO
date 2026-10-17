# Audit aplicație și hituri — 3 octombrie 2026

## Rezultat și schimbarea livrată

Auditul curent confirmă funcționarea motorului, acoperirea designurilor și
coerența UI–worker. Raritatea hiturilor de 4 și 5 este compatibilă cu probabilitățile configurației
folosite. Auditul a corectat nota brută `pool_selection_note`, care menționa
„țintă 3+” inclusiv pentru jocurile/configurațiile evaluate la 4+. Acum descrie
selecția top-N după scorul final și departajarea canonică. Două comentarii
similare au fost aliniate. Această corecție de text nu schimbă selecția
numerelor sau variantele.

Corecțiile funcționale și optimizarea geometrică sunt deja integrate prin
[PR #132](https://github.com/ciprianbodu/LOTO/pull/132). Înaintea sincronizării, copia locală (`fec88b5`) conținea codul verificat atunci și
13 rânduri noi în zece istorice externe. Verificarea remote a identificat
PR #133–135, deja integrate în `main` (`ea198dc`). Au fost aduse local fără
suprascrierea stării utilizatorului și incluse în reverificare. Acestea extind
optimizarea profilului variantelor și adaugă experimentul de selecție a pool-ului.

## Configurația care explică rezultatele actuale

Ultimul job finalizat la verificare, din **2 octombrie 2026, 07:09 București**,
a folosit **pool 11**, garanție clasică 4, condiție 4 și fără plafon de variante,
cu limita de două consecutive activă. Istoricul românesc ajunge la 1 octombrie.
Rezultatul anterior folosea pool 16. Setarea salvată ulterior în interfață este
**pool 6 pentru următoarea generare**; nu schimbă retroactiv rezultatul pool 11.

„Primele 10 variante” limitează lista afișată. Garanția și probabilitățile de
mai jos presupun toate variantele rezultatului, nu numai primele zece.

| Joc | Variante distincte | 4+ pe cel puțin o variantă | 5+ pe cel puțin o variantă | 5+ în pool |
|---|---:|---:|---:|---:|
| Loto 6/49 | 34 | 1,787838% | 0,057624% | 0,128849% |
| Loto 5/40 | 66 | 3,851625% | 0,060182% | 0,361090% |
| Joker, numai Urna 1 | 66 | 0,956162% | 0,005402% | 0,037814% |

Valorile sunt probabilități combinatorice exacte, calculate pe variantele
persistate ale jobului, sub modelul extragerilor uniforme. Nu sunt rate
observate în backtest și nu presupun că variantele ar fi independente.
La 5/40 se verifică cinci numere jucate față de toate cele șase extrase;
prima categorie, definită prin primele cinci extrase, nu este separată aici.
Joker Urna 2 nu este inclusă în probabilitățile Urnei 1 din tabel.

Pentru 6/49, 0,057624% înseamnă aproximativ **un eveniment 5+ la 1.735 de
extrageri în medie**, nu un termen până la următorul hit. Sub același model,
cu această configurație și extrageri independente, probabilitatea să nu apară
niciun 5+ în 100 de extrageri este aproximativ **94,40%**.

Cu pool 6, 6/49 produce o singură variantă distinctă: probabilitățile uniforme
sunt aproximativ 0,098714% pentru 4+ și 0,001852% pentru 5+. Dimensiunea pool-ului
și variantele efectiv jucate au deci un efect major asupra frecvenței așteptate.
Setarea utilizatorului nu a fost modificată de audit.

O garanție completă de 4 spune: dacă pool-ul conține cel puțin patru numere
extrase, există o variantă cu cel puțin patru. Nu garantează că pool-ul va
conține acele numere și nici că cinci numere din pool vor ajunge împreună pe o
variantă. Cu garanție clasică 4 deja completă, wheeling-ul a atins plafonul de
4+ impus de pool; mai poate îmbunătăți distribuția hiturilor de 5+ în limitele
numărului de variante și ale garanției păstrate.

## Îmbunătățirea geometrică deja activă

Optimizarea automată a coverelor complete de 4 păstrează pool-ul și numărul
variantelor distincte; nu scade profilul de hituri la niciun prag și nicio
mărime a intersecției. Acceptă numai îmbunătățiri verificate exact.

Pe rezultatul **anterior din auditul de 1 octombrie, cu pool 16**:

| Joc | Variante, neschimbate | P(5+ pe cel puțin o variantă) înainte | După | Creștere relativă |
|---|---:|---:|---:|---:|
| Loto 6/49 | 195 | 0,322837% | 0,324868% | 0,6291% |
| Loto 5/40 | 452 | 0,399153% | 0,399700% | 0,1371% |

Creșterile sunt mici, dar exacte, fără cost suplimentar pentru acele sisteme.
Nu reprezintă o creștere demonstrată a puterii predictive a scorerului.
La Joker, pentru cinci numere extrase și variante distincte de cinci numere,
probabilitatea de 5 este invariabilă la același număr de variante.

Activarea este automată la pornirea normală și o generare nouă. Nu este nevoie
de comanda manuală `LOTO_WHEEL_METHOD=hitcover`. Cu buget pozitiv se aplică
hitcover în limitele sale; fără plafon, coverele complete eligibile de 4
primesc optimizarea pentru 5+. Sunt descrise în raportul rundei 3 din 1 octombrie.

PR #134 extinde schimburile cu dominanță exactă și la covere clasice de alte
ordine, designuri condiționale, bugete și completarea biletelor fizice.

Recalculul pe algoritmul actual, folosind pool-urile și scorurile salvate ale
ultimului job pool 11, reproduce exact aceleași variante la toate trei jocurile.
Numărul de variante, acoperirea de 4 și toate componentele profilului sunt
identice. **Nu există un câștig suplimentar măsurat pentru acea configurație.**
Dovada locală este `scratch/audit_2026-10-03/current_ticket_gains.json`.

Auditul a corectat și acțiunea **Bilet complet la 5/40**: umplerea locurilor
suplimentare căuta câștiguri pentru cinci numere extrase, deși jocul extrage
șase. Acum geometria vine din registrul loteriei. Se păstrează întâi exact
construcția livrată, apoi se încearcă o rafinare pentru șase numere extrase,
cu baza garanției înghețată și dominanță verificată pe întregul profil.
Schimbarea directă a căutării putea conduce la un alt optim local mai slab;
regresia pentru pool 10, garanție 4 și opt variante previne acest lucru.

Exemple măsurate pentru bilet complet 5/40, la același număr de variante:
pool 8, garanție 3, 12 variante: P(5+) crește de la 0,0107076423% la
0,0107336949%; pool 9, garanție 4, 40 variante: de la 0,0343895081% la
0,0344937187%. Niciun prag/intersecție nu scade. Aceste exemple nu sunt
configurația ultimului job pool 11 din tabelul de mai sus.

## Logica scorurilor și validarea temporală

Reverificarea celor patru benchmarkuri românești confirmă starea `fresh` și
semnăturile curente. Metodele efective ale ultimului rezultat sunt
`croston_interval` pentru 6/49, `hot_consistency` pentru 5/40 și
`pagerank_cooc` pentru Joker Urna 1; Urna 2 folosește `ridge_pooled_feats`.
Țintele lor sunt, respectiv, 3+, 4+, 3+ și top-1.

Ținta 3+ selectează după rata de 3+, deci nu optimizează direct 4+/5+.
Ținta 4+ schimbă criteriul de selecție; nu dovedește singură o îmbunătățire
viitoare. Nu s-a schimbat automat nici ținta, nici metoda salvată.

Replay-ul temporal din 1 octombrie rămâne aplicabil: istoricele românești nu
s-au schimbat între audituri. A evaluat 52 de metode × 180 de ținte × patru
jocuri, adică 37.440 de apeluri de scoring, pentru pool 16 la jocurile principale
și top-1 la Urna 2. Nu este un replay al configurației actuale pool 11/6.
Scorerul vede numai extragerile
dinaintea zilei țintei, iar selecția vede numai rezultatele dinaintea blocului
evaluat. Scorurile plate/inutilizabile sunt respinse.

Evaluarea finală pe 60 de extrageri ulterioare perioadei inițiale de selecție
nu a demonstrat un avantaj stabil care să justifice promovarea unei metode
noi. Este o analiză retrospectivă: formulele au fost dezvoltate pe istoricul
disponibil, deci această perioadă nu constituie un holdout extern al formulelor.
Nu s-a ales retrospectiv metoda cu cel mai bun rezultat din acele 60 de extrageri.

Experimentul din PR #135 compară patru reguli de selecție în opt celule
(32 de teste), inclusiv 19.400 de ținte străine distincte. Recalculul independent
al testelor binomiale și al corecției Holm coincide cu JSON-ul publicat; minimul
p ajustat este 0,563. Niciun candidat nu justifică promovarea. Majoritatea
celulelor urmăresc 3+, deci rezultatele nu demonstrează un câștig pentru 4+/5+
la configurația curentă.

În runnerul experimentului s-a identificat o abatere de implementare:
scorurile plate puteau deveni clasamente după numărul mai mare, în loc să
activeze fallback-ul `frequency` prevăzut de protocol. Runnerul folosește acum
validatorul comun, numără fallback-urile și identifică punctele de reluare
prin date, cod și registry, astfel încât rezultate intermediare vechi să nu
fie reutilizate după schimbarea implementării. Parametrii și protocolul
preînregistrat rămân nemodificați.

Rezultatele publicate la 2 octombrie sunt păstrate ca rezultate originale,
**nerecalculate după această corecție**. În 1.269 de verificări punctuale
(47 de metode, nouă istorice, trei prefixe) nu au apărut scoruri inutilizabile;
aceasta nu este o verificare exhaustivă a evaluării originale. Impactul
numeric al abaterii asupra acesteia rămâne necunoscut. Recalculul statistic
confirmă agregatele publicate, nu elimină această limită a generării lor.
Nu se promovează niciun candidat; o reluare corectată salvează un rezultat separat.

## Verificări și reproducere

Auditul de aplicație din 3 octombrie a rulat pe o copie locală izolată, cu
Python 3.14.7 / Windows:

- 13 istorice validate și 832 de verificări ale scorerilor;
- 151 de designuri locale validate integral;
- 26 de pipeline-uri, 24 de comparații cu buget și 13 profiluri de covere complete;
- un worker separat a terminat toate cele 13 rezultate de joc, cu payload decodat;
- interfața a răspuns HTTP 200, folosind coadă și setări izolate;
- fișierele de stare, benchmark și istoric protejate au rămas neschimbate;
- durată: 38,53 secunde.

Suita completa finala: **1.978 teste trecute, 17 omise, zero esecuri**,
486,05 secunde pe Python 3.14.7 / Windows. Cele 272 de avertismente provin
din constructia datelor NumPy din teste. Sursele Python au fost comparate
cu snapshot-ul verificat; fisierele de stare protejate sunt intacte.

Corecția de text nu schimbă geometria, metricile, schema sau cheile cache.
Raportul normalizează deja nota din rezultatele istorice, fără rescrierea lor.
Bilet complet este calculat la cerere; cheia cache-ului geometric include
numărul de bile extrase și nu reutilizează căutarea pentru alt draw_n.
Versiunile din main sunt worker v11, WF v30 și benchmark v21.
Corecția descrierii nu impune invalidare suplimentară.

Dovezi locale: `scratch/audit_2026-10-03/application_final.json`,
`scratch/audit_2026-10-03/pytest_final.log` și
`scratch/audit_round3/selection_replay_all_180.json`.
Scripturi reproductibile: `scripts/analysis/audit_application.py` și
`scripts/analysis/audit_selection_replay.py`. Nu au fost suprascrise rezultatele
benchmarkului utilizatorului și nu au fost incluse în commit fișiere runtime.
