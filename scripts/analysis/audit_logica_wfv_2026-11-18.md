# Alegerea metodei înaintea extragerii și verificarea schemelor reduse

## Ce măsoară cele două validări

În reluarea existentă, fiecare predicție folosește numai extragerile din zilele precedente. Metoda poate însă proveni din benchmarkul de azi, care cunoaște rezultate ulterioare zilei simulate. Reluarea arată ce variante ar fi produs acea metodă și permite verificarea hiturilor și a premiilor. Pentru a evalua și alegerea metodei am introdus o a doua validare care mută momentul selecției în trecut. Metodologia urmează evaluarea cu origine mobilă și separă selecția de măsurarea rezultatului ulterior. [Hyndman și Athanasopoulos, secțiunea 5.10](https://otexts.com/fpp3/tscv.html) · [Cawley și Talbot, 2010](https://jmlr.org/papers/v11/cawley10a.html)

Experimentul compară nouă metode comune, cu minimum 250 de extrageri pentru antrenare și 100 de zile cu rezultate anterioare pentru selecție. Ultimele 30% dintre zilele eligibile constituie perioada finală. Toate regulile văd aceleași extrageri și au același buget în interiorul fiecărui joc. Scorurile se actualizează înaintea fiecărei zile, inclusiv pentru metoda fixată la început. Metoda selectată rămâne fixă în intervalul atribuit. Toate extragerile din aceeași zi folosesc același trecut.

Am măsurat două criterii de selecție. Primul folosește 5+ pe cea mai bună variantă, apoi 4+ și media hiturilor pe cea mai bună variantă pentru departajare. Al doilea apelează funcția decizională a aplicației pe poolul brut, cu ferestre de 10/30/70/100% din zilele anterioare și cele nouă metode ale experimentului. Pentru rezultatul jucat am aplicat limita de două numere consecutive, am sortat poolul și am desfășurat schema. Arhivele au fost folosite și în studii precedente. Comparația completă de producție cu toți scorerii și un test prospectiv pe extrageri noi rămân distincte.

## Rezultate pe cele 13 jocuri

Unitatea tabelului este o **extragere cu cel puțin o variantă de 5+**. Primul criteriu alege metoda după hiturile pe variante. „La final” permite selecției să vadă și rezultatele perioadei testate. „La început” fixează alegerea înainte de prima extragere a testului. „La 26” repetă selecția la fiecare 26 de zile cu extrageri. Joker Urna 2 apare separat și numără potrivirile bilei unice.

| Joc | Extrageri | Frecvență simplă | La final | La început | La 26 | Zilnic |
|---|---:|---:|---:|---:|---:|---:|
| România · 6/49 · Cod 48 | 686 | 1 | 0 | 0 | 0 | 0 |
| România · Joker, Urna 1 · Cod 24 | 561 | 0 | 0 | 0 | 0 | 0 |
| România · 5/40 · Cod 16 | 434 | 0 | 1 | 0 | 0 | 0 |
| Germania · Lotto 6aus49 | 1.641 | 0 | 1 | 0 | 0 | 0 |
| Polonia · Lotto | 1.837 | 0 | 1 | 1 | 1 | 1 |
| Spania · La Primitiva | 1.159 | 1 | 1 | 0 | 0 | 0 |
| Austria · Lotto 6 aus 45 | 1.006 | 0 | 2 | 0 | 0 | 0 |
| Belgia · Lotto | 368 | 0 | 0 | 0 | 0 | 0 |
| Ungaria · Hatoslottó | 454 | 0 | 1 | 1 | 1 | 1 |
| Cehia · Sportka | 2.164 | 1 | 0 | 0 | 0 | 0 |
| Slovacia · Loto | 1.426 | 2 | 2 | 2 | 2 | 2 |
| Bulgaria · Toto 2 | 108 | 1 | 1 | 0 | 0 | 0 |
| EuroMillions · urna principală | 495 | 0 | 0 | 0 | 0 | 0 |
| **Total jocuri principale** | **12.339** | **6** | **10** | **4** | **4** | **4** |
| Joker · Urna 2, top-1 | 561 | 16 | 25 | 25 | 25 | 25 |

Pentru cele trei jocuri românești (1.681 de extrageri), frecvența simplă a obținut un rezultat de 5+ pe variantă. Cele trei reguli care au ales în avans după hiturile pe variante au obținut zero. În străinătate comparația folosește un pool de 10 numere și o schemă românească simulată, cu 15 variante pentru jocurile cu șase numere pe bilet sau 14 variante pentru cele cu cinci. Cele 561 de observații din Urna 2 aparțin acelorași 561 de extrageri Joker din tabelul principal. La 5/40 hiturile se numără printre toate cele șase numere extrase, fără a identifica separat categoria I.

| Selecție pe toate jocurile principale | 5+ în pool | 5+ pe variantă | 4+ pe variantă |
|---|---:|---:|---:|
| Frecvență simplă | 12 | 6 | 114 |
| După variante, la final | 16 | 10 | 143 |
| După variante, la început | 7 | 4 | 153 |
| După variante, la fiecare 26 de zile cu extrageri | 9 | 4 | 136 |
| După variante, zilnic | 10 | 4 | 141 |
| După decizia pe pool, la final | 22 | 7 | 158 |
| După decizia pe pool, la început | 13 | 6 | 121 |
| După decizia pe pool, la fiecare 26 de zile cu extrageri | 8 | 1 | 134 |

Cele 153 de rezultate de 4+ față de 114 pentru frecvența simplă sunt o observație secundară. La obiectivul principal de 5+ aceeași comparație a dat 4 față de 6. Pentru cele cinci reguli care decid înainte de extragere și cele 14 serii, raportul JSON conține și comparația pereche cu frecvența simplă. Sub ipoteza extragerilor uniforme independente, valoarea e maximă a fost 1,46 la un prag de 1.400 pentru cele 70 de comparații. Datele nu susțin înlocuirea automată a scorerului sau o cadență optimă de realegere.

## Corecții în motor, benchmark și afișare

- În Windows, procesul principal și procesele de calcul puteau porni cu ținte de hituri diferite. Un caz reprodus folosea metoda aleasă în procesul principal și rezerva `frequency` în cinci pași paraleli. Ținta este acum fixată la pornirea reluării și transmisă proceselor. Cache-urile potențial afectate au o semnătură nouă.
- Schimbarea ferestrei de validare reutilizează pașii comuni cu aceeași arhivă, decizie și configurație. La schemele LR se verifică și numărul de variante pe extragere. Pe 160 de rânduri din fiecare istoric românesc, cele patru ferestre au cerut 465 de calcule pentru 993 de pași, cu 528 reutilizați. Toate cele 12 comparații între versiuni au dat aceleași pooluri, variante, hituri și grupe de premii.
- Controlul amestecat din benchmark păstrează acum calendarul, inclusiv extragerile multiple din aceeași zi. Scorul și clasamentul se calculează o singură dată pentru același trecut. Cele 57 de metode pe 14 serii au păstrat aceleași rezultate pe date reale în cele 798 de comparații efectuate. Într-o probă Sportka cu 240 de extrageri au fost necesare 120 de clasamente, iar mediana pentru metoda de frecvență a scăzut de la 39,9 la 18,2 ms.
- Pentru un cod LR, acoperirea tuturor submulțimilor de patru poziții depinde numai de schema oficială. Motorul o calculează o singură dată pe schemă. Verificarea tuturor celor 33 de coduri, cu câte patru pooluri reetichetate, a dat aceleași rânduri și aceeași acoperire în 132 de comparații. La 5/40 Cod 48 (930 de variante), 140 de generări au durat 0,258 s înainte și 0,011 s după modificare (mediană locală din cinci rulări).
- În UI și raport, dacă mărimea poolului se schimbă în timp, referința aleatoare este acum media probabilităților hipergeometrice pe extrageri. Fiecare extragere are aceeași pondere indiferent de numărul de variante. Un test binomial cu probabilitate unică se afișează numai când toate mărimile cunoscute sunt identice și acoperă toate observațiile, sau când singura dimensiune disponibilă provine din configurația unei reluări vechi. Dacă se cunosc dimensiunile doar pentru o parte dintre extrageri, comparația se omite.
- Clasamentul din UI și ratingul din e-mail precizează că rata de benchmark privește primele K numere după scor. Limita de două consecutive a schimbat 1.141 dintre 5.810 pooluri într-o verificare pe România. La 6/49, Joker și 5/40, numărul extragerilor cu 4+ pe o variantă a trecut de la 19/5/12 la 23/5/14. La 5+ numărătorile au rămas 1/0/1.

## Verificare și reproducere

În aplicație, panoul **Walk-forward cu alegerea metodei înainte de test** citește CSV-ul jocului și preia la apăsarea butonului codul LR ales. În browser au fost calculate pe rând la 6/49 Cod 48 (9 numere, 12 variante) și Cod 49 (10 numere, 15 variante). Ambele au evaluat aceleași 686 de extrageri din 655 de zile, între 27 februarie 2020 și 15 noiembrie 2026. Pe Cod 49, regula aleasă retrospectiv după variante a afișat două rezultate de 5+, iar cea fixată înaintea testului a afișat zero.

```powershell
python -m scripts.analysis.nested_wfv_2026_11_18 --output scratch/nested_wfv.json
```

Fișierul JSON conține protocolul, amprentele CSV-urilor, numărul de extrageri și de zile, metodele folosite și rezultatele fiecărei reguli la fiecare joc. Parametrul `--protocol-only` exportă doar definiția. Metodele și bugetele folosite în generarea curentă rămân cele alese în aplicație.

Pe copiile izolate ale aplicației, verificarea cumulată a acoperit 235 de fișiere de teste, cu 7.582 de teste trecute, 25 omise și zero eșecuri rămase după corecții. Workerul a finalizat toate cele 13 jocuri. Auditul de integrare a acoperit 13 istorice, 912 verificări ale scorerilor și 26 de fluxuri de generare. Auditul celor 277 de fișiere de design a încheiat 53.557 de comparații exacte fără diferențe. Cele 22 de fișiere urmărite din producție au păstrat aceleași amprente SHA-256.
