# Catalogul compact de metode · validare la 20 noiembrie 2026

În România, niciuna dintre cele trei metode noi nu a depășit referința uniformă la pragul statistic de 5% după corecția pentru cele 27 de comparații. În cele șapte scheme românești de numere principale, selectorul dinamic compact a obținut mai multe extrageri cu 4+ decât selectorul anterior într-o schemă, același număr în trei și mai puține în trei. Arhivele fuseseră deja studiate. Confirmarea unui avantaj cere extrageri viitoare.

## România

O reușită 4+ înseamnă că lista propusă pentru o extragere conține cel puțin patru dintre numerele extrase. O extragere cu cinci sau șase potriviri contribuie o singură dată la această coloană. La 5/40 comparăm pool-ul cu toate cele șase bile extrase. La Joker Urna 2 măsurăm separat dacă bila apare pe primul loc sau în primele cinci. Doar primul loc este jucat în configurația evaluată.

| Joc | Pool / Cod LR | Extrageri test | Static nou | Static vechi | Dinamic nou | Dinamic vechi | Aleator | Așteptate uniform |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 6/49 | 9 / 48 | 719 | 3 | 6 | 5 | 5 | 2 | 5,32 |
| 6/49 | 16 / 59 | 719 | 55 | 58 | 56 | 62 | 57 | 57,23 |
| 5/40 | 8 / 16 | 465 | 3 | 9 | 3 | 4 | 5 | 4,43 |
| 5/40 | 16 / 37 | 465 | 73 | 86 | 72 | 81 | 70 | 74,52 |
| 5/40 | 19 / 48 | 465 | 140 | 128 | 146 | 146 | 125 | 131,48 |
| Joker Urna 1 | 10 / 24 | 592 | 6 | 6 | 5 | 4 | 4 | 3,68 |
| Joker Urna 1 | 15 / 12 | 592 | 29 | 13 | 22 | 22 | 19 | 21,30 |
| Joker Urna 2 | Top 1 | 592 | 17 | 27 | — | — | 27 | 29,60 |
| Joker Urna 2 | Top 5 | 592 | 145 | 147 | — | — | 143 | 148,00 |

Fiecare prognoză a folosit exclusiv informațiile disponibile înaintea zilei extragerii, după minimum 250 de extrageri anterioare. Extragerile obișnuite și suplimentare din aceeași zi au primit același pool și au fost punctate individual. Pe urnele principale am admis cel mult două numere consecutive și am dezactivat penalizarea aparițiilor recente. Toate intervalele de test se încheie pe 19 noiembrie 2026. Prima zi de test este 7 noiembrie 2019 pentru 6/49, 3 aprilie 2022 pentru 5/40 și 8 aprilie 2021 pentru Joker.

**Static.** Primele 70% din zilele eligibile au determinat metoda cu cele mai multe reușite. Am păstrat-o pe ultimele 30%. Catalogul nou a oferit frecvența ponderată și trei metode care estimează, respectiv, frecvența pe trei intervale, tranziția dintre două zile și reapariția în funcție de timpul scurs. Toate cele trei metode noi au furnizat scoruri utilizabile la fiecare dată evaluată. Catalogul anterior a avut 56 de metode eligibile în experiment. La egalitate am preferat frecvența, apoi ordinea alfabetică. În Urna 2 am clasat metodele și pentru criteriul consultativ top 5. Frecvența a câștigat pe perioada de selecție la ambele dimensiuni ale listei noi.

**Dinamic.** Înaintea fiecărei zile de test, versiunile v3 și v4 au folosit rezultatele ultimelor 104 zile de prognoză. Metoda cu cele mai multe reușite 4+ trebuie să depășească martorul aleator pe intervalul de selecție. Frecvența reprezintă rezerva în caz contrar sau când există mai puțin de 26 de zile de validare. V3 compară 1.011 candidați, iar v4 compară patru. Codurile LR stabilesc mărimea pool-ului din tabel. Pentru potrivirile pe un bilet plătit contează și combinațiile înscrise în schema respectivă.

**Comparații statistice.** Coloana „Aleator” arată rezultatul unei metode aleatoare reproductibile pentru fiecare istoric, cu aceleași restricții de pool. Referința exactă din ultima coloană folosește distribuția hipergeometrică, respectiv probabilitățile 1/20 și 5/20 în Urna 2. Am calculat teste binomiale unilaterale față de extrageri uniforme independente și teste pereche pe extragerile în care metodele au dat rezultate diferite. Am aplicat corecția Holm separat pentru 27 de combinații metodă–configurație, nouă alegeri statice și șapte reguli dinamice. Cel mai mic *p* ajustat față de probabilitatea teoretică a fost 1,000 pentru metodele noi, 0,549 pentru alegerea statică și 0,528 pentru v4.

La Joker Urna 1 cu 15 numere, noua alegere statică, frecvența, a avut 29 de reușite, față de 13 pentru vechea alegere statică și 19 pentru martorul aleator. Pentru comparația pereche dintre alegerile statice, *p* a fost 0,0098 înainte de corecție și 0,088 după corecția pe cele nouă configurații. Fișierul CSV include rezultatele fiecărei metode noi, liderii retrospectivi, numărul de reușite 5+/6 la urnele unde sunt posibile și comparațiile pereche. [Tabelul complet România](compact_ro_2026_11_20.csv)

## Alte zece jocuri europene

Pe 11.030 de extrageri de test am evaluat primele zece numere din clasamentul urnei principale. Am ales separat pentru fiecare joc cea mai bună dintre cele trei metode noi pe primele 70% din zile și am păstrat-o pe ultimele 30%. În total, alegerea a produs 136 de reușite 4+, față de 135,43 așteptate, 119 pentru metoda aleatoare, 117 pentru frecvență și 128 pentru metoda arhivată DMD. Rezultatele încep la date diferite, în funcție de arhiva fiecărui joc.

Metoda de frecvență pe trei intervale, evaluată individual în toate jocurile, a avut 158 de reușite 4+ și 11 reușite 5+. Pentru 4+, testul cumulat pe geometriile celor zece jocuri a dat *p* = 0,0304 și *p* ajustat = 0,0912 după corecția pe cele trei metode. Datele fiecărui joc și valorile de referință se găsesc în rezultatele complete. [Rezultate internaționale](compact_foreign_2026_11_20.json)

## Reproducere

Din rădăcina proiectului, cu dependențele aplicației instalate, se pot rula ambele studii.

```bash
python -m scripts.analysis.compact_ro_2026_11_20 --jobs 4 --output compact_ro.csv
python -m scripts.analysis.compact_foreign_2026_11_20 --output compact_foreign.json
```

În studiul românesc, rularea implicită reface previziunile metodelor vechi consemnate pentru fiecare pool. Opțiunea `--rescan-legacy` reface și clasamentul complet din cele 56 de metode eligibile ale vechiului catalog. Opțiunea `--cache-dir` păstrează rezultatele scorerilor statici între rulări. [Script România](compact_ro_2026_11_20.py) · [Script internațional](compact_foreign_2026_11_20.py)
