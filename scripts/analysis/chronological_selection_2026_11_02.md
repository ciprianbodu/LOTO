# Selecție cronologică, comparație la buget egal și registru prospectiv

Implementare pe `codex/chronological-evaluation`, separat de `main`. Aplicația activă și datele benchmarkului din checkout-ul utilizatorului nu au fost schimbate sau repornite.

## Constatări în cele trei module

`benchmark/decision.py::decide_optimal_config_for_pool` alege scoreri din foldurile primite, prin ferestre comune, porți de completitudine/departajare și corecția Holm. Foldurile benchmarkului curent provin din istoricul disponibil la momentul rulării. Corecția între metode nu transformă reevaluarea aceleiași perioade într-o evaluare exterioară selecției.

`core/method_selector.py::get_winner_name` și `get_ensemble_for_game` rezolvă decizia salvată acum, pe joc și dimensiunea poolului. API-ul nu primește o dată istorică a deciziei. `core/backtesting.py::_retroactive_step_stateless` taie corect istoricul înaintea întregii zile țintă, dar motorul consultă această decizie curentă. Prin urmare, rezultatele vechi pot fi evaluate cu o metodă aleasă ulterior folosind inclusiv acele rezultate. Reconstituirea biletelor rămâne utilă, dar nu validează separat selecția.

Distincția dintre alegerea parametrilor și evaluarea procedurii complete este descrisă și în [documentația oficială scikit-learn despre evaluarea cu selecție internă și test exterior](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html). Implementarea de aici păstrează ordinea temporală; nu folosește împărțiri aleatorii ale istoriei.

## Comportament nou

`core/chronological_evaluation.py` primește un protocol explicit, fixat înaintea rulării: joc, buget B, obiectiv, ținta deciziei metodelor, grila pool/lookback/filtre/garanție/design, numărul de extrageri interne și eșantioanele de probabilitate. Toate metodele din registru intră în foldurile interne; nicio metodă nu este ștearsă sau adăugată într-o listă de excludere. `random` și `frequency` sunt obligatorii. Erorile și predicțiile lipsă sunt înregistrate separat; nu devin hituri zero sau observații eliminate avantajos.

Pentru fiecare țintă exterioară:

1. Se construiesc predicții pentru date anterioare, fiecare din propriul prefix strict anterior zilei sale. Lookback-ul și limitele de zi sunt transmise și metodelor care folosesc acest context.
2. Pe aceste predicții anterioare se rulează algoritmul existent de decizie, inclusiv Holm. Parametrii opționali noi evită schimbarea țintei globale și citirea curării actuale într-un experiment istoric. În producție, argumentele implicite și comportamentul rămân aceleași.
3. Dintre configurațiile declarate se alege cea cu cele mai multe reușite interne pe variantele efective, la obiectivul declarat (`4plus` sau `any_prize`). Ordinea grilei rezolvă egalitățile. Metoda aleasă în interior este reevaluată pe interior pentru această alegere; această optimizare rămâne integral în interior. Doar pasul exterior e evaluare a procedurii.
4. Se generează și se fixează variantele țintei, apoi se consultă rezultatul ei. La Joker, și metoda Urnei 2 este aleasă numai pe trecut. La 5/40 sunt șase numere extrase și cinci numere pe variantă; categoria I verifică primele cinci în ordinea oficială.

Urma salvată include datele folosite la selecție, indicii interni, scorurile configurațiilor, deciziile/încrederea/Holm, toate variantele și amprenta lor. Nu se citesc `best_methods.json` sau `folds.csv` în acest evaluator și nu se scriu decizii, cache-uri WF sau stare adaptivă de producție. Anularea oprește calculul; un eșec nu publică o decizie parțială.

Grila definește procedura evaluată; o opțiune omisă din grilă este fixă, nu pretins optimizată. Formulele metodelor și configurația cercetării au fost dezvoltate retrospectiv. Această evaluare nu poate anula acea selecție și nu este un holdout prospectiv. Intervalele frecvențelor istorice sunt descriptive, fără test de superioritate sau promisiune de avantaj.

## Variante și bani

`core/ticket_experiment.py` compară exact B variante distincte pentru:

- poolul selectat, folosind generatorul existent;
- o căutare limitată pe întregul univers al jocului;
- un martor aleator simplu pe întregul univers.

Un cover care se termină sub plafon este completat explicit cu variante distincte din același pool, până la B. Completarea apare în audit. Dacă poolul nu poate furniza B variante, protocolul este refuzat; dimensiunea poolului și bugetul nu se schimbă tacit. Garanția cerută nu este o afirmație de acoperire completă la un plafon insuficient.

Căutarea pe univers maximizează numai obiectivul declarat pe un eșantion uniform de proiectare, fără extrageri istorice. Nu pretinde optim global. Raportarea probabilităților folosește alt eșantion, comun celor trei strategii. Sunt separate P(cel puțin un premiu), P(4+), probabilitatea exactă a categoriei I, costul, brutul estimat și brutul minus cost. Intervalele Monte Carlo măsoară eroarea simulării uniforme, nu avantajul predictiv.

