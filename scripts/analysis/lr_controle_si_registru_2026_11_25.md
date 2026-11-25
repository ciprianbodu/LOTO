# Controalele de acoperire LR și identitatea extragerilor din registru

Am renumărat toate variantele plătite din 66 de portofolii ale versiunii `e6cad789115d22ac839fc5e9390d3838bc4c9bfb`. Cele 42 de controale din grila de referință includ pragurile taxei pe bilet și configurațiile cu două bile Joker eligibile. Celelalte 24 provin din generator, pentru trei jocuri, patru bugete și două obiective. Generatorul a folosit arhiva până la 22.11.2026, baza completă, metoda normală, selecția dinamică, lookback 100 și cinci bile Joker eligibile. Am păstrat separat fiecare bilet, zonă, variantă plătită și slot Joker. Numerele din fișiere documentează controlul istoric.

## Portofolii și costuri

Tabelul arată numărătorii obiectivului precizat. Pentru 6/49 numitorul este 13.983.816, pentru 5/40 este 3.838.380, iar pentru Joker este 1.221.759 la 4+ și 24.435.180 la orice premiu. Cele șapte valori de pornire din cerere pentru generator coincid cu renumărarea. La 5/40, obiectivele 4+ și orice premiu au aceleași bilete pentru bugetul de 300 lei.

| Origine | Joc | Obiectiv | Buget, lei | Bile Joker eligibile | Cost, lei | Numărător verificat | Prima aplicare, s | Din cache, ms |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Generator | 6/49 | 4+ | 300 | — | 296.50 | 383,269 | 0.466 | 0.067 |
| Generator | 6/49 | 4+ | 400 | — | 392.50 | 468,412 | 0.464 | 0.068 |
| Generator | 5/40 | 4+ | 300 | — | 285.50 | 119,292 | 0.515 | 0.074 |
| Generator | joker | 4+ | 200 | 5 | 196.50 | 5,325 | 0.469 | 0.075 |
| Generator | joker | 4+ | 300 | 5 | 295.00 | 7,862 | 0.471 | 0.071 |
| Generator | joker | Orice premiu | 300 | 5 | 296.00 | 6,720,220 | 0.596 | 0.083 |
| Generator | joker | 4+ | 400 | 5 | 386.50 | 9,899 | 0.508 | 0.077 |
| Grilă | joker | Orice premiu | 300 | 2 | 295.50 | 5,101,830 | 0.543 | 0.080 |

Timpii măsoară aplicarea motorului de acoperire existent, cu pool-ul deja selectat, pe Windows cu CPython 3.15.2. Fiecare configurație a pornit într-un proces nou. Timpul din cache este mediana a trei repetări în același proces. Biletele celor patru execuții au coincis. La 6/49 cu 400 lei, cele 49 de variante distincte dau exact 49/13.983.816 la 6/6.

În materialul accesibil pentru această verificare, ZIP-ul `teste_regresie_e6cad78.zip` conține 48 de fișiere de test ale versiunii de bază. SHA-256 este `89df874e15ca00282b66092e390289395031ef0446cf283a71f17aec3f0c9472`. Reproducerea candidatului `expanded-placement-beam12-two-zone-v1` necesită arhiva experimentală cu prototipul și portofoliile candidate. Câștigurile de 8/42 și 7/24 sunt rezultatele comunicate de autor pentru acea arhivă. Controalele publicate aici confirmă numai portofoliile de pornire. Politica predictivă și alegerea ferestrei de învățare se păstrează.

## Verificare fără căutare

În rădăcina proiectului se poate verifica atât amprenta sursei, cât și desfășurarea schemelor, costul și numărătorile, folosind această comandă.

```sh
python -m scripts.analysis.verify_lr_portfolio_witnesses testdata/lr_portfolio_witness_controls.json --source testdata/portofolii_initiale_e6cad78.json
```

Fiecare dintre cele 66 de cazuri are categoria `incumbent_control`. Pozițiile principale încep de la 1, iar indexul slotului Joker de la 0. Un martor candidat verificat pe aceeași geometrie poate costa cel mult cât portofoliul de pornire și trebuie să păstreze numărătorul obiectivului și pe cei de 4+, 5+ și 6 unde pragul este posibil. Reducerea costului, duplicatele plătite și șansa de cel puțin un succes se calculează separat. Aceste verificări privesc probabilitățile necondiționate și nu certifică un maxim global sau toate garanțiile condiționate.

## Registru, cache și configurații imposibile

Pe pagina oficială din septembrie 2026, cele două trageri 6/49 din 13 septembrie au indicii de principală și suplimentară. Într-un registru de test, patru importuri simultane ale paginii au adăugat în total două evaluări. Pentru proba de rectificare am atribuit principalei combinația suplimentarei. Au rămas două evenimente active cu numere identice și s-a păstrat o revizie anterioară a principalei. Reimportul paginii inițiale nu a schimbat punctajul activ. Separat, am construit o bază cu codul original de la `e6cad78`. După migrare, raportul a identificat două evaluări vechi în așteptare. Reimportul aceleiași pagini oficiale le-a reconciliat cu cele două trageri. Conținutul inițial al predicției și al ambelor evaluări a rămas identic octet cu octet. Amprenta paginii efectiv consultate este `4a10a208d595e5f66344542b2e9a09fa860d6983bb5c3e9aefc0b8b174275473`. [Regulile registrului și ale migrării](lr_prospective_2026_11_25.md)

Schimbarea funcției de căutare a invalidat în același proces atât recomandarea pe buget, cât și rezultatul reutilizat de interfață. În browser, după repornire, workerul și interfața au redat rezultatul finalizat. În test am simulat trimiterea e-mailului și oprirea sistemului. Pentru 5/40, codul 37, 200 lei, în baza 10–40, metoda normală a generat 39 de variante la 195,50 lei. În modul invers, după excluderea celor 16 numere directe, rămân 15. Generatorul și ambele moduri de validare explică de ce schema de 16 numere nu poate fi completată.
