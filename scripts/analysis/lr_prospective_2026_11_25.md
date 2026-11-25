# Registrul prospectiv LR

Apăsarea butonului „Generează recomandarea” sau confirmarea explicită a bazei afișează jocul, momentul local al înregistrării și data tragerii țintă. Recomandările jucabile din acel rezultat se înregistrează automat. Mișcarea sliderului și celelalte recalculări automate au rol de previzualizare.

Registrul reține data și ora cu decalajul fusului Europe/Bucharest, data țintei și ora-limită, identificatorul Git dacă este disponibil și o amprentă SHA-256 a surselor locale și a catalogului. Identitatea istoricului include fișierul, data până la care a fost folosit, volumul și amprenta transmisă de motorul de predicție. Amprenta se recalculează din conținut la fiecare generare explicită. Înregistrarea cuprinde metodele, setările și poolurile, zonele oficiale, fiecare variantă cu Jokerul aferent și multiplicitatea plătită, precum și costul pe bilete.

## Ținta prospectivă

Programul obișnuit are trageri joi și duminică. Într-o zi de tragere, recomandarea poate viza acea zi până la ora 15:30, limita pentru joc online, dacă rezultatele zilei nu apar deja în istoricul cunoscut. După limită sau după apariția unui rezultat al zilei, registrul alege următoarea tragere eligibilă. [Programul Loteriei Române](https://www.loto.ro/?page_id=1066)

Calculul datei acceptă excepții cu dată și oră furnizate dintr-un program publicat. Interfața curentă folosește programul săptămânal obișnuit. Ora-limită se verifică din nou înainte de confirmarea scrierii. O modificare ulterioară a calendarului rămâne separată de înregistrarea inițială și nu mută retrospectiv predicția pe altă dată.

## Import și verificare

Actualizarea istoricului consultă arhiva oficială pentru zilele ajunse la termen. Registrul identifică tragerea după codul oficial publicat sau după tipul ori ordinea confirmate pentru acel joc și acea dată. O tragere principală și una suplimentară primesc evaluări separate chiar dacă au aceleași numere. Dacă mai multe rânduri pretind aceeași identitate sau informațiile din sursă nu o stabilesc, registrul consemnează conflictul, împreună cu amprenta paginii și datele observate.

O corecție confirmată creează o revizie care indică revizia înlocuită. La 5/40 amprenta rezultatului păstrează separat a șasea bilă, iar categoria I folosește primele cinci. La Joker amprenta și evaluarea includ a doua urnă. Raportul obișnuit preia doar ultima revizie acceptată pentru fiecare tragere. Reimportul unei revizii deja înlocuite nu o reactivează. Când pagina nu publică un număr de revizie sau o oră oficială a rectificării, registrul folosește ordinea observării, consemnează incompatibilitățile din aceeași pagină și păstrează edițiile cunoscute în istoricul de audit.

Migrarea SQLite se aplică într-o tranzacție și păstrează fiecare predicție și evaluare veche exact în forma inițială. În raport apare separat câte evaluări vechi așteaptă reconcilierea. Registrul le poate lega de o revizie când aceeași pagină oficială, verificată prin amprentă, confirmă o singură tragere pentru acel rezultat. Când două trageri din aceeași pagină au rezultat identic, rândul vechi rămâne la audit fără atribuire. Cititorul `read_prospective_report` afișează evaluările active și conflictele și poate include reviziile, evaluările înlocuite și rândurile vechi. Actualizarea obișnuită raportează separat înregistrările adăugate și evaluările active.

Importul automat verifică țintele fără evaluare confirmată și reverifică rezultatele din ultimele 31 de zile, inclusiv pentru trageri suplimentare. Pentru un control punctual al țintelor mai vechi se poate apela `refresh_official_evaluations(include_settled=True)`. Fișierul SQLite rămâne local. Probele acoperă rectificarea, reimportul, două trageri cu aceleași numere, conflictul de identitate, migrarea, scrierea concurentă, întreruperea tranzacției și citirea după repornire.
