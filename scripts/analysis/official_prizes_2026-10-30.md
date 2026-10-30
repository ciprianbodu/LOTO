# Premii oficiale in Istoric hits — 30.10.2026

In tabelele pliabile `VARIANTA 4+` si `VARIANTA 5+`, coloana `Nimerite`
include categoria si valoarea unitara publicata de Loteria Romana pentru
varianta afisata. Exemplu verificat pentru 29.10.2026, Loto 6/49:
`4 numere (cat. III — 1.015,35 lei)`.

Suma este cea publicata pentru o varianta castigatoare la acea extragere.
Nu este un castig total al wheel-ului si nu reprezinta un calcul de ROI.
Tabelul pool-ului pastreaza numai numarul de hituri; un pool nu este o varianta.

## Surse si categorii

Arhiva oficiala lunara, accesata prin filtrul POST `select-year` / `select-month`:

- [6/49](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/649_si_noroc/rezultate_extragere.html)
- [5/40](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/540_si_super_noroc/rezultate_extrageri.html)
- [Joker](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/joker_si_noroc_plus/rezultate_extrageri.html)

GET ignora filtrul lunar. Linkul din tabel deschide pagina oficiala; tooltipul
arata data si luna care trebuie selectata pentru verificarea in arhiva.
Fixturele reduse din `testdata/official_prizes` pastreaza numai selectorii
si tabelele numerice, cu URL-ul si parametrii cererii in `sources.json`.

Regulile provin din [FAQ oficial](https://www.loto.ro/?page_id=1070),
[descrierea Joker](https://www.loto.ro/?p=3904) si
[brosura 5/40](https://www.loto.ro/images/upload/pages/Brosura_Loto_5_din_40.pdf).

- 6/49: 6 / 5 / 4 / 3 numere corespund categoriilor I / II / III / IV.
  Categoria IV exista din 02.05.2010; tabela oficiala este verificata inainte
  de atribuirea unei categorii istorice.
- 5/40: categoria I cere primele cinci numere extrase; II cere cinci din
  toate cele sase, iar III patru din sase. Se foloseste ordinea oficiala,
  nu ordinea crescatoare din celula de afisare.
- Joker: categoria depinde si de bila din Urna 2 a aceleiasi variante.
  Daca numarul sau categoria nu pot fi confirmate, nu se afiseaza o suma.

`REPORT` este separat de valoarea platita si se afiseaza ca report, fara a
prezenta fondul sau reportul drept premiu incasat. Pentru valorile de dinainte
de denominarea din 2005, cand arhiva nu precizeaza explicit unitatea, se pastreaza
valoarea istorica publicata cu explicatia unitatii neconfirmate; nu se face o
conversie presupusa in lei actuali.

## Cache si interfata

Implicit: `runtime_paths.RUNTIME_ROOT / official_prizes.db`, adica
`D:\_BUILD\_LOTO\official_prizes.db` pe statia curenta. Este stare locala,
in afara Google Drive, si nu se comite.

SQLite pastreaza jocul, data, numerele, Joker, categoriile, numarul de castiguri,
sumele publicate, reportul, provenienta si amprenta raspunsului oficial.
Identitatea include numerele: doua trageri in aceeasi zi nu se confunda.
Nu exista rezerva care sa aleaga premiul numai dupa data.

O cerere descarca o luna intreaga a unui joc. Lunile reusite din trecut sunt
refolosite, iar luna curenta, lipsurile si erorile au intervale de reincercare.
Lease-urile SQLite impiedica descarcarea simultana a aceleiasi luni de catre
doi clienti. Un refresh gol, partial sau esuat nu elimina date verificate.

La deschiderea sectiunii se cer numai extragerile paginii vizibile. HTTP si
SQLite ruleaza intr-un thread separat; temporizatorul UI citeste numai memoria.
Filtrele si paginarea se pastreaza cand sosesc sumele. Butonul de reincercare
permite reluarea explicita a datelor lipsa, fara a descarca din nou premiile
deja gasite.

Arhiva poate omite unele trageri suplimentare. Daca numerele nu coincid exact,
premiul ramane indisponibil. Nici lipsa contextului dintr-un cache WF vechi
nu este completata prin presupuneri. Jocurile straine nu folosesc aceste premii.

Import manual, optional, din radacina proiectului:

```powershell
python -m scripts.sync_official_prizes --year 2026 --month 10
python -m scripts.sync_official_prizes --game 6/49 --year 2026 --month 10 --force
```

## Verificare

Probe live: octombrie 2026, 27 de extrageri, cu verificarea independenta a
valorilor din 29.10.2026; apoi aceeasi baza redeschisa cu accesul la retea
interzis, fara nicio noua descarcare. Septembrie a adaugat 24 de extrageri,
pentru un total de 51 in baza pregatita pentru instalare.

Testele acopera parserul, persistenta, erorile si concurenta, categoriile,
tragerile din aceeasi zi, ordinea 5/40, Joker, unitatile istorice si interfata.
Verificarea in browser a confirmat afisarea pe cele trei jocuri si la 390 px.
O problema de actualizare a copiilor de randuri NiceGUI a fost reprodusa in
browser si reparata; testul cu `ui.table` real cade fara corectura.

Suita completa pe Windows / Python 3.14.10: 3801 teste trecute, 20 sarite,
zero esecuri (15 min 39 s). Dupa corectura gasita in browser s-au reluat cele
94 de teste UI/regresii, inclusiv noul test cu tabel NiceGUI real; toate au
trecut. Ultima precizare a tooltipului a fost verificata separat in testul
formatterului.

Auditul aplicatiei: 13 istorice, 848 comparatii de scoruri, 154 de designuri,
26 de fluxuri de generare, 13 profile de cover complet, worker pe 13 jocuri si
UI HTTP 200; fisierele protejate ale copiei testate au ramas intacte.
Sintaxa si `git diff --check` trec. Pagina de verificare vizuala foloseste
variante de test si premii oficiale; nu reprezinta rezultate obtinute de
scoreri. Aplicatia activa nu a fost repornita.
