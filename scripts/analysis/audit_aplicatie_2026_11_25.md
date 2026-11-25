# Audit LOTO din 25 noiembrie 2026

Am verificat aplicația Windows pornind de la versiunea `441596e`. Secțiunile de mai jos arată ce s-a executat, ce s-a confruntat cu arhiva oficială și ce s-a verificat numai prin citirea codului. Toate generările, modificările de coadă și reluările istorice din audit au folosit copii separate.

## Extragerile și efectul asupra validării

Cele 13 istorice conțin acum 48.305 extrageri. Pentru cele trei jocuri românești, am confruntat fiecare dintre cele 1.009 luni disponibile până la 25 noiembrie 2026. În total, cele 6.949 de rezultate publicate în arhiva lunară se regăsesc în CSV, inclusiv numărul Joker sau poziția bilei a șasea la 5/40. În aprilie și mai 2020 tragerile au fost suspendate. Registrul lunar păstrează adresa sursei, amprenta paginii, numărul de înregistrări înainte și după și diferențele pe lună. [Registru lunar](audit_istorice_lunar_2026_11_25.csv) · [Comunicatul Loteriei](https://www.loto.ro/?p=2865)

| Joc | Înainte | După | Din arhiva lunară | Zile cu două trageri distincte |
|---|---:|---:|---:|---:|
| 6/49 | 2.604 | 2.616 | 2.391 | 68 |
| 5/40 | 1.755 | 2.498 | 2.348 | 63 |
| Joker | 2.207 | 2.210 | 2.210 | 63 |

La 6/49, revista oficială confirmă separat seturile celor 11 trageri manuale din 1993. Loteria confirmă data primei extrageri. Pentru următoarele zece, două arhive istorice indică aceleași date, însă sursa lor inițială comună nu poate fi exclusă. Primele unsprezece combinații sunt consemnate în ordine numerică. La 5/40 am păstrat toate cele 150 de înregistrări anterioare lui 1998. Arhivele secundare confirmă data și combinația tuturor, iar pentru zece există și rezultate tipărite contemporane verificate în ordinea extragerii. [Primele trageri 6/49](https://www.loto.ro/storage/2022/04/2012-09.pdf#page=7) · [Prima extragere](https://www.loto.ro/storage/2022/04/01-1.pdf) · [Arhiva datelor](https://forum.softpedia.com/topic/222740-loto-649/page__st__396/) · [Reperul oficial de 879 de trageri 5/40](https://www.loto.ro/storage/2022/04/2011-24.pdf#page=8)

Când ambele rubrici din 1 decembrie 2019 au aceeași etichetă în arhiva lunară, am verificat ordinea principală/suplimentară și în relatările zilei. La 6/49 principala începe cu 40 și 22, iar suplimentara cu 28 și 39. Revista Loteriei le separă prin marcajele P și S. La 5/40 tragerea cu primele bile 3 și 6 este principala, iar cea cu 15 și 24 este suplimentara. [Revista Loteriei](https://www.loto.ro/storage/2022/04/2021-09.pdf) · [Rezultatele contemporane](https://www.digi24.ro/stiri/actualitate/social/loto-1-decembrie-2019-trageri-duble-la-loto-649-joker-loto-540-premiu-urias-de-peste-371-milioane-de-euro-la-649-1225070)

La 6/49, rezultatele contemporane identifică separat și ambele trageri din 26 noiembrie și 31 decembrie 2017. Am verificat ordinea celor patru combinații în CSV. La 5/40, procesul-verbal din 10 martie 2024 confirmă numărul 31 la poziția a șasea, de care depinde deosebirea dintre categoriile I și II. [26 noiembrie 2017](https://actualmm.ro/numerele-norocoase-rezultatele-extragerii-la-loto-de-duminica-26-noiembrie/) · [31 decembrie 2017](https://www.digi24.ro/stiri/actualitate/loto-numerele-norocoase-la-tragerile-speciale-de-anul-nou-853251) · [Omologare 5/40](https://www.loto.ro/storage/2024/03/Super_L_Pr.pdf)

În prima etapă istoricele erau verificate structural. O verificare separată, după conținut, a identificat **35 de diferențe** între CSV-urile românești și cele 1.319 rezultate din copia bazei locale de premii. Am recitit cele 14 pagini lunare corespunzătoare pe loto.ro. Toate cele 35 de rezultate din baza de premii se regăsesc în arhiva oficială consultată.

| Joc | Rezultate din baza de premii | Coincideau înainte de corectură, inclusiv bonusul | Diferențe identificate |
|---|---:|---:|---:|
| Loto 6/49 | 652 | 650 | 2 |
| Joker | 378 | 371 | 7 |
| Loto 5/40 | 289 | 263 | 26 |

În cinci cazuri am găsit combinația oficială în CSV sub altă dată. În 23 de cazuri lipseau atât ziua, cât și combinația oficială. În alte șapte, ziua apărea, dar combinația oficială lipsea. La tragerile speciale pot exista mai multe combinații în aceeași zi. O diferență de acest fel nu justifică, singură, ștergerea rândului local. La 5/40, a șasea bilă coincide în toate cele 263 de extrageri identificate în comun. Walk-forward și Re-Bench lucrează cu rândurile și datele din CSV, astfel încât diferențele pot modifica atât antrenamentul pe zilele anterioare, cât și punctajul măsurat. Lista celor 35 de cazuri este la sfârșitul raportului. [Loto 6/49](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/649_si_noroc/rezultate_extragere.html), [Joker](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/joker_si_noroc_plus/rezultate_extrageri.html), [Loto 5/40](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/540_si_super_noroc/rezultate_extrageri.html).

Am corectat cele 35 de diferențe și am adăugat extragerea Joker din 04.03.2021, confirmată în timpul verificării lunii vecine. La 6/49 s-au schimbat două date. La Joker am adăugat două extrageri și am corectat date sau bile în alte șase rânduri. La 5/40 am adăugat 26 de extrageri, cu păstrarea fiecărui rând anterior. **După corectură, toate cele 1.319 rezultate din proba de premii coincid cu CSV-urile**, inclusiv bila Joker și poziția celei de-a șasea bile la 5/40. Cele 133 de extrageri din cele 15 luni verificate integral se regăsesc în ambele surse. La încheierea acestei prime etape, cele 13 istorice însumau 47.575 de extrageri. Tabelul de la începutul raportului include și corecțiile din verificarea tuturor anilor. [Joker în februarie și martie 2021](https://www.loto.ro/storage/2022/04/2021-16.pdf), [6/49 din 05.02.2023](https://www.loto.ro/storage/2023/02/Loto_649.pdf), [6/49 din 26.01.2025](https://www.loto.ro/storage/2025/01/loto649-2.pdf).

Pentru zilele cu două extrageri 5/40 am verificat și care a fost tragerea principală. La 05.12.2021, de exemplu, principala începe cu 31, 39 și suplimentara cu 18, 35. Pagina lunară afișează mai întâi suplimentara. Importul oficial ordonează acum principala înaintea suplimentarei în istoricul jocului. [Paști 2021](https://www.loto.ro/storage/2022/04/2021-18.pdf), [Moș Nicolae 2021](https://www.loto.ro/storage/2022/04/2021-49.pdf), [Anul Nou 2021](https://www.loto.ro/storage/2022/04/2022-01.pdf), [Paști 2022](https://www.loto.ro/images/upload/Omologari/super_l.pdf).

Ultimul jurnal de pornire de pe stație consemnează extragerile din 22.11.2026 pentru toate cele trei jocuri. În cazul 5/40, pagina secundară se oprea la 19.11, iar aplicația a găsit extragerea din 22.11 pe loto.ro. Pentru cele zece jocuri externe am verificat toate fișierele și am testat citirea răspunsurilor înregistrate de la furnizori.

## Metoda normală, metoda inversă și intervalul 10–40

Cele 31 de numere din interval au aceeași probabilitate de a conține toate bilele principale extrase, indiferent de metoda folosită la alegerea biletului. Pentru o extragere uniformă fără înlocuire, formula este `C(31, k) / C(N, k)`. Frecvența istorică din tabel folosește fișierele corectate.

| Eveniment | Probabilitate teoretică | Observații în CSV |
|---|---:|---:|
| 6/49, toate cele șase bile | 5,2652% | 139 din 2.616 |
| Joker, cele cinci bile principale | 13,9071% | 290 din 2.210 |
| 5/40, toate cele șase bile extrase | 19,1821% | 446 din 2.498 |
| 5/40, primele cinci bile, care stabilesc categoria I | 25,8220% | 614 din 2.498 |

Am refăcut 24 de validări cu 100 lei și obiectivul „orice premiu”, folosind cele trei jocuri, metodele normală și inversă, baza completă și intervalul 10–40, cu selecție fixă și dinamică. Din 336 de situații evaluate, 325 au produs bilete, ale căror variante au fost expandate și punctate independent. În 11 cazuri Joker invers cu baza 10–40, numerele eligibile rămase după excluderea pool-ului direct nu puteau forma schema. Am verificat și că ambele extrageri din 13 septembrie folosesc numai istoricul anterior zilei. Aceste probe verifică execuția și punctajul. Pentru o concluzie despre câștigurile viitoare ar fi nevoie de rezultate prospective.

| Defect reprodus | Corectura verificată |
|---|---|
| În generarea obișnuită și în validarea unei scheme LR explicite, codul 48 la 6/49 putea produce un pool cu trei consecutive. | Baza 10–20 admite cel mult opt numere cu limita de două consecutive. Pentru cele nouă cerute, motorul refuză combinația și explică de ce. Verificarea se aplică în generator și în cele două căi de validare. |
| Comparația automată la Joker elimina toate rândurile fără bila bonus, inclusiv premiile certe cu trei numere principale și ratările certe cu zero. | Punctajul păstrează rezultatele cunoscute și identifică situațiile nedecidabile. În testul statistic al avantajului, rezultatele necunoscute au tratament conservator, iar extragerile din aceeași zi contribuie cu un singur factor. |
| Rapoartele vorbeau despre zile inclusiv pentru fișiere fără date calendaristice. | Re-Bench și comparația bazelor precizează când reluarea urmează ordinea rândurilor. |

## Bilete, bugete și premii

- Am confruntat toate cele **33 de coduri LR**, cu **5.527 de variante**, și am verificat 220.346 de submulțimi de poziții. Cele 232 de scheme cu garanție completă acoperă toate cele 289.200 de grupe promise în probele independente. Toate cele 277 de amprente de integritate au coincis.
- Am recalculat costul și probabilitatea pentru 42 de planuri la șapte bugete între 100 și 1.000 lei, am verificat 54 de planuri adaptate la pool și am comparat 66 de portofolii. Din cele 42, pentru 32 s-a putut demonstra optimalitatea în căutarea evaluată, pentru șapte există o limită superioară, iar pentru trei planuri Joker există numai probabilitatea realizată de plan. Prețurile și taxarea pe bilet au fost confruntate cu tarifele oficiale. [Tarife Loteria Română](https://www.loto.ro/?page_id=1068)
- Am comparat 71.266 de cazuri de încadrare la premiu și 530 de rezultate între variantele individuale și forma compactă. La Joker contează asocierea bilei cu aceeași variantă. La 5/40 contează poziția bilei a șasea. Am recitit și două formate istorice de pe loto.ro: `REPORT *` la Joker și sumele cu separatori englezești la 5/40.
- Recomandarea Joker validează acum bilele urnei a doua și refuză o selecție goală, bile duplicate sau valori nevalide. Pentru rezultate salvate cu bile lipsă, interfața arată câte variante au efectiv un Joker. Pentru bile diferite, șansa de a prinde cel puțin o dată bila extrasă este numărul de bile distincte jucate împărțit la 20.

## Interfață, persistență și pornire

| Situație reprodusă | Rezultat după corectură |
|---|---|
| Se bifa un joc suplimentar după deschiderea paginii cu un singur joc. | Formularul încarcă istoricul jocului ales și semnalează jocurile omise dacă le lipsește istoricul. |
| Înregistrarea unei cereri noi eșua după golirea rezultatului anterior. | Rezultatul și validarea veche rămân accesibile când cererea nu poate fi salvată. |
| Un raport cronologic salvat cu o structură coruptă împiedica afișarea panoului. | Pagina rămâne accesibilă și arată eroarea de citire. |
| E-mailul indica un cod LR din metadate deși variantele concrete erau altele. | Raportul și e-mailul folosesc variantele efectiv generate și semnalează nepotrivirea. |
| O anulare cu lista de lucrări goală anula întreaga coadă. | O listă goală anulează zero lucrări. Anularea explicită a tuturor rămâne disponibilă. |
| Coada putea folosi baza stației în timpul unei rulări cu director separat. Inițializarea concurentă sau înlocuirea bazei putea lăsa coloane lipsă. | Coada folosește directorul ales dacă nu are o bază explicită. Migrarea este tranzacțională. Înlocuirea fișierului declanșează reverificarea schemei. Preluarea unei lucrări returnează rândul din tranzacția care a revendicat-o. |
| O blocare temporară a cozii la scrierea cauzei unei erori oprea workerul. | Workerul reîncearcă înregistrarea aceleiași erori cu identitatea lucrării înainte de a trece la următoarea. Jurnalul păstrează maximum 6.000 de caractere inclusiv la prima scriere. |
| Două porniri suprapuse puteau scrie din aceeași versiune veche a istoricului. | Scrierile locale au acces exclusiv pe durata reverificării și înlocuirii fișierului. Un CSV extern invalid și un istoric fără extrageri sunt semnalate înainte de actualizare, respectiv publicare. |
| Pornirea putea închide alt serviciu pentru că folosea portul 8000. Actualizarea putea selecta procesele altui mediu Python cu un nume asemănător. | Lansatorul identifică procesul LOTO după comanda și calea efective. Dacă portul rămâne ocupat, se oprește înainte de resetarea cozii. Actualizarea verifică exact calea mediului dedicat. |
| Alte utilitare de întreținere puteau actualiza Python global, alege o bază relativă la resetare sau urma o legătură la curățare. | Verificările specifice refuză fiecare dintre aceste situații. Testele au folosit procese simulate, directoare temporare și un port local de probă. |

În browser au trecut generarea 6/49, generarea pentru Austria, recuperarea rezultatului după reîncărcare, Re-Bench LR și walk-forward LR cu 10–40. La bugetul de 171 lei, proba 6/49 a dat codul 49, 15 variante și 120,50 lei. Am verificat conținutul e-mailurilor în copia de test. SMTP și operațiunile de oprire a calculatorului au rămas dezactivate.

## Probe de execuție

Generatorul, dependențele de calcul și NiceGUI au rulat pe Python 3.15.2. Toate cele șase importuri obligatorii au trecut și verificarea pachetelor instalate a încheiat fără incompatibilități declarate. Într-o copie izolată, am compilat sursele Python și am rulat testele din directorul `tests/`. În verificarea de integrare, workerul a încheiat cu starea `COMPLETED` pentru toate cele 13 jocuri. Pagina a răspuns cu HTTP 200. Am verificat 96 de rezultate de scoring, 26 de generări cu și fără plafon, 24 de comparații de buget și 13 acoperiri complete. Amprentele istoricelor și ale fișierelor de stare protejate din copia de probă au rămas identice.

**Arhiva privată de teste de pe stație, la începutul auditului** — 8.216 trecute, 104 eșuate, 15 erori, 23 omise. Arhiva a rulat în șapte copii independente, cu materialele locale de cercetare pe care unele teste le importă. Două eșecuri de autentificare Git proveneau din blocarea transportului HTTP în test. Cele patru scenarii de autentificare și eroare au trecut separat, cu serverul temporar de pe 127.0.0.1. Suitele de mai sus se suprapun, deci cifrele nu se adună.

O parte dintre testele istorice cer vechiul catalog de scoreri, vechiul prag implicit de 5, modulul retras `py314_io`, comenzi de oprire după numărul portului sau etichete și controale schimbate. În studiul istoric de selecție, două simulări de test nu acceptau argumentele funcțiilor curente; testul calendaristic cerea un scorer retras. Variantele de probă adaptate numai în copia temporară au confirmat contractul cronologic. Am actualizat șase fișiere de teste deja publicate pentru ca versiunea Git să verifice restricțiile LR, protejarea serviciilor străine, potrivirea codului cu variantele și lansarea Re-Bench. Arhiva privată a rămas în forma găsită pe stație.

În validarea cronologică din Re-Bench au fost reluate 687 de extrageri la 6/49, 434 la 5/40 și 562 pentru fiecare urnă Joker. Dacă un scorer nu produce scoruri utilizabile, raportul atribuie acum extragerea metodei de rezervă care a furnizat scorurile. Am verificat corecția separat pentru o zi cu două extrageri; în aceste patru intervale reale nu au avut loc substituiri.

## Concurență, evaluarea biletelor și verificări de performanță

Pe Windows și Linux, sistemul de operare eliberează lockul când procesul moare. O a doua instanță de worker pe aceeași coadă iese înainte de recuperarea joburilor. Copia locală a unui CSV este recitită sub lock înainte de îmbinare, iar un rezultat incompatibil publicat între timp este semnalat. SQLite păstrează revendicarea separată a e-mailului și a opririi. Dacă procesul dispare după solicitarea trimiterii, rezultatul extern rămâne incert. Interfața poate reafișa biletul din coadă.

Fiecare portofoliu se evaluează pe variantele complete, incluzând costul tuturor achizițiilor și taxa pe bilet. O variantă plătită în două zone apare de două ori în cost și o singură dată la numărul de variante distincte. La 5/40 șase bile în pool permit cel mult cinci potriviri pe o variantă. La Joker o bilă bonus aflată pe altă variantă se punctează numai la varianta căreia i-a fost atribuită.

În Re-Bench cu buget, fereastra suplimentară de selecție compară portofoliile pe cel mult 60 de zile anterioare. La validare există două opțiuni. Prima fixează metoda înainte de test și actualizează scorurile zilnic. A doua reia generatorul la fiecare zi. Ambele trageri speciale ale unei zile folosesc același prefix. Scorurile din pool și verdictul statistic pe p brut rămân disponibile separat.

Cele 42 de comparații combinatorice, numărătorii exacți, limitele superioare și cazurile fără îmbunătățire sunt în [raportul căutării pe scheme LR](lr_portfolio_search_2026_11_25.md). Datele pentru metodele predictive sunt retrospective. [Studiul pe portofoliile acelorași zile](advanced_lr_validation_2026_11_25.json)

### Ce testează cele trei metode predictive

Fixed Share actualizează ponderea fiecărui expert din succesul sau ratarea întregului portofoliu oficial. Regula deterministă joacă biletele unui singur expert. Comparația din arhivă folosește Hedge pe scoruri. Stacking-ul nou folosește rangurile previziunilor făcute înaintea zilei, dezacordurile și erorile din trecut pentru a estima probabilități marginale. Martorul Ridge din arhivă folosește caracteristici construite direct pe istoric. Modelul bayesian nou păstrează o distribuție explicită a duratei regimului, cu trunchiere pentru CPU și regularizare spre extragerile uniforme. Arhiva cuprinde și metode cu uitare, stare latentă sau amestec restrâns. Studiul precizează martorul efectiv măsurat pentru fiecare candidat. [Agregarea Fixed Share](https://mwarmuth.bitbucket.io/pubs/J39.pdf) · [Stacked generalization](https://doi.org/10.1016/S0893-6080%2805%2980023-1) · [Detectarea bayesiană a schimbărilor](https://arxiv.org/abs/0710.3742)

Separat, între 13 septembrie și 29 octombrie 2026, am reluat pe aceleași 14 zile metoda fixată înainte de test, selectorul dinamic disponibil în aplicație și fiecare dintre cele trei metode noi. Toate au produs bilete pentru toate cele 15 extrageri ale fiecărui joc, la preț identic. Niciuna nu a avut 4+ pe o variantă. La 6/49 și la Joker au existat câștiguri în categoriile inferioare pentru unele metode. Raportul păstrează ponderile, variantele, suma plătită și motivele de rezervă. [Comparația pe zile comune](advanced_lr_validation_2026_11_25.md) · [Reproducerea celor cinci politici](advanced_lr_selector_replay.py)

Cele două extensii opționale, modelul pentru mulțimi de cardinalitate fixă și boosting-ul pe reziduuri, au rămas propuneri. Comparațiile celor trei candidați prioritari pe bilete nu au furnizat un câștig predictiv care să justifice încă un nivel de modelare. [Calibrare, rezultate pe bilete și alarme pe extrageri uniforme](advanced_lr_validation_2026_11_25.md)

### Cât de des se schimbă metoda când sunt punctate biletele

La 200 lei am comparat 24 de configurații pe câte 180 de extrageri de intrare. Pentru fiecare joc am combinat ambele baze, ambele moduri și obiectivele 4+ și „orice premiu”. Fereastra de selecție cuprinde 60 de zile și 61 de extrageri. Alegerea pe toate variantele a diferit de alegerea pe pool în 12 din 22 de configurații evaluabile (54,5%). Acestea sunt rezultate în fereastra folosită la alegere. Configurațiile împart aceleași extrageri.

| Joc | Configurații evaluate | Alegere schimbată | Exemplu în fereastra de selecție |
|---|---:|---:|---|
| 6/49 | 8 din 8 | 2 | În modul invers cu 10–40, extragerile cu 4+ pe o variantă au trecut de la 1 la 3 din 61. |
| 5/40 | 6 din 8 | 6 | Cu baza completă în modul normal, 4+ a trecut de la 1 la 5 din 61. În ambele probe inverse 10–40, schema 37 cerea 16 numere și mai erau eligibile doar 15. |
| Joker | 8 din 8 | 4 | În modul normal complet, numărul extragerilor cu orice premiu a trecut de la 13 la 18 din 61. În cele două probe inverse 10–40, portofoliul a putut fi jucat la 37 de extrageri din 61. Datele detaliate păstrează toate cele 24 de cazuri nejucabile pentru fiecare dintre cele două probe. |

Prețul în bani și numărul extragerilor punctate sunt identice în fiecare pereche de rețete luată în comparație. [Rezultatele celor 24 de configurații](lr_selection_2026_11_25.json) · [Scriptul de reproducere](compare_lr_selection.py)

### Timp și memorie pe aceleași intrări

Am folosit câte 110 extrageri din aceleași CSV-uri corectate pentru versiunea de referință și versiunea modificată. Bugetul a fost 100 lei, obiectivul 4+ și selectorul fix, iar reluarea a folosit ultimele 10% dintre rânduri. Fiecare pereche de valori arată prima execuție și repetarea în același proces. Procesul fiecărei versiuni a pornit cu un director de cache separat. Memoria este maximul observat al procesului Python la eșantionare de 10 ms. În paralel au rulat teste, astfel că timpii sunt orientativi, fără concluzie de accelerare.

| Joc și bază | Operație | Înainte, secunde | După, secunde | Memorie maximă înainte / după, MiB |
|---|---|---:|---:|---:|
| 6/49, normal complet | Generare | 0.004 / 0.003 | 0.175 / 0.094 | 85.8 / 136.2 |
| 6/49, normal complet | Re-Bench | 0.594 / 0.086 | 1.181 / 0.922 | 135.8 / 141.2 |
| 6/49, normal complet | Validare | 0.076 / 0.076 | 0.812 / 0.860 | 135.9 / 141.2 |
| 5/40, normal 10–40 | Generare | 0.620 / 0.004 | 0.452 / 0.108 | 134.5 / 138.6 |
| 5/40, normal 10–40 | Re-Bench | 0.101 / 0.101 | 1.224 / 0.875 | 134.8 / 142.7 |
| 5/40, normal 10–40 | Validare | 0.087 / 0.085 | 0.878 / 0.661 | 134.9 / 142.8 |
| Joker, invers complet | Generare | 0.585 / 0.006 | 0.465 / 0.068 | 134.3 / 137.9 |
| Joker, invers complet | Re-Bench | 0.130 / 0.133 | 1.123 / 1.022 | 134.6 / 141.9 |
| Joker, invers complet | Validare | 0.222 / 0.224 | 1.551 / 1.443 | 134.7 / 142.0 |
În aceste trei comparații la 100 lei, criteriul pe variante și criteriul anterior au ales aceeași rețetă în toate cele trei cazuri. Pe cele 11 extrageri de test pentru fiecare joc au fost zero rezultate 4+. Eșantionul este prea mic pentru o concluzie predictivă. Creșterea timpului pentru anumite scenarii include evaluarea variantelor și a metodelor suplimentare.

Pentru reproducere, rulează într-o copie separată, cu `LOTO_RUNTIME_DIR`, `LOTO_JOBS_DB` și `LOTO_WF_CACHE_DIR` locale distincte. Comparația folosește Python 3.15.2 pe Windows. Parametrii exacți se găsesc în [scriptul de măsurare](measure_lr_pipeline.py). Comenzile de mai jos presupun o copie completă a repository-ului.

```powershell
python -m pip check
python -m compileall -q .
python -m pytest -q -p no:cacheprovider
python -m scripts.analysis.measure_lr_pipeline . _ISTORIC/loto_6_49.csv rezultat.json --game 6/49
python -m scripts.analysis.compare_lr_selection _ISTORIC selectie.json --rows 180 --budget 200
python -m scripts.analysis.compare_lr_portfolio_search --help
python -m scripts.analysis.advanced_lr_validation --help
```

### Cele trei regresii de la versiunea de referință

| Test | Cauza și verificarea |
|---|---|
| `test_ui_rebench_uses_per_draw_scoring_and_keeps_random_baseline` | Testul extrăgea handlerul din interfață și îi furniza un context incomplet. Contextul de probă include acum starea Re-Bench. Testul exercită în continuare punctarea pe extragere și prezența martorului aleator. |
| `test_new_ui_jobs_ignore_the_retired_consecutive_setting` | Așteptarea 0 provenea de la vechea regulă. Pentru un job LR nou am verificat în cererea trimisă workerului limita curentă de două consecutive. |
| `test_submit_worker_result_panel_and_wf_options_at_19` | Vechiul rezultat de referință cerea toate numerele 22–40 consecutiv. Testul urmărește alegerea celor 19 în ordinea scorurilor în condițiile limitei, de la lansarea jobului până la afișare și la opțiunile de validare. |

### Modulele adăugate pentru această livrare

| Fișier | Rol verificat |
|---|---|
| `process_lock.py`, `ui_job_status.py` | Exclusivitate între procese și citirea în fundal a stării cozii. |
| `covering/lr_catalog_cache.py`, `covering/lr_pool_milp.py` | Invalidarea la modificarea codurilor și optimizarea zonelor oficiale cu o limită de cost. |
| `loto_enterprise/benchmark/lr_provenance.py`, `lr_budget_scoring.py` | Identificarea formulelor și planurilor compatibile și punctarea rețetelor pe toate biletele plătite. |
| `loto_enterprise/benchmark/methods_online.py`, `loto_enterprise/core/lr_ticket_experts.py` | Calibrare temporală, distribuția duratei regimului și alegerea expertului după portofoliile zilelor anterioare. |
| `loto_enterprise/core/lr_played_portfolio.py`, `lr_prospective.py` | Expandarea și punctarea biletelor. Păstrarea predicției inițiale și înregistrarea separată a rezultatului publicat. |
| `scripts/analysis/advanced_lr_validation.py`, `compare_lr_portfolio_search.py`, `measure_lr_pipeline.py` | Reproducerea experimentelor predictive, a comparațiilor de acoperire la același cost și a măsurătorilor de timp și memorie. Compararea selecției pe pool cu selecția pe bilete este în `scripts/analysis/compare_lr_selection.py`. |

### Verificarea deciziilor și a tragerilor speciale

Înainte de calculul unui pool LR, workerul, recomandarea pe buget și reluarea zilnică verifică pentru fiecare urnă că istoricul din decizia generală de benchmark este un prefix al datelor disponibile și că versiunea motorului corespunde. Scorurile folosesc aceeași decizie pe durata generării. O decizie generală incompatibilă permite folosirea rezervei calculate pe trecutul disponibil. Profilurile Re-Bench specifice metodei și bazei au propria verificare a formulelor, catalogului, geometriei, bugetului și rezultatului direct exclus de metoda inversă. Am verificat cazul în care corectarea unei bile Joker afectează numai decizia urnei a doua.

Într-o copie de test, publicarea principalei după ce CSV-ul stocase suplimentara a refăcut ordinea oficială a celor două. Două extrageri 5/40 cu aceleași șase numere și cu bila a șasea diferită au rămas distincte. O a doua actualizare a produs un fișier identic. Pentru etichete oficiale ambigue se păstrează ordinea existentă.

### Verificări complete pe două sisteme

La revizia `54da387`, pe Windows au trecut 6.821 de teste și 20 au fost omise. Am confruntat fiecare caz din colecția completă de 6.841 cu rezultatele execuției, în șase copii izolate. Pe Linux au trecut 6.826 și 15 au fost omise. În verificarea separată a lansatoarelor și proceselor Windows de pe GitHub au trecut 187 de teste și 17 au fost omise. Verificările dependențelor, compilarea și pornirea NiceGUI au trecut. Actualizările ulterioare sunt verificate de același flux la fiecare publicare pe `main`. [Rularea verificată](https://github.com/ciprianbodu/LOTO/actions/runs/45656307769) · [Rulările pentru versiunea publicată](https://github.com/ciprianbodu/LOTO/actions/workflows/loto-ci.yml?query=branch%3Amain)

## Inventarul modulelor

250 de fișiere Python publicabile, inclusiv utilitarele și studiile istorice, au trecut verificarea de sintaxă. Tabelele arată suplimentar ce a fost observat în execuție în procesele instrumentate. Numărul reprezintă funcții, metode și clase cu nume distincte în fișier. O funcție apelată o dată și una apelată de o mie de ori se numără fiecare o singură dată. Măsura nu reprezintă acoperirea tuturor ramurilor. Probele independente de browser, calcul sau întreținere descrise mai sus se adaugă acestui inventar.

### Aplicație, operare și comenzi

| Modul | Funcție verificată | Observat în procesele instrumentate |
|---|---|---|
| `analiza_4plus.py` | Comandă de benchmark sau analiză manuală a scorurilor pe istoric | 1 nume executate |
| `analiza_escalada.py` | Comandă de benchmark sau analiză manuală a scorurilor pe istoric | 3 nume executate |
| `analiza_patterns_last10.py` | Comandă de benchmark sau analiză manuală a scorurilor pe istoric | 1 nume executate |
| `app_nicegui.py` | Pornire NiceGUI, selecții, persistența preferințelor și coordonarea lucrărilor | 99 nume executate |
| `bench_all_methods.py` | Comandă de benchmark sau analiză manuală a scorurilor pe istoric | 7 nume executate |
| `bench_outcome.py` | Reluare, identitatea rulării, rezultatele salvate și starea validării | 11 nume executate |
| `budget_cover.py` | Intrări publice spre motor, bugete și transformarea pool-ului în variante | 5 nume executate |
| `cleanup_old_processes.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 19 nume executate |
| `cleanup_residual.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 7 nume executate |
| `conftest.py` | Izolarea istoricului, stării UI și marcajelor din coadă în teste | 3 nume executate |
| `history_write_lock.py` | Citirea surselor, validarea extragerilor, actualizarea și păstrarea istoricului | 1 nume executate |
| `job_queue.py` | Revendicare, progres, anulare, recuperare și persistarea rezultatului | 22 nume executate |
| `loto_engine.py` | Intrări publice spre motor, bugete și transformarea pool-ului în variante | 15 nume executate |
| `lr_walk_forward_run.py` | Reluare, identitatea rulării, rezultatele salvate și starea validării | 8 nume executate |
| `rebench_history_store.py` | Reluare, identitatea rulării, rezultatele salvate și starea validării | 8 nume executate |
| `reset_jobs.py` | Revendicare, progres, anulare, recuperare și persistarea rezultatului | 1 nume executate |
| `runtime_paths.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 2 nume executate |
| `runtime_version.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 2 nume executate |
| `scan_all_methods_hits.py` | Comandă de benchmark sau analiză manuală a scorurilor pe istoric | 4 nume executate |
| `scripts/sync_official_prizes.py` | Legarea variantelor de categoriile și sumele din arhiva oficială | sintaxă verificată |
| `ui_bench.py` | Comenzi Re-Bench, parametrii efectivi și afișarea clasamentelor/rapoartelor | 29 nume executate |
| `ui_history_upload.py` | Încărcare CSV, ferestre de istoric și afișarea hiturilor | 5 nume executate |
| `ui_history_windows.py` | Încărcare CSV, ferestre de istoric și afișarea hiturilor | 1 nume executate |
| `ui_hits.py` | Încărcare CSV, ferestre de istoric și afișarea hiturilor | 20 nume executate |
| `ui_lr_budget.py` | Buget, variante jucabile, selecție de bază și validare LR | 15 nume executate |
| `ui_lr_portfolio.py` | Buget, variante jucabile, selecție de bază și validare LR | 5 nume executate |
| `ui_lr_rebench.py` | Comenzi Re-Bench, parametrii efectivi și afișarea clasamentelor/rapoartelor | 17 nume executate |
| `ui_lr_walk_forward.py` | Buget, variante jucabile, selecție de bază și validare LR | 8 nume executate |
| `ui_nested_wfv.py` | Comenzi Re-Bench, parametrii efectivi și afișarea clasamentelor/rapoartelor | 2 nume executate |
| `ui_prizes.py` | Categorii, probabilități și afișarea rezultatelor reale | 38 nume executate |
| `ui_results.py` | Categorii, probabilități și afișarea rezultatelor reale | 48 nume executate |
| `ui_runtime.py` | Pornire NiceGUI, selecții, persistența preferințelor și coordonarea lucrărilor | 36 nume executate |
| `ui_shared.py` | Pornire NiceGUI, selecții, persistența preferințelor și coordonarea lucrărilor | 16 nume executate |
| `update_csv.py` | Citirea surselor, validarea extragerilor, actualizarea și păstrarea istoricului | 16 nume executate |
| `update_externe.py` | Citirea surselor, validarea extragerilor, actualizarea și păstrarea istoricului | 25 nume executate |
| `verifica_istoric.py` | Citirea surselor, validarea extragerilor, actualizarea și păstrarea istoricului | 3 nume executate |
| `verifica_mediu.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 5 nume executate |
| `verify_imports.py` | Director local, versiuni, dependențe sau întreținere pe Windows | 2 nume executate |
| `wheeling_methods.py` | Intrări publice spre motor, bugete și transformarea pool-ului în variante | modul încărcat |
| `worker.py` | Revendicare, progres, anulare, recuperare și persistarea rezultatului | 23 nume executate |

### Acoperire și probabilități

| Modul | Funcție verificată | Observat în procesele instrumentate |
|---|---|---|
| `covering/__init__.py` | Inițializarea pachetului și exporturile acestuia | modul încărcat |
| `covering/anneal_hits.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 8 nume executate |
| `covering/budget_climb.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 5 nume executate |
| `covering/budget_designs.py` | Geometrie și certificarea acoperirii seturilor de variante | 7 nume executate |
| `covering/common.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 11 nume executate |
| `covering/designs.py` | Geometrie și certificarea acoperirii seturilor de variante | 13 nume executate |
| `covering/dispatch.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 9 nume executate |
| `covering/exhaustive.py` | Probabilități combinatorice, enumerare și acoperire exactă | 4 nume executate |
| `covering/extended_pool.py` | Geometrie și certificarea acoperirii seturilor de variante | 11 nume executate |
| `covering/final_profile.py` | Geometrie și certificarea acoperirii seturilor de variante | 1 nume executate |
| `covering/greedy.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 1 nume executate |
| `covering/guarantee_portfolio.py` | Geometrie și certificarea acoperirii seturilor de variante | 4 nume executate |
| `covering/higher_hits.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 7 nume executate |
| `covering/hypergeo.py` | Probabilități combinatorice, enumerare și acoperire exactă | 1 nume executate |
| `covering/ilp.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 2 nume executate |
| `covering/large_pool.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 11 nume executate |
| `covering/lr_any_prize_649.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 4 nume executate |
| `covering/lr_any_prize_joker.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 3 nume executate |
| `covering/lr_budget.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 1 nume executate |
| `covering/lr_pool_budget.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 13 nume executate |
| `covering/lr_pool_ranking.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 3 nume executate |
| `covering/lr_portfolio.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 11 nume executate |
| `covering/lr_schemes.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 35 nume executate |
| `covering/lr_selection.py` | Scheme LR, restricții, cost sau probabilitate exactă a obiectivului | 10 nume executate |
| `covering/multihit.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 2 nume executate |
| `covering/probability.py` | Probabilități combinatorice, enumerare și acoperire exactă | 9 nume executate |
| `covering/profile_swap.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 6 nume executate |
| `covering/refined_covers.py` | Geometrie și certificarea acoperirii seturilor de variante | 6 nume executate |
| `covering/search.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 2 nume executate |
| `covering/weighted.py` | Generarea sau alegerea variantelor de acoperire în limitele bugetului | 5 nume executate |

### Motor, încărcare și benchmark

| Modul | Funcție verificată | Observat în procesele instrumentate |
|---|---|---|
| `loto_enterprise/__init__.py` | Inițializarea pachetului și exporturile acestuia | modul încărcat |
| `loto_enterprise/benchmark/__init__.py` | Inițializarea pachetului și exporturile acestuia | 1 nume executate |
| `loto_enterprise/benchmark/bench_cache.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 8 nume executate |
| `loto_enterprise/benchmark/chronological_validation.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 2 nume executate |
| `loto_enterprise/benchmark/curated.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 4 nume executate |
| `loto_enterprise/benchmark/decision.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 24 nume executate |
| `loto_enterprise/benchmark/freshness.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 12 nume executate |
| `loto_enterprise/benchmark/hardware.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 3 nume executate |
| `loto_enterprise/benchmark/hit_target.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 5 nume executate |
| `loto_enterprise/benchmark/hw_sampler.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 8 nume executate |
| `loto_enterprise/benchmark/lr_hypotheses.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 7 nume executate |
| `loto_enterprise/benchmark/matrix_reader.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | sintaxă verificată |
| `loto_enterprise/benchmark/methods.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 11 nume executate |
| `loto_enterprise/benchmark/methods_common.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 7 nume executate |
| `loto_enterprise/benchmark/methods_compact.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 6 nume executate |
| `loto_enterprise/benchmark/methods_markov.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 2 nume executate |
| `loto_enterprise/benchmark/reporting.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 5 nume executate |
| `loto_enterprise/benchmark/restricted_decision.py` | Catalog de scoreri, metode comparate și selecția sub ipoteza LR | 3 nume executate |
| `loto_enterprise/benchmark/runner.py` | Evaluare cronologică, cache, praguri și instrumente de benchmark | 25 nume executate |
| `loto_enterprise/core/__init__.py` | Inițializarea pachetului și exporturile acestuia | modul încărcat |
| `loto_enterprise/core/adaptive_feedback.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 11 nume executate |
| `loto_enterprise/core/backtesting.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 20 nume executate |
| `loto_enterprise/core/base_threshold.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 8 nume executate |
| `loto_enterprise/core/cache_io.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 5 nume executate |
| `loto_enterprise/core/draw_validation.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 1 nume executate |
| `loto_enterprise/core/dynamic_pools.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 2 nume executate |
| `loto_enterprise/core/dynamic_winner.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 8 nume executate |
| `loto_enterprise/core/generated_prize.py` | Legarea variantelor de categoriile și sumele din arhiva oficială | 1 nume executate |
| `loto_enterprise/core/history.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 5 nume executate |
| `loto_enterprise/core/lotteries.py` | Istoric, geometrie, adaptare și reluare pe extrageri | 32 nume executate |
| `loto_enterprise/core/lr_budget_forecast.py` | Recomandare LR, comparația bazelor, momentul alegerii și evaluare | 17 nume executate |
| `loto_enterprise/core/lr_mail_forecast.py` | Recomandare LR, comparația bazelor, momentul alegerii și evaluare | 1 nume executate |
| `loto_enterprise/core/lr_range_analysis.py` | Recomandare LR, comparația bazelor, momentul alegerii și evaluare | 12 nume executate |
| `loto_enterprise/core/lr_range_timing.py` | Recomandare LR, comparația bazelor, momentul alegerii și evaluare | 3 nume executate |
| `loto_enterprise/core/lr_walk_forward.py` | Recomandare LR, comparația bazelor, momentul alegerii și evaluare | 9 nume executate |
| `loto_enterprise/core/method_selector.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 28 nume executate |
| `loto_enterprise/core/official_prizes.py` | Legarea variantelor de categoriile și sumele din arhiva oficială | 31 nume executate |
| `loto_enterprise/core/pool_prize.py` | Legarea variantelor de categoriile și sumele din arhiva oficială | 6 nume executate |
| `loto_enterprise/core/pool_selection.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 3 nume executate |
| `loto_enterprise/core/prize_categories.py` | Legarea variantelor de categoriile și sumele din arhiva oficială | 7 nume executate |
| `loto_enterprise/core/ranking.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 5 nume executate |
| `loto_enterprise/core/ro_text.py` | Formatarea textului afișat în română | 1 nume executate |
| `loto_enterprise/core/score_validation.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 1 nume executate |
| `loto_enterprise/core/walk_forward_adapter.py` | Validare pas cu pas și identitatea versiunii din cache | 34 nume executate |
| `loto_enterprise/core/wf_sig.py` | Validare pas cu pas și identitatea versiunii din cache | 4 nume executate |
| `loto_enterprise/engine/__init__.py` | Inițializarea pachetului și exporturile acestuia | modul încărcat |
| `loto_enterprise/engine/pipeline.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 2 nume executate |
| `loto_enterprise/engine/scoring.py` | Clasament, departajări, reguli de pool și generarea rezultatului | 8 nume executate |

### Studii și utilitare de analiză

Din cele 132 de fișiere Python publicate în `scripts/analysis/`, codul de validare cronologică `nested_wfv_2026_11_18.py` rulează și din aplicație. Paginile de analiză pot citi rezultate produse anterior de alte studii. Experimentele istorice păstrează catalogul și parametrii declarați la data lor. Am verificat separat inventarul local extins, care cuprinde și materiale nepublicate. Unele studii de căutare, rulări Monte Carlo și generări de catalog au primit numai verificare de sursă și de sintaxă.

| Fișier în `scripts/analysis/` | Rol | Observat în procesele instrumentate |
|---|---|---|
| `advanced_screen_2026-11-01.py` | Studiu, analiză sau instrument de cercetare offline | 19 nume executate |
| `analyze_lr_schemes.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `archived_ro_2026_11_21.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `audit_all_designs_2026_11_04.py` | Verificare sau comparație offline | sintaxă verificată |
| `audit_application.py` | Verificare sau comparație offline | 5 nume executate |
| `audit_claude_wheeling_2026_10_18.py` | Verificare sau comparație offline | sintaxă verificată |
| `audit_consecutive_2026_10_16.py` | Verificare sau comparație offline | sintaxă verificată |
| `audit_exhaustive_2026-11-01.py` | Verificare sau comparație offline | sintaxă verificată |
| `audit_method_windows.py` | Verificare sau comparație offline | 3 nume executate |
| `audit_output.py` | Verificare sau comparație offline | 9 nume executate |
| `audit_patterns_and_designs.py` | Verificare sau comparație offline | 5 nume executate |
| `audit_pool_odds.py` | Verificare sau comparație offline | sintaxă verificată |
| `audit_selection_replay.py` | Verificare sau comparație offline | 8 nume executate |
| `bench_anneal_hits.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_base_threshold.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `bench_budget_climb.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_budget_cover.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `bench_large_pool_certified.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_lr_groups_2026_11_05.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_lr_replay_2026_11_04.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_probability_py314.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_py314_scoring.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bench_wheel_generation.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `benchmark_large_pool_2026_10_30.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `benchmark_py314_wf_backfill.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `compact_foreign_2026_11_20.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `compact_ro_2026_11_20.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `compare_certified_history_2026_11_04.py` | Verificare sau comparație offline | sintaxă verificată |
| `compare_wheel_methods_2026-11-01.py` | Verificare sau comparație offline | sintaxă verificată |
| `cover_geometry_screen_2026-11-01.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `dynamic_vs_fixed_ro_2026_11_21.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `anneal.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `bounds_table.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lib_exact.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pipeline.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `run_jobs.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `surrogate.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `verify_extended_designs.py` | Verificare sau comparație offline | sintaxă verificată |
| `external_replication.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `forward_test.py` | Studiu, analiză sau instrument de cercetare offline | 15 nume executate |
| `frozen_dmd.py` | Studiu, analiză sau instrument de cercetare offline | 4 nume executate |
| `sprt_operating.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `forward_test_joker.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `frozen_ens.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `gen_budget_designs.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_budget_designs_pick5.py` | Producere sau verificare offline de date și scheme | 2 nume executate |
| `gen_covering_designs.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_design_manifest.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_lotto_designs.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_refined_covers.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_refined_covers_ilp.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gen_refined_lotto.py` | Producere sau verificare offline de date și scheme | sintaxă verificată |
| `gpu_vs_cpu_2026-11-01.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `history_fit_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `history_window_summary_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | 1 nume executate |
| `hit5plus_screen2_2026-11-01.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `hit5plus_screen_2026-11-01.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `hit_evidence_2026_11_04.py` | Studiu, analiză sau instrument de cercetare offline | 6 nume executate |
| `hit_screen_2026-10-09.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `hit_screen_posthoc_2026-10-09.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `import_ljcr_designs.py` | Producere sau verificare offline de date și scheme | 3 nume executate |
| `import_lr_schemes.py` | Producere sau verificare offline de date și scheme | 6 nume executate |
| `legacy_methods_2026_11_20.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `__init__.py` | Studiu, analiză sau instrument de cercetare offline | modul încărcat |
| `dynamic_bank.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `dynamic_trajectories.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `methods_bayes.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `methods_evalue.py` | Studiu, analiză sau instrument de cercetare offline | 1 nume executate |
| `methods_experimental.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `methods_learning.py` | Studiu, analiză sau instrument de cercetare offline | 2 nume executate |
| `methods_recency.py` | Studiu, analiză sau instrument de cercetare offline | 14 nume executate |
| `methods_relational.py` | Studiu, analiză sau instrument de cercetare offline | 8 nume executate |
| `methods_selfcalib.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `methods_wave2.py` | Studiu, analiză sau instrument de cercetare offline | 12 nume executate |
| `lr_catalogue_audit_2026_11_15.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lr_near_hits_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `lr_pair_mixture_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lr_pool_rules_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lr_pool_study_2026_11_04.py` | Studiu, analiză sau instrument de cercetare offline | 7 nume executate |
| `lr_portfolio_bounds_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lr_requested_geometry_2026_11_04.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `lr_tail_2026_11_05.py` | Studiu, analiză sau instrument de cercetare offline | 11 nume executate |
| `lr_tail_objective_2026_11_05.py` | Studiu, analiză sau instrument de cercetare offline | 9 nume executate |
| `method_screen_2026-10-10.py` | Studiu, analiză sau instrument de cercetare offline | 21 nume executate |
| `methods_wave4_bank_2026_11_09.py` | Studiu, analiză sau instrument de cercetare offline | 25 nume executate |
| `nested_wfv_2026_11_18.py` | Validare activă în Re-Bench și studiu istoric | 10 nume executate |
| `pattern_base_reduction.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pattern_constraints.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pattern_predictability.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pool_adaptive_649_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `pool_adaptive_cache_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 8 nume executate |
| `pool_adaptive_candidates_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 10 nume executate |
| `pool_candidates_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 11 nume executate |
| `pool_combination_cache_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 8 nume executate |
| `pool_combination_eval_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 1 nume executate |
| `pool_combinations_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `pool_combinations_649_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 4 nume executate |
| `pool_combinations_649_2026_10_19_initial.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pool_extension_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 8 nume executate |
| `pool_extension_cache_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 14 nume executate |
| `pool_extension_candidates_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 11 nume executate |
| `pool_extension_eval_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 4 nume executate |
| `pool_extension_statistics_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 5 nume executate |
| `erratum_2026-10-07.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `pool_hit_experiment.py` | Studiu, analiză sau instrument de cercetare offline | 15 nume executate |
| `pool_score_cache_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 7 nume executate |
| `pool_selection_649_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `prediction_screen_2026-10-05.py` | Studiu, analiză sau instrument de cercetare offline | 7 nume executate |
| `preregister_frequency_joker4_gate_2026_11_14.py` | Studiu, analiză sau instrument de cercetare offline | 9 nume executate |
| `preregister_weibull_k085_gate_2026_11_09.py` | Studiu, analiză sau instrument de cercetare offline | 8 nume executate |
| `probability_opt_2026_11_04.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `prove_lr_optimality.py` | Studiu, analiză sau instrument de cercetare offline | 3 nume executate |
| `refine_budget_designs.py` | Producere sau verificare offline de date și scheme | 11 nume executate |
| `render_pool_adaptive_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `render_pool_combinations_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `render_pool_extension_2026_10_19.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `shared_bias_2026-10-09.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `spatial_math_screen_2026-10-05.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `tail_joker_1000.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `tail_pools_1000.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `tail_references_1000.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `tail_signals_1000.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `tail_walk_forward_1000.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `validate_dynamic_lr_2026_11_19.py` | Verificare sau comparație offline | sintaxă verificată |
| `validate_pool_adaptive_2026_10_19.py` | Verificare sau comparație offline | 6 nume executate |
| `validate_pool_combinations_2026_10_19.py` | Verificare sau comparație offline | 10 nume executate |
| `validate_pool_extension_2026_10_19.py` | Verificare sau comparație offline | 6 nume executate |
| `validate_pool_screen_2026_10_19.py` | Verificare sau comparație offline | 6 nume executate |
| `validation_window_study_2026_11_18.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `verify_lr_test_batches_2026_11_04.py` | Verificare sau comparație offline | sintaxă verificată |
| `wave3_quick_eval_2026_11_08.py` | Studiu, analiză sau instrument de cercetare offline | sintaxă verificată |
| `wave4_pattern_screen_2026_11_09.py` | Studiu, analiză sau instrument de cercetare offline | modul încărcat |

### Lansatoare și dependențe externe

`START_8000.bat` și `ACTUALIZARI.bat` au fost verificate prin citirea comenzilor și probe de selecție a proceselor. Am verificat sintaxa PowerShell pentru `scripts/launcher_git.ps1` și `scripts/find_python315.ps1`. Testele de sincronizare au folosit depozite temporare. Apelurile de instalare a pachetelor și oprirea proceselor existente ale utilizatorului nu au fost executate. Fluxul de publicare a istoricului verifică modificările față de Git și păstrează schema de adăugare la sfârșit.

## Cele 35 de diferențe de la începutul auditului

Coloanele despre CSV reproduc situația anterioară corecturii. Extracția Joker din 04.03.2021 a apărut ulterior la confruntarea lunii vecine. Verificarea finală acoperă cele 1.319 rezultate din baza de premii și toate extragerile lunilor afectate.

| Joc | Data oficială | În arhiva oficială | În CSV înainte de corectură | Combinația oficială figura în altă zi la |
|---|---|---|---|---|
| 6/49 | 05.02.2023 | 23, 8, 19, 11, 31, 33 | Dată absentă | 09.02.2023 |
| 6/49 | 26.01.2025 | 25, 49, 28, 33, 23, 43 | Dată absentă | 27.01.2025 |
| Joker | 28.02.2021 | 34, 45, 20, 37, 2 + J18 | Dată absentă | — |
| Joker | 19.09.2021 | 23, 3, 38, 7, 15 + J9 | 23, 3, 38, 7, 15 + J19 | — |
| Joker | 09.03.2023 | 30, 18, 43, 22, 4 + J16 | 30, 18, 43, 22, 14 + J16 | — |
| Joker | 18.08.2024 | 25, 11, 40, 10, 32 + J12 | 30, 26, 1, 36, 29 + J13 | 22.08.2024 |
| Joker | 22.08.2024 | 30, 26, 1, 36, 29 + J13 | 25, 11, 40, 10, 32 + J12 | 18.08.2024 |
| Joker | 26.01.2025 | 26, 4, 2, 32, 28 + J6 | Dată absentă | 27.01.2025 |
| Joker | 06.03.2025 | 33, 3, 41, 38, 37 + J19 | 33, 3, 41, 38, 27 + J19 | — |
| 5/40 | 01.05.2021 | 24, 2, 29, 17, 26, 40 | 23, 20, 18, 30, 26, 40 | — |
| 5/40 | 06.05.2021 | 10, 34, 36, 37, 38, 1 | Dată absentă | — |
| 5/40 | 13.05.2021 | 22, 7, 13, 1, 35, 12 | Dată absentă | — |
| 5/40 | 20.05.2021 | 27, 6, 40, 36, 21, 16 | Dată absentă | — |
| 5/40 | 27.05.2021 | 10, 1, 6, 12, 15, 17 | Dată absentă | — |
| 5/40 | 03.06.2021 | 36, 25, 39, 19, 18, 27 | Dată absentă | — |
| 5/40 | 10.06.2021 | 6, 34, 39, 8, 33, 21 | Dată absentă | — |
| 5/40 | 17.06.2021 | 12, 21, 16, 3, 5, 37 | Dată absentă | — |
| 5/40 | 24.06.2021 | 22, 33, 38, 18, 13, 14 | Dată absentă | — |
| 5/40 | 05.08.2021 | 34, 12, 11, 20, 19, 5 | Dată absentă | — |
| 5/40 | 12.08.2021 | 3, 33, 25, 13, 17, 20 | Dată absentă | — |
| 5/40 | 19.08.2021 | 2, 9, 30, 21, 8, 26 | Dată absentă | — |
| 5/40 | 26.08.2021 | 16, 12, 39, 24, 17, 40 | Dată absentă | — |
| 5/40 | 05.12.2021 | 18, 35, 31, 30, 32, 25 | 31, 39, 37, 29, 21, 22 | — |
| 5/40 | 12.12.2021 | 32, 35, 25, 33, 36, 6 | Dată absentă | — |
| 5/40 | 19.12.2021 | 19, 25, 29, 20, 7, 10 | Dată absentă | — |
| 5/40 | 31.12.2021 | 2, 8, 32, 27, 12, 11 | 12, 21, 13, 25, 1, 24 | — |
| 5/40 | 07.04.2022 | 15, 8, 19, 34, 26, 24 | Dată absentă | — |
| 5/40 | 14.04.2022 | 9, 23, 13, 20, 29, 15 | Dată absentă | — |
| 5/40 | 23.04.2022 | 5, 19, 25, 14, 17, 32 | 24, 17, 30, 37, 27, 28 | — |
| 5/40 | 28.04.2022 | 21, 20, 40, 37, 23, 12 | Dată absentă | — |
| 5/40 | 02.06.2022 | 4, 1, 10, 29, 34, 40 | Dată absentă | — |
| 5/40 | 09.06.2022 | 35, 31, 36, 26, 8, 20 | Dată absentă | — |
| 5/40 | 16.06.2022 | 18, 31, 13, 28, 12, 16 | Dată absentă | — |
| 5/40 | 23.06.2022 | 30, 3, 38, 8, 35, 15 | Dată absentă | — |
| 5/40 | 30.06.2022 | 27, 12, 9, 21, 28, 22 | Dată absentă | — |

## Limitele verificării

Datele anterioare arhivei lunare au grade diferite de confirmare, precizate la începutul raportului. Rezultatele de predicție pe arhive sunt retrospective. Mostrele pe bugete, jocuri și configurații sunt enumerate în rapoartele dedicate. Mesajele, oprirea sistemului și erorile de rețea din teste au fost simulate.

## Probă de reluare a interfeței

Într-o copie izolată, cu istoric de test și bază SQLite separată, am pornit interfața și workerul, apoi am cerut în browser generarea la 6/49. Transportul de e-mail simulat a pierdut confirmarea după predarea mesajului, iar apelul simulat de oprire a reușit. Ambele au găsit în baza de date marcajul `claimed` înainte de executare. După terminarea generării și a validării, stările au fost `smtp=uncertain` și `shutdown=done`.

După repornirea procesului interfeței și reîncărcarea paginii, browserul a afișat rezultatul recuperat și mesajul „Acțiunea nu se repetă automat”. Jurnalul transporturilor a consemnat în total un apel de e-mail și un apel de oprire. E-mailul și oprirea PC-ului au fost simulate pe întreaga durată a probei.
