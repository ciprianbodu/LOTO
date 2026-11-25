# Metode LR noi, verificare retrospectivă din 25.11.2026

În cele 108 extrageri din 104 zile, între 16.11.2025 și 22.11.2026, selecția Fixed Share a prins 4+ numere pe o variantă oficială la o extragere pentru 6/49, la una pentru 5/40 și la niciuna pentru Joker. Metoda frecvenței a obținut aceleași totaluri. Studiul retrospectiv are prea puține evenimente pentru a demonstra un avantaj predictiv. Fixed Share se poate alege explicit din aplicație. Cele două metode numerice intră în evaluare fără selecție implicită în producție.

| Joc | Cost pe extragere | Fixed Share / Hedge anterior / Frecvență | BOCPD / Uitare Dirichlet | Stacking temporal / Ridge anterior |
| --- | ---: | ---: | ---: | ---: |
| Loto 6/49 | 96,50 lei | 1 / 0 / 1 | 0 / 2 | 2 / 4 |
| Loto 5/40 | 90,50 lei | 1 / 1 / 1 | 0 / 0 | 0 / 1 |
| Joker | 98,50 lei | 0 / 0 / 0 | 0 / 0 | 0 / 1 |

Fiecare număr din tabel reprezintă extrageri cu cel puțin patru potriviri pe aceeași variantă plătită. Analiza folosește 100 lei buget nominal pe joc și baza completă. În cadrul fiecărui joc candidații joacă la același preț. La 5/40, istoricul păstrează ordinea oficială a celor șase bile, iar varianta jucată are cinci numere. Pragul 5+ măsoară potrivirile, iar categoria I se evaluează după ordinea oficială. Pragul 6+ se aplică numai la 6/49. Pentru Joker, toate metodele din comparația portofoliilor folosesc aceeași ierarhie de frecvență la Urna 2. Calibrarea acelei urne se măsoară și separat.

Din cele 42 de comparații aplicabile, 26 au avut aceleași totaluri în fiecare zi, iar celelalte 16 au avut mai puțin de cinci zile discordante. Minimul valorilor p brute a fost 0,50. După ajustarea Holm, toate valorile p au fost 1,00. La pragul 5+, numai frecvența și uitarea Dirichlet anterioară au avut câte un rezultat, ambele la 6/49. La pragul 6+ nu a fost înregistrată nicio potrivire.

## Protocol

- Fiecare metodă primește numai extragerile din zilele precedente. Tragerile duble din aceeași zi folosesc același portofoliu. Ultimele 104 zile distincte formează fereastra de evaluare. Biletele provin din schemele LR oficiale, cu maximum două numere consecutive în pool-ul principal. Numărul de potriviri se măsoară pe cea mai bună variantă efectiv plătită.
- Fixed Share pornește cu ponderi egale pentru cinci metode, alege portofoliul cu ponderea cea mai mare și actualizează ponderile numai după ce întreaga zi este cunoscută. Pierderea zilnică este media indicatorului de neatingere a obiectivului pentru portofoliul complet. Funcția de generare folosește până la 104 zile anterioare, o rată de învățare de 0,7 și o redistribuire uniformă de 0,05. În modul invers, fiecare expert exclude pool-ul direct ales de aceeași politică pentru ziua respectivă și, la Joker, bilele directe din Urna 2.
- Stacking-ul estimează probabilități marginale de includere cu regresie logistică regularizată. Caracteristicile unei zile provin din zilele precedente și includ ranguri, dezacordul dintre scoruri și erorile istorice. Probabilitățile însumează numărul de bile extrase în urnă. BOCPD estimează distribuția lungimii perioadei de la ultima schimbare, trunchiată la 104 zile, cu hazard de 1/52 și prior echivalent cu 200 de extrageri uniforme. Verosimilitatea compusă folosește marginalele bilelor. Raportul măsoară Brier și pierderea logaritmică marginală, cu valori mai mici pentru prognoze mai bine calibrate.
- Pentru portofolii, statistica pereche însumează diferențele pe zile complete. Valorile p brute provin din permutarea exactă bilaterală a semnelor diferențelor zilnice, sub ipoteza de simetrie a acestora. Ajustarea Holm cuprinde toate comparațiile aplicabile. Intervalele empirice de 95% resamplează aceleași perechi de zile pentru ambele metode și exprimă diferența de rată în puncte procentuale. Rezultatele se citesc împreună cu numărul de zile discordante. Când acesta este zero, un interval empiric de la 0 la 0 reflectă observațiile identice, iar frecvența rară din populație rămâne necunoscută. Analiza este exploratorie.

## Calibrare și martor uniform

Pe istoricul real, stacking-ul a îmbunătățit ambele pierderi marginale față de martorul uniform numai la 6/49, unde Brier a trecut de la 0,107455 la 0,107378 și pierderea logaritmică de la 0,371776 la 0,371409. BOCPD a obținut o îmbunătățire mică numai la Urna 1 Joker, de la 0,098765 la 0,098751 și de la 0,348832 la 0,348758. La Urna 2 Joker ambele metode au avut pierderi mai mari decât martorul uniform.

