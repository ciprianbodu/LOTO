# Audit aplicație și hituri — 1 octombrie 2026, runda 2

## Rezultat

Auditul nou repară selecția pe ferestre, validarea Joker și legătura dintre
rezultatul workerului și istoricul folosit de UI/WF. Căutarea geometrică pentru
bugete limitate găsește aranjamente de bilete mai bune, inclusiv la pool 16,
fără schimbarea scorerului sau creșterea numărului de variante.

Optimizarea rămâne automată la un buget pozitiv, inclusiv în „Bilet complet”.
Pornirea normală cu START_8000.bat este suficientă. Cache-urile au fost
actualizate la worker v9 și WF v28, pentru a nu servi rezultate cu vechea
geometrie sau vechea validare Joker.

## Defecte reparate

1. **Ferestre suplimentare puteau promova artificial un scorer.** Decizia
   verifica prezența ferestrelor comune, dar calcula statisticile și pe
   ferestrele disponibile numai la un candidat. Un exemplu reproductibil
   transforma un scorer care pierdea pe ferestrele comune în câștigător prin
   șase ferestre suplimentare favorabile. Acum ratele, Wilson, liftul,
   consistența, egalitățile, fallback-ul, ensemble-ul și sim_depth folosesc
   exact ferestrele comune. UI importă același contract; rândurile suplimentare
   nu mai schimbă decizia sau cifrele afișate.
2. **Urna 2 putea raporta hit fals prin trunchiere.** Valorile fracționare
   precum 1,5 deveneau 1; infinitul putea opri pasul. Validarea comună cere
   întregi 1..20 pentru bila reală și cea jucată. Datele invalide nu produc hit.
3. **Un upload nou putea schimba validarea unui rezultat vechi.** UI și
   walk-forward foloseau dataset-ul live, deși workerul procesase snapshot-ul
   din config_json. Acum păstrează sursa și identitatea țară/joc din jobul
   persistent, inclusiv la recuperarea după repornire. Înlocuirea CSV-ului
   nu schimbă rezultatul afișat sau istoricul lui de validare. Un snapshot
   corupt este raportat indisponibil; nu este înlocuit tacit cu alt istoric.
   Schema cozii și payload-ul rezultatului nu s-au schimbat.
   Decodarea păstrează datele ZZ-LL exact ca workerul (convert_dates=False),
   inclusiv când ziua și luna sunt ambele cel mult 12.
4. **Ultima extragere depindea de ordinea CSV-ului.** Panoul folosește ordinea
   cronologică normalizată și ultima extragere validă. CSV-urile inversate,
   sursele încărcate și identitățile externe au regresii separate.
5. **Testul de lock Git depindea de procese externe.** Procesele Git folosite
   de Codex pentru comparații împiedicau corect ștergerea lock-ului în helper,
   dar testul presupunea că inventarul global este gol. Testele izolează
   inventarul în clonele temporare și verifică ambele situații. Garda reală
   din lansator rămâne aceeași; procesele externe nu au fost oprite.
6. **Auditorul putea compara WF cu rânduri respinse de motor.** Acum folosește
   aceeași cronologie validată și indexare după eliminarea rândurilor invalide.
   Sursa originală pentru hash și cheia cache rămâne intactă.

## Creșterea exactă a șanselor pe bilete

`balanced_cover_positions` adaugă trei ordini deterministe de explorare,
cu acoperirea submulțimilor de patru prioritară și cea de trei secundară,
apoi cel mult trei treceri de schimburi locale. Lucrează numai cu pozițiile
geometrice; nu citește istoricul sau scorurile și nu modifică ranking-ul.
Generatorii precedenți sunt evaluați înaintea celor noi.

Acceptarea se face după maparea canonică și repararea numerelor lipsă:
număr identic de bilete, fără duplicate, numărători cel puțin egale pentru
FIECARE prag de hituri și FIECARE mărime a intersecției pool-extragere,
cu îmbunătățire strictă la 3+ sau 4+. Această verificare păstrează toate
probabilitățile uniforme și acoperirea clasică a garanției. Limitele rămân
pool ≤16, pick ≤6, garanție < pick, 1..64 variante și bază incomplet acoperită.

Matricea verificată compară noul algoritm cu **hitcover-ul automat anterior**:
pool 11/16, pick 5/6, garanție 3/4, plafon 3/7/10/30. Din 32 configurații,
15 se îmbunătățesc și 17 rămân identice; zero regresii la vreun prag.
Testele includ o enumerare independentă a intersecțiilor.

