# Cât istoric ajută la alegerea numerelor?

Studiu pe arhivele celor 13 jocuri și pe Urna 2 Joker, realizat la 18.11.2026. Procentele testate sunt 10%, 30%, 70% și 100%. Am variat separat perioada pe care sunt comparate metodele și perioada pe care o primește o metodă fixă pentru calcularea scorurilor. În fiecare experiment, cele patru reguli au întâlnit aceleași extrageri finale, cu același pool și același număr de variante în cadrul jocului. La 5/40 am folosit toate cele șase numere extrase pentru hiturile variantei de cinci. La EuroMillions am evaluat numerele principale.

**Pe schemele oficiale românești, 10% a obținut două extrageri cu 5+ pe bilet, față de zero la 30%, 70% și 100%.** Perioada anterioară a ales însă 30% la 6/49 și 5/40 și 10% la Joker. Aplicate ulterior, aceste alegeri au înregistrat zero rezultate cu 5+ pe biletele celor trei scheme. Rezultatul nu justifică aplicarea automată a procentului de 10%.

## Lungimea perioadei de validare

Fiecare metodă primește toate extragerile dinaintea zilei țintă. După minimum 250 de extrageri de încălzire și 100 de zile cu predicții evaluate, selectorul compară metodele pe ultimele 10/30/70/100% din zilele eligibile anterioare. Numărăm toate extragerile din fiecare zi, fără a folosi o extragere ca informație pentru alta din aceeași zi. Sunt nouă metode comune din registru, plus câte o metodă din recomandarea auditată la 6/49, 5/40 și Urna 2. Este un selector experimental cu reguli fixe de departajare. Re-Bench de producție are propria matrice de metode și propriile reguli de calificare.

Pentru fiecare joc am ales procentul pe primele 70% dintre zilele rămase, apoi l-am evaluat pe ultimele 30%, separat pentru obiectivul pe bilet și cel în pool. În perioada finală, procentul ales rămâne fix, iar selectorul zilnic poate folosi rezultatele deja cunoscute. Totalul este de 12.339 de extrageri finale. Cele 561 de rezultate ale Urnei 2 provin din aceleași extrageri Joker și se raportează separat.

### Obiectivul pe variantă

Tabelul numără extragerile în care cel puțin o variantă are 5+ hituri. La Urna 2 numărăm potrivirile exacte. Codurile străine sunt scheme de referință simulate pe un pool de zece numere. În România folosim rândurile autentice ale codurilor 48, 24 și 16. La egalitate de 5+ selectorul compară 4+, apoi media hiturilor pe cea mai bună variantă.

| Joc și măsură | Extrageri | 10% | 30% | 70% | 100% | Ales anterior |
|---|---:|---:|---:|---:|---:|---:|
| RO · Loto 6/49 · Cod 48 | 686 | 1 | 0 | 0 | 0 | 30% |
| RO · Joker · Cod 24 | 561 | 0 | 0 | 0 | 0 | 10% |
| RO · Joker · Urna 2 · top-1 | 561 | 37 | 34 | 38 | 32 | 100% |
| RO · Loto 5/40 · Cod 16 | 434 | 1 | 0 | 0 | 0 | 30% |
| DE · Lotto 6aus49 · Cod 49 simulat | 1641 | 2 | 1 | 1 | 0 | 10% |
| PL · Lotto · Cod 49 simulat | 1837 | 1 | 1 | 1 | 1 | 70% |
| ES · La Primitiva · Cod 49 simulat | 1159 | 0 | 1 | 0 | 0 | 30% |
| AT · Lotto 6 aus 45 · Cod 49 simulat | 1006 | 0 | 0 | 0 | 0 | 10% |
| BE · Lotto · Cod 49 simulat | 368 | 0 | 0 | 0 | 0 | 10% |
| HU · Hatoslottó · Cod 49 simulat | 454 | 0 | 1 | 1 | 1 | 10% |
| CZ · Sportka · Cod 49 simulat | 2164 | 1 | 0 | 0 | 0 | 70% |
| SK · Loto · Cod 49 simulat | 1426 | 0 | 1 | 1 | 2 | 70% |
| BG · Toto 2 6/49 · Cod 49 simulat | 108 | 0 | 0 | 0 | 0 | 30% |
| EU · EuroMillions · Cod 24 simulat | 495 | 0 | 0 | 0 | 0 | 10% |

Pe toate jocurile principale, la 10/30/70/100% au fost 6/5/4/4 extrageri cu 5+ pe variantă și 149/131/144/141 cu 4+. Procentul ales anterior pentru fiecare joc a avut 5 rezultate cu 5+ și 144 cu 4+. În România, cele trei scheme însumează 2/0/0/0 rezultate cu 5+ și 15/10/12/9 cu 4+, iar procentele prestabilite dau 0 și 10. În aceste totaluri o extragere contează o singură dată, indiferent câte variante ating pragul.

### Obiectivul în pool

