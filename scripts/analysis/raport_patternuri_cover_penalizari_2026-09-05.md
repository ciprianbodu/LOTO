# Audit tipare, penalizări și designuri — 5 septembrie 2026

Am obținut o reducere verificabilă a costului pentru patru designuri și am
corectat validarea configurației jucate. Tiparele investigate nu justifică
introducerea unei noi metode de predicție în producție.

Date: `_ISTORIC/`, până la **03.09.2026**. Număr de extrageri: **2580** la
6/49, **1731** la 5/40, **2183** la Joker. Pool **11**, ca în configurația
salvată a aplicației. Calcule exclusiv CPU.

## Tiparele testate

Am rerulat cele trei analize existente pentru constrângeri, autocorelații și
reducerea bazei. Maximul |z| pentru cele 27 de constrângeri rămâne 2,70;
niciuna dintre cele 18 verificări de autocorelație lag 1 nu are p < 0,05.
Cel mai mic p de permutare: 0,0815, Joker/maxim. Reducerea bazei la numere
sub un prag sau la numere pare nu arată un avantaj repetabil.

Noul experiment testează trei reguli de calendar: numere mai frecvente în
aceeași zi a săptămânii, lună sau trimestru. Fiecare folosește numai extrageri
anterioare țintei; data țintă este informație cunoscută. Frecvențele pe calendar
sunt stabilizate cu 50 de pseudo-extrageri din distribuția trecutului, parametru
fixat înainte de verificare. Am testat și cinci variante ale penalizării deja
existente: N=1/factor 0,5; N=3/factor 0; N=3/factor 0,5; N=3/factor 0,8;
N=5/factor 0,5. Referința comună este scorerul real `frequency` din proiect.

Protocol: 50% istoric inițial, 20% pentru alegerea variantei, 30% pentru
verificarea alegerii. Scorurile se recalculează înaintea fiecărei extrageri
folosind strict prefixul anterior. Selecția folosește rata de 3+ în pool,
apoi media hiturilor. Referința poate câștiga; nu se impune o schimbare.
Corecție Holm pentru comparațiile multiple; prag 0,05.

| Joc | Extrageri în verificare | Varianta aleasă înaintea verificării | Rată 3+ | Referință aleatorie exactă |
|---|---:|---|---:|---:|
| 6/49 | 774 | aceeași lună | 12,53% | 11,74% |
| 5/40, primele 5 | 520 | frequency | 11,92% | 11,71% |
| Joker, Urna 1 | 655 | aceeași zi a săptămânii | 9,31% | 8,53% |

Nicio alegere nu confirmă avantajul: p ajustat Holm este aproximativ **0,767**
pentru fiecare dintre cele trei alegeri. Nici examinarea exploratorie a tuturor
celor 27 de combinații joc × variantă nu produce o descoperire după corecție
(cel mai mic p ajustat: **0,252**). Ratele de 4+ sunt raportate separat în JSON,
fără schimbarea ulterioară a criteriului de selecție.

Acest holdout este rezervat în experimentul prezent; istoricul era deja
disponibil proiectului și a fost analizat în alte experimente. Nu este o probă
pe extrageri viitoare. Rezultatul nu exclude orice tipar posibil, dar nu susține
promovarea regulilor investigate. Registry-ul, curarea și blacklist-ul rămân
neschimbate; nicio metodă blacklistată nu este folosită de experiment.

## Penalizarea actuală: N=3, factor 0,5

Comparație pe **același scorer frequency**, pe aceleași extrageri rezervate
verificării. Nu este o măsurare a întregului ensemble actual din Auto-Pilot.

| Joc | Fără penalizare, 3+ | Cu penalizare, 3+ | Diferență |
|---|---:|---:|---:|
| 6/49 | 104/774 = 13,44% | 92/774 = 11,89% | −1,55 puncte procentuale |
| 5/40, primele 5 | 62/520 = 11,92% | 72/520 = 13,85% | +1,92 puncte procentuale |
| Joker, Urna 1 | 72/655 = 10,99% | 68/655 = 10,38% | −0,61 puncte procentuale |

Efectul își schimbă semnul între jocuri. Nu există motiv demonstrat pentru a
impune această penalizare tuturor jocurilor. N=0 este o referință utilă pentru
comparație; setarea salvată N=3/factor 0,5 a fost păstrată ca preferință.
Un număr extras recent nu devine, prin acest fapt, mai puțin probabil la o
extragere independentă.

Corecții implementate:

- Factorul **0** ajunge acum ca 0 în walk-forward; expresia veche `or 0.5`
  îl transforma în 0,5.
- WF primește garanția, condiția și plafonul rezultatului generat. Anterior
  folosea o garanție internă și bilete nelimitate. Plafonul este citit și din
  `context.max_variants`, unde îl păstrează workerul.
- Cheia cache-ului separă garanțiile, condițiile și bugetele și include
  conținutul fișierelor de design condițional L. Factorii cu mai mult de trei
  zecimale nu mai pot împărți accidental aceeași cheie.
- Inclusiv fallback-ul fără decizie bench distinge ferestrele de istoric.
- UI explică garanția condițională și arată plafonul realmente evaluat.

