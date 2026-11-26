# Observațiile oficiale și recuperarea selecției LR

Verificarea a pornit de la `ff23ad1761e1534fdf3835c97cb30b0b41f1bcbf`, versiunea `main` pe care s-au reprodus independent cele trei probleme. Probele de registru folosesc extrageri sintetice. Proba de selecție folosește istoricul Joker din Git până la 22.11.2026 și provoacă o eroare temporară de memorie în citirea certificatului. Fiecare rulare are propria bază SQLite și propriul director de cache.

## Ce se schimbă la același eveniment

| Importuri, în ordinea sosirii | La versiunea inițială | După remediere |
|---|---|---|
| A observat la 17, A reconfirmat la 20, B observat la 19 și importat ultimul | B devine activ și primește o evaluare | A rămâne activ. Registrul păstrează reconfirmarea de la 20 și motivul respingerii lui B |
| A → B → A, pagină nouă fără număr oficial de ediție | Ultimul A dispare fără explicație | B rămâne activ. Registrul păstrează observația și conflictul |
| A → B → A, cu ediții oficiale succesive 10, 11 și 12 | Unicitatea eveniment–conținut împiedică a treia revizie | A3 înlocuiește B2. A1 și B2 rămân consultabile, cu evaluările lor inițiale |
| Reimportul ediției vechi A după B | B rămâne activ | B rămâne activ. Reimportul nu adaugă punctaj și nu avansează ora ultimei confirmări |

`loto_enterprise/core/lr_prospective.py` migrează registrul la schema SQLite 3 într-o tranzacție. Identificatorii, legăturile și conținutul vechilor predicții și evaluări se păstrează. Migrarea reconstituie observațiile cunoscute din reviziile existente. Orele reconfirmărilor omise de vechea versiune nu pot fi recuperate din acele revizii.

Scrierea unei observații și compararea cu ultima confirmare au loc sub aceeași blocare SQLite. Orele se compară în UTC. Registrul deosebește o confirmare, o ediție istorică reimportată și un conflict. La revenirea la numere anterioare, o amprentă nouă a paginii lunare lasă ediția incertă. Parserul din `official_prizes.py` folosește numărul versiunii numai când sursa îl precizează explicit pentru rezultat. Două trageri oficiale distincte pot avea aceleași numere.

## Selecția după o întrerupere a rafinării

Proba folosește 300 lei, ținta 4+, 5 bile Joker eligibile, metoda directă, baza completă și selecția pe 32 de date. Costul biletelor este 295,00 lei în fiecare fază. Numărătorii de mai jos au numitorul 1.221.759.

| Fază | La versiunea inițială | După remediere |
|---|---|---|
| Eroarea simulată este activă | frequency · 7.862 | frequency · 7.862 · rezultat provizoriu |
| Prima cerere după recuperare | frequency · 7.920 | compact_multiscale_shrink · 7.920 |
| Recalculare de control, după golirea exclusivă a cache-ului selectorului | compact_multiscale_shrink · 7.920 | compact_multiscale_shrink · 7.920 |

La ultima versiune, apelul obișnuit după recuperare reproduce metoda, pool-ul, biletele și dovezile recalculării de control. În faza cu eroare, versiunea inițială a încercat rafinarea de 833 de ori. Cererea remediată folosește o singură rețetă de rezervă pentru toate variantele evaluate în acea calibrare.

`covering/lr_pool_budget.py` fixează codurile întregi, pozițiile și asocierile Joker ale portofoliului. `covering/lr_refinement_status.py` identifică rețeta și consemnează erorile și anulările. Scorarea istorică și generarea aplică aceeași rețetă pe numerele zilei. O soluție verificată obținută la termen poate fi refolosită. Un calcul provizoriu păstrează bilete jucabile și este reluat la următoarea cerere.

Comparația bazelor, Re-Bench, selecția live, Fixed Share și walk-forward folosesc această informație. Un profil Re-Bench provizoriu nu înlocuiește profilul complet. Fixed Share păstrează ultimul punct de reluare complet, iar validarea raportează distinct câte extrageri au folosit rafinare provizorie. O eroare a rafinării și folosirea unei metode predictive de rezervă sunt înregistrate separat.

## Menținerea acoperirii și a hiturilor istorice