Aceeași procedură selectează metoda după 5+ în pool, apoi după 4+ și media hiturilor în pool. Cele 17 extrageri cu 5+ din coloana de 30% au produs trei extrageri cu 5+ și pe o variantă. La 100% raportul este de 13 în pool și cinci pe variante.

| Joc și măsură | Extrageri | 10% | 30% | 70% | 100% | Ales anterior |
|---|---:|---:|---:|---:|---:|---:|
| RO · Loto 6/49 · pool 9 | 686 | 2 | 1 | 1 | 1 | 10% |
| RO · Joker · pool 10 | 561 | 0 | 1 | 0 | 0 | 30% |
| RO · Joker · Urna 2 · top-1 | 561 | 37 | 34 | 38 | 32 | 100% |
| RO · Loto 5/40 · pool 8 | 434 | 2 | 1 | 1 | 0 | 30% |
| DE · Lotto 6aus49 · pool 10 | 1641 | 1 | 4 | 4 | 3 | 100% |
| PL · Lotto · pool 10 | 1837 | 2 | 2 | 3 | 2 | 70% |
| ES · La Primitiva · pool 10 | 1159 | 2 | 3 | 2 | 2 | 10% |
| AT · Lotto 6 aus 45 · pool 10 | 1006 | 1 | 1 | 1 | 1 | 30% |
| BE · Lotto · pool 10 | 368 | 0 | 0 | 0 | 0 | 30% |
| HU · Hatoslottó · pool 10 | 454 | 0 | 1 | 1 | 1 | 30% |
| CZ · Sportka · pool 10 | 2164 | 4 | 3 | 0 | 2 | 70% |
| SK · Loto · pool 10 | 1426 | 0 | 0 | 0 | 1 | 30% |
| BG · Toto 2 6/49 · pool 10 | 108 | 0 | 0 | 0 | 0 | 100% |
| EU · EuroMillions · pool 10 | 495 | 0 | 0 | 0 | 0 | 10% |

În ipoteza extragerilor uniforme și independente, diferența dintre două decizii cauzale la aceeași geometrie are medie condiționată zero. Pentru comparațiile cu 100% s-a folosit un amestec fix de patru pariuri statistice pe diferențele pereche. La 108 comparații, pragul comun al statisticii comparative e pentru un risc cumulat de 5% este 2.160. Maximul observat este 2,085. Arhivele au mai fost folosite pentru proiectare și cercetare, iar evenimentele 5+ pe variante sunt rare.

## Lungimea istoricului primit de o metodă fixă

După 250 de zile de încălzire, metoda primește ultimele 10/30/70/100% dintre zilele anterioare, rotunjite în sus și păstrate integral. La fiecare extragere refacem scorurile și poolul cu limita curentă de două consecutive. Primele 70% dintre zilele eligibile servesc alegerii procentului. Rezultatele următoare provin din ultimele 30%, cu 12.643 de extrageri finale și 592 de observații suplimentare pentru bila Joker. Perioada diferă de primul experiment deoarece nu este nevoie de alte 100 de zile pentru clasarea mai multor metode.

### Metoda frequency pe toate jocurile

În România numărăm 5+ pe o variantă oficială, iar în țările străine 5+ în poolul de zece numere. La Joker Urna 2 numărăm bilele ghicite. Metoda folosește implementarea frequency din registru, cu ponderare pentru recență.

| Joc și măsură | Extrageri | 10% | 30% | 70% | 100% | Ales anterior |
|---|---:|---:|---:|---:|---:|---:|
| RO · Loto 6/49 · Cod 48 | 718 | 0 | 0 | 0 | 1 | 30% |
| RO · Joker · Cod 24 | 592 | 0 | 0 | 0 | 0 | 100% |
| RO · Joker · Urna 2 · top-1 | 592 | 22 | 21 | 19 | 17 | 70% |
| RO · Loto 5/40 · Cod 16 | 465 | 0 | 0 | 0 | 0 | 100% |
| DE · Lotto 6aus49 · pool 10 | 1671 | 1 | 1 | 3 | 1 | 10% |
| PL · Lotto · pool 10 | 1867 | 2 | 0 | 0 | 0 | 10% |
| ES · La Primitiva · pool 10 | 1189 | 1 | 1 | 0 | 2 | 70% |
| AT · Lotto 6 aus 45 · pool 10 | 1036 | 2 | 1 | 1 | 0 | 30% |
| BE · Lotto · pool 10 | 398 | 2 | 2 | 2 | 1 | 10% |
| HU · Hatoslottó · pool 10 | 484 | 1 | 0 | 0 | 0 | 30% |
| CZ · Sportka · pool 10 | 2148 | 1 | 2 | 4 | 3 | 10% |
| SK · Loto · pool 10 | 1412 | 4 | 3 | 1 | 2 | 70% |
| BG · Toto 2 6/49 · pool 10 | 138 | 0 | 1 | 1 | 1 | 30% |
| EU · EuroMillions · pool 10 | 525 | 0 | 0 | 0 | 0 | 10% |

