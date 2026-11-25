# Registrul prospectiv LR

Apăsarea butonului „Generează recomandarea” sau confirmarea explicită a bazei afișează jocul, momentul local al înregistrării și data tragerii țintă. Recomandările jucabile din acel rezultat se înregistrează automat. Mișcarea sliderului și celelalte recalculări automate au rol de previzualizare.

Registrul reține data și ora cu decalajul fusului Europe/Bucharest, data țintei și ora-limită, identificatorul Git dacă este disponibil și o amprentă SHA-256 a surselor locale și a catalogului. Identitatea istoricului include fișierul, data până la care a fost folosit, volumul și amprenta transmisă de motorul de predicție. Înregistrarea cuprinde metodele, setările și poolurile, zonele oficiale, fiecare variantă cu Jokerul aferent și multiplicitatea plătită, precum și costul pe bilete.

## Ținta prospectivă

Programul obișnuit are trageri joi și duminică. Într-o zi de tragere, recomandarea poate viza acea zi până la ora 15:30, limita pentru joc online, dacă rezultatele zilei nu apar deja în istoricul cunoscut. După limită sau după apariția unui rezultat al zilei, registrul alege următoarea tragere eligibilă. [Programul Loteriei Române](https://www.loto.ro/?page_id=1066)

Calculul datei acceptă excepții cu dată și oră furnizate dintr-un program publicat. Interfața curentă folosește programul săptămânal obișnuit. O modificare ulterioară a calendarului rămâne separată de înregistrarea inițială și nu mută retrospectiv predicția pe altă dată.

## Import și verificare

Actualizarea normală a istoricului consultă rezultatele lunare oficiale pentru zilele ajunse la termen din registrul local. Fiecare rezultat distinct al datei țintă primește propria evaluare. Pentru 5/40, identificatorul păstrează separat a șasea bilă, iar evaluarea ține seama de primele cinci la categoria I. La Joker, rezultatul și fiecare variantă includ a doua urnă.

Predicțiile și evaluările se păstrează separat într-un fișier SQLite local, de regulă în directorul de funcționare al aplicației. Două înregistrări identice pentru aceeași țintă returnează prima înregistrare. Reimportul aceluiași rezultat păstrează o singură evaluare. Rezultatele recente sunt reverificate timp de 31 de zile pentru a prelua o posibilă tragere suplimentară publicată separat. O țintă fără rezultat oficial rămâne în așteptare.

Probele acoperă pragul orar din aceeași zi, trecerea la ora de vară, o tragere mutată în afara programului săptămânal, refuzul capturii retrospective, scrierea concurentă, reluarea după repornire și importul separat al tragerilor distincte.
