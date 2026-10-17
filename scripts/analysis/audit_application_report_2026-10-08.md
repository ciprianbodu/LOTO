# Audit aplicație — 8 octombrie 2026

## Rezultat

Codul de la `origin/main` 43efcef a fost revizuit pe cinci zone: pool și
bilete, bench și decizie, walk-forward, worker și UI, date și actualizatoare.
Accentul a căzut pe calea folosită acum: pool 16 cu ținta 4+. Fiecare
constatare a fost verificată de un al doilea agent care a încercat s-o
infirme și a reprodus-o pe copii izolate. Au rămas 18 constatări, toate
confirmate: una gravă, opt medii și nouă minore. Toate sunt reparate. Fiecare
reparație are un test care pică pe codul vechi și trece pe cel nou. Reparațiile
au trecut apoi printr-o revizie adversarială separată, iar observațiile ei sunt
reparate și ele.

Nucleul a rezistat: pool-ul, biletele și hiturile calculate sunt corecte.
Defectele stau în jurul lui: date scrise în istoric, decizia salvată pe altă
țintă, texte și numere afișate pentru altă metodă decât cea care a produs
pool-ul.

Nu se promovează nicio metodă. La k16, ținta 4+, metoda aleasă nu trece
corecția Holm pe niciun joc (6/49 p = 0,435; 5/40 p = 1,000; Joker p = 0,125).

## Defecte reparate

### Date și actualizatoare

1. **Spania pierdea zilele 1-9 ale fiecărei luni (grav).** Exportul
   lawebdelaprimitiva.com scrie `Jue-1-10-2026`, iar parserul cerea două
   cifre. Din cele 4198 de rânduri citea 2958, niciunul din zilele 1-9.
   Actualizatorul raporta „la zi”, iar după prima extragere din 10-10 golurile
   deveneau permanente și se publicau. Parserul acceptă acum ziua și luna fără
   zero. `plan_update` refuză și o sursă care nu mai listează o zi stocată.
   CSV-ul versionat nu are încă 01, 03 și 05-10-2026; prima rulare ACTUALIZARI
   le adaugă singură.
2. **O extragere corectată pe loto49.ro intra drept a doua extragere a zilei.**
   `update_csv` adăuga rândul corectat lângă cel stocat, iar PushHistory îl
   publica. Acum zilele comune site-CSV din ultimele 60 de zile trebuie să
   coincidă; în ziua ultimei extrageri stocate, site-ul poate avea în plus a
   doua extragere a zilei.
3. **Un rând invalid de pe site era sărit tăcut.** Extragerile de după el
   intrau, iar el nu mai venea niciodată. Acum un rând respins sau necitibil,
   datat la sau după ultima extragere stocată, oprește scrierea jocului. Din
   revizie: și rândurile pe care modelul nu le potrivea (cifră în plus, celulă
   lipsă, care împrumuta „20” din anul următor).
4. **O extragere copiată după rândul anterior** (tiparul 24-10-2024 = 27-10-2024)
   se scria în istoricul local și era refuzată abia la commit. Acum nu se
   scrie.
5. **O pagină fără extrageri** (altă structură, pagină de protecție) era
   raportată „la zi”. Acum e EROARE, iar START_8000 și ACTUALIZARI o arată în
   consolă.
6. **`verifica_istoric` accepta o dată din viitor** (27-09-2062), care devenea
   ultima extragere și oprea `update_csv` pe toate stațiile. Acum refuză datele
   de după mâine, datele mai vechi decât rândul anterior și datele scrise
   necanonic („4-10-2026”). Toate cele 13 CSV-uri versionate trec.

### Decizie

7. **Decizia salvată putea fi pe 3+ cu 4+ selectat.** Un bench din consolă,
   o schimbare de țintă refuzată sau UI-ul oprit în timpul bench-ului lăsau
   decizia veche, iar panoul spunea „Benchmark la zi”. La pool 16, 6/49 juca
   `ridge_pooled_feats` (câștigătorul pe 3+) în loc de `markov_lag2`. Decizia
   își poartă acum ținta, se recalculează la pornire după deschiderea portului
   și la finalul bench-ului; pe folds incomplet panoul avertizează. Bench-ul
   din consolă ia ținta din UI.
8. **Bench-ul scria decizia în doi pași.** În cele ~9 s dintre scrieri, sau
   definitiv la o oprire în fereastra aceea, producția juca vechii câștigători
   fără poarta față de random. Acum o singură scriere atomică.

### Walk-forward

9. **„Istoric hits” putea arăta altă metodă.** După o schimbare a țintei sau un
   Re-Bench terminat în timpul validării, jocurile validate după aceea erau
   scorate de metoda nouă și afișate sub pool-ul produs de cea veche, fără
   avertisment. Acum un joc a cărui metodă nu mai e cea din decizie e sărit,
   cu mesaj în panou și în raport.

### UI și mail

10. **Mailul nu spunea „avantaj nedemonstrat”**, deși toate celulele k16 la 4+
    sunt nedemonstrate. Acum îl spune, ca panoul.
11. **START_8000 ștergea ultimul rezultat** după ce UI-ul îl afișase, deci a
    doua zi „📜 Istoric hits” era gol. `reset_jobs.py` păstrează acum ultimul
    job COMPLETED.
12. **Mailul data numerele pentru extragerea de azi** chiar când extragerea de
    azi era deja în CSV. Acum data e după ultima extragere din istoric.
13. **Nota Holm și ratingul veneau din decizia de acum**, nu de la metoda care
    a produs pool-ul. Acum apar numai pentru aceeași metodă, citite la pool-ul
    cu care s-a ales metoda.
14. **„Rezultate RECUPERATE” arăta ora UTC** drept ora locală. Acum ora locală.
15. **Butoanele de generare** sugerau că „setări manuale” ocolește decizia
    bench-ului. Ambele joacă metoda din decizie; etichetele o spun.

### Pool și bilete

16. **Un buget egal cu designul complet** dădea sub 100% (6/49 pool 16,
    garanție 3, buget 45: 99,46%) sau bilete în plus. Acum, în motor, bugetul
    care cuprinde designul primește designul.
17. **La egalitate de bilete, designul rămânea** și când greedy-ul îl domina
    exact (5/40 pool 16, garanție 3: 4+ 5,06% față de 5,10%). Acum se ia
    greedy-ul.
18. **„Bilet complet” dispersat aplica limita de consecutive relaxată pentru
    pool** (până la 16) pe fiecare variantă. Acum limita cerută.

## Pool 16, ținta 4+

Separat de audit, aceeași zi a adus mai puține bilete pentru aceeași garanție
și șanse mai mari la buget peste 64 de bilete:
`pool16_4plus_2026-10-08.md`.

## Verificare

Detaliile sunt în §2 din AGENTS.md: numărul testelor, rularea suitei și
ce s-a verificat pe date reale. Pe paginile reale loto49.ro descărcate pe
08-10, `update_csv` nu refuză nimic, iar toate jocurile rămân „la zi”.