Pe 10.868 de extrageri din cele zece jocuri străine sunt 14/11/12/10 evenimente 5+ în pool. Aplicarea ferestrei alese pe calibrare produce nouă. Pe cele 1.775 de extrageri românești, aceleași durate dau 0/0/0/1 evenimente 5+ pe o variantă.

### Metodele românești din recomandarea din 16 noiembrie

Referințele sunt metodele identificate în recomandarea auditată la 16.11.2026. Înghețarea lor evită căutarea unui alt câștigător după ce am văzut perioada finală.

| Joc și măsură | Extrageri | 10% | 30% | 70% | 100% | Ales anterior |
|---|---:|---:|---:|---:|---:|---:|
| RO · Loto 6/49 · Cod 48 · naive_bayes_last | 718 | 0 | 1 | 0 | 1 | 10% |
| RO · Joker · Cod 24 · runs_persistence | 592 | 0 | 0 | 0 | 0 | 10% |
| RO · Joker · Urna 2 · top-1 · ridge_pooled_feats | 592 | 33 | 39 | 41 | 41 | 70% |
| RO · Loto 5/40 · Cod 16 · croston_interval | 465 | 0 | 1 | 1 | 1 | 30% |

În total, testul de antrenare are 72 de comparații pereche ale ferestrelor scurte și ale regulii preselectate cu 100%. Valoarea p minimă este 0,495, iar cea minimă ajustată Holm este 1. Verificarea independentă a reprodus 52.940 de evaluări frequency prin numărarea directă a hiturilor.

## Integrarea în aplicație

Re-Bench folosește implicit 10/30/70/100% la toate jocurile, atât din interfață, cât și din CLI. CLI acceptă și alte liste explicite de procente distincte. Fereastra de 100% din benchmark păstrează 80 de extrageri pentru inițializare. Deciziile existente cu 60% păstrează măsurătorile efectuate. La următoarea rulare completă se evaluează grila nouă.

În „Analiză & Clasament”, selectorul stabilește cât istoric simulează următoarea cerere. Implicitul acestei reluări este 30%. Cererea păstrează alegerea și după o modificare ulterioară în UI, iar recuperarea din cache și din SQLite păstrează procentul inițial. Lungimea simulării schimbă numărul de extrageri raportate. În setarea separată pentru antrenare, 0 înseamnă întregul istoric disponibil. Studiile folosesc procente din zile întregi. Benchmarkul și reluarea obișnuită stabilesc dimensiunile din numărul de rânduri și au perioade de încălzire diferite.

Validarea de integrare a executat CLI-ul real pe 11 țări sau grupări, cu 320 de rânduri reale per istoric, două metode și patru ferestre. Toate cele 112 evaluări au finalizat 32/96/224/240 de extrageri la 10/30/70/100%. Pe câte 72 de extrageri românești, două cereri trimise prin coadă către worker au simulat 7 extrageri la 10% și 50 la 70% pentru fiecare dintre cele trei coduri. Au fost verificate și reluarea manuală, cea automată după Re-Bench și recuperarea din cache și SQLite.

Pe cele zece istorice străine, 20 de joburi prin worker, la 10% și 70%, au acoperit toate cele 1.280 de extrageri așteptate în proba de 160 de rânduri per joc. Austria a folosit rezerva frequency pentru o decizie salvată pe altă țintă. Dacă utilizatorul schimbă efectiv ținta după generare, interfața identifică schimbarea deciziei. Într-o altă probă, pe 32 de rânduri per joc, toate cele 52 de combinații joc/fereastră au fost recuperate din cache. Pe datele comune între ferestre, hiturile au fost identice la aceeași metodă și schemă.

Verificare cumulată pe codul livrat, cu reluarea fișierelor afectate de ultimele schimbări, 7.556 de teste trecute, 25 omise și zero eșecuri. În browser am verificat procentele, pragurile 4+/5+, rezultatele Urnei 2, salvarea setării de 70% și mesajul privind ferestrele deciziei anterioare. Cele 22 de fișiere de date, decizii și setări urmărite în această verificare au păstrat aceleași amprente SHA-256.

## Reproducere

Din rădăcina proiectului, pe aceeași versiune a celor 13 CSV-uri, creează directorul de rezultate și rulează comenzile de mai jos. Fișierul compact publicat pentru interfață conține și amprentele SHA-256 ale surselor.

```powershell
New-Item -ItemType Directory -Force scratch/history_windows_replay
python -m scripts.analysis.validation_window_study_2026_11_18 --output scratch/history_windows_replay/validation.json
python -m scripts.analysis.history_fit_2026_11_18 --output scratch/history_windows_replay/fit
python -m scripts.analysis.history_window_summary_2026_11_18 scratch/history_windows_replay/validation.json scratch/history_windows_replay/fit/results.json scratch/history_windows_replay/ui.json --as-of 2026-11-18
```