Cache-ul WF este acum **v23**. Următorul WF recalculează rezultatele; cache-urile
anterioare rămân pe disc. Calea de generare din `loto_engine.py` și scorurile
de producție nu au fost modificate.

WF rămâne o evaluare retrospectivă a metodelor selectate de bench. Dacă
selecția metodelor a folosit și aceeași perioadă de istoric, scorurile WF nu
constituie un test complet independent al procesului de selecție.

## Designurile de acoperire

Am verificat independent toate cele **151** de fișiere C/L: dimensiunea
biletelor, unicitatea numerelor, limitele numerelor și toate submulțimile
relevante. Toate au acoperire exactă 100% pentru garanția declarată. Nu sunt
bilete duplicate în fișiere. Pentru 46 de designuri, numărul de bilete atinge
limita inferioară calculată (Schönheim pentru C, limita de volum pentru L).
Pentru restul, acest audit nu demonstrează optimalitatea globală.

Patru fișiere aveau blocuri redundante. Eliminarea s-a făcut secvențial,
numai când toate țintele unui bilet erau încă acoperite de celelalte bilete,
cu reverificare exhaustivă înaintea scrierii atomice.

| Fișier | Înainte | După | Garanție după reducere |
|---|---:|---:|---|
| C_14_6_5.txt | 459 | 457 | 5 dacă 5, 100% |
| C_15_5_4.txt | 348 | 347 | 4 dacă 4, 100% |
| C_15_6_5.txt | 692 | 689 | 5 dacă 5, 100% |
| L_11_5_5_4.txt | 26 | 25 | 4 dacă 5, 100% |

Economia de 7 bilete este suma pe aceste **patru configurații**, nu reducerea
unei singure sesiuni. La setarea salvată pool 11/garanție 4/condiție clasică,
aceste eliminări nu schimbă numărul de bilete. Garanția păstrată nu înseamnă
și păstrarea tuturor hiturilor suplimentare, dincolo de garanția cerută.

Exemplu Joker, pool 11, Urna 1, după reducere. Probabilitățile de mai jos
sunt calculate exact pentru un pool fix într-o extragere uniformă, prin
enumerarea submulțimilor din pool și ponderarea extensiilor din afara lui.
Nu sunt rezultate de predicție; biletele sunt cele generate fără scoruri.

| Garanție | Bilete | Acoperirea condiției | P(cel puțin un bilet cu 3+) | P(cel puțin un bilet cu 4+) |
|---|---:|---:|---:|---:|
| 3 dacă 3 | 20 | 100% | 8,53% | 0,314% |
| 3 dacă 4 | 9 | 100% | 4,31% | 0,133% |
| 4 dacă 4 | 66 | 100% | 8,53% | 0,956% |
| 4 dacă 5 | 25 | 100% | 8,26% | 0,372% |

Prin urmare, „100%” pentru un design condițional nu înseamnă aceeași
probabilitate ca la un cover clasic. Economia de bilete vine cu o condiție
mai greu de îndeplinit. Pentru evaluarea jocului, rândul **BILET** contează;
hitul de pool poate fi doar un plafon.

## Limita interpretării la 5/40

Aplicația folosește primele cinci numere pe axa principală de scoring și WF.
La 5/40, categoria I folosește primele cinci, dar categoriile II și III folosesc
toate cele șase numere extrase. Trei hituri nu reprezintă premiu la acest joc.
Aceste reguli sunt confirmate în [descrierea oficială a Loteriei Române](https://www.loto.ro/?p=3921).

Noul audit include suplimentar `holdout_all_six_540` și probabilități exacte
pentru șase numere în `wheel_comparison`. Pe cele 520 de extrageri rezervate,
pool-ul frequency are 21 cazuri cu 4+ din toate șase, iar varianta penalizată
23. Diferența mică nu justifică o recomandare. Sunt hituri de **pool**, nu
număr de premii. Evaluarea principală a aplicației nu a fost migrată la un
model distinct pentru fiecare categorie de câștig.

## Reproducere și verificare

```powershell
& 'D:\_BUILD\_LOTO\.venv\Scripts\python.exe' scripts/analysis/audit_patterns_and_designs.py
```

Implicit scrie numai `bench_results/pattern_design_audit.json`; nu schimbă
designuri sau decizii de producție. Opțiunea explicită `--prune-redundant`
elimină blocuri redundante numai după verificare. Raportul JSON al acestei
rulări păstrează hash-urile CSV/design și modificările înainte/după.

Au trecut 301 teste în suita extinsă pentru wheeling/WF și 32 în ultima
verificare țintită (seturi parțial suprapuse). Testele noi compară biletele WF
cu generarea directă, inclusiv ramura de worker, penalizarea 0, designurile
condiționale și bugetele. Compilare Python reușită; UI testat izolat pe port
8099, răspuns **HTTP 200**, fără recuperarea joburilor utilizatorului.

Fișierele bench existente, `best_methods.json`, curarea, blacklist-ul și
preferințele salvate nu au fost rescrise de audit.