Pe Windows, CPython 3.15.2, verificarea independentă a regăsit aceleași numere, bilete fizice, costuri și numărători ca la `ff23ad17` pentru toate cele 42 de cazuri din grilă și toate cele 24 din generator. Au fost renumărate 90 de portofolii, inclusiv cele 24 transpuse pe pool-urile numerice din arhivă. Față de versiunea anterioară optimizării geometrice, `e6cad78`, se păstrează câștigul la același cost pe 8/42 și 7/24 de rânduri.

| Joc, buget | Cost efectiv | Numărător 4+ pe o variantă | Numitor |
|---|---:|---:|---:|
| 6/49, 300 lei | 296,50 lei | 383.414 | 13.983.816 |
| 6/49, 400 lei | 392,50 lei | 468.744 | 13.983.816 |
| 5/40, 300 lei | 285,50 lei | 119.402 | 3.838.380 |
| Joker, 200 lei | 196,50 lei | 5.384 | 1.221.759 |
| Joker, 300 lei | 295,00 lei | 7.920 | 1.221.759 |
| Joker, 400 lei | 386,50 lei | 9.944 | 1.221.759 |

La 6/49 și 400 lei se păstrează 12.274 rezultate favorabile la 5+ și 49 de variante distincte la 6/6. Pentru Joker „orice premiu”, la 300 lei și cinci bile eligibile, rezultatul este 6.720.220/24.435.180 la costul de 296,00 lei. Configurația separată cu două bile acoperă 5.111.685 rezultate la costul de 295,50 lei.

Cele 108 comparații între generare și replay au produs 87 de planuri jucabile și 21 de refuzuri concordante. Cele 97 de evaluări au reprodus aceleași metode, pool-uri, bilete și hituri. Aceste evaluări repetă unele extrageri în configurații diferite. La 5/40, 200 lei, invers în 10–40, ambele căi explică refuzul prin cele 15 numere eligibile rămase pentru cele 16 poziții necesare. Fluxul prin interfață, coadă și worker s-a încheiat cu rezultat, cu SMTP și oprirea sistemului simulate.

Medianele primului apel și ale apelului repetat, în milisecunde, au fost 90,33/2,75 înainte și 93,35/2,82 după pentru grilă. Pentru generator au fost 320,85/12,70 înainte și 260,72/13,65 după. Timpii provin din câte o rulare pe aceeași stație.

## Reproducere

Dintr-o copie completă a proiectului, cu Python 3.15 și dependențele din `requirements_base.txt`, alege directoare temporare noi pentru `LOTO_RUNTIME_DIR` și `LOTO_WF_CACHE_DIR`. Arhiva experimentală menționată în comenzile de mai jos se găsește local în `_TESTE`.

```powershell
python -m pytest -q tests/test_lr_prospective.py tests/test_lr_refinement_recovery.py
python -m pytest -q tests/test_lr_advanced_methods.py tests/test_lr_range_analysis.py tests/test_lr_walk_forward.py tests/test_ui_lr_budget_inverse.py tests/test_ui_lr_rebench.py
python -O -m scripts.analysis.verify_lr_experimental_archive _TESTE/Teste_LOTO_2026_11_25.zip --witnesses testdata/lr_experimental_witnesses_2026_11_25.json --certificates covering_designs/lr/expanded_certificates.json
python -m scripts.analysis.replay_lr_expanded_grid --archive _TESTE/Teste_LOTO_2026_11_25.zip --out "$env:TEMP/loto-grid.json" --workers 1
python -m scripts.analysis.replay_lr_expanded_workflow --out "$env:TEMP/loto-workflow.json"
python -m pytest -q -p no:cacheprovider --durations=15
```

Arhiva `Dovezi_verificare_LOTO_main_2026_11_26.zip` conține și probele externe `root_ledger_repro.py`, `reliability/independent_ledger_probes.py`, `hits/probe_transient_selection_cache.py`, `hits/probe_pipeline_parity.py` și `portfolio/independent_cover_audit.py`. Pentru a relua proba migrării după remediere, așteptarea literală a schemei SQLite din proba independentă trece de la 2 la 3. Copia originală și această adaptare sunt păstrate distinct în dovezile locale. Proba root conține un SHA fix al bazei în datele sale sintetice.

## Limitele verificării

Numărătorii combinatorici descriu reuniunea rezultatelor favorabile pe variantele plătite într-o extragere uniformă. Hiturile istorice provin din extragerile parcurse în replay. Un rezultat sintetic din testul registrului verifică exclusiv contabilizarea. Intervenția nu schimbă catalogul predictiv și nu testează un avantaj pe extrageri viitoare.