Exemple: pool 16, garanție cerută 4, scoruri fixe `(număr * 17) % 23`.
Probabilitățile sunt necondiționate, pentru **cel puțin o variantă** din întregul
set. Bugetul, pool-ul și costul sunt identice înainte și după.

| Joc / variante | 3+ înainte → după | 4+ înainte → după |
|---|---:|---:|
| 6/49 / 7 | 10,9242% → 11,3145% | 0,6857% → 0,6872% |
| 5/40 / 10 | 11,1821% → 15,0705% | 0,7151% → 0,7802% |
| Joker Urna 1 / 10 | 4,4766% → 6,0139% | 0,1581% → 0,1645% |

La 5/40 ținta premiată folosită de aplicație este 4+; 3 numere nu aduc premiu.
Joker măsoară aici numai Urna 1. Șansa de hit a pool-ului nu se schimbă.
Alte configurații, inclusiv unele bugete 6/49, pot rămâne identice.
Nu există afirmație de optimalitate, predicție sau profit.

Căutarea este CPU și memoizată, cu cache-uri limitate. Pe matricea de mai sus,
prima construcție a durat cel mult 4,486 s; repetarea cel mult 0,115 s.
La plafon 64, cazurile măsurate au durat 6,752 s (pick 6) și 4,629 s (pick 5)
la rece, respectiv 0,026/0,017 s la repetare. Sunt măsurări locale, nu ETA
pentru întreaga aplicație.

## Dovezi de execuție

Auditul a folosit o copie stabilă a datelor locale de la commit-ul
93757bfb855fa59e355f237bc920d953ab9d1cf2. Codul Python testat din copie este
identic cu cel integrat. Re-Bench-ul utilizatorului a rămas activ: folds.csv
și report.json modificate de acesta sunt păstrate și excluse din commit.
Nu am rescris decizia, istoricul pool-urilor sau rezultatele lui de benchmark.
Verificările de hash înainte/după privesc copia izolată auditată.

- Python 3.14.7, Windows; 52 metode, 51 per joc în curare, random adăugat de runner.
- 13 istorice din registru, validate integral; 832 verificări scoring/ranking.
- 151 designuri locale, toate acoperite exhaustiv, zero blocuri eliminabile.
- 26 pipeline-uri și 24 comparații exacte de probabilitate.
- Worker separat cu SQLite temporar: 13 joburi COMPLETED, decodare validă.
- UI real pe port liber: HTTP 200, surse și setări izolate.
- 111 regresii noi pentru ferestre, Joker, sursa rezultatului și hitcover,
  plus cazul suplimentar al lansatorului cu Git activ.
- 60 clasamente randate din snapshot-ul benchmarkului, fără discrepanțe.
  Ultimul rezultat persistent: 6/49, Joker și 5/40, pool 6, respectiv 1/5/5
  variante, acoperire 100%. Auditorul a recalculat 776 înregistrări WF v28
  pentru 6/49, fără discrepanțe de hituri; pentru Joker și 5/40 cache-ul acestei
  configurații lipsește și este raportat ca atare.
- Suita completă finală: 1.855 trecute, 17 omise, zero eșecuri (529,32 s).
  Cele 272 avertismente sunt deprecieri NumPy din generatoare de date de test.
- Compilarea surselor modificate și git diff --check: trecute.

## Diagnosticul statistic al scorerilor

Trei ferestre distincte de câte 60 extrageri, recalculare înaintea fiecărei
ținte, excluderea tuturor extragerilor din aceeași zi; pool-uri principale
6/8/10/12/16 și top-1 Urna 2. Cele 52 metode au cerut 37.440 evaluări,
fără erori de scorer. Din 1.612 comparații planificate, 1.605 au eșantion complet.
57 valori p brute sunt sub 0,05, dar **zero trec corecția Holm**; minimul ajustat
este 0,523217. Nu am promovat un scorer nou și nu am activat un blend nevalidat.

Diagnosticul este retrospectiv și nu reprezintă un holdout extern neatins.
Auditul validează contractele și coerența istoricului local; nu reverifică
fiecare extragere veche la sursa oficială.

## Reproducere

Folosește o copie stabilă a surselor dacă un Re-Bench scrie în paralel.
Scripturile nu rescriu benchmarkul sau decizia de producție:

```powershell
python scripts/analysis/audit_application.py --output scratch/application_audit.json
python scripts/analysis/audit_method_windows.py --workers 2 --output scratch/method_windows
python scripts/analysis/audit_output.py --report scratch/rendered_report.txt
python -m pytest -q
```