Pentru 6/49, B variante distincte au probabilitatea categoriei I `B / C(49,6)` indiferent de concentrarea lor în pool, în modelul uniform. Componența și suprapunerea pot schimba probabilitatea categoriilor inferioare. Geometria și categoriile sunt conforme [descrierii oficiale Loto 6/49](https://www.loto.ro/?p=3876).

Prețul pe variantă și taxele totale sunt declarate explicit; în comparația de aici se presupune aceeași împachetare în bilete simple și aceleași taxe pentru fiecare portofoliu. Taxe necompletate înseamnă cost complet/net necunoscute. Estimările folosesc sumele unitare oficiale ale extragerii exacte. Reportul, ordinea 5/40 necunoscută sau moneda istorică neconfirmată nu devin premii în lei. Netul este înainte de impozit. Participarea suplimentară ar putea schimba numărul câștigătorilor și sumele unitare; nici simularea, nici registrul de variante nu dovedesc cumpărarea biletelor.

Probabilitatea recuperării costului viitor nu este inventată în lipsa distribuției premiilor viitoare. Frecvența istorică a recuperării costului este raportată separat, cu numărul cazurilor financiare cunoscute. Sumele lipsă nu devin zero. Bugetul nu crește după pierderi.

## Registru prospectiv

`core/prospective_ledger.py` păstrează local evenimente SQLite de generare, fixare a țintei și rezultat. Workerul înregistrează automat listele complete de variante reale, inclusiv rezultatele reutilizate din cache, înaintea returnării rezultatului către UI. Reutilizarea unui portofoliu nu certifică momentul construcției lui inițiale; evenimentul certifică doar înregistrarea locală actuală. Pașii WF și replay-urile retrospective nu intră în registru. O eroare de înregistrare este vizibilă în rezultatul UI și nu se numește dovadă prospectivă.

Generarea reține toate variantele, data înregistrării UTC, amprentele codului și istoricului, ultima dată din sursă, metoda/ambii scoreri Joker, parametrii, numărul de variante, tariful și martorul aleator la același B. Pentru fiecare generare, utilizatorul fixează separat data viitoare și taxele totale; se salvează și costul complet. O generare fără țintă fixată rămâne **neclasificată prospectiv**. Nu se ghicește ora-limită oficială: regula conservatoare cere fixarea înainte de ora 00:00 a zilei țintă, Europe/Bucharest. Nu se acceptă surse din viitor sau care ating ziua țintă. O înregistrare nu poate schimba ulterior ținta.

Rezultatul se adaugă după încheierea zilei țintă, pentru identitatea exactă din istoricul local și arhiva loto.ro. Datele lipsă sau mai multe extrageri în acea zi cer identificare neambiguă; nu se reconstruiesc variante după aflarea numerelor. Fără premii complete necesare evaluării, rezultatul financiar rămâne în așteptare.

Declanșatoarele SQLite refuză UPDATE/DELETE. Lanțul SHA-256 și exportul detectează modificări accidentale, iar scrierea este tranzacțională și idempotentă. Acesta este un registru local, nu o certificare externă a orei: administratorul poate înlocui întreaga bază și modifica ceasul. Exportarea amprentei către o sursă independentă de timp este necesară pentru o atestare mai puternică. Rezultatele prospective includ martorul la același cost, numărul cazurilor în așteptare și intervale descriptive; nu selectează doar câștigurile.

## Utilizare

În **Analiză & Clasament → Evaluare cronologică și registru prospectiv** există calculul explicit, descărcarea raportului cu variante și controalele registrului. Re-Bench și istoricul curent își păstrează fluxul. Istoricul cu decizia curentă este etichetat explicit ca reconstituire, nu validare cronologică a selecției. Noul calcul nu pornește la startup și nu trimite mail sau shutdown.

Exemplu `protocol.json`:

```json
{
  "game": "loto_6_49",
  "budget": 64,
  "parameters": [
    {"pool_size": 16, "lookback": 0, "guarantee": 6},
    {"pool_size": 16, "lookback": 100, "guarantee": 6}
  ],
  "objective": "4plus",
  "hit_target": 4,
  "inner_draws": 30,
  "variant_price_bani": 800,
  "ticket_fees_bani": 0
}
```

```powershell
python -m scripts.evaluate_chronological --protocol protocol.json --outer-draws 10 --output raport.json
python -m scripts.prospective_registry list
python -m scripts.prospective_registry commit "ID-din-registru" 2026-11-05 --ticket-fees-bani 0
python -m scripts.prospective_registry settle "ID-din-registru"
python -m scripts.prospective_registry summary
python -m scripts.prospective_registry verify
python -m scripts.prospective_registry export registru.json
```

Calea implicită este `RUNTIME_ROOT/prospective_ledger.db`, cu override `LOTO_PROSPECTIVE_DB` sau `--db`. Evaluarea citește numai cache-ul local de premii; verificarea explicită a rezultatului prospectiv poate încărca luna oficială. Fiecare test are propriul registru, în plus față de runtime/coadă/cache separate. Limitele declarate de resurse sunt 100.000 variante și 20 milioane de intrări în matricea de proiectare; depășirea cere reducerea explicită a bugetului sau eșantionului, fără plafon ascuns.

## Probe reale

Rapoartele [6/49](chronological_2026_11_02/loto_6_49.md), [Joker](chronological_2026_11_02/joker.md) și [5/40](chronological_2026_11_02/loto_5_40.md), însoțite de JSON-urile cu variante și decizii, folosesc toate cele **53 metode**, poolurile **16/12/19**, lookback candidat **0/100**, ultimele **3 extrageri** (25 și 29 octombrie, 1 noiembrie 2026), câte **30 extrageri interne** și **64 variante distincte** pe strategie. Probabilități: 10.000 extrageri uniforme independente de cele 4.000 folosite la căutare. Tarife declarate: 8/7/5 lei; ipoteză explicită: taxe totale 0, înainte de impozit. Premiile din noiembrie au fost încărcate de la loto.ro numai în copia de test.

| Joc | Cost total pe strategie | Brut pool / net pool | Brut univers / net univers | Brut martor / net martor |
|---|---:|---:|---:|---:|
| 6/49 | 1.536 lei | 0 / −1.536 lei | 200 / −1.336 lei | 100 / −1.436 lei |
| Joker | 1.344 lei | 0 / −1.344 lei | 303,59 / −1.040,41 lei | 105,15 / −1.238,85 lei |
| 5/40 | 960 lei | 0 / −960 lei | 0 / −960 lei | 0 / −960 lei |

Nicio strategie nu a recuperat costul în aceste nouă cazuri. Trei extrageri pe joc verifică funcționarea, nu demonstrează superioritatea unei strategii. Pentru detaliile și incertitudinile probabilităților, se folosesc rapoartele complete; cifrele de mai sus nu sunt o recomandare de joc.

## Verificare

- Teste noi: invarianta selecției la modificarea viitorului, excluderea întregii zile, ferestre/lookback/context aliniate, toate metodele, scoruri goale/plate/NaN/inf, Joker ambele urne, 5/40 șase extrase, buget distinct fix și comparație cu enumerare independentă pe geometrie mică.
- Registru: tranzacții concurente, idempotentă, interdicții SQL de modificare/ștergere, depistarea alterării, termene în fusul României, ceas dat înapoi, sursă din viitor, rezultate înainte de termen, identități/premii incorecte, costuri și monedă necunoscute, păstrarea variantelor reale ale workerului.
- Browser real, port liber și runtime separat: HTTP 200, submit → worker real → rezultat cu 8 variante și chitanță de înregistrare, fixarea zilei 05.11.2026 înainte de termen, export cu liste integrale și lanț verificat, calcul cronologic explicit cu toate metodele și costuri egale, export JSON. Nicio eroare de browser. Acțiunile mail/shutdown au fost blocate în probă; WF automat existent a fost oprit numai în bootstrap-ul probei, pentru a verifica separat calculul nou explicit.
- 285 verificări ale schemelor de acoperire și ale interfeței existente au trecut. Suita completă, în patru copii locale separate: **4.233 trecute, 22 sărite, zero eșecuri**, în aproximativ 7 minute și 10 secunde. Cele două regresii adăugate ulterior (condiția clasică 0 și Joker fără bila extrasă) au trecut la rerularea modulelor relevante, împreună cu verificarea contractului worker: 91 trecute. Total final verificat: **4.235 teste distincte trecute, 22 sărite**. Numărătoarea suitei este extrasă din jurnalele JUnit, fără a aduna de două ori testele repetate.
- Verificarea completă a găsit o cursă la inițializarea WAL a registrului; inițializarea se face acum o singură dată pe instanță, cu blocare între fire și reîncercare limitată pentru alte instanțe/procese. Testul concurent și un test cu instanțe independente verifică aceeași înregistrare unică. Interfața își actualizează sinteza imediat după fixare/rezultat; lipsa rezultatelor nu este afișată drept net zero.
- Într-un worktree Windows nou, `core.autocrlf` schimba șase designuri LF în CRLF și invalida manifestul existent. `.gitattributes` fixează LF numai pentru aceste șase fișiere. Octeții finali coincid cu HEAD și cu manifestul; nicio variantă și nicio garanție nu s-au schimbat. Prima probă în sandbox a fost întreruptă după refuzuri MSYS și blocaje de mediu; rezultatul complet de mai sus provine din rerularea în directoare locale scurte, cu cozi și cache-uri izolate.
- Renumărare independentă a categoriilor și a sumelor, fără `estimate_generated_prize`: **27 portofolii × 64 variante**, toate costurile, bruturile și neturile coincid cu rapoartele. Probe și numărători compacte: [validation.json](chronological_2026_11_02/validation.json).
