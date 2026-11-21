# Audit LOTO · 21 noiembrie 2026

## Selecția metodei

Selectorul dinamic număra extragerile cu 4+ în pool. Patru numere pot apărea în pool și rămâne împărțite între variante. Am aplicat structura fiecărui cod LR la alegerea metodei, cu ordinea crescătoare folosită de terminal. Regula compară pe aceleași zile câte extrageri au avut 4+ pe cel puțin o variantă. Cinci sau șase potriviri pe o variantă intră o singură dată în total. Numărătorile pentru fiecare prag sunt în auditul rezultatului. [Selectorul](../../loto_enterprise/core/dynamic_winner.py)

Validarea automată de după Re-Bench folosește același criteriu pentru clasamentul din România. Include regula dinamică a generatorului, regulile cu metodă fixată la început sau schimbată periodic și martorii. Am eliminat din validarea curentă limita de două numere consecutive pe care generatorul curent o eliminase deja. Joker Urna 2 se evaluează printr-o singură bilă. Opțiunea de generare cu selecția dinamică debifată folosește decizia Re-Bench după dimensiunea pool-ului. [Raportul automat](../../loto_enterprise/benchmark/chronological_validation.py) · [Comparația walk-forward](nested_wfv_2026_11_18.py)

## Reluarea pe 33 de coduri LR

Am confruntat cele două criterii cu aceleași cinci metode, aceleași variante oficiale și aceeași secvență aleatoare de control. La fiecare termen s-au folosit cel mult 104 zile de extragere anterioare. Scorul fiecărei metode a folosit exclusiv extragerile disponibile înaintea zilei țintă. Zilele cu mai multe extrageri au primit același pool, iar fereastra de comparație cuprinde ultimele 30% din zilele eligibile după încălzirea de 250 de extrageri.

| Joc | Extragere inițială în test | Extrageri | Coduri LR | Mai multe / egale / mai puține reușite cu selecția pe variantă |
| --- | --- | ---: | ---: | ---: |
| Loto 6/49 | 07.11.2019 | 719 | 7 | 2 / 4 / 1 |
| Loto 5/40 | 03.04.2022 | 465 | 16 | 2 / 13 / 1 |
| Joker Urna 1 | 08.04.2021 | 592 | 10 | 3 / 6 / 1 |
| Total | | 1.776 | 33 | 7 / 23 / 3 |

La Cod 57 din 6/49, selecția pe variante a găsit 16 extrageri cu 4+, selecția după pool 13 și martorul aleator 12. În modelul uniform, media pentru cele 719 extrageri este 12,85. Au fost zero reușite la 5+ și 6 pentru toate cele trei reguli pe acest cod. Ferestrele se încheie la 19.11.2026 și includ arhive folosite în cercetări anterioare. Cea mai mică valoare *p* corectată Holm față de modelul uniform a fost 0,621. Auditul confirmă că geometria poate schimba selecția, iar datele rămân insuficiente pentru a confirma un avantaj față de aleator. [Schemele și probabilitățile exacte](../../covering/lr_schemes.py) · [Definiția martorilor](archived_ro_2026_11_21.py)

## Verificarea fluxurilor

| Flux | Verificare |
| --- | --- |
| Istorice și import | 13 fișiere cu 47.528 extrageri. Valide pentru geometria jocului, fără rânduri duplicate și în ordine cronologică. În arhiva locală, toate cele trei jocuri românești ajung la 19.11.2026, cu 2.603 extrageri la 6/49, 1.754 la 5/40 și 2.206 la Joker. |
| Scheme LR și acoperire | 26.520 comparații directe între numărătorile motorului și variantele expandate, pe toate cele 33 de coduri. Am corectat enumerarea exhaustivă pentru pool-uri cu numere neconsecutive și numărarea biților peste poziția 32 în backendul de rezervă. Proba independentă cu biletul 33–38 a dat 903 extrageri cu zero, 258 cu o potrivire și 15 cu două, pentru extrageri de câte două bile din 49. |
| Re-Bench și walk-forward | Re-Bench pe datele reale a produs 96 de rânduri și raportul automat cu patru fluxuri. La 6/49, Cod 57 a avut 16 reușite din 687 pentru regula dinamică și 10 pentru martor în această fereastră separată. Verificarea pentru 6aus49 din Germania a produs un raport distinct cu 1.642 de extrageri. |
| Interfață, worker și buget | HTTP 200, interacțiuni în Chromium și generare prin worker. La 200 lei, 6/49 a folosit Cod 57 cu 12 numere și 22 de variante, la 176,50 lei. La 5/40, zonele Cod 15 și Cod 17 au folosit același pool sursă de 19 numere, la 195,50 lei. La Joker, toate zonele au folosit numere din pool-ul calculat de 15 numere. Am verificat și optarea pentru un singur joc, reluarea pool-ului salvat și evidența walk-forward din panou. |
| Premii, costuri și istoric | Am confruntat categorii între jocuri, inclusiv ordinea primelor cinci bile la 5/40, categoria IV la 6/49 înainte și după introducere și rezultatul Joker cu și fără bila din Urna 2. Suita verifică estimările, taxele, proveniența rulării, cache-ul, exportul, generarea emailului și persistența joburilor. |
| Regresii și operare | Python 3.15, 6.519 teste trecute, 129 omise și 36 de eșecuri identice cu referința. Am actualizat așteptările schimbate în copiile de test. Browserul și workerul au rulat în Linux, cu arhivele locale. Importul live de la Loteria Română, expedierea emailului și lansatoarele în Windows nativ au rămas în afara verificării. |