Simularea martorului generează independent 24 de istorice a câte 450 de zile pe urnă, cu o tragere uniformă fără înlocuire pe zi. După 128 de zile inițiale, scorurile următoare se calculează înainte de observarea extragerii. Raportul JSON cuprinde pierderea medie în cele 7.728 de prognoze evaluate pentru fiecare urnă, abaterea dintre replici și eroarea maximă a sumei probabilităților marginale.

Pe toate cele patru urne, BOCPD a avut în simularea uniformă pierderi mai mari decât martorul. Excesul mediu Brier a fost între 0,0000163 și 0,0000473, iar cel logaritmic între 0,000157 și 0,000207. Eroarea maximă a sumei probabilităților a fost sub 4 × 10⁻¹⁵.

Regula de alarmă, fixată înaintea rulării, cere o probabilitate posterioară de cel puțin 10% ca o nouă perioadă să înceapă în ziua tocmai observată. Numărătoarea începe după cele 128 de zile inițiale. În martorul uniform, orice alarmă este falsă prin construcția simulării.

| Urnă | Zile cu alarmă din 7.728 | Rată pe zi | Replici cu cel puțin o alarmă din 24 | Rată pe replică |
| --- | ---: | ---: | ---: | ---: |
| Loto 6/49 | 0 | 0% | 0 | 0% |
| Loto 5/40 | 0 | 0% | 0 | 0% |
| Joker, Urna 1 | 2 | 0,02588% | 2 | 8,33% |
| Joker, Urna 2 | 0 | 0% | 0 | 0% |

Aceste frecvențe descriu simularea și pragul folosit. Cele trei cazuri fără alarme observate au câte 24 de replici.

## Comparație cu selectorul dinamic din aplicație

Am folosit intervalul 13.09.2026–29.10.2026, stabilit înainte de evaluare pentru a include ultima tragere dublă comună din arhivă. Fiecare joc are 14 zile și 15 extrageri evaluate. Cele două extrageri din 13 septembrie au același portofoliu pe zi și două rezultate distincte. Metoda fixată de Re-Bench a folosit numai extragerile până la 10.09.2026 inclusiv. Generatorul dinamic și cele trei metode noi au refăcut portofoliul pe baza întregului istoric anterior fiecărei zile.

Fiecare dintre cele cinci politici a avut zero extrageri cu 4+ sau 5+ potriviri pe aceeași variantă oficială. La 6/49 a avut și zero extrageri cu 6 potriviri. Pragul 6+ este neaplicabil variantelor de cinci numere de la 5/40 și Joker. Toate portofoliile au putut fi evaluate, la același cost în cadrul fiecărui joc. Tabelul redă câte extrageri au adus cel puțin un premiu conform categoriei oficiale, inclusiv premiile sub pragul 4+.

| Joc | Cost portofoliu | Re-Bench fixat | Dinamic | BOCPD | Stacking | Fixed Share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Loto 6/49 | 96,50 lei | 0 | 0 | 1 | 1 | 0 |
| Loto 5/40 | 90,50 lei | 0 | 0 | 0 | 0 | 0 |
| Joker | 98,50 lei | 0 | 1 | 1 | 0 | 0 |

Re-Bench fixat a ales pentru urna principală `frequency` la 6/49, `compact_multiscale_shrink` la 5/40 și `compact_transition_prior` la Joker. Selectorul dinamic a ales `markov_lag2` în toate cele 14 zile pentru 6/49 și 5/40. La Joker, a folosit frecvența în toate cele 14 zile. Pentru urna principală, selecția nu a depășit martorul aleator. Pentru Urna 2, aplicația a respins decizia globală incompatibilă cu istoricul disponibil la data prognozei și a folosit frecvența. În celelalte perechi de joc și politică nu s-a raportat nicio selecție de rezervă. Pentru cele două metode numerice, Urna 2 folosește frecvența, iar Fixed Share folosește regula proprie de portofoliu.

Câmpul `selector_replay` din JSON păstrează decizia folosită, motivul selecției de rezervă, costul și amprenta portofoliului pe fiecare zi, distribuția potrivirilor pe cea mai bună variantă și indicatorii 4+, 5+ și 6+. Cele 15 extrageri per joc oferă o probă de reproducere a politicilor, cu prea puține evenimente pentru o concluzie privind avantajul predictiv.

## Reproducere

Studiul folosește ordinea corectată a celor două trageri Loto 6/49 din 01.12.2019. Recalcularea a schimbat amprenta istoricului 6/49. Martorul aleator, a cărui sămânță numerică depinde de istoricul primit, a trecut de la o extragere cu 4+ la două. Rezultatele metodelor comparate, valorile p și intervalele au rămas identice.

Rulați din directorul proiectului, cu configurația Python obișnuită și o copie a istoricelor verificate pe 25.11.2026. Fișierul JSON consemnează amprentele SHA-256 ale intrărilor, numărul de extrageri folosite, ferestrele de test și semințele simulării și ale bootstrap-ului.

```powershell
python -m scripts.analysis.advanced_lr_validation --history _ISTORIC --days 104 --budget 100 --output advanced_lr_validation_reprodus.json
python -m scripts.analysis.advanced_lr_selector_replay --history _ISTORIC --first-day 2026-09-13 --last-day 2026-10-29 --budget 100 --workers 3 --output selector_replay_reprodus.json
```

[Datele agregate și comparațiile statistice](advanced_lr_validation_2026_11_25.json)
