# Corecții mail și verificarea outputului — 16 noiembrie 2026

## Rezultatul verificării

Mesajul primit pentru 19-11-2026 coincide cu rezultatul jobului: 6/49 Cod 48, pool 9 și 12 variante; Joker Cod 24, pool 10, Joker 9 și 14 variante; 5/40 Cod 16, pool 8 și 21 de variante. Numerele, ultima extragere CSV și maximele retrospective au fost confruntate separat cu istoricele. Costurile sunt 96/98/105 lei în variante, în total 299 lei, la care se adaugă taxele pe biletele fizice.

Auditul a identificat două defecte în cod. Rotunjirea ascundea diferența dintre rata măsurată și scorul Wilson. Trimiterea în fundal citea starea UI după terminarea jobului, astfel încât un rezultat nou putea înlocui numerele jobului pentru care se trimitea mesajul. Al doilea defect a fost reprodus cu transport simulat; nu există dovada că a afectat mesajul primit de utilizator.

## Comportamentul corectat

La încheierea jobului, aplicația capturează numerele, identitatea jocului, codul LR, auditul scorerului, istoricele sursă și data. Deciziile și ratingurile sunt desprinse de starea globală înaintea pornirii threadului de trimitere. Schimbarea selecției, încărcarea altui istoric, un job nou sau trecerea în ziua următoare nu mai pot modifica acel mesaj. Variantele și istoricul simulat voluminos nu sunt copiate. Pe jobul real, copierea sub lacăt durează 0,14–0,23 ms; captura completă încălzită durează 66–69 ms. Transportul SMTP și calculul retrospectiv rămân în fundal.

Ratingul se calculează din foldurile exacte prin aceleași funcții canonice de ponderare, Wilson și baseline ca benchmarkul. Textul `rationale`, deja rotunjit, nu mai este sursă de cifre. Helperul verifică jocul, poolul, metoda, ținta, celula deciziei, semnătura motorului, matricea de folduri, ferestrele și numărul de evaluări; refuză folduri mai noi decât decizia și înlocuiri de fișiere în timpul citirii. În lipsa unei potriviri verificabile, numerele rămân disponibile, iar ratingul este declarat indisponibil.

| Rating | Rata observată | Wilson z=1 | Baseline uniform |
| --- | ---: | ---: | ---: |
| 6/49, 5+ în pool 9 | 0,137% | 0,076% | 0,037% |
| Joker, 5+ în pool 10 | 0,139% | 0,073% | 0,021% |
| 5/40, 5+ în pool 8 | 0,175% | 0,092% | 0,047% |
| Joker Urna 2, top-1 | 6,26% | 5,70% | 5,00% |

Precizia urmează formatterul comun al UI, inclusiv pentru evenimente foarte rare. Emailul spune expres „în pool”, păstrează avertismentele statistice și caracterul retrospectiv al celui mai bun rezultat. Costul identifică variantele și taxa separată pe bilet. Un rezultat gol, invalid sau fără pool nu poate declanșa trimiterea; o eroare de compunere este logată fără expedierea unui mesaj inutil.

## Verificare

Cele 36 de regresii independente ale helperului verifică ponderarea și Wilson prin oracol separat, geometria Urnei 2, folduri incomplete, duplicate, eșuate sau modificate și extragerea cronologică validă. Cele 13 cazuri de snapshot verifică înlocuirea stării după captură, coduri LR pe același pool, schimbarea datei și a bifei de mail, absența copiilor voluminoase și refuzul trimiterilor invalide. Testele vechi simulează acum datele numerice exacte, în locul parsării textului rotunjit.

Pe sursele finale, suita celor 224 de fișiere locale trece în 12 copii izolate: **7.376 teste trecute, 25 omise, zero eșecuri și erori**. Singura reluare de grup a adaptat un fixture vechi de mail care furniza numai textul rotunjit; codul de producție a rămas identic. Amprentele surselor testate coincid cu sursele livrate, iar cele 19 căi protejate sunt neschimbate. Compilarea celor cinci module și `git diff --check` trec.

Proba integrată finală verifică 13 istorice, 912 comparații de scoruri, 26 de fluxuri și worker separat pe 13 jocuri cu rezultate decodate; UI răspunde HTTP 200. SMTP este blocat în toate procesele probei integrate; nu s-a înregistrat nicio încercare de conectare. Revizia independentă a surselor finale nu identifică probleme materiale rămase.

Verificarea vizuală finală în instanța izolată, cu jobul 42 recuperat, confirmă clasamentele la poolurile 9/10/8, ratele exacte și costurile. Toate cele 47 de variante afișate coincid în ordine cu rezultatul workerului; cele 14 variante Joker au aceeași bilă 9. Istoricul precedent este etichetat separat. Serverul de test a fost oprit după verificare.

Conținutul rezultatului real a fost recompus local pentru 19-11-2026, cu toate cele patru ratinguri exacte. Niciun email real nu a fost trimis în audit. Testele blochează SMTP; verificarea de transport folosește un înlocuitor. Livrarea anterioară este confirmată de mesajul primit furnizat de utilizator. Mecanismul UTF-8/STARTTLS de transport nu a fost modificat.

Testele rămân locale în `_TESTE/`. Fișierele de producție, benchmarkul, istoricele și coada activă sunt protejate; aplicația de pe portul 8000 nu a fost repornită de audit. Corecțiile sunt încărcate la următoarea pornire normală.

Dovezi locale: `scratch/mail_audit_2026_11_16/independent/email_preview_after_fix.txt`, `capture_benchmark.json`, `race_probe.json`; `scratch/mail_fix_2026_11_16/full_suite/` și `application_integration.json`, `visual_ui_verification.json` și `full_suite/verification_final.json`.
