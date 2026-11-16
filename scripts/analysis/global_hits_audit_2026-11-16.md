# Audit global al hiturilor LOTO

Audit din 16 noiembrie 2026, pe sursele de la commitul `407430d`, cu verificarea fluxului LR introdus în aceeași zi. Configurațiile de pornire sunt 6/49 Cod 48 (9 numere, 12 variante), 5/40 Cod 16 (8 numere, 21 variante) și Joker Cod 24 (10 numere, 14 variante).

## Corecția semnăturii deciziei

Revizia numerică Hedge introdusă la 15 noiembrie separa deja foldurile, cache-ul walk-forward și rezultatele workerului prin `hedge-center1`. Semnătura generală a deciziei compara însă doar versiunea globală v24 și lista celor 57 de metode. O decizie calculată înaintea corecției putea rămâne declarată actuală pe aceleași date.

Diferența este reproductibilă pe exact prefixul Joker de 2.204 extrageri folosit de benchmarkul vechi. În fereastra de 221 de ținte, Hedge are 4 evenimente 5+ la pool 16 în foldul vechi și 5 în calculul actual. Mediile de hituri se schimbă și la poolurile 12, 17 și 18. Această comparație identifică măsurători provenite din versiuni diferite; nu constituie o confirmare predictivă.

`freshness.compute_engine_signature` include acum reviziile numerice din aceeași funcție `method_cache_semantics` folosită de cache-ul foldurilor. Formatul cu trei câmpuri rămâne compatibil. Dacă registry-ul nu are metode cu revizii, hashul vechi se păstrează. Verificarea unei decizii nu o rescrie: semnătura veche produce recomandarea `stale / full_rebench`, iar o rulare actuală primește noua semnătură.

Regresia nouă a eșuat înaintea corecției și trece după aceasta. Cele 45 de teste țintite au trecut în copie izolată. Nu s-au modificat scorerii, schemele oficiale sau politica statistică fără corecție multiplă stabilită anterior.

## Corecția clasamentului afișat în UI

Verificarea în interfața reală a identificat încă o neconcordanță. Rezultatele curente aveau Joker Cod 24, pool 10 și 14 variante, respectiv 5/40 Cod 16, pool 8 și 21 de variante. Clasamentul principal folosea însă poolul 7 din istoricul precedent restaurat. La Joker, acest lucru schimba inclusiv poarta și eticheta de rezervă a deciziei.

Pentru un istoric marcat ca precedent, clasamentul preia acum poolul rezultatului afișat, cu rezervă la selecția curentă dacă nu există rezultat. Istoricul vechi păstrează poolul, codul, costul și hiturile proprii; statusul principal îl identifică explicit drept „Istoric precedent”. Un istoric nou își păstrează prioritatea anterioară. Au fost corectate și rezultatele vechi care păstrau mărimea cerută a poolului de 12 deși conțineau numai șase numere. Clasamentul, eticheta poolului, titlul rezultatului recuperat, costul și raportul folosesc acum același calcul al numerelor distincte; dimensiunea salvată este doar rezervă când numerele lipsesc. Douăsprezece cazuri de regresie și celelalte teste UI vizate trec, în total 56 de teste. Înaintea corecției, ambele ramuri ale clasamentului reproduceau eroarea 12 în loc de 6.

Corecția a fost verificată vizual într-o instanță izolată, cu rezultatul real recuperat din copia read-only a jobului 40. Clasamentele afișează pool 10 și pool 8, iar istoricul precedent rămâne la pool 7. Toate cele 47 de variante vizibile (12/14/21) coincid, în ordine, cu rezultatul workerului și cu rândurile oficiale remapate pe pool. Joker 9 este atașat fiecăreia dintre cele 14 variante. Costurile curente afișate sunt 96/98/105 lei în variante; în istoricul vechi 5/40, Cod 15 rămâne corect la 9 × 5 = 45 lei.

Capturile și textele UI sunt în folderul local de vizualizări al conversației. Dovezile de comparație sunt în `scratch/audit_2026_11_16/visual_ui_verification.json`. Instanța izolată are emailul și oprirea PC dezactivate; verificarea pe portul 8000 a fost de citire și navigare.

## Verificarea hiturilor și a variantelor

| Zonă | Acoperirea verificării | Rezultat |
| --- | --- | --- |
| Catalogul designurilor | 277 fișiere, 519 secțiuni, 232 covere complete, 33 LR, 53.557 comparații exacte | Zero diferențe și amprente conforme |
| Generarea LR | Toate cele 33 de coduri prin generatorul real; rânduri oficiale, număr de variante, Joker și metadate | Coincidență cu enumerarea separată |
| Istoricul LR compact | 33 coduri, 66 pași, 11.054 variante remapate independent | Zero diferențe la hituri, reprezentanți și grupele de premii |
| Scoreri | 57 metode; 912 combinații joc, metodă și lungime de istoric | Determinism, istoric nemodificat și paritate benchmark–producție |
| Limitele temporale | 12 ținte, trei excluderi ale zilei țintă, 11 pooluri, 44 rate | Valorile 3+/4+/5+/6+ coincid cu oracolul sintetic |
| Integrarea generală | 13 istorice, 26 fluxuri, worker separat pe toate 13 jocurile | Rezultate decodate; UI HTTP 200 |

