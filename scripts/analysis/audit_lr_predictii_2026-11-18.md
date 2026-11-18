# Predicții pentru schemele reduse LR — 18.11.2026

Audit pornit de la `22bbc02`, cu codurile LR implicite 48 la 6/49, 16 la 5/40 și 24 la Joker. Măsura principală este numărul de extrageri pentru care cel puțin o variantă oficială a schemei are 5+ hituri. Sunt raportate separat pragul 4+, hiturile în pool, Joker cu bila din urna a doua și categoria I la 5/40.

## Modelul pe perechi

Candidatul `pair_lr` estimează distribuția frecvențelor perechilor extrase împreună, cu înjumătățire la 256 de zile cu extrageri și un prior uniform echivalent cu 100 de extrageri. În model, o extragere are probabilitate 80% să fie uniformă. Pentru componenta de 20%, se alege o pereche din distribuția regularizată, iar celelalte bile se aleg uniform dintre numerele rămase. Aceasta definește o distribuție normalizată pe extrageri complete, fără repunere. Recența și ponderile sunt ipoteze fixe de testat.

Pentru un prag, fiecare intersecție favorabilă cu poolul se numără o singură dată, chiar dacă mai multe variante LR ating pragul. Numărul de extrageri favorabile condiționat de o pereche se obține după cum ambele numere, unul singur sau niciunul cad în pool. Astfel, metoda optimizează evenimentul de pe rândurile schemei, aplicate poolului în ordine numerică. Oracolul independent enumeră explicit extragerile unui univers mic și însumează ponderile perechilor. Pe cele trei coduri reale, ponderile uniforme reproduc exact probabilitățile catalogului, iar căutarea nu acceptă schimburi.

Căutarea pleacă de la poolul metodei actuale pentru joc și examinează toate schimburile permise de câte un număr, în cel mult două treceri. Acceptă o creștere strictă a probabilității **estimate** de 5+, cu 4+ cel puțin egal în același model. Păstrează codul, numărul variantelor, ordinea oficială și limita de două consecutive. Rezultatele istorice sunt apoi evaluate pe extragerile reale, indiferent de creșterea prezisă de model.

Primele 250 de rânduri încălzesc modelul. Predicțiile aceleiași zile folosesc numai extrageri din zile anterioare. Codul, parametrii și oracolul au fost fixați înainte de calcularea rezultatelor. Metodele de referință sunt liderii actuali fixați din decizii pentru aceste pooluri, reluați numai cu informația din trecutul fiecărei extrageri. `frequency` este controlul. Pentru Joker, aceeași regulă separată de frecvență din trecut alege bila a doua în toate comparațiile. La 5/40, categoria I se evaluează pe primele cinci numere în ordinea CSV.

## Rezultatele modelului pe perechi

5.810 extrageri eligibile, dintre care 1.775 în fereastra principală, ultimele 30% din zilele eligibile ale fiecărui joc. Cifrele de mai jos numără extragerile cu cel puțin o variantă care trece pragul.

| Joc / schemă / extrageri | 4+ metoda actuală → perechi | 5+ metoda actuală → perechi | 5+ așteptat uniform | p la 5+, Holm |
| --- | ---: | ---: | ---: | ---: |
| 6/49 / Cod 48 / 718 | 12 → 10 | 1 → 1 | 0,1522 | 0,424 |
| 5/40 / Cod 16 / 465 | 4 → 7 | 1 → 0 | 0,0848 | 1,000 |
| Joker / Cod 24 / 592 | 4 → 2 | 0 → 0 | 0,0068 | 1,000 |

Pe toate cele 5.810 extrageri, 5+ se schimbă de la 1 la 2 la 6/49, de la 1 la 1 la 5/40 și de la 0 la 0 la Joker. La Joker, 5+ cu bila a doua are zero rezultate pentru toate regulile. În fereastra principală, orice premiu apare de 46 ori pentru metoda actuală și de 49 ori pentru candidat, cu aceeași regulă pentru bila Joker. La 5/40, categoria I are zero evenimente în ambele ferestre. Diferențele sunt descriptive, iar arhivele au mai fost folosite în cercetare.

Chiar în ipoteza dublării ratei uniforme de 5+, puterea testului în fereastra principală, cu prag conservator de 0,05/3, este de circa 3,79% / 1,28% / 1,35% pentru 6/49 / 5/40 / Joker. Raportul de verosimilitate al extragerilor complete e un diagnostic separat al distribuției predictive. Valoarea sa nu certifică hituri 5+ pe schemă. [Referință metodologică pentru rapoarte de verosimilitate secvențiale](https://arxiv.org/abs/2210.01948).

## Studiul independent

Al doilea studiu fixează, înainte de rulare, două reguli cu un model EWMA de ponderi individuale regularizate. Prima caută 4+ și păstrează probabilitățile estimate ale pragurilor superioare. A doua caută 5+ și departajează prin 4+. Ambele folosesc rândurile LR efective și au evaluat 124.085 de schimburi pe aceleași 5.810 extrageri. Oracolul lor a enumerat independent extrageri complete ponderate, cu eroare maximă 2,78e−16.

În fereastra principală, cele două reguli dau aceleași numărători 4+/5+. La 6/49 sunt 11/2 față de 12/1, la 5/40 sunt 7/0 față de 4/1, iar la Joker sunt 4/1 față de 4/0. Unicul 5+ Joker este numai în urna principală. Câștigurile și pierderile pe aceleași extrageri nu confirmă superioritatea față de `frequency` sau față de metodele folosite acum. O coincidență rară față de modelul uniform și creșterea estimată de un model sunt dovezi distincte de un avantaj stabil față de alternativa jucată.

Testele de cauzalitate au schimbat integral extragerea țintă și viitorul, lăsând identice poolul și ponderile acelei zile. Proba pe perechi include o zi cu două extrageri. Modelul schimbă ponderile începând cu ziua următoare.

## Replicare geografică

Modelul pe perechi a fost refolosit cu aceiași parametri și aceleași 12 variante simulate Cod 48 pe toate cele șase istorice străine cu geometrie 6/49 din registru. Geometria este doar instrumentul evaluării pentru Bulgaria, Cehia, Germania, Spania, Polonia și Slovacia. Referințele `naive_bayes_last` și `frequency` sunt aceleași în fiecare familie.

| Fereastră | Extrageri | Naive Bayes 4+/5+ | Frequency 4+/5+ | Perechi 4+/5+ |
| --- | ---: | ---: | ---: | ---: |
| Toate | 30.288 | 223 / 9 | 220 / 6 | 241 / 6 |
| Ultimele 30% din zile | 8.575 | 66 / 2 | 68 / 2 | 61 / 2 |

În ultima fereastră, modelul pe perechi are 36 câștiguri și 41 pierderi la 4+ față de Naive Bayes. La 5+ are două câștiguri și două pierderi. Nici aici nu apare un avantaj stabil. Hashul scriptului și al celor șase istorice a rămas identic. Dovezi și rezultate pe fiecare țară în `scratch/lr_pair_foreign_replication_2026_11_18/`.

## Alternative combinatorice pe scheme oficiale

La plafoanele implicite, 12 variante la 6/49, 21 la 5/40 și 14 la Joker, niciun alt cod individual din catalog nu crește strict 5+. Codurile 48/16/24 rămân maximele respective pentru acest obiectiv la aceste bugete. Există compromisuri verificabile dacă se combină mai multe scheme oficiale întregi pe pooluri disjuncte.

| Joc și organizare | Variante | Șansa unui premiu | Șansa 4+ | Șansa 5+ pe numerele principale |
| --- | ---: | ---: | ---: | ---: |
| Joker, Cod 24 | 14 | 9,120416% | 0,221157% | 0,001145889% |
| Joker, Cod 34 + Cod 45, aceeași bilă J | 14 | 10,274841% | 0,207324% | 0,001145889% |
| Joker, Cod 34 + Cod 45, bile J diferite | 14 | 12,053146% | 0,207324% | 0,001145889% |
| 5/40, Cod 16 | 21 | 0,951964% | 0,951964% | 0,018236860% |
| 5/40, două scheme Cod 15 | 18 | 0,999380% | 0,999380% | 0,015840016% |

La Joker, toate cele trei configurații au aceeași șansă la 5+J, 14 / 24.435.180, respectiv 0,000057294%. Codurile 34 și 45 au împreună 16 numere principale disjuncte. Alegerea a două bile J diferite reduce suprapunerea evenimentelor de premiu mic. Enumerarea directă dă 2.945.208 extrageri favorabile oricărui premiu din 24.435.180. Calculul măsoară incidența premiilor, fără a compara valoarea lor. Costul variantelor la Joker este același, iar taxele se aplică fiecărui bilet fizic. Comparația de portofoliu este cercetare cu rândurile LR oficiale și nu pretinde un maxim global peste poolurile suprapuse. Aplicația generează în continuare câte un cod pe joc.

## Aplicație și validare

- Avertismentul LR arată acum scăderile probabilității oricărui premiu Joker și categoriei exacte 2+J. De la Cod 24 la Cod 25, șansa oricărui premiu scade de la 9,120416% la 7,934564%, iar 2+J de la 1,474071% la 1,287574%. Calculul folosește cel puțin o variantă oficială și aceeași bilă Joker pe fiecare variantă. Un sistem poate câștiga mai multe categorii, deci probabilitatea exactă 2+J se numără direct, fără scăderea cozilor maximului.
- Mailul afișează numărul variantelor și costul sistemului generat generic separat de codurile LR de referință. Codul LR fixat în job sau audit păstrează eticheta schemei efective. Snapshotul reține numărul variantelor generice fără a copia lista.
- Catalogul final verificat cuprinde 277 de fișiere, 519 designuri, toate cele 33 de coduri LR și 53.557 comparații exhaustive, fără diferențe. Auditul integrat validează 13 istorice, 912 probe de paritate a scorurilor, 26 de fluxuri și workerul pe toate cele 13 jocuri. UI răspunde HTTP 200 în copia izolată.
- Oracolul Joker confruntă 24.626 de scenarii și 1.705.980 de clasificări de variante pentru toate cele zece coduri, fără diferențe. Verificarea vizuală pe UI finală arată avertismentul la trecerea Cod 24 → Cod 25.
- **7.499 teste trecute, 25 omise**, pe 229 de fișiere, după două reluări. O așteptare locală de mail generic a fost aliniată la eticheta de referință, iar întreaga grupă a trecut. Un test Windows al lansatorului, cu o expirare simulată, nu a atins limita la prima rulare și a trecut izolat fără modificări. Cauza primei anomalii de timp nu este demonstrată. Cele 19 căi protejate coincid înainte și după teste. Testele și probele au rulat cu SMTP blocat, în copii separate.

Toate metodele studiate rămân experimentale. Arhiva reutilizată și evenimentele rare lasă deschisă întrebarea predictivă, iar dovezile de până acum susțin păstrarea predictorilor actuali.

Modelul pe perechi poate fi reprodus cu `python -m scripts.analysis.lr_pair_mixture_2026_11_18 --output rezultate.json`, într-o copie de lucru cu runtime izolat. Parametrii sunt în script. Raportul JSON include amprentele istoricului și rândurilor LR, câștigurile și pierderile pe aceleași extrageri, probabilitățile uniforme exacte și ferestrele. Dovezi locale în `scratch/audit_lr_pair_2026_11_18/`, `scratch/lr4_research_2026_11_18/`, `scratch/audit_lr_frontier_2026_11_18/` și `scratch/audit_lr_22bbc02/ui_worker/`. Reuniunile LR și cazurile Joker cu bile diferite sunt verificate în `disjoint_official_unions.json` și `joker_distinct_balls.json`.