Matricea constructorului generic cuprinde 44 de configurații. În 43 comparații la același număr de variante, rezultatul final păstrează toate probabilitățile 3+/4+/5+/6 și acoperirea garanției față de greedy; 34 cresc strict cel puțin un prag. Acestea sunt optimizări existente, confirmate de audit. Cazul cu mai puține variante este separat de comparația la cost egal. Proba nu dovedește păstrarea tuturor categoriilor exacte Joker în orice configurație.

## Posibilitățile de optimizare în catalogul oficial

Enumerarea celor 33 de scheme și a celor 186 de comparații la cost mai mic sau egal nu găsește o alternativă care să domine simultan pragurile înalte și garanțiile urmărite. Pentru bugetele confirmate de 12/21/14 variante, verificarea tuturor partițiilor în coduri oficiale întregi exclude, prin limita reuniunii evenimentelor, o îmbunătățire simultană. Concluzia privește catalogul verificat și extragerile uniforme; nu afirmă optimalitate pentru orice buget sau orice sistem posibil.

Probabilități exacte pentru cel puțin o variantă a codului curent, la extrageri uniforme:

| Joc și cod | Variante | 4+ | 5+ | 6 |
| --- | ---: | ---: | ---: | ---: |
| 6/49 Cod 48 | 12 | 0,739454810% | 0,021195931% | 0,000085813% |
| 5/40 Cod 16 | 21 | 0,951964110% | 0,018236860% | — |
| Joker Cod 24, numere principale | 14 | 0,221156546% | 0,001145889% | — |

La 5/40, 5+ privește toate cele șase numere extrase și nu identifică singur categoria I. La Joker, tabelul privește urna întâi; premiile efective țin seama și de bila Joker.

Selecția actuală optimizează rata țintei în pool. Codurile LR de pe același pool pot folosi același scorer, deși au desfășurări diferite. Interfața explică distincția, iar hiturile variantelor LR se evaluează separat. Alegerea unor scoreri după rezultatul retrospectiv pe LR ar necesita cercetare și confirmare separată; auditul nu promovează un astfel de candidat.

## Datele active și aplicarea corecției

Un Re-Bench independent al aplicației de producție a început în timpul auditului, la 09:04:01, ca proces copil al UI-ului. Rularea a actualizat fișierele benchmarkului. Actualizările sale nu au fost oprite sau restaurate. Probele finale folosesc copii temporare cu runtime, cache, coadă și setări izolate. Lansatorul stației a întrerupt de două ori procesele auditului care utilizau venv-ul dedicat; verificarea finală folosește CPython 3.15.2 separat, cu aceleași biblioteci CPU. Un test local care citea CSV-ul în curs de actualizare folosește acum o matrice sintetică completă, păstrând aserțiunile privind refuzul unei decizii pe folduri incomplete.

După repornirea aplicației stației și încheierea benchmarkului, verificarea finală găsește toate cele patru decizii `fresh`. Registry-ul are 57 de metode, versiunea cache-ului este v24, iar hashul actual `2aae843e85b4d899` include revizia numerică. Semnăturile istoricului corespund celor 2.602 / 1.753 / 2.205 extrageri actuale (Joker este folosit pentru ambele urne). Auditul nu a lansat această repornire sau benchmarkul de producție.

## Verificarea finală

Pe sursele finale, toate cele 222 de fișiere locale au fost verificate exact o dată, în 12 copii independente: **7.327 teste trecute, 25 omise, zero eșecuri și erori**. Runtime: CPython 3.15.2, aceleași biblioteci CPU ale venv-ului. Amprentele tuturor surselor Python din copii coincid cu fișierele livrate. Compilarea celor trei module și `git diff --check` trec.

Ultima probă integrată pe sursele finale a validat cele 13 istorice, 912 comparații de scoruri, 26 de fluxuri, worker real pe 13 jocuri cu payload decodat și interfață HTTP 200. Cele 19 fișiere existente protejate sunt identice înainte și după rularea finală. Toate cele 13 istorice sunt identice și față de începutul auditului; actualizările benchmarkului de producție au fost păstrate.

Corecțiile UI au fost verificate în copia izolată. Interfața activă de pe portul 8000 încarcă aceste modificări la următoarea pornire normală. Schema și metodele care produc variantele rămân neschimbate.

Dovezi principale: `scratch/audit_2026_11_16/global_audit_final.json`, `delivery/verification.json`, cele 12 loguri `delivery/suite_*.log`, `delivery/application_integration.json`, `visual_ui_verification.json` și probele din subfolderele `covering`, `selection`, `ui_worker`.
